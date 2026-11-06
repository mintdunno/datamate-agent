"""Reviewed operations over immutable raw batches, with bounded native working state."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import uuid4

import polars as pl
from duckdb import DuckDBPyConnection
from openpyxl.styles.numbers import is_date_format

from datamate.preparation.models import (
    CandidateFact,
    LineageEdge,
    RawDisposition,
    RawRecord,
    SemanticBindings,
    TypedValue,
)
from datamate.preparation.normalization import (
    CleaningPlan,
    CleaningStep,
    parse_candidates,
    select_parse,
)

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
        if step.operation.name in {"trim_text", "parse_values"}:
            if not set(step.operation.columns) <= {c for b in referenced for c in b.columns}:
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
    if step.operation.name == "trim_text":
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
        tuple(lineage),
        tuple(state.events),
    )
