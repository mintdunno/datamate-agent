"""Streaming source contracts shared by loaders and preparation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
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
    workbook_epoch: datetime | None = None
    merged_ranges: tuple[tuple[int, int, int, int], ...] | None = None
    header_formulas: tuple[str | None, ...] = ()
    header_formats: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    sha256: str


_WEEKDAYS = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
)


@dataclass(frozen=True)
class SourceDocument:
    split_id: str
    source: str
    sha256: str
    text: str

    def __post_init__(self) -> None:
        if (
            not self.split_id
            or not self.source
            or sha256(self.text.encode("utf-8")).hexdigest() != self.sha256
        ):
            raise ValueError("source document hash or identity is invalid")


@dataclass(frozen=True)
class ClauseDeclaration:
    kind: Literal["week_start"]
    value: str

    def __post_init__(self) -> None:
        if self.kind != "week_start" or self.value not in _WEEKDAYS:
            raise ValueError("unsupported clause declaration")


@dataclass(frozen=True)
class RuleClause:
    clause_id: str
    source_document: SourceDocument
    line_start: int
    line_end: int
    quote: str
    quote_sha256: str
    section: str | None
    authority: Literal["general", "industry", "readme"]
    industry_scope: str | None
    status: Literal["MAPPED", "UNMAPPED", "CONFLICT", "NOT_APPLICABLE"]
    declaration: ClauseDeclaration | None = None
    dependencies: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        lines = self.source_document.text.splitlines(keepends=True)
        if not 1 <= self.line_start <= self.line_end <= len(lines):
            raise ValueError("clause line span is invalid")
        if self.quote != "".join(lines[self.line_start - 1 : self.line_end]):
            raise ValueError("clause quote does not match source span")
        if sha256(self.quote.encode("utf-8")).hexdigest() != self.quote_sha256:
            raise ValueError("clause quote hash is invalid")
        if self.status == "MAPPED" and self.declaration is None:
            raise ValueError("mapped clause requires a typed declaration")
        if self.status == "UNMAPPED" and self.declaration is not None:
            raise ValueError("unmapped clause cannot carry a declaration")
        if self.authority != "industry" and self.industry_scope is not None:
            raise ValueError("industry scope requires industry authority")


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
    headers: tuple[RawValue, ...] = ()
    declared_types: tuple[str | None, ...] = ()


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


class SampleRef(ContractModel):
    record_id: str
    column_id: str
    pattern_id: str
    pattern_count: int = Field(ge=1)
    kind: Literal["data", "header", "preamble"] = "data"


class ColumnProfile(ContractModel):
    column_id: str
    source_header: dict[str, object]
    declared_type: str | None
    raw_types: dict[str, int]
    null_count: int = Field(ge=0)
    blank_count: int = Field(ge=0)
    unique_count_lower_bound: int = Field(ge=0)
    unique_count_exact: bool
    format_counts: dict[str, int]
    format_counts_truncated: bool
    distinct_formats: int = Field(ge=0)


class TableProfile(ContractModel):
    table_id: str
    source: str
    source_sha256: Sha256
    source_table: str
    row_count: int = Field(ge=0)
    raw_record_count: int = Field(ge=0)
    record_kinds: dict[str, int]
    columns: tuple[ColumnProfile, ...]
    samples: tuple[SampleRef, ...]
    profile_version: str
    candidate_keys: tuple[str, ...]

    @property
    def digest(self) -> str:
        import hashlib
        import json

        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
