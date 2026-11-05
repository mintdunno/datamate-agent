"""Streaming source contracts shared by loaders and preparation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256

RawValue = str | int | float | bool | bytes | date | datetime | time | None


@dataclass(frozen=True)
class SourceRow:
    number: int
    values: tuple[RawValue, ...]
    formulas: tuple[str | None, ...] = ()
    formats: tuple[str | None, ...] = ()
    record_key: dict[str, RawValue] | None = None


@dataclass(frozen=True)
class SourceTable:
    """Rows must be consumed before advancing the containing table iterator."""

    name: str
    headers: tuple[RawValue, ...]
    header_row: int | None
    rows: Iterable[SourceRow]
    preamble: tuple[SourceRow, ...] = ()
    declared_types: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class BusinessInputs:
    split_id: str
    business_id: str
    split_root: Path
    business_root: Path
    sources: tuple[SourceFile, ...]
    documents: tuple[SourceFile, ...]


class EvidenceRef(ContractModel):
    source: NonblankString
    sha256: Sha256
    locator: NonblankString
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    quote_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def valid_span(self) -> Self:
        if (self.line_start is None) != (self.line_end is None) or (
            self.line_start is not None and self.line_end < self.line_start
        ):
            raise ValueError("evidence line span must be complete and ordered")
        return self


@dataclass(frozen=True)
class RawRecord:
    record_id: str
    table_id: str
    source: SourceRow
    kind: str


@dataclass(frozen=True)
class StagedTable:
    table_id: str
    source: SourceFile
    source_sha256: str
    name: str
    header_row: int | None
    columns: tuple[str, ...]
    row_count: int
    raw_record_count: int


TypedValue = RawValue | Decimal


@dataclass(frozen=True)
class CandidateFact:
    fact_id: str
    table_role: str
    values: dict[str, TypedValue]


@dataclass(frozen=True)
class RawDisposition:
    record_id: str
    kind: Literal["retained", "structural", "duplicate", "quarantined", "unsupported"]
    reason: str
    evidence_refs: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class LineageEdge:
    fact_id: str
    record_id: str
    column_id: str
    step_id: str
