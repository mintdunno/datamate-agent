"""Reviewed operations over immutable raw batches, with bounded native working state."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

import polars as pl
from duckdb import DuckDBPyConnection

from datamate.preparation.models import (
    CandidateFact,
    LineageEdge,
    RawDisposition,
    RawRecord,
    SemanticBindings,
    TypedValue,
)
from datamate.preparation.normalization import CleaningPlan, CleaningStep


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
        if step.operation.name == "trim_text":
            if not set(step.operation.columns) <= {c for b in referenced for c in b.columns}:
                raise ValueError("operation column has no accepted source binding")
            witnesses = {e.model_dump_json() for b in referenced for e in b.evidence}
            if not {e.model_dump_json() for e in step.evidence} <= witnesses:
                raise ValueError("operation evidence does not belong to accepted bindings")


class OperationState:
    """Only the active batch stays in Python; cross-batch state belongs to owned SQL tables."""

    def __init__(self, db: DuckDBPyConnection, boundary_id: str):
        self.db = db
        self.table_name = "op_" + uuid4().hex
        db.execute(f'CREATE TEMP TABLE "{self.table_name}"(key VARCHAR PRIMARY KEY,payload JSON)')
        self._authorized = frozenset()
        self._closed = False
        self.reset_boundary(boundary_id)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def authorize(self, plan: CleaningPlan, bindings: SemanticBindings) -> None:
        self._authorized = frozenset()
        validate_plan(plan, bindings)
        self._authorized = frozenset(step.step_id for step in plan.steps)

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
