"""Evaluator-owned normalization selection; production preparation never imports this module."""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Self

import duckdb
from duckdb import DuckDBPyConnection
from pydantic import Field, JsonValue, PrivateAttr, TypeAdapter, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.preparation.discovery import discover_business
from datamate.preparation.models import EvidenceRef, TableProfile

SELECTOR_VERSION = "stratified-source-v1"
STRATUM_TARGETS = {"rare": 4, "common": 4, "identity": 4, "temporal": 4, "structure": 4}
PROOF_VERSION = "source-proof-v5"
ARTIFACT_OBSERVATION_VERSION = "artifact-observation-v1"
AssertionStage = Literal[
    "raw", "parsed_cell", "canonical_fact", "disposition", "relationship", "capability"
]
ExpectedBehavior = Literal["TRANSFORM", "PRESERVE", "SOURCE_ABSENCE", "PROCESSING_AMBIGUITY"]
_CHECKER_SEAL = object()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _safe_component(value: str) -> bool:
    return (
        bool(value.strip())
        and value not in {".", ".."}
        and not any(char in value for char in "/\\:\x00")
    )


class PilotPolicy(ContractModel):
    version: int = Field(default=1, ge=1)
    split_id: Literal["train"] = "train"
    development: tuple[NonblankString, ...]
    checkpoint: tuple[NonblankString, ...]
    seed: int = Field(default=42, ge=0)
    cases_per_business: Literal[20] = 20
    minimum_determinate: int = Field(default=10, ge=10)
    campaign_limit: int = Field(default=120, ge=1, le=120)
    phase_limits: dict[str, int] = Field(
        default_factory=lambda: {"smoke": 20, "development": 60, "final": 40}
    )

    @property
    def business_ids(self) -> tuple[str, ...]:
        return self.development + self.checkpoint

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        ids = self.business_ids
        if (
            len(self.development) != 4
            or len(self.checkpoint) != 1
            or len(set(ids)) != 5
            or not all(_safe_component(value) for value in ids)
        ):
            raise ValueError("pilot requires four distinct development and one checkpoint business")
        if self.minimum_determinate > self.cases_per_business:
            raise ValueError("determinate minimum exceeds case count")
        ceilings = {"smoke": 20, "development": 60, "final": 40}
        if set(self.phase_limits) != set(ceilings) or any(
            type(value) is not int or not 1 <= value <= ceilings[key]
            for key, value in self.phase_limits.items()
        ):
            raise ValueError("invalid campaign phase ceilings")
        if sum(self.phase_limits.values()) > self.campaign_limit:
            raise ValueError("phase ceilings exceed campaign total")
        return self


class SourceSnapshot(ContractModel):
    split_id: NonblankString
    files: dict[NonblankString, dict[NonblankString, Sha256]]

    @model_validator(mode="after")
    def allowlisted_sources(self) -> Self:
        for business, files in self.files.items():
            if not _safe_component(business):
                raise ValueError("invalid snapshot business")
            if not {"rulebook.md", "calendar.csv"} <= files.keys():
                raise ValueError("snapshot requires active split rules/calendar")
            for name in files:
                parts = name.split("/")
                if name in {"rulebook.md", "calendar.csv"}:
                    continue
                if (
                    len(parts) < 3
                    or parts[:2] != ["data", business]
                    or not all(_safe_component(part) for part in parts)
                    or Path(name).suffix.lower() not in {".csv", ".xlsx", ".sqlite", ".md"}
                ):
                    raise ValueError("snapshot contains non-source path")
        return self


class CatalogCase(ContractModel):
    case_id: int = Field(ge=1, le=44)
    owner: Literal["N", "M", "Q"]
    task: int = Field(ge=1, le=24)
    test_modules: tuple[NonblankString, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def test_paths(self) -> Self:
        if any(
            not path.startswith("tests/")
            or not path.endswith(".py")
            or not all(_safe_component(part) for part in path.split("/"))
            for path in self.test_modules
        ):
            raise ValueError("catalog tests must be repository test modules")
        return self


def load_case_catalog(path: Path) -> tuple[CatalogCase, ...]:
    cases = TypeAdapter(tuple[CatalogCase, ...]).validate_json(path.read_bytes())
    if len(cases) != 44 or {case.case_id for case in cases} != set(range(1, 45)):
        raise ValueError("catalog must contain all 44 cases exactly once")
    return tuple(sorted(cases, key=lambda case: case.case_id))


class NormalizationCase(ContractModel):
    case_id: Sha256
    business_id: NonblankString
    partition: Literal["development", "checkpoint"]
    record_id: NonblankString
    column_id: NonblankString
    source: NonblankString
    source_sha256: Sha256
    table_id: NonblankString
    stratum: Literal["rare", "common", "identity", "temporal", "structure"]


class CaseManifest(ContractModel):
    source_policy_digest: Sha256
    profile_digests: tuple[Sha256, ...]
    selector_version: str
    stratum_targets: dict[str, int]
    missing_strata: dict[str, dict[str, int]]
    cases: tuple[NormalizationCase, ...] = Field(min_length=100, max_length=100)

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.case_id for case in self.cases}) != 100:
            raise ValueError("manifest requires 100 distinct cases")
        return self


class ReferenceProposal(ContractModel):
    """Untrusted reviewer boundary; no verification/confidence fields are accepted."""

    case_id: Sha256
    assertion_stage: AssertionStage = "raw"
    revision: int = Field(ge=1)
    revision_reason: NonblankString | None = None
    expected_behavior: ExpectedBehavior
    expected_value: JsonValue
    proof_kind: NonblankString
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def explain_revisions(self) -> Self:
        if self.revision > 1 and self.revision_reason is None:
            raise ValueError("reference revision requires an evidence-based reason")
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


@dataclass(frozen=True)
class ProofCheckResult:
    """Internal checker output, never an LLM response schema."""

    proposal_digest: str
    verified: bool
    reason: str
    version: str = PROOF_VERSION
    _seal: object = field(default=None, repr=False, compare=False)


class ArtifactObservationTarget(ContractModel):
    """Private, fixed artifact address; never an executable query or learner input."""

    version: Literal["artifact-observation-v1"] = ARTIFACT_OBSERVATION_VERSION
    stage: AssertionStage
    scope: Literal["source_cell", "calendar_days", "relationship"]
    fields: tuple[Literal["value", "date", "holiday", "product_id", "matched_record_id"], ...]
    cardinality: Literal["one"] = "one"
    value_type: Literal["date", "datetime", "int"] | None = None

    @model_validator(mode="after")
    def fixed_target(self) -> Self:
        if self.scope == "source_cell":
            valid = self.fields == ("value",) and (
                (self.stage == "raw" and self.value_type is None)
                or (self.stage in {"parsed_cell", "canonical_fact"} and self.value_type is not None)
            )
        elif self.scope == "calendar_days":
            valid = (
                self.stage == "canonical_fact"
                and self.fields == ("date", "holiday")
                and self.value_type is None
            )
        else:
            valid = (
                self.stage == "relationship"
                and self.fields == ("product_id", "matched_record_id")
                and self.value_type is None
            )
        if not valid:
            raise ValueError("unsupported artifact observation target")
        return self


class AdjudicatedReference(ContractModel):
    case_id: Sha256
    assertion_stage: AssertionStage = "raw"
    revision: int = Field(ge=1)
    revision_reason: str | None = None
    expected_behavior: ExpectedBehavior | None
    expected_value: JsonValue
    evidence_status: Literal["VERIFIED", "PROVISIONAL", "UNRESOLVED"]
    proposal_digest: Sha256 | None
    checker_version: NonblankString
    reason: NonblankString
    evidence: tuple[EvidenceRef, ...] = ()
    observation_target: ArtifactObservationTarget | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    _checker_seal: object = PrivateAttr(default=None)

    @property
    def determinate(self) -> bool:
        return (
            self.assertion_stage != "raw"
            and self.evidence_status == "VERIFIED"
            and self.expected_behavior
            in {
                "TRANSFORM",
                "PRESERVE",
            }
        )


class ReferenceSuite(ContractModel):
    contract_version: Literal["artifact-observation-v1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    manifest_digest: Sha256
    references: tuple[AdjudicatedReference, ...] = Field(min_length=100, max_length=100)

    @model_validator(mode="after")
    def distinct_cases(self) -> Self:
        if len({reference.case_id for reference in self.references}) != 100:
            raise ValueError("reference suite requires 100 distinct cases")
        if self.contract_version is None and any(ref.observation_target for ref in self.references):
            raise ValueError("artifact observation target requires a versioned suite contract")
        for ref in self.references:
            if ref.observation_target and ref.observation_target.stage != ref.assertion_stage:
                raise ValueError("artifact observation target stage disagrees with reference")
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def _source_cell(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    cell = value.get("cell", value)
    if not isinstance(cell, dict) or set(cell) != {"type", "value"}:
        return None
    return cell


def _source_text(value: object) -> str | None:
    cell = _source_cell(value)
    if cell is None or cell["type"] not in {"str", "date", "datetime"}:
        return None
    raw = cell["value"]
    return raw if isinstance(raw, str) else None


def _expected_date(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if set(value) == {"type", "value"} and value["type"] == "date":
            return value["value"]
        if set(value) == {"date"}:
            return value["date"]
    return None


def _is_iso_date(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if proposal.assertion_stage != "parsed_cell":
        return False
    raw, expected = _source_text(observed[0]), _expected_date(proposal.expected_value)
    if not isinstance(raw, str) or not isinstance(expected, str):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw, re.ASCII):
        return False
    try:
        return date.fromisoformat(raw).isoformat() == expected
    except ValueError:
        return False


def _is_native_date(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if proposal.assertion_stage not in {"parsed_cell", "canonical_fact"}:
        return False
    raw, expected = _source_cell(observed[0]), _expected_date(proposal.expected_value)
    if raw is None or raw["type"] != "date" or not isinstance(expected, str):
        return False
    try:
        return date.fromisoformat(str(raw["value"])).isoformat() == expected
    except ValueError:
        return False


def _is_day_first_date(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if proposal.assertion_stage != "parsed_cell":
        return False
    raw, expected = _source_text(observed[0]), _expected_date(proposal.expected_value)
    if not isinstance(raw, str) or not isinstance(expected, str):
        return False
    match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})", raw, re.ASCII)
    if match is None:
        return False
    day, month, year = map(int, match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError:
        return False
    if parsed.isoformat() != expected:
        return False
    if day > 12:
        return True
    # A neighboring unambiguous cell does not establish the target row's format.
    target_source = proposal.evidence[0].source
    if not target_source.startswith("data/"):
        return False
    business = target_source.split("/")[1]
    return any(
        ref.quote_sha256 is not None
        and ref.source in {"rulebook.md", f"data/{business}/README.md"}
        and isinstance(value, dict)
        and isinstance(value.get("quote"), str)
        and re.fullmatch(
            r"(?:dates are dd/mm/yyyy|date_format\s*[:=]\s*dd/mm/yyyy)\.?",
            value["quote"].strip().casefold(),
        )
        is not None
        for ref, value in zip(proposal.evidence[1:], observed[1:], strict=True)
    )


def _is_native_timestamp(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if proposal.assertion_stage != "parsed_cell" or not isinstance(proposal.expected_value, str):
        return False
    cell = _source_cell(observed[0])
    if cell is None or cell["type"] != "datetime":
        return False
    native = str(cell["value"])
    # datetime.fromisoformat silently truncates sub-microsecond precision.
    if any(re.search(r"[.,][0-9]{7,}", value) for value in (native, proposal.expected_value)):
        return False
    try:
        return datetime.fromisoformat(native) == datetime.fromisoformat(proposal.expected_value)
    except ValueError:
        return False


def _is_calendar_integer(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if proposal.assertion_stage != "parsed_cell":
        return False
    raw = _source_text(observed[0])
    expected = proposal.expected_value
    if isinstance(expected, dict):
        if set(expected) != {"type", "value"} or expected["type"] != "int":
            return False
        expected = expected["value"]
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{4}", raw, re.ASCII):
        return False
    if isinstance(expected, bool) or not isinstance(expected, int):
        return False
    return int(raw) == expected and 1800 <= expected <= 2200


def _is_source_calendar_event(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if (
        proposal.assertion_stage != "canonical_fact"
        or not isinstance(proposal.expected_value, dict)
        or set(proposal.expected_value) != {"date", "holiday"}
    ):
        return False
    raw = [
        (ref, value)
        for ref, value in zip(proposal.evidence, observed, strict=True)
        if ref.locator.startswith("raw:")
    ]
    if len(raw) < 2:
        return False
    target_ref, target_value = raw[0]
    holiday = _source_text(target_value)
    expected_date = proposal.expected_value["date"]
    if holiday != proposal.expected_value["holiday"] or not isinstance(expected_date, str):
        return False
    try:
        if date.fromisoformat(expected_date).isoformat() != expected_date:
            return False
    except ValueError:
        return False
    target_record = target_ref.locator[4:].split("/", 1)[0]
    for ref, value in raw[1:]:
        if ref.source != target_ref.source or ref.sha256 != target_ref.sha256:
            continue
        if ref.locator[4:].split("/", 1)[0] != target_record:
            continue
        if _source_text(value) == expected_date:
            return True
    return False


def _is_trusted_key_relationship(
    proposal: ReferenceProposal,
    observed: list[object],
    reader: Callable[[EvidenceRef], object],
) -> bool:
    if (
        proposal.assertion_stage != "relationship"
        or not isinstance(reader, SourceProofReader)
        or not isinstance(proposal.expected_value, dict)
        or set(proposal.expected_value) != {"product_id", "matched_record_id"}
    ):
        return False
    raw = [
        (ref, value)
        for ref, value in zip(proposal.evidence, observed, strict=True)
        if ref.locator.startswith("raw:")
    ]
    if len(raw) != 2:
        return False
    authority = [
        (ref, value["quote"])
        for ref, value in zip(proposal.evidence, observed, strict=True)
        if isinstance(value, dict) and isinstance(value.get("quote"), str)
    ]
    (source_ref, source_value), (matched_ref, matched_value) = raw
    business = source_ref.source.split("/")[1] if source_ref.source.startswith("data/") else ""
    if not business or not matched_ref.source.startswith(f"data/{business}/"):
        return False
    if not any(
        ref.quote_sha256 is not None
        and ref.source in {"rulebook.md", f"data/{business}/README.md"}
        and re.fullmatch(
            r"(?:tables join by product code|các file nối với nhau qua mã)\.?",
            quote.strip().casefold(),
        )
        is not None
        for ref, quote in authority
    ):
        return False
    source_cell, matched_cell = _source_cell(source_value), _source_cell(matched_value)
    key = proposal.expected_value["product_id"]
    matched_record = matched_ref.locator[4:].split("/", 1)[0]
    source_record = source_ref.locator[4:].split("/", 1)[0]
    if (
        not isinstance(key, str)
        or not key.strip()
        or proposal.expected_value["matched_record_id"] != matched_record
        or source_record == matched_record
        or source_record.rsplit(":", 1)[0] == matched_record.rsplit(":", 1)[0]
        or source_ref.source == matched_ref.source
        or Path(source_ref.source).suffix.lower() == ".sqlite"
        or Path(matched_ref.source).suffix.lower() == ".sqlite"
        or not reader.key_column_declared(source_ref)
        or not reader.key_column_declared(matched_ref)
        or source_cell != {"type": "str", "value": key}
        or matched_cell != {"type": "str", "value": key}
        or source_value.get("record_kind") != "data"
        or matched_value.get("record_kind") != "data"
    ):
        return False
    return reader.key_count(matched_ref, key) == 1


def _is_demonstrated_ambiguity(proposal: ReferenceProposal, observed: list[object]) -> bool:
    if (
        proposal.assertion_stage != "parsed_cell"
        or proposal.expected_behavior != "PROCESSING_AMBIGUITY"
        or not isinstance(proposal.expected_value, dict)
        or set(proposal.expected_value) != {"interpretations"}
        or len(proposal.evidence) != 1
    ):
        return False
    raw = _source_text(observed[0])
    values = proposal.expected_value["interpretations"]
    if not isinstance(raw, str) or not isinstance(values, list) or len(values) != 2:
        return False
    match = re.fullmatch(r"([1-9][0-9]{0,2})\.([0-9]{3})", raw, re.ASCII)
    if match is None or not all(isinstance(value, str) for value in values):
        return False
    try:
        proposed = {Decimal(value) for value in values}
    except InvalidOperation:
        return False
    admissible = {Decimal(raw), Decimal("".join(match.groups()))}
    return len(proposed) == len(admissible) == 2 and proposed == admissible


def _is_declared_absence(
    proposal: ReferenceProposal,
    observed: list[object],
    reader: Callable[[EvidenceRef], object],
) -> bool:
    if (
        proposal.assertion_stage != "capability"
        or proposal.expected_behavior != "SOURCE_ABSENCE"
        or not isinstance(reader, SourceProofReader)
        or not isinstance(proposal.expected_value, dict)
        or set(proposal.expected_value) != {"field"}
    ):
        return False
    field = proposal.expected_value["field"]
    source = proposal.evidence[0].source
    if not isinstance(field, str) or not field.strip() or not source.startswith("data/"):
        return False
    business = source.split("/")[1]
    clauses = [
        value["quote"].casefold()
        for ref, value in zip(proposal.evidence, observed, strict=True)
        if isinstance(value, dict)
        and isinstance(value.get("quote"), str)
        and ref.source in {"rulebook.md", f"data/{business}/README.md"}
    ]
    escaped = re.escape(field.casefold())
    declared = any(
        re.search(rf"\bno\s+{escaped}\b.*\b(recorded|stored|collected)\b", quote)
        or re.search(rf"{escaped}.*không.*(ghi|lưu)", quote)
        for quote in clauses
    )
    return bool(declared and reader.column_absent(business, field))


def check_reference_proof(
    proposal: ReferenceProposal, evidence_reader: Callable[[EvidenceRef], object]
) -> ProofCheckResult:
    """Verify fixed proof kinds against source-only observations; others remain unresolved."""
    proposal = ReferenceProposal.model_validate_json(proposal.model_dump_json())
    if proposal.expected_behavior not in {"TRANSFORM", "PRESERVE"} and not (
        (
            proposal.proof_kind == "declared_source_absence"
            and proposal.expected_behavior == "SOURCE_ABSENCE"
        )
        or (
            proposal.proof_kind == "demonstrated_ambiguity"
            and proposal.expected_behavior == "PROCESSING_AMBIGUITY"
        )
    ):
        return ProofCheckResult(
            proposal.digest,
            False,
            "proof kind does not establish refusal behavior",
            _seal=_CHECKER_SEAL,
        )
    try:
        observed = [evidence_reader(ref) for ref in proposal.evidence]
    except (ValueError, OSError, KeyError) as exc:
        return ProofCheckResult(
            proposal.digest,
            False,
            f"source evidence unavailable: {type(exc).__name__}",
            _seal=_CHECKER_SEAL,
        )
    verified = False
    kind = proposal.proof_kind
    if kind == "exact_observation":
        verified = (
            proposal.expected_behavior == "PRESERVE"
            and proposal.assertion_stage == "raw"
            and _digest(_source_cell(observed[0])) == _digest(proposal.expected_value)
        )
    elif kind in {"iso_calendar_date", "calendar_iso_date"}:
        verified = _is_iso_date(proposal, observed)
    elif kind == "iso_date_native_cell":
        verified = _is_native_date(proposal, observed) or _is_iso_date(proposal, observed)
    elif kind in {
        "day_first_date_format",
        "sheet_scoped_day_first_date",
        "native_complete_dmy_date",
    }:
        verified = _is_day_first_date(proposal, observed)
    elif kind in {"typed_calendar_integer", "calendar_integer_field"}:
        verified = _is_calendar_integer(proposal, observed)
    elif kind == "native_order_timestamp":
        verified = _is_native_timestamp(proposal, observed)
    elif kind == "trusted_key_relationship":
        verified = _is_trusted_key_relationship(proposal, observed, evidence_reader)
    elif kind in {"source_calendar_event", "scoped_calendar_row"}:
        verified = _is_source_calendar_event(proposal, observed)
    elif kind == "declared_source_absence":
        verified = _is_declared_absence(proposal, observed, evidence_reader)
    elif kind == "demonstrated_ambiguity":
        verified = _is_demonstrated_ambiguity(proposal, observed)
    reason = (
        "independent source proof" if verified else "proof lacks distinguishing source evidence"
    )
    return ProofCheckResult(proposal.digest, verified, reason, _seal=_CHECKER_SEAL)


class SourceProofReader:
    """Read frozen raw cells and source document spans without candidate normalization."""

    def __init__(self, db: DuckDBPyConnection, sources: SourceSnapshot, split_root: Path):
        self.db = db
        self.sources = sources
        self.split_root = split_root.resolve()
        self._source_bytes: dict[str, bytes] = {}

    def _bytes(self, evidence: EvidenceRef) -> bytes:
        if not any(
            files.get(evidence.source) == evidence.sha256 for files in self.sources.files.values()
        ):
            raise ValueError("source is outside frozen snapshot")
        path = (self.split_root / evidence.source).resolve()
        if not path.is_relative_to(self.split_root):
            raise ValueError("source escapes split root")
        if evidence.source not in self._source_bytes:
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != evidence.sha256:
                raise ValueError("source hash differs from frozen snapshot")
            self._source_bytes[evidence.source] = content
        return self._source_bytes[evidence.source]

    def column_absent(self, business: str, column: str) -> bool:
        """Require a complete frozen source-table inventory and no exact named column."""
        files = self.sources.files.get(business)
        if files is None:
            return False
        expected = {
            source
            for source in files
            if source.startswith(f"data/{business}/")
            and Path(source).suffix.lower() in {".csv", ".xlsx", ".sqlite"}
        }
        rows = self.db.execute(
            "SELECT source,source_sha256,headers_json FROM meta.source_tables"
        ).fetchall()
        seen = set()
        for source, sha256, headers_json in rows:
            if source not in expected:
                continue
            if files[source] != sha256:
                return False
            seen.add(source)
            headers = json.loads(headers_json)
            if any(
                isinstance(header, dict)
                and str(header.get("value", "")).strip().casefold() == column.strip().casefold()
                for header in headers
            ):
                return False
        return seen == expected

    def key_column_declared(self, evidence: EvidenceRef) -> bool:
        """Accept only explicit product-code headers in the cited physical table."""
        self._bytes(evidence)
        match = re.fullmatch(r"raw:(t_[a-f0-9]{64}):[0-9]+/(c[0-9]{4})", evidence.locator)
        if match is None:
            return False
        table_id, column_id = match.groups()
        row = self.db.execute(
            "SELECT headers_json FROM meta.source_tables "
            "WHERE table_id=? AND source=? AND source_sha256=?",
            [table_id, evidence.source, evidence.sha256],
        ).fetchone()
        if row is None:
            return False
        headers = json.loads(row[0])
        index = int(column_id[1:]) - 1
        if index >= len(headers) or not isinstance(headers[index], dict):
            return False
        header = headers[index].get("value")
        return isinstance(header, str) and header.strip().casefold() in {
            "product_id",
            "product_code",
            "sku",
            "mã hàng",
            "mã sản phẩm",
            "mã sp",
            "ma hang",
            "ma san pham",
            "ma sp",
        }

    def key_count(self, evidence: EvidenceRef, key: str) -> int:
        """Count exact data-row collisions in the cited destination key column."""
        self._bytes(evidence)
        match = re.fullmatch(r"raw:(t_[a-f0-9]{64}):[0-9]+/(c[0-9]{4})", evidence.locator)
        if match is None:
            raise ValueError("key count requires a physical source coordinate")
        table_id, column_id = match.groups()
        row = self.db.execute(
            "SELECT count(*) FROM staging.raw_rows r JOIN meta.source_tables t "
            "ON t.table_id=r.table_id WHERE r.table_id=? AND r.kind='data' "
            "AND t.source=? AND t.source_sha256=? "
            "AND json_extract_string(r.values_json, ?)=?",
            [table_id, evidence.source, evidence.sha256, f"$[{int(column_id[1:]) - 1}].value", key],
        ).fetchone()
        return int(row[0]) if row else 0

    def __call__(self, evidence: EvidenceRef) -> object:
        self._bytes(evidence)
        if evidence.locator.startswith("raw:"):
            cell = read_source_observation(self.db, self.sources, evidence)
            record_id = evidence.locator[4:].split("/", 1)[0]
            row = self.db.execute(
                "SELECT r.kind,r.source_row,t.header_row FROM staging.raw_rows r "
                "JOIN meta.source_tables t ON t.table_id=r.table_id "
                "WHERE r.record_id=? AND t.source=? AND t.source_sha256=?",
                [record_id, evidence.source, evidence.sha256],
            ).fetchone()
            if row is None:
                raise ValueError("source row metadata missing")
            return {"cell": cell, "record_kind": row[0], "source_row": row[1], "header_row": row[2]}
        if evidence.line_start is None or evidence.line_end is None:
            raise ValueError("document evidence needs an exact line span")
        span = f"{evidence.line_start}-{evidence.line_end}"
        if evidence.locator not in {f"md:{span}", f"line:{evidence.line_start}"}:
            raise ValueError("document locator disagrees with line span")
        lines = self._source_bytes[evidence.source].decode("utf-8-sig").splitlines(keepends=True)
        if evidence.line_end > len(lines):
            raise ValueError("document span exceeds source")
        quote = "".join(lines[evidence.line_start - 1 : evidence.line_end]).rstrip("\r\n")
        if evidence.quote_sha256 is not None and (
            hashlib.sha256(quote.encode("utf-8")).hexdigest() != evidence.quote_sha256
        ):
            raise ValueError("document quote hash mismatch")
        return {"quote": quote}


@dataclass(frozen=True)
class ReferenceEvaluation:
    suite: ReferenceSuite
    determinate_counts: dict[str, int]
    minimum_met: bool


def evaluate_reference_proposals(
    manifest: CaseManifest,
    proposals: Sequence[ReferenceProposal],
    readers: dict[str, Callable[[EvidenceRef], object]],
    *,
    minimum_determinate: int = 10,
) -> ReferenceEvaluation:
    """Adjudicate each proposed case once and preserve every selected case in the suite."""
    if minimum_determinate < 1:
        raise ValueError("minimum must be positive")
    cases = {case.case_id: case for case in manifest.cases}
    adjudicated = []
    seen = set()
    for raw_proposal in proposals:
        proposal = ReferenceProposal.model_validate_json(raw_proposal.model_dump_json())
        if proposal.case_id in seen or proposal.case_id not in cases:
            raise ValueError("proposal is duplicate or outside frozen manifest")
        seen.add(proposal.case_id)
        business = cases[proposal.case_id].business_id
        if business not in readers:
            raise ValueError("missing source-only reader for proposed business")
        proof = check_reference_proof(proposal, readers[business])
        adjudicated.append(adjudicate_reference(proposal, proof))
    suite = freeze_reference_suite(manifest, adjudicated)
    counts = {case.business_id: 0 for case in manifest.cases}
    for ref in suite.references:
        if ref.determinate:
            counts[cases[ref.case_id].business_id] += 1
    return ReferenceEvaluation(
        suite, counts, all(count >= minimum_determinate for count in counts.values())
    )


def adjudicate_frozen_reference_files(
    manifest_path: Path,
    selection_path: Path,
    proposals_root: Path,
    raw_root: Path,
    split_root: Path,
    suite_path: Path,
) -> ReferenceEvaluation:
    """Reproduce and atomically freeze an evaluator-only suite from immutable source inputs."""
    manifest = CaseManifest.model_validate_json(manifest_path.read_bytes())
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    policy = PilotPolicy.model_validate_json(json.dumps(selection["policy"]))
    sources = SourceSnapshot.model_validate(selection["sources"])
    if manifest.source_policy_digest != freeze_selection_policy(policy, sources):
        raise ValueError("frozen manifest disagrees with source selection")
    proposals = []
    with ExitStack() as stack:
        readers = {}
        for business in policy.business_ids:
            path = proposals_root / business / "proposals.json"
            proposals.extend(TypeAdapter(list[ReferenceProposal]).validate_json(path.read_bytes()))
            db = stack.enter_context(
                duckdb.connect(str(raw_root / business / "raw.duckdb"), read_only=True)
            )
            readers[business] = SourceProofReader(db, sources, split_root)
        result = evaluate_reference_proposals(
            manifest, proposals, readers, minimum_determinate=policy.minimum_determinate
        )
    write_frozen_reference_suite(suite_path, result.suite)
    return result


def read_source_observation(
    db: DuckDBPyConnection, sources: SourceSnapshot, evidence: EvidenceRef
) -> object:
    """Read only frozen raw staging, never clean/candidate values or parser output."""
    if not any(files.get(evidence.source) == evidence.sha256 for files in sources.files.values()):
        raise ValueError("evidence is outside frozen source snapshot")
    if not evidence.locator.startswith("raw:") or evidence.locator.count("/") != 1:
        raise ValueError("unsupported observation locator")
    record_id, column_id = evidence.locator[4:].split("/")
    row = db.execute(
        "SELECT r.values_json,t.columns_json FROM staging.raw_rows r "
        "JOIN meta.source_tables t ON t.table_id=r.table_id "
        "WHERE r.record_id=? AND t.source=? AND t.source_sha256=?",
        [record_id, evidence.source, evidence.sha256],
    ).fetchone()
    if row is None:
        raise ValueError("raw evidence coordinates do not match source")
    values, columns = json.loads(row[0]), json.loads(row[1])
    if column_id not in columns or columns.index(column_id) >= len(values):
        raise ValueError("source observation column is unavailable")
    return values[columns.index(column_id)]


def adjudicate_reference(
    proposal: ReferenceProposal, proof: ProofCheckResult
) -> AdjudicatedReference:
    proposal = ReferenceProposal.model_validate_json(proposal.model_dump_json())
    if proof._seal is not _CHECKER_SEAL:
        raise ValueError("verification must originate from the evaluator checker")
    if proof.proposal_digest != proposal.digest:
        raise ValueError("proof digest does not match reference proposal")
    reference = AdjudicatedReference(
        case_id=proposal.case_id,
        assertion_stage=proposal.assertion_stage,
        revision=proposal.revision,
        revision_reason=proposal.revision_reason,
        expected_behavior=proposal.expected_behavior,
        expected_value=proposal.expected_value,
        evidence_status="VERIFIED" if proof.verified else "UNRESOLVED",
        proposal_digest=proposal.digest,
        checker_version=proof.version,
        reason=proof.reason,
        evidence=proposal.evidence,
    )
    reference._checker_seal = _CHECKER_SEAL
    return reference


def freeze_reference_suite(
    manifest: CaseManifest, references: Sequence[AdjudicatedReference]
) -> ReferenceSuite:
    manifest = CaseManifest.model_validate_json(manifest.model_dump_json())
    cases = {case.case_id: case for case in manifest.cases}
    selected = {}
    for reference in references:
        if reference.evidence_status == "VERIFIED" and reference._checker_seal is not _CHECKER_SEAL:
            raise ValueError("verified reference must originate from evaluator checker")
        reference = AdjudicatedReference.model_validate_json(reference.model_dump_json())
        if reference.case_id not in cases or reference.case_id in selected:
            raise ValueError("reference is duplicate or outside frozen manifest")
        case = cases[reference.case_id]
        if not reference.evidence or (
            reference.evidence[0].source != case.source
            or reference.evidence[0].sha256 != case.source_sha256
            or reference.evidence[0].locator != f"raw:{case.record_id}/{case.column_id}"
        ):
            raise ValueError("reference target does not match frozen source case")
        selected[reference.case_id] = reference
    for case_id in cases.keys() - selected.keys():
        selected[case_id] = AdjudicatedReference(
            case_id=case_id,
            revision=1,
            expected_behavior=None,
            expected_value=None,
            evidence_status="UNRESOLVED",
            proposal_digest=None,
            checker_version=PROOF_VERSION,
            reason="no independent adjudication",
        )
    return ReferenceSuite(
        manifest_digest=manifest.digest,
        references=tuple(selected[key] for key in sorted(selected)),
    )


def select_cases(
    policy: PilotPolicy, sources: SourceSnapshot, profiles: Sequence[TableProfile]
) -> CaseManifest:
    source_digest = freeze_selection_policy(policy, sources)
    if len({profile.table_id for profile in profiles}) != len(profiles):
        raise ValueError("duplicate physical source profile")
    pools = {business: [] for business in policy.business_ids}
    for profile in profiles:
        matches = [
            business
            for business, files in sources.files.items()
            if files.get(profile.source) == profile.source_sha256
        ]
        if not matches:
            raise ValueError("profile does not match frozen source evidence")
        columns = {column.column_id: column for column in profile.columns}
        for sample in profile.samples:
            column = columns[sample.column_id]
            hints = {"rare" if sample.pattern_count <= 2 else "common"}
            if sample.column_id in profile.candidate_keys:
                hints.add("identity")
            header = str(column.source_header.get("value", "")).casefold()
            if set(column.raw_types) & {"date", "datetime", "time"} or any(
                token in header for token in ("date", "ngày", "ngay", "time", "calendar")
            ):
                hints.add("temporal")
            if sample.kind != "data":
                hints = {"structure"}
            rank = _digest([policy.seed, profile.table_id, sample.record_id, sample.column_id])
            for business in matches:
                pools[business].append((rank, profile, sample, hints))
    cases = []
    deficits = {}
    for business in sorted(pools):
        pool = sorted(pools[business], key=lambda item: item[0])
        selected = []
        seen = set()
        counts = {stratum: 0 for stratum in STRATUM_TARGETS}

        def take(item, stratum, *, seen=seen, counts=counts, selected=selected, business=business):
            _, profile, sample, _ = item
            seen.add(sample.record_id)
            counts[stratum] += 1
            selected.append(
                NormalizationCase(
                    case_id=_digest(
                        [
                            source_digest,
                            SELECTOR_VERSION,
                            business,
                            sample.record_id,
                            sample.column_id,
                            stratum,
                        ]
                    ),
                    business_id=business,
                    partition="checkpoint" if business in policy.checkpoint else "development",
                    record_id=sample.record_id,
                    column_id=sample.column_id,
                    source=profile.source,
                    source_sha256=profile.source_sha256,
                    table_id=profile.table_id,
                    stratum=stratum,
                )
            )

        for stratum, target in STRATUM_TARGETS.items():
            for item in pool:
                if counts[stratum] >= target:
                    break
                if stratum in item[3] and item[2].record_id not in seen:
                    take(item, stratum)
        for item in pool:
            if len(selected) >= policy.cases_per_business:
                break
            if item[2].record_id not in seen:
                take(item, sorted(item[3])[0])
        if len(selected) != policy.cases_per_business:
            raise ValueError("insufficient distinct source records for frozen pilot cases")
        deficits[business] = {
            s: target - counts[s] for s, target in STRATUM_TARGETS.items() if counts[s] < target
        }
        cases.extend(selected)
    # Risk strata are source hints, not verified semantic/difficulty labels. Persist deficits.
    return CaseManifest(
        source_policy_digest=source_digest,
        profile_digests=tuple(sorted(profile.digest for profile in profiles)),
        selector_version=SELECTOR_VERSION,
        stratum_targets=dict(STRATUM_TARGETS),
        missing_strata=deficits,
        cases=tuple(sorted(cases, key=lambda case: case.case_id)),
    )


def load_pilot_policy(path: Path) -> PilotPolicy:
    return PilotPolicy.model_validate_json(path.read_bytes())


def snapshot_sources(
    split_root: Path, business_ids: Sequence[str], *, split_id: str
) -> SourceSnapshot:
    if len(set(business_ids)) != len(business_ids):
        raise ValueError("duplicate snapshot business")
    files = {}
    for business in sorted(business_ids):
        inputs = discover_business(split_root, business, split_id=split_id)
        files[business] = {
            item.relative_path: item.sha256 for item in (*inputs.sources, *inputs.documents)
        }
    return SourceSnapshot(split_id=split_id, files=files)


def _selection_payload(policy: PilotPolicy, sources: SourceSnapshot) -> bytes:
    # Revalidate nested mappings: frozen Pydantic models do not freeze their dict values.
    policy = PilotPolicy.model_validate_json(policy.model_dump_json())
    sources = SourceSnapshot.model_validate_json(sources.model_dump_json())
    if policy.split_id != sources.split_id:
        raise ValueError("selection split does not match source split")
    if set(policy.business_ids) != set(sources.files):
        raise ValueError("selection businesses do not match snapshot businesses")
    for name in ("rulebook.md", "calendar.csv"):
        if len({files[name] for files in sources.files.values()}) != 1:
            raise ValueError("inconsistent active split source hashes")
    return json.dumps(
        {"policy": policy.model_dump(mode="json"), "sources": sources.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def freeze_selection_policy(policy: PilotPolicy, sources: SourceSnapshot) -> str:
    return hashlib.sha256(_selection_payload(policy, sources)).hexdigest()


def write_frozen_selection(path: Path, policy: PilotPolicy, sources: SourceSnapshot) -> str:
    payload = _selection_payload(policy, sources)
    _write_once(path, payload)
    return hashlib.sha256(payload).hexdigest()


def write_frozen_reference_suite(path: Path, suite: ReferenceSuite) -> str:
    suite = ReferenceSuite.model_validate_json(suite.model_dump_json())
    payload = json.dumps(
        suite.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    _write_once(path, payload)
    return suite.digest


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".selection-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publishes complete bytes atomically without replacing another writer.
        # Unsupported filesystems fail closed rather than falling back to overwrite/rename.
        os.link(temporary, path)
    finally:
        temporary.unlink()
