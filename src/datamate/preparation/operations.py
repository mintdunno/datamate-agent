"""Reviewed operations over immutable raw batches, with bounded native working state."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, localcontext
from uuid import uuid4

import polars as pl
from duckdb import DuckDBPyConnection
from openpyxl.styles.numbers import is_date_format

from datamate.preparation.catalog import accept_entity_links, reconcile_quantity, resolve_units
from datamate.preparation.models import (
    Binding,
    CandidateFact,
    EvidenceRef,
    LineageEdge,
    RawDisposition,
    RawRecord,
    SemanticBindings,
    StagedTable,
    StructuralDecision,
    TypedValue,
)
from datamate.preparation.normalization import (
    CleaningPlan,
    CleaningStep,
    encoded,
    parse_candidates,
    select_parse,
)
from datamate.preparation.storage import _decoded, read_staged_table

_PARSE_ROLES = {
    "money": {
        "order_total",
        "line_net_amount",
        "payment_amount",
        "unit_cost",
        "unit_price",
        "line_discount_amount",
        "order_discount_amount",
    },
    "percentage": {"line_discount_percent", "order_discount_percent"},
    "quantity": {"quantity", "inventory_quantity", "quantity_base"},
    "id": {"order_id", "product_id", "customer_id"},
    "date": {"ordered_at", "payment_at"},
    "datetime": {"ordered_at", "payment_at"},
}
_GROUP_RULE = (
    "| Ô để trống ở các dòng sau của cùng một nhóm dòng | giống ô đó ở dòng đầu nhóm: khách, "
    "ghi chú, trạng thái của đơn áp cho mọi dòng của đơn. Không phải thiếu dữ liệu, "
    "không có nghĩa là khách lẻ hay đơn hoàn thành |"
)
_DUPLICATE_RULE = (
    "| Hai dòng của cùng một đơn giống nhau ở mọi ô | lỗi xuất file, tính một lần. "
    "Hai dòng giống nhau của hai đơn khác nhau là hai lần bán, tính cả hai |"
)


def _rule_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).replace("*", "").casefold().split())


@dataclass(frozen=True)
class TransformEvent:
    record_id: str
    column_id: str
    step_id: str
    status: str
    before: TypedValue
    after: TypedValue
    reason: str


@dataclass(frozen=True)
class BatchResult:
    facts: tuple[CandidateFact, ...]
    dispositions: tuple[RawDisposition, ...]
    lineage: tuple[LineageEdge, ...]
    events: tuple[TransformEvent, ...]


OPERATION_ORDER = {
    "classify_structure": 0,
    "fill_merged": 1,
    "inherit_group": 2,
    "deduplicate_order": 3,
    "preserve_raw": 3,
    "trim_text": 4,
    "parse_values": 5,
    "resolve_entities": 6,
    "normalize_quantity": 7,
    "derive_line_amount": 8,
}


def validate_plan(plan: CleaningPlan, bindings: SemanticBindings) -> None:
    plan = CleaningPlan.model_validate(plan)
    if plan.bindings_digest != bindings.digest:
        raise ValueError("plan binding fingerprint is stale")
    if sorted(plan.profile_digests) != sorted(bindings.profile_digests):
        raise ValueError("plan profile fingerprints are stale")
    accepted = {b.binding_id: b for b in bindings.accepted}
    rank = OPERATION_ORDER
    previous = {}
    if len({step.step_id for step in plan.steps}) != len(plan.steps):
        raise ValueError("duplicate operation steps")
    for step in plan.steps:
        if rank[step.operation.name] < previous.get(step.table_id, -1):
            raise ValueError("operation prerequisites are out of order")
        previous[step.table_id] = rank[step.operation.name]
        if step.table_id not in bindings.table_ids:
            raise ValueError("step references unknown table")
        referenced = []
        for identifier in step.binding_ids:
            binding = accepted.get(identifier)
            if binding is None or binding.table_id != step.table_id:
                raise ValueError("step references unaccepted or cross-table binding")
            referenced.append(binding)
        if step.operation.name != "preserve_raw":
            if hasattr(step.operation, "columns") and not set(step.operation.columns) <= {
                c for b in referenced for c in b.columns
            }:
                raise ValueError("operation column has no accepted source binding")
            witnesses = {e.model_dump_json() for b in referenced for e in b.evidence}
            witnesses.update(
                EvidenceRef(
                    source=c.source_document.source,
                    sha256=c.source_document.sha256,
                    locator=f"clause:{c.clause_id}",
                    line_start=c.line_start,
                    line_end=c.line_end,
                    quote_sha256=c.quote_sha256,
                ).model_dump_json()
                for c in bindings.clauses
            )
            if not {e.model_dump_json() for e in step.evidence} <= witnesses:
                raise ValueError("operation evidence does not belong to accepted bindings")
            if step.operation.name in {"resolve_entities", "normalize_quantity"}:
                _validate_resolution_step(step, referenced)
            if step.operation.name == "parse_values":
                for column in step.operation.columns:
                    if not any(
                        b.kind == "role"
                        and b.value in _PARSE_ROLES[step.operation.role]
                        and column in b.columns
                        for b in referenced
                    ):
                        raise ValueError("parse role is not justified by accepted column semantics")
            if step.operation.name == "derive_line_amount":
                from datamate.preparation.financial import FORMULA_ROLES

                op = step.operation
                roles = {
                    b.value: b.columns[0]
                    for b in referenced
                    if b.kind == "role" and len(b.columns) == 1
                }
                required = FORMULA_ROLES[op.formula]
                formula_bindings = [
                    b
                    for b in referenced
                    if b.kind == "measure" and b.value.startswith("line_amount:")
                ]
                if (
                    len(formula_bindings) != 1
                    or formula_bindings[0].value != "line_amount:" + op.formula
                    or not set(formula_bindings[0].dependencies)
                    <= {b.binding_id for b in referenced}
                    or any(role not in roles for role in required)
                    or roles.get("quantity") != op.quantity_column
                    or roles.get("unit_price") != op.unit_price_column
                    or (len(required) == 3 and roles.get(required[2]) != op.discount_column)
                    or "line_net_amount" in roles
                ):
                    raise ValueError(
                        "line formula lacks exact source roles, unit basis or authority"
                    )
            if step.operation.name in {"inherit_group", "deduplicate_order"}:
                if not any(
                    b.kind == "role"
                    and b.value == "order_id"
                    and b.columns == (step.operation.group_key,)
                    for b in referenced
                ):
                    raise ValueError("group operation requires an accepted order key")
                expected = (
                    _GROUP_RULE if step.operation.name == "inherit_group" else _DUPLICATE_RULE
                )
                applicable = [
                    c
                    for c in bindings.clauses
                    if c.source_document.source == "rulebook.md"
                    and _rule_text(c.quote) == _rule_text(expected)
                ]
                if not any(
                    e.locator == f"clause:{c.clause_id}" for c in applicable for e in step.evidence
                ):
                    raise ValueError("active rulebook has no qualified group convention")
                if step.operation.name == "deduplicate_order" and (
                    not any(b.kind == "grain" and b.value == "order_line" for b in referenced)
                    or any(
                        b.kind == "grain" and b.value in {"payment_event", "inventory_snapshot"}
                        for b in referenced
                    )
                ):
                    raise ValueError(
                        "raw duplicate exclusion requires unambiguous order-line grain"
                    )
                if step.operation.name == "inherit_group":
                    permitted = {
                        "order_id",
                        "customer_id",
                        "customer_name",
                        "ordered_at",
                        "order_total",
                        "order_status",
                        "payment_method",
                        "note",
                    }
                    if any(
                        not any(
                            b.kind == "role" and b.value in permitted and col in b.columns
                            for b in referenced
                        )
                        for col in step.operation.columns
                    ):
                        raise ValueError("cannot inherit line-level fields")


def _validate_resolution_step(step: CleaningStep, referenced: list[Binding]) -> None:
    identifiers = {b.binding_id for b in referenced}
    if any(not set(b.dependencies) <= identifiers for b in referenced):
        raise ValueError("resolution requires accepted binding dependencies")
    if not {e for b in referenced for e in b.evidence} <= set(step.evidence):
        raise ValueError("resolution requires complete dependency evidence")

    def require_role(column, roles):
        if not any(
            b.kind == "role" and b.value in roles and b.columns == (column,) for b in referenced
        ):
            raise ValueError("resolution field has no accepted source role")

    operation = step.operation
    if operation.name == "resolve_entities":
        column = operation.columns[0]
        role = operation.scope + ("_name" if operation.input_kind == "alias" else "_id")
        require_role(column, {role})
        if not any(
            b.kind == "entity" and b.entity_scope == operation.scope and b.columns == (column,)
            for b in referenced
        ):
            raise ValueError("entity resolution requires scoped source declarations")
    else:
        require_role(operation.product_column, {"product_id", "product_name"})
        require_role(operation.quantity_column, {"quantity", "inventory_quantity"})
        require_role(operation.unit_column, {"unit"})
        if operation.preconverted_column:
            require_role(operation.preconverted_column, {"quantity_base"})
        if not any(b.kind == "unit" and b.columns == (operation.unit_column,) for b in referenced):
            raise ValueError("quantity conversion requires source unit declarations")


class OperationState:
    """Only the active batch stays in Python; cross-batch state belongs to owned SQL tables."""

    def __init__(self, db: DuckDBPyConnection, boundary_id: str):
        self.db = db
        self.table_name = "op_" + uuid4().hex
        db.execute(f'CREATE TEMP TABLE "{self.table_name}"(key VARCHAR PRIMARY KEY,payload JSON)')
        self._authorized = frozenset()
        self._step_ids = {}
        self.binding_entries = {}
        self.source_clauses = ()
        self.bindings = None
        self._closed = False
        self.reset_boundary(boundary_id)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def authorize(self, plan: CleaningPlan, bindings: SemanticBindings) -> None:
        self._authorized = frozenset()
        self.binding_entries = {}
        self.source_clauses = ()
        self._unit_contexts = {}
        self._entity_contexts = {}
        self._step_ids = {}
        validate_plan(plan, bindings)
        self._authorized = frozenset(step.step_id for step in plan.steps)
        self.binding_entries = {
            e.binding.binding_id: e for e in bindings.entries if e.status == "ACCEPTED"
        }
        self.source_clauses = bindings.clauses
        self.bindings = bindings

    def step_identifier(self, step: CleaningStep) -> str:
        # Retain the immutable object to prevent object-id reuse from bypassing authorization.
        cached = self._step_ids.get(id(step))
        if cached is not None and cached[0] is step:
            return cached[1]
        identifier = step.step_id
        if identifier in self._authorized:
            self._step_ids[id(step)] = (step, identifier)
        return identifier

    def reset_boundary(self, boundary_id: str) -> None:
        if self._closed or not boundary_id.strip():
            raise ValueError("invalid operation boundary or closed state")
        self.boundary_id = boundary_id
        self.db.execute(f'DELETE FROM "{self.table_name}"')
        self._signature = ()
        self.cells = {}
        self.dispositions = {}
        self.origins = {}
        self.events = []
        self.extra_lineage = []

    def close(self) -> None:
        if not self._closed:
            self.db.execute(f'DROP TABLE "{self.table_name}"')
            self.cells.clear()
            self.origins.clear()
            self.events.clear()
            self.dispositions.clear()
            self._closed = True

    def _begin_batch(self, batch: Sequence[RawRecord], step: CleaningStep) -> None:
        if self._closed:
            raise ValueError("operation state is closed")
        signature = tuple(record.record_id for record in batch)
        if self._signature == signature:
            return
        self._signature = signature
        self.cells = {
            r.record_id: {f"c{i + 1:04d}": value for i, value in enumerate(r.source.values)}
            for r in batch
        }
        self.dispositions = {
            r.record_id: RawDisposition(
                r.record_id,
                "retained" if r.kind == "data" else "quarantined",
                "unqualified source record"
                if r.kind == "data"
                else "unclassified source structure",
            )
            for r in batch
        }
        self.origins = {
            (r.record_id, c): [self.step_identifier(step)]
            for r in batch
            for c in self.cells[r.record_id]
        }
        self.events = []
        self.extra_lineage = []
        from datamate.preparation.text_state import apply_text_to_batch

        apply_text_to_batch(self, batch)
        # Identity uses original values plus independently accepted Unicode repairs.
        # Later trim/case operations cannot manufacture a join-key proof.
        self._source_values = {record: dict(values) for record, values in self.cells.items()}


def classify_structure(
    records: Sequence[RawRecord], table: StagedTable, bindings: SemanticBindings
) -> tuple[StructuralDecision, ...]:
    if any(r.table_id != table.table_id for r in records):
        raise ValueError("structure scope differs from physical source")
    accepted = [b for b in bindings.accepted if b.table_id == table.table_id]
    roles = [b for b in accepted if b.kind == "role"]
    markers = [b for b in accepted if b.kind == "structure"]
    decisions = []
    for record in records:
        values = record.source.values
        evidence = (
            EvidenceRef(
                source=table.source.relative_path,
                sha256=table.source_sha256,
                locator=f"raw:{record.record_id}/{table.columns[0]}",
            ),
        )
        role, status, scope = "data", "ACCEPTED", None
        if all(value is None or value == "" for value in values):
            role = "unresolved" if any(record.source.formulas) else "blank"
            status = "UNRESOLVED" if role == "unresolved" else "ACCEPTED"
        elif len({c for binding in roles for c in binding.columns}) >= 2 and tuple(
            encoded(v) for v in values
        ) == tuple(encoded(v) for v in table.headers):
            role = "header"
        else:
            for marker in markers:
                if marker.value == "preheader_key_value":
                    from datamate.preparation.annotations import has_annotation_proof

                    if marker.marker == record.record_id and has_annotation_proof(marker, bindings):
                        role = "metadata"
                        evidence += marker.evidence
                        break
                    continue
                index = table.columns.index(marker.columns[0])
                value = values[index] if index < len(values) else None
                if not isinstance(value, str):
                    continue
                if marker.value == "summary" and value == marker.marker:
                    role, status, scope = "summary", "UNRESOLVED", table.table_id
                    evidence += marker.evidence
                    break
                if marker.value == "note" and value.startswith(marker.marker):
                    critical = {table.columns.index(c) for b in roles for c in b.columns} - {index}
                    if all(
                        i >= len(values) or values[i] is None or values[i] == "" for i in critical
                    ):
                        role = "note"
                        evidence += marker.evidence
                        break
            if role == "data" and record.kind != "data":
                role, status = "unresolved", "UNRESOLVED"
        decisions.append(StructuralDecision(record.record_id, role, status, evidence, scope))
    complete = len(records) == table.raw_record_count and len(
        {r.record_id for r in records}
    ) == len(records)
    amount_roles = [b for b in roles if b.value == "line_net_amount" and len(b.columns) == 1]
    if complete and len(amount_roles) == 1:
        index = table.columns.index(amount_roles[0].columns[0])
        data_values = [
            r.source.values[index]
            for r, d in zip(records, decisions, strict=True)
            if d.role == "data"
        ]
        if data_values and all(
            type(value) is int or (isinstance(value, Decimal) and value.is_finite())
            for value in data_values
        ):
            with localcontext() as context:
                context.prec = 100
                total = sum((Decimal(value) for value in data_values), Decimal(0))
            for position, (record, decision) in enumerate(zip(records, decisions, strict=True)):
                value = record.source.values[index]
                if (
                    decision.role == "summary"
                    and (type(value) is int or isinstance(value, Decimal))
                    and value == total
                ):
                    decisions[position] = StructuralDecision(
                        decision.record_id,
                        "summary",
                        "ACCEPTED",
                        decision.evidence_refs,
                        decision.anchor_scope,
                    )
    return tuple(decisions)


def _fill_merged(batch: Sequence[RawRecord], step: CleaningStep, state: OperationState) -> None:
    table = read_staged_table(state.db, step.table_id)
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            continue
        for mincol, minrow, maxcol, maxrow in table.merged_ranges or ():
            column = f"c{mincol:04d}"
            if column not in step.operation.columns or not minrow <= record.source.number <= maxrow:
                continue
            before = state.cells[record.record_id].get(column)
            if mincol != maxcol:
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "UNRESOLVED",
                        before,
                        before,
                        "horizontal merge has no field-preserving fill",
                    )
                )

                continue
            key = f"merge:{table.table_id}:{mincol}:{minrow}:{maxrow}"
            if record.source.number == minrow:
                if before is not None and record.kind == "data":
                    state.db.execute(
                        f'INSERT INTO "{state.table_name}" VALUES (?,?) '
                        "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                        [
                            key,
                            json.dumps({"value": encoded(before), "record_id": record.record_id}),
                        ],
                    )
                continue
            row = state.db.execute(
                f'SELECT payload FROM "{state.table_name}" WHERE key=?', [key]
            ).fetchone()
            if not row:
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "UNRESOLVED",
                        before,
                        before,
                        "merged anchor unavailable",
                    )
                )
                continue
            anchor = json.loads(row[0])
            value = _decoded(anchor["value"])
            if before is None:
                state.cells[record.record_id][column] = value
                state.origins[record.record_id, column].append(state.step_identifier(step))
                state.extra_lineage.append(
                    LineageEdge(
                        "row:" + record.record_id,
                        anchor["record_id"],
                        column,
                        state.step_identifier(step),
                    )
                )
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "APPLIED",
                        before,
                        value,
                        "inside declared vertical merge",
                    )
                )
            elif encoded(before) != encoded(value):
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "UNRESOLVED",
                        before,
                        before,
                        "merged data conflict",
                    )
                )


def _inherit_group(batch: Sequence[RawRecord], step: CleaningStep, state: OperationState) -> None:
    key = "group:" + state.step_identifier(step)
    stored = state.db.execute(
        f'SELECT payload FROM "{state.table_name}" WHERE key=?', [key]
    ).fetchone()
    carry = json.loads(stored[0]) if stored else None
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            carry = None
            continue
        cells = state.cells[record.record_id]
        group = cells.get(step.operation.group_key)
        if group is not None and group != "":
            if carry is None or encoded(group) != carry["group"]:
                carry = {
                    "group": encoded(group),
                    "origin": record.record_id,
                    "first": {c: encoded(cells.get(c)) for c in step.operation.columns},
                    "conflicts": [],
                }
        elif carry:
            cells[step.operation.group_key] = _decoded(carry["group"])
            state.extra_lineage.append(
                LineageEdge(
                    "row:" + record.record_id,
                    carry["origin"],
                    step.operation.group_key,
                    state.step_identifier(step),
                )
            )
        if carry is None:
            state.events.append(
                TransformEvent(
                    record.record_id,
                    step.operation.group_key,
                    state.step_identifier(step),
                    "UNRESOLVED",
                    group,
                    group,
                    "missing first group key",
                )
            )
            continue
        for column in step.operation.columns:
            before = cells.get(column)
            first = _decoded(carry["first"][column])
            if before is None or before == "":
                if first is not None and first != "" and column not in carry["conflicts"]:
                    cells[column] = first
                    state.extra_lineage.append(
                        LineageEdge(
                            "row:" + record.record_id,
                            carry["origin"],
                            column,
                            state.step_identifier(step),
                        )
                    )
                    state.events.append(
                        TransformEvent(
                            record.record_id,
                            column,
                            state.step_identifier(step),
                            "APPLIED",
                            before,
                            first,
                            "first group value",
                        )
                    )
                else:
                    state.events.append(
                        TransformEvent(
                            record.record_id,
                            column,
                            state.step_identifier(step),
                            "UNRESOLVED",
                            before,
                            before,
                            "first group value unavailable or conflicting",
                        )
                    )
            elif first is not None and first != "" and encoded(before) != encoded(first):
                if column not in carry["conflicts"]:
                    carry["conflicts"].append(column)
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "UNRESOLVED",
                        before,
                        before,
                        "group value conflict",
                    )
                )
    state.db.execute(f'DELETE FROM "{state.table_name}" WHERE key=?', [key])
    if carry is not None:
        state.db.execute(f'INSERT INTO "{state.table_name}" VALUES (?,?)', [key, json.dumps(carry)])


def _deduplicate_order(
    batch: Sequence[RawRecord], step: CleaningStep, state: OperationState
) -> None:
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            continue
        group = state.cells[record.record_id].get(step.operation.group_key)
        if group is None or group == "":
            state.events.append(
                TransformEvent(
                    record.record_id,
                    step.operation.group_key,
                    state.step_identifier(step),
                    "UNRESOLVED",
                    group,
                    group,
                    "duplicate scope lacks order key",
                )
            )
            continue
        signature = json.dumps(
            [
                encoded(group),
                [encoded(v) for v in record.source.values],
                record.source.formulas,
                record.source.formats,
            ],
            sort_keys=True,
            ensure_ascii=False,
        )
        key = "seen:" + step.table_id + ":" + hashlib.sha256(signature.encode()).hexdigest()
        old = state.db.execute(
            f'SELECT payload FROM "{state.table_name}" WHERE key=?', [key]
        ).fetchone()
        if old:
            original = json.loads(old[0])["record_id"]
            state.dispositions[record.record_id] = RawDisposition(
                record.record_id,
                "duplicate",
                "exact same-order source duplicate",
                step.evidence,
                original,
            )
            state.extra_lineage.extend(
                LineageEdge(
                    "row:" + original, record.record_id, column, state.step_identifier(step)
                )
                for column in state.cells[record.record_id]
            )
        else:
            state.db.execute(
                f'INSERT INTO "{state.table_name}" VALUES (?,?)',
                [key, json.dumps({"record_id": record.record_id})],
            )


def _source_date_context(state: OperationState, table_id: str) -> tuple[dict, datetime | None]:
    exists = state.db.execute(
        "SELECT 1 FROM duckdb_tables() WHERE schema_name='meta' AND table_name='source_tables'"
    ).fetchone()
    if not exists:
        return {}, None
    info = state.db.execute(
        "SELECT source,table_name,workbook_epoch FROM meta.source_tables WHERE table_id=?",
        [table_id],
    ).fetchone()
    if not info or not info[0].lower().endswith(".xlsx"):
        return {}, None
    years = set(re.findall(r"(?<!\d)([12]\d{3})(?!\d)", info[1]))
    source_year_rule = any(
        clause.source_document.source == "rulebook.md"
        and "ngày không ghi năm" in clause.quote.casefold().replace("*", "")
        and "năm của sheet" in clause.quote.casefold().replace("*", "")
        for clause in state.source_clauses
    )
    context = {"sheet_year": int(next(iter(years)))} if len(years) == 1 and source_year_rule else {}
    return context, info[2]


def _derived_value(
    record: RawRecord,
    column: str,
    value: TypedValue,
    dependencies: tuple[str, ...],
    step: CleaningStep,
    state: OperationState,
) -> None:
    if column in state.cells[record.record_id]:
        raise ValueError("derived field already exists")
    state.cells[record.record_id][column] = value
    state.origins[record.record_id, column] = [state.step_identifier(step)]
    state.extra_lineage.extend(
        LineageEdge(
            "row:" + record.record_id, record.record_id, dependency, state.step_identifier(step)
        )
        for dependency in dependencies
    )


def _resolve_entities(
    batch: Sequence[RawRecord], step: CleaningStep, state: OperationState
) -> None:
    operation = step.operation
    column = operation.columns[0]
    output = "entity_id_" + column
    # Inspect every declaration in this table/scope, including conflicting candidates;
    # selecting a smaller binding list must not hide a contradictory source map.
    identifier = state.step_identifier(step)
    if identifier not in state._entity_contexts:
        proposals = tuple(
            e.binding
            for e in state.bindings.entries
            if e.binding.kind == "entity"
            and e.binding.table_id == step.table_id
            and e.binding.entity_scope == operation.scope
        )
        graph = accept_entity_links(proposals, state.bindings)
        components = {
            key: nodes
            for scope, nodes in graph.components
            if scope == operation.scope
            for key in nodes
        }
        blocked = set()
        for conflict in graph.conflicts:
            for key in (conflict.value, conflict.entity_target):
                blocked.update(components.get(key, (key,)))
        declared = {key for _, key, _ in graph.aliases}
        declared.update(components)
        authorized = set(step.binding_ids)
        alias_keys = defaultdict(set)
        alias_bindings = defaultdict(list)
        link_bindings = defaultdict(list)
        approved_aliases = set(graph.aliases)
        for scope, key, name in graph.aliases:
            if scope == operation.scope:
                alias_keys[name].add(key)
        for binding in proposals:
            if (
                binding.entity_relation == "alias"
                and (operation.scope, binding.value, binding.entity_target) in approved_aliases
            ):
                alias_bindings[(binding.value, binding.entity_target)].append(binding)
        for binding in graph.accepted_links:
            link_bindings[binding.value].append(binding)
        state._entity_contexts[identifier] = (
            components,
            blocked,
            declared,
            authorized,
            set(graph.ambiguous_aliases),
            alias_keys,
            alias_bindings,
            link_bindings,
        )
    (
        components,
        blocked,
        declared,
        authorized,
        ambiguous,
        alias_keys,
        alias_bindings,
        link_bindings,
    ) = state._entity_contexts[identifier]
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            continue
        before = state._source_values[record.record_id].get(column)
        key = None
        if type(before) is str and before == before.strip() and before:
            if operation.input_kind == "id":
                candidate = operation.namespace + ":" + before
                if candidate in declared:
                    key = candidate
            elif (operation.scope, before) not in ambiguous:
                candidates = {
                    key
                    for key in alias_keys.get(before, ())
                    if key.partition(":")[0] == operation.namespace
                }
                canonical = {min(components.get(key, (key,))) for key in candidates}
                if len(canonical) == 1:
                    key = next(iter(candidates))
        supported = key is not None and key not in blocked
        component = set(components.get(key, (key,))) if key else set()
        required = [binding for node in component for binding in link_bindings.get(node, ())]
        if operation.input_kind == "alias":
            required.extend(
                binding for node in component for binding in alias_bindings.get((node, before), ())
            )
        elif not required:
            required.extend(
                binding
                for (node, _), entries in alias_bindings.items()
                if node == key
                for binding in entries
            )
        supported = (
            supported and bool(required) and all(b.binding_id in authorized for b in required)
        )
        after = min(component) if supported else None
        if supported:
            _derived_value(record, output, after, (column,), step, state)
            from datamate.preparation.native_relations import has_native_proof

            native = [b for b in required if has_native_proof(b, state.bindings)]
            if operation.scope == "product" and operation.namespace == "product" and native:
                source_codes = {
                    b.value.partition(":")[2] for b in native if b.value.startswith("product:")
                }
                if len(source_codes) != 1:
                    raise ValueError("native product identity is ambiguous")
                _derived_value(
                    record, "product_id", next(iter(source_codes)), (column,), step, state
                )
                for binding in native:
                    for ref in binding.evidence:
                        if ref.locator.startswith("raw:"):
                            source_record, source_column = ref.locator[4:].rsplit("/", 1)
                            state.extra_lineage.append(
                                LineageEdge(
                                    "row:" + record.record_id,
                                    source_record,
                                    source_column,
                                    state.step_identifier(step),
                                )
                            )
        state.events.append(
            TransformEvent(
                record.record_id,
                output,
                state.step_identifier(step),
                "APPLIED" if supported else "UNRESOLVED",
                before,
                after,
                "current scoped entity declaration"
                if supported
                else "identity lacks unique conflict-free source proof",
            )
        )


def _decimal_quantity(value: TypedValue) -> Decimal:
    if type(value) is int:
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise ValueError("quantity must be an integer or Decimal")
    # Reuse the declared lossless precision bound and finite-operand checks.
    reconcile_quantity(value, value, None)
    return value


def _normalize_quantity(
    batch: Sequence[RawRecord], step: CleaningStep, state: OperationState
) -> None:
    operation = step.operation
    dependencies = (operation.product_column, operation.quantity_column, operation.unit_column)
    if operation.preconverted_column:
        dependencies += (operation.preconverted_column,)
    if state.step_identifier(step) not in state._unit_contexts:
        grouped = defaultdict(list)
        evidence = defaultdict(set)
        authorized = set(step.binding_ids)
        for entry in state.bindings.entries:
            binding = entry.binding
            if binding.table_id == step.table_id and binding.kind == "unit":
                grouped[binding.unit_product].append(
                    replace(entry, status="UNRESOLVED") if entry.status == "CONFLICT" else entry
                )
                if binding.binding_id in authorized and entry.status == "ACCEPTED":
                    evidence[binding.unit_product].update(binding.evidence)
        contexts = {
            key: replace(state.bindings, entries=tuple(entries)) for key, entries in grouped.items()
        }
        state._unit_contexts[state.step_identifier(step)] = (contexts, evidence, {})
    contexts, evidence, resolved = state._unit_contexts[state.step_identifier(step)]
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            continue
        cells = state.cells[record.record_id]
        before = cells.get(operation.quantity_column)
        after = None
        reason = "quantity lacks current product-scoped unit proof"
        try:
            product = state._source_values[record.record_id].get(operation.product_column)
            unit = state._source_values[record.record_id].get(operation.unit_column)
            if type(product) is not str or type(unit) is not str:
                raise ValueError(reason)
            context = contexts.get(product)
            if context is None:
                raise ValueError("product has no source unit declarations")
            key = (product, unit)
            if key not in resolved:
                if len(resolved) >= 10000:
                    resolved.pop(next(iter(resolved)))
                resolved[key] = resolve_units(context, product_id=product, unit=unit)
            resolution = resolved[key]
            if resolution.status != "ACCEPTED":
                raise ValueError("unit graph is unresolved or conflicting")
            # Every source edge used for this row must be authorized and evidenced.
            if not set(resolution.evidence) <= evidence[product]:
                raise ValueError("unit path is missing accepted evidence dependencies")
            raw_quantity = _decimal_quantity(before)
            expected = reconcile_quantity(raw_quantity, Decimal(0), resolution.factor)
            after = _decimal_quantity(expected.expected_base_quantity)
            preconverted = cells.get(operation.preconverted_column)
            if operation.preconverted_column and preconverted is not None:
                preconverted = _decimal_quantity(preconverted)
                if reconcile_quantity(raw_quantity, preconverted, resolution.factor).mismatch:
                    raise ValueError("source preconverted quantity mismatch")
                after = preconverted
            for output, value in (
                ("quantity_base", after),
                ("quantity_base_unit", resolution.base_unit),
                ("quantity_conversion_factor", resolution.factor),
            ):
                _derived_value(record, output, value, dependencies, step, state)
            for ref in resolution.evidence:
                if ref.locator.startswith("raw:"):
                    source_record, column = ref.locator[4:].rsplit("/", 1)
                    state.extra_lineage.append(
                        LineageEdge(
                            "row:" + record.record_id,
                            source_record,
                            column,
                            state.step_identifier(step),
                        )
                    )
            reason = "current product-scoped conversion and source reconciliation"
        except ValueError as error:
            after = None
            reason = str(error)
        state.events.append(
            TransformEvent(
                record.record_id,
                "quantity_base",
                state.step_identifier(step),
                "APPLIED" if after is not None else "UNRESOLVED",
                before,
                after,
                reason,
            )
        )


def execute_batch(
    batch: Sequence[RawRecord], step: CleaningStep, state: OperationState
) -> BatchResult:
    step = CleaningStep.model_validate(step)
    if len(batch) > 1000 or len({r.record_id for r in batch}) != len(batch):
        raise ValueError("batch must contain at most 1000 distinct records")
    if any(r.table_id != step.table_id for r in batch):
        raise ValueError("operation scope does not match source table")
    if (
        step.operation.name != "preserve_raw"
        and state.step_identifier(step) not in state._authorized
    ):
        raise ValueError("transformation is not authorized by a validated plan")
    state._begin_batch(batch, step)
    if step.operation.name == "classify_structure":
        table = read_staged_table(state.db, step.table_id)
        # Semantic classification sees accepted text; physical equality and
        # deduplication continue to use the original immutable records.
        from datamate.preparation.text_state import effective_header

        profiles = {p.table_id: p for p in getattr(state, "text_profiles", ())}
        if table.table_id in profiles:
            profile = profiles[table.table_id]
            table = replace(
                table,
                headers=tuple(
                    effective_header(profile, c)
                    if c.source_header.get("type") == "str"
                    else table.headers[i]
                    for i, c in enumerate(profile.columns)
                ),
            )
        semantic_batch = tuple(
            replace(
                record,
                source=replace(
                    record.source,
                    values=tuple(state.cells[record.record_id][c] for c in table.columns),
                ),
            )
            for record in batch
        )
        for decision in classify_structure(semantic_batch, table, state.bindings):
            kind = (
                "quarantined"
                if decision.status == "UNRESOLVED"
                else "retained"
                if decision.role == "data"
                else "structural"
            )
            state.dispositions[decision.record_id] = RawDisposition(
                decision.record_id,
                kind,
                "source structure: " + decision.role,
                decision.evidence_refs,
            )
    elif step.operation.name == "fill_merged":
        _fill_merged(batch, step, state)
    elif step.operation.name == "inherit_group":
        _inherit_group(batch, step, state)
    elif step.operation.name == "deduplicate_order":
        _deduplicate_order(batch, step, state)
    elif step.operation.name == "trim_text":
        for column in step.operation.columns:
            records = [r for r in batch if state.dispositions[r.record_id].kind == "retained"]
            if any(column not in state.cells[r.record_id] for r in records):
                raise ValueError("operation references an unavailable source column")
            values = [state.cells[r.record_id][column] for r in records]
            if all(value is None or type(value) is str for value in values):
                # The working column has a declared lossless dtype, never the mixed raw payload.
                changed = (
                    pl.Series("value", values, dtype=pl.String, strict=True)
                    .str.strip_chars()
                    .to_list()
                )
            else:
                changed = [value.strip() if type(value) is str else value for value in values]
            for record, before, after in zip(records, values, changed, strict=True):
                supported = before is None or type(before) is str
                state.cells[record.record_id][column] = after
                state.origins[record.record_id, column].append(state.step_identifier(step))
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "APPLIED" if supported else "UNRESOLVED",
                        before,
                        after,
                        "trim whitespace" if supported else "non-text source value preserved",
                    )
                )
    elif step.operation.name == "parse_values":
        source_context, epoch = (
            _source_date_context(state, step.table_id)
            if step.operation.role in {"date", "datetime"}
            else ({}, None)
        )
        for column in step.operation.columns:
            witnesses = [
                state.binding_entries[i]
                for i in step.binding_ids
                if column in state.binding_entries[i].binding.columns
            ]
            format_bindings = [e for e in witnesses if e.binding.kind == "format"]
            roles = [e for e in witnesses if e.binding.kind == "role"]
            if len({entry.binding.value for entry in format_bindings}) > 1:
                raise ValueError("conflicting source grammars")
            binding = format_bindings[0] if format_bindings else roles[0]
            for record in batch:
                if state.dispositions[record.record_id].kind != "retained":
                    continue
                before = state.cells[record.record_id][column]
                context = dict(source_context)
                if step.operation.role in {"date", "datetime"} and not isinstance(
                    before, (date, datetime, time)
                ):
                    index = int(column[1:]) - 1
                    style = (
                        record.source.formats[index] if index < len(record.source.formats) else None
                    )
                    if epoch is not None and (
                        (style and is_date_format(style)) or binding.binding.value == "excel_serial"
                    ):
                        context["workbook_epoch"] = epoch
                percent_style_unknown = False
                if step.operation.role == "percentage":
                    index = int(column[1:]) - 1
                    style = (
                        record.source.formats[index] if index < len(record.source.formats) else None
                    )
                    if (
                        isinstance(style, str)
                        and "%" in style
                        and type(before) in (int, float, Decimal)
                    ):
                        if re.fullmatch(r"[0#]+(?:\.[0#]+)?\s*%", style):
                            context["percentage_ratio"] = True
                        else:
                            percent_style_unknown = True
                candidates = (
                    ()
                    if percent_style_unknown
                    else parse_candidates(before, step.operation.role, step.evidence, context)
                )
                decision = select_parse(candidates, binding)
                after = decision.value if decision.status == "ACCEPTED" else before
                state.cells[record.record_id][column] = after
                state.origins[record.record_id, column].append(state.step_identifier(step))
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        state.step_identifier(step),
                        "APPLIED" if decision.status == "ACCEPTED" else decision.status,
                        before,
                        after,
                        decision.reason,
                    )
                )
    elif step.operation.name == "resolve_entities":
        _resolve_entities(batch, step, state)
    elif step.operation.name == "normalize_quantity":
        _normalize_quantity(batch, step, state)
    elif step.operation.name == "derive_line_amount":
        from datamate.preparation.financial import compute_line_amount

        op = step.operation
        dependencies = (op.quantity_column, op.unit_price_column) + (
            (op.discount_column,) if op.discount_column is not None else ()
        )
        for record in batch:
            if state.dispositions[record.record_id].kind != "retained":
                continue
            cells = state.cells[record.record_id]
            try:
                value = compute_line_amount(
                    cells.get(op.quantity_column),
                    cells.get(op.unit_price_column),
                    formula=op.formula,
                    discount=cells.get(op.discount_column) if op.discount_column else None,
                )
            except (ValueError, TypeError, ArithmeticError):
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        "derived_line_net_amount",
                        state.step_identifier(step),
                        "UNRESOLVED",
                        None,
                        None,
                        "line formula dependencies or precision unresolved",
                    )
                )
                continue
            _derived_value(record, "derived_line_net_amount", value, dependencies, step, state)
            state.events.append(
                TransformEvent(
                    record.record_id,
                    "derived_line_net_amount",
                    state.step_identifier(step),
                    "APPLIED",
                    None,
                    value,
                    "explicit same-unit source line formula; no rounding",
                )
            )
    elif step.operation.name != "preserve_raw":
        raise ValueError("unregistered operation")
    facts = []
    lineage = []
    for record in batch:
        if state.dispositions[record.record_id].kind != "retained":
            continue
        fact_id = "row:" + record.record_id
        facts.append(CandidateFact(fact_id, "physical_row", dict(state.cells[record.record_id])))
        for column in state.cells[record.record_id]:
            lineage.extend(
                LineageEdge(fact_id, record.record_id, column, identifier)
                for identifier in dict.fromkeys(state.origins[record.record_id, column])
            )
    eliminated = {"row:" + d.record_id for d in state.dispositions.values() if d.kind != "retained"}
    edges = tuple(
        dict.fromkeys([*lineage, *(e for e in state.extra_lineage if e.fact_id not in eliminated)])
    )
    return BatchResult(
        tuple(facts),
        tuple(state.dispositions[r.record_id] for r in batch),
        edges,
        tuple(state.events),
    )
