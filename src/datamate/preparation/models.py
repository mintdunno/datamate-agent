"""Streaming source contracts shared by loaders and preparation."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, DayString, NonblankString, Sha256

RawValue = str | int | float | Decimal | bool | bytes | date | datetime | time | None
BINDING_VALIDATOR_VERSION = "source-binding-v3"


class UndecodedText(bytes):
    """Original SQLite TEXT bytes that could not be decoded strictly as UTF-8."""


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
    decoding: dict[str, object] | None = None


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


def _solver_source(path: str) -> bool:
    if path in {"rulebook.md", "calendar.csv"}:
        return True
    parts = path.split("/")
    forbidden = (
        "answer",
        "question",
        "reference",
        "suite",
        "label",
        "checkpoint",
        "state",
        ".env",
    )
    return (
        len(parts) >= 3
        and parts[0] == "data"
        and all(part not in {"", ".", ".."} for part in parts)
        and not any(
            "\\" in part or any(word in part.casefold() for word in forbidden) for part in parts
        )
    )


class NormalizationIssue(ContractModel):
    issue_id: NonblankString
    scope: NonblankString
    kind: NonblankString
    detail: NonblankString
    required_capability: bool = False

    @model_validator(mode="after")
    def source_only_diagnostic(self) -> Self:
        if any(
            token in self.detail.casefold()
            for token in ("expected_label", "reference_answer", "ground_truth", "checkpoint")
        ):
            raise ValueError("reference artifact is not a solver issue")
        return self


class SourceExcerpt(ContractModel):
    evidence: EvidenceRef
    text: str

    @model_validator(mode="after")
    def source_bound(self) -> Self:
        if not _solver_source(self.evidence.source):
            raise ValueError("source-only evidence path required")
        if self.evidence.line_start is None or self.evidence.line_end is None:
            raise ValueError("source excerpt requires an exact span")
        if self.evidence.quote_sha256 != sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("excerpt hash differs from cited source span")
        if any(
            token in self.text.casefold()
            for token in ("expected_label_sentinel", "reference_answer_sentinel")
        ):
            raise ValueError("reference sentinel is not solver evidence")
        try:
            payload = json.loads(self.text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and any(
            key.casefold() in {"expected_label", "reference_answer", "ground_truth", "score"}
            for key in payload
        ):
            raise ValueError("reference artifact is not solver evidence")
        return self


class EvidencePacket(ContractModel):
    issue_id: NonblankString
    bindings_digest: Sha256
    profile_digests: tuple[Sha256, ...]
    excerpts: tuple[SourceExcerpt, ...] = Field(min_length=1)
    omitted: tuple[EvidenceRef, ...] = ()

    @model_validator(mode="after")
    def source_only_and_unique(self) -> Self:
        if any(not _solver_source(item.source) for item in self.omitted):
            raise ValueError("source-only omitted path required")
        refs = tuple(item.evidence for item in self.excerpts) + self.omitted
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate evidence reference")
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
    workbook_epoch: datetime | None = None
    merged_ranges: tuple[tuple[int, int, int, int], ...] | None = None


@dataclass(frozen=True)
class StructuralDecision:
    record_id: str
    role: Literal["data", "header", "blank", "note", "summary", "unresolved"]
    status: Literal["ACCEPTED", "UNRESOLVED"]
    evidence_refs: tuple[EvidenceRef, ...]
    anchor_scope: str | None = None


TypedValue = RawValue


@dataclass(frozen=True)
class CandidateFact:
    fact_id: str
    table_role: str
    values: dict[str, TypedValue]


@dataclass(frozen=True)
class ParseCandidate:
    value: TypedValue
    grammar: str
    unit: str | None = None
    context_requirements: tuple[str, ...] = ()
    precision_supported: bool = True


@dataclass(frozen=True)
class ParseDecision:
    status: Literal["ACCEPTED", "UNRESOLVED", "UNSUPPORTED"]
    value: TypedValue
    grammar: str | None
    alternatives: tuple[ParseCandidate, ...]
    reason: str


@dataclass(frozen=True)
class RawDisposition:
    record_id: str
    kind: Literal["retained", "structural", "duplicate", "quarantined", "unsupported"]
    reason: str
    evidence_refs: tuple[EvidenceRef, ...] = ()
    duplicate_of: str | None = None


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
    header_repair: dict[str, object] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
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
    source_table_display: str | None = Field(default=None, exclude_if=lambda value: value is None)
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


class Binding(ContractModel):
    kind: Literal[
        "role", "grain", "status", "entity", "unit", "measure", "capability", "format", "structure"
    ]
    table_id: NonblankString
    columns: tuple[NonblankString, ...] = Field(min_length=1)
    value: NonblankString
    profile_digest: Sha256
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=50)
    dependencies: tuple[Sha256, ...] = ()
    snapshot_at: DayString | None = None
    available: bool | None = None
    marker: NonblankString | None = None
    entity_target: NonblankString | None = None
    entity_scope: NonblankString | None = None
    entity_relation: Literal["explicit_map", "trusted_code", "relation_key", "alias"] | None = None
    unit_product: NonblankString | None = None
    unit_target: NonblankString | None = None
    unit_factor: Decimal | None = None
    unit_base: bool | None = None

    @model_validator(mode="after")
    def relevant_attributes(self) -> Self:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("duplicate binding columns")
        if (self.kind == "structure") != (self.marker is not None):
            raise ValueError(
                "structural declarations require a marker, and only structures use markers"
            )
        if self.snapshot_at is not None and not (
            self.kind == "grain" and self.value == "inventory_snapshot"
        ):
            raise ValueError("snapshot date requires snapshot grain")
        if self.available is not None and self.kind != "capability":
            raise ValueError("availability requires a capability declaration")
        if (
            any((self.entity_target, self.entity_scope, self.entity_relation))
            and self.kind != "entity"
        ):
            raise ValueError("entity fields require an entity binding")
        if (
            any(
                value is not None
                for value in (
                    self.unit_product,
                    self.unit_target,
                    self.unit_factor,
                    self.unit_base,
                )
            )
            and self.kind != "unit"
        ):
            raise ValueError("unit fields require a unit binding")
        if self.unit_factor is not None and (
            not self.unit_factor.is_finite() or self.unit_factor <= 0
        ):
            raise ValueError("unit factor must be finite and positive")
        if self.unit_factor is not None:
            parts = self.unit_factor.as_tuple()
            if max(len(parts.digits) + max(parts.exponent, 0), -min(parts.exponent, 0)) > 38:
                raise ValueError("unsupported unit factor precision")
        return self

    @property
    def binding_id(self) -> str:
        import json

        return sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ResolvedBinding:
    binding: Binding
    status: Literal["ACCEPTED", "UNRESOLVED", "CONFLICT"]
    reason: str
    clause_ids: tuple[str, ...] = ()
    acceptance_rule: str | None = None


@dataclass(frozen=True)
class SemanticBindings:
    entries: tuple[ResolvedBinding, ...]
    clauses: tuple[RuleClause, ...]
    profile_digests: tuple[str, ...]
    table_ids: tuple[str, ...] = ()
    qualified_measures: tuple[tuple[str, str, str], ...] = ()
    validator_version: str = BINDING_VALIDATOR_VERSION
    profile_sources: tuple[tuple[str, str, str, str], ...] = ()

    @property
    def digest(self) -> str:
        import json

        payload = {
            "validator": self.validator_version,
            "profiles": self.profile_digests,
            "profile_sources": self.profile_sources,
            "clauses": sorted(c.clause_id for c in self.clauses),
            "entries": [
                (e.binding.binding_id, e.status, e.clause_ids, e.acceptance_rule)
                for e in self.entries
            ],
        }
        return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @property
    def accepted(self) -> tuple[Binding, ...]:
        return tuple(entry.binding for entry in self.entries if entry.status == "ACCEPTED")

    def capability(self, table_id: str, name: str) -> bool | None:
        values = [
            b.available
            for b in self.accepted
            if b.table_id == table_id and b.kind == "capability" and b.value == name
        ]
        return values[0] if len(values) == 1 else None


@dataclass(frozen=True)
class EntityResolution:
    accepted_links: tuple[Binding, ...]
    components: tuple[tuple[str, tuple[str, ...]], ...]
    aliases: tuple[tuple[str, str, str], ...]
    ambiguous_aliases: tuple[tuple[str, str], ...]
    conflicts: tuple[Binding, ...]
    unresolved: tuple[Binding, ...]


@dataclass(frozen=True)
class UnitResolution:
    status: Literal["ACCEPTED", "UNRESOLVED", "CONFLICT"]
    factor: Decimal | None
    base_unit: str | None
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class QuantityReconciliation:
    raw_quantity: Decimal
    base_quantity: Decimal
    expected_base_quantity: Decimal | None
    mismatch: bool | None


_MEASURE_PREDICATES = {
    "revenue": ("exclude_cancelled", "exclude_returned"),
    "debt": ("exclude_cancelled", "exclude_cod", "exclude_marketplace", "positive_amount"),
    "item_quantity": ("exclude_cancelled", "exclude_returned", "exclude_fee"),
    "visit_count": (),
    "work_count": (),
}


class MeasurePolicy(ContractModel):
    """Reviewed measure vocabulary, distinct from fact retention or query qualification."""

    measure: Literal["revenue", "debt", "item_quantity", "visit_count", "work_count"]
    projection_id: str = Field(pattern=r"^p_[a-f0-9]{16,64}$")
    clause_ids: tuple[Sha256, ...] = ()
    version: Literal["measure-v1"] = "measure-v1"

    @property
    def predicates(self) -> tuple[str, ...]:
        return _MEASURE_PREDICATES[self.measure]

    @property
    def digest(self) -> str:
        import json

        return sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
