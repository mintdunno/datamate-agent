"""Reviewed operations over immutable raw batches, with bounded native working state."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, localcontext
from uuid import uuid4

import polars as pl
from duckdb import DuckDBPyConnection
from openpyxl.styles.numbers import is_date_format

from datamate.preparation.models import (
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
    "money": {"order_total", "line_net_amount", "payment_amount", "unit_cost"},
    "quantity": {"quantity", "inventory_quantity"},
    "id": {"order_id", "product_id", "customer_id"},
    "date": {"ordered_at", "payment_at"},
    "datetime": {"ordered_at", "payment_at"},
}


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


def validate_plan(plan: CleaningPlan, bindings: SemanticBindings) -> None:
    plan = CleaningPlan.model_validate(plan)
    if plan.bindings_digest != bindings.digest:
        raise ValueError("plan binding fingerprint is stale")
    if sorted(plan.profile_digests) != sorted(bindings.profile_digests):
        raise ValueError("plan profile fingerprints are stale")
    accepted = {b.binding_id: b for b in bindings.accepted}
    if len({step.step_id for step in plan.steps}) != len(plan.steps):
        raise ValueError("duplicate operation steps")
    for step in plan.steps:
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
            if not {e.model_dump_json() for e in step.evidence} <= witnesses:
                raise ValueError("operation evidence does not belong to accepted bindings")
            if step.operation.name == "parse_values":
                for column in step.operation.columns:
                    if not any(
                        b.kind == "role"
                        and b.value in _PARSE_ROLES[step.operation.role]
                        and column in b.columns
                        for b in referenced
                    ):
                        raise ValueError("parse role is not justified by accepted column semantics")


class OperationState:
    """Only the active batch stays in Python; cross-batch state belongs to owned SQL tables."""

    def __init__(self, db: DuckDBPyConnection, boundary_id: str):
        self.db = db
        self.table_name = "op_" + uuid4().hex
        db.execute(f'CREATE TEMP TABLE "{self.table_name}"(key VARCHAR PRIMARY KEY,payload JSON)')
        self._authorized = frozenset()
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
        validate_plan(plan, bindings)
        self._authorized = frozenset(step.step_id for step in plan.steps)
        self.binding_entries = {
            e.binding.binding_id: e for e in bindings.entries if e.status == "ACCEPTED"
        }
        self.source_clauses = bindings.clauses
        self.bindings = bindings

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
            (r.record_id, c): [step.step_id] for r in batch for c in self.cells[r.record_id]
        }
        self.events = []
        self.extra_lineage = []


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
                        step.step_id,
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
                        step.step_id,
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
                state.origins[record.record_id, column].append(step.step_id)
                state.extra_lineage.append(
                    LineageEdge(
                        "row:" + record.record_id, anchor["record_id"], column, step.step_id
                    )
                )
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        step.step_id,
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
                        step.step_id,
                        "UNRESOLVED",
                        before,
                        before,
                        "merged data conflict",
                    )
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


def execute_batch(
    batch: Sequence[RawRecord], step: CleaningStep, state: OperationState
) -> BatchResult:
    step = CleaningStep.model_validate(step)
    if len(batch) > 1000 or len({r.record_id for r in batch}) != len(batch):
        raise ValueError("batch must contain at most 1000 distinct records")
    if any(r.table_id != step.table_id for r in batch):
        raise ValueError("operation scope does not match source table")
    if step.operation.name != "preserve_raw" and step.step_id not in state._authorized:
        raise ValueError("transformation is not authorized by a validated plan")
    state._begin_batch(batch, step)
    if step.operation.name == "classify_structure":
        table = read_staged_table(state.db, step.table_id)
        for decision in classify_structure(batch, table, state.bindings):
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
                state.origins[record.record_id, column].append(step.step_id)
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        step.step_id,
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
                candidates = parse_candidates(before, step.operation.role, step.evidence, context)
                decision = select_parse(candidates, binding)
                after = decision.value if decision.status == "ACCEPTED" else before
                state.cells[record.record_id][column] = after
                state.origins[record.record_id, column].append(step.step_id)
                state.events.append(
                    TransformEvent(
                        record.record_id,
                        column,
                        step.step_id,
                        "APPLIED" if decision.status == "ACCEPTED" else decision.status,
                        before,
                        after,
                        decision.reason,
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
    return BatchResult(
        tuple(facts),
        tuple(state.dispositions[r.record_id] for r in batch),
        tuple([*lineage, *state.extra_lineage]),
        tuple(state.events),
    )
