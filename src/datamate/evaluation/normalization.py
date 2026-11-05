"""Evaluator-owned normalization selection; production preparation never imports this module."""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

from duckdb import DuckDBPyConnection
from pydantic import Field, JsonValue, TypeAdapter, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.preparation.discovery import discover_business
from datamate.preparation.models import EvidenceRef, TableProfile

SELECTOR_VERSION = "stratified-source-v1"
STRATUM_TARGETS = {"rare": 4, "common": 4, "identity": 4, "temporal": 4, "structure": 4}
PROOF_VERSION = "source-proof-v1"
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


class AdjudicatedReference(ContractModel):
    case_id: Sha256
    revision: int = Field(ge=1)
    revision_reason: str | None = None
    expected_behavior: ExpectedBehavior | None
    expected_value: JsonValue
    evidence_status: Literal["VERIFIED", "PROVISIONAL", "UNRESOLVED"]
    proposal_digest: Sha256 | None
    checker_version: NonblankString
    reason: NonblankString
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def determinate(self) -> bool:
        return self.evidence_status == "VERIFIED" and self.expected_behavior in {
            "TRANSFORM",
            "PRESERVE",
        }


class ReferenceSuite(ContractModel):
    manifest_digest: Sha256
    references: tuple[AdjudicatedReference, ...] = Field(min_length=100, max_length=100)

    @model_validator(mode="after")
    def distinct_cases(self) -> Self:
        if len({reference.case_id for reference in self.references}) != 100:
            raise ValueError("reference suite requires 100 distinct cases")
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def check_reference_proof(
    proposal: ReferenceProposal, evidence_reader: Callable[[EvidenceRef], object]
) -> ProofCheckResult:
    """The reader is evaluator-owned and must verify source hashes and coordinates.

    This first checker proves exact preservation only. Semantic proof strategies
    are added with independent real evidence; unsupported strategies fail closed.
    In particular, agreeing arithmetic under two scales is not a distinguishing proof.
    """
    proposal = ReferenceProposal.model_validate_json(proposal.model_dump_json())
    verified = False
    reason = "proof kind has no independently qualified checker"
    if proposal.proof_kind == "exact_observation" and proposal.expected_behavior == "PRESERVE":
        try:
            observed = evidence_reader(proposal.evidence[0])
            verified = _digest(observed) == _digest(proposal.expected_value)
            reason = "exact source observation" if verified else "source observation differs"
        except (ValueError, OSError) as exc:
            reason = f"source evidence unavailable: {type(exc).__name__}"
    return ProofCheckResult(proposal.digest, verified, reason, _seal=_CHECKER_SEAL)


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
    return AdjudicatedReference(
        case_id=proposal.case_id,
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


def freeze_reference_suite(
    manifest: CaseManifest, references: Sequence[AdjudicatedReference]
) -> ReferenceSuite:
    manifest = CaseManifest.model_validate_json(manifest.model_dump_json())
    cases = {case.case_id: case for case in manifest.cases}
    selected = {}
    for reference in references:
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
