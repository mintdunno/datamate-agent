"""Direct evaluator-only normalization baseline and single-use checkpoint runner."""

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.evaluation.normalization import (
    CaseManifest,
    NormalizationCase,
    PilotPolicy,
    ReferenceSuite,
    SourceSnapshot,
    freeze_selection_policy,
)

CONFIG_KEYS = frozenset({"code", "config", "prompt", "profile", "operator", "validator", "audit"})


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


class CheckpointConsumed(RuntimeError):
    """The source-bound checkpoint was already exposed or started."""


class CandidateCaseResult(ContractModel):
    case_id: Sha256
    business_id: NonblankString
    status: Literal["COMMITTED", "ABSTAINED", "QUARANTINED"]
    value: JsonValue = None

    @model_validator(mode="after")
    def value_matches_status(self) -> Self:
        if self.status != "COMMITTED" and self.value is not None:
            raise ValueError("noncommitted case cannot carry a value")
        if self.status == "COMMITTED" and self.value is None:
            raise ValueError("committed case requires a value")
        return self


class CandidateResultSet(ContractModel):
    case_results: tuple[CandidateCaseResult, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.case_id for case in self.case_results}) != len(self.case_results):
            raise ValueError("duplicate candidate case")
        return self


class NormalizationMetrics(ContractModel):
    total_cases: int
    verified_determinate_denominator: int
    verified_determinate_successes: int
    verified_determinate_commitments: int
    committed_correctness: float | None
    verified_determinate_success_rate: float | None
    verified_negative_denominator: int
    unsupported_negative_commitments: int
    provisional_agreements: int
    abstentions: int
    quarantines: int
    business_determinate: dict[str, int]
    business_successes: dict[str, int]


def score_normalization(results: CandidateResultSet, suite: ReferenceSuite) -> NormalizationMetrics:
    """Score only supplied case IDs; refusal and provisional agreement stay separate."""
    results = CandidateResultSet.model_validate_json(results.model_dump_json())
    suite = ReferenceSuite.model_validate_json(suite.model_dump_json())
    refs = {ref.case_id: ref for ref in suite.references}
    denominator = successes = commitments = negative = negative_commits = provisional = 0
    abstentions = quarantines = 0
    by_business: dict[str, int] = {}
    business_successes: dict[str, int] = {}
    for result in results.case_results:
        if result.case_id not in refs:
            raise ValueError("candidate result outside frozen reference suite")
        ref = refs[result.case_id]
        by_business.setdefault(result.business_id, 0)
        business_successes.setdefault(result.business_id, 0)
        abstentions += result.status == "ABSTAINED"
        quarantines += result.status == "QUARANTINED"
        if ref.determinate:
            denominator += 1
            by_business[result.business_id] += 1
            if result.status == "COMMITTED":
                commitments += 1
                if _bytes(result.value) == _bytes(ref.expected_value):
                    successes += 1
                    business_successes[result.business_id] += 1
        elif ref.evidence_status == "VERIFIED" and ref.expected_behavior in {
            "SOURCE_ABSENCE",
            "PROCESSING_AMBIGUITY",
        }:
            negative += 1
            negative_commits += result.status == "COMMITTED"
        elif ref.evidence_status == "PROVISIONAL" and result.status == "COMMITTED":
            provisional += _bytes(result.value) == _bytes(ref.expected_value)
    return NormalizationMetrics(
        total_cases=len(results.case_results),
        verified_determinate_denominator=denominator,
        verified_determinate_successes=successes,
        verified_determinate_commitments=commitments,
        committed_correctness=successes / commitments if commitments else None,
        verified_determinate_success_rate=successes / denominator if denominator else None,
        verified_negative_denominator=negative,
        unsupported_negative_commitments=negative_commits,
        provisional_agreements=provisional,
        abstentions=abstentions,
        quarantines=quarantines,
        business_determinate=by_business,
        business_successes=business_successes,
    )


class BusinessPreparation(ContractModel):
    business_id: NonblankString
    case_results: tuple[CandidateCaseResult, ...] = Field(min_length=20, max_length=20)
    audit_status: Literal["PASS", "PARTIAL", "FAIL"]
    full_cpu_audit: bool
    cache_mode: Literal["COLD", "REPLAY", "READY_CACHE"]
    accepted_plan_reuse: bool
    measurements_complete: bool
    elapsed_seconds: float | None = Field(default=None, ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    peak_disk_bytes: int | None = Field(default=None, ge=0)
    api_attempts: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def measured_pass(self) -> Self:
        if self.measurements_complete and any(
            value is None
            for value in (
                self.elapsed_seconds,
                self.peak_rss_bytes,
                self.peak_disk_bytes,
                self.api_attempts,
                self.input_tokens,
                self.output_tokens,
            )
        ):
            raise ValueError("complete measurement requires every resource and API field")
        if any(case.business_id != self.business_id for case in self.case_results):
            raise ValueError("case business differs from preparation business")
        return self


class CampaignRunManifest(ContractModel):
    record_kind: Literal["frozen_spec", "result"]
    phase: Literal["baseline", "final"]
    run_id: NonblankString
    candidate_id: NonblankString
    artifact_dir: NonblankString
    cache_root: NonblankString
    policy_digest: Sha256
    source_policy_digest: Sha256
    case_manifest_digest: Sha256
    reference_suite_digest: Sha256
    config_hashes: dict[str, Sha256]
    all_case_ids: tuple[Sha256, ...] = Field(min_length=100, max_length=100)
    development_case_ids: tuple[Sha256, ...] = Field(min_length=80, max_length=80)
    checkpoint_case_ids: tuple[Sha256, ...] = Field(min_length=20, max_length=20)
    execution_mode: Literal["COLD", "REPLAY"]
    level2_status: Literal["DISABLED", "UNQUALIFIED", "QUALIFIED"]
    level2_required: bool
    mandatory_capabilities_met: bool
    parent_spec_digest: Sha256 | None = None
    observations: tuple[BusinessPreparation, ...] = ()
    development_metrics: NormalizationMetrics | None = None
    checkpoint_metrics: NormalizationMetrics | None = None
    qualification: Literal["PASS", "PARTIAL", "FAIL"] | None = None
    failure_type: NonblankString | None = None

    @model_validator(mode="after")
    def strict_record(self) -> Self:
        if set(self.config_hashes) != CONFIG_KEYS:
            raise ValueError("campaign config hashes are incomplete")
        if len(set(self.all_case_ids)) != 100 or len(set(self.development_case_ids)) != 80:
            raise ValueError("campaign case IDs must be unique")
        if (
            len(set(self.checkpoint_case_ids)) != 20
            or (set(self.development_case_ids) | set(self.checkpoint_case_ids))
            != set(self.all_case_ids)
            or set(self.development_case_ids) & set(self.checkpoint_case_ids)
        ):
            raise ValueError("campaign partitions must preserve all 100 cases")
        if self.record_kind == "frozen_spec" and any(
            value is not None
            for value in (
                self.parent_spec_digest,
                self.development_metrics,
                self.checkpoint_metrics,
                self.qualification,
                self.failure_type,
            )
        ):
            raise ValueError("frozen specification cannot contain results")
        if self.record_kind == "frozen_spec" and self.observations:
            raise ValueError("frozen specification cannot contain observations")
        if self.record_kind == "result" and (
            self.parent_spec_digest is None or self.qualification is None
        ):
            raise ValueError("result must link its frozen specification")
        if self.phase == "baseline" and self.checkpoint_metrics is not None:
            raise ValueError("baseline cannot contain checkpoint diagnostics")
        if self.record_kind == "result":
            if self.failure_type is not None and self.qualification != "FAIL":
                raise ValueError("failed result cannot claim qualification")
            if self.failure_type is None:
                expected_businesses = 4 if self.phase == "baseline" else 5
                if (
                    len(self.observations) != expected_businesses
                    or len({item.business_id for item in self.observations}) != expected_businesses
                    or self.development_metrics is None
                    or self.development_metrics.total_cases != 80
                    or (
                        self.phase == "final"
                        and (
                            self.checkpoint_metrics is None
                            or self.checkpoint_metrics.total_cases != 20
                        )
                    )
                ):
                    raise ValueError("complete result requires all measurements and partitions")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(_bytes(self.model_dump(mode="json"))).hexdigest()


class CandidateFreeze(ContractModel):
    manifest_path: Path
    sha256: Sha256


class CheckpointReservation(ContractModel):
    ledger_path: Path
    checkpoint_key: Sha256
    claim_id: NonblankString
    spec_digest: Sha256


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".campaign-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink()


def _save(path: Path, record: CampaignRunManifest) -> None:
    _write_once(path, _bytes(record.model_dump(mode="json")))


def _load(path: Path, *, expected: str | None = None) -> CampaignRunManifest:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise ValueError("campaign record digest differs from frozen bytes")
    record = CampaignRunManifest.model_validate_json(raw)
    if raw != _bytes(record.model_dump(mode="json")):
        raise ValueError("campaign record is not canonical")
    return record


def _validate_sources(sources: SourceSnapshot, split_root: Path) -> None:
    root = split_root.resolve()
    for files in sources.files.values():
        for relative, frozen_hash in files.items():
            path = (root / relative).resolve()
            if (
                not path.is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != frozen_hash
            ):
                raise ValueError("source snapshot drift")


def _preflight(
    policy: PilotPolicy,
    suite: ReferenceSuite,
    cases: CaseManifest,
    sources: SourceSnapshot,
    split_root: Path,
    config_hashes: dict[str, str],
) -> tuple[tuple[NormalizationCase, ...], tuple[NormalizationCase, ...]]:
    policy = PilotPolicy.model_validate_json(policy.model_dump_json())
    suite = ReferenceSuite.model_validate_json(suite.model_dump_json())
    cases = CaseManifest.model_validate_json(cases.model_dump_json())
    sources = SourceSnapshot.model_validate_json(sources.model_dump_json())
    if set(config_hashes) != CONFIG_KEYS or any(
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in config_hashes.values()
    ):
        raise ValueError("invalid config digest set")
    if cases.source_policy_digest != freeze_selection_policy(policy, sources):
        raise ValueError("source policy drift")
    if suite.manifest_digest != cases.digest or (
        {ref.case_id for ref in suite.references} != {case.case_id for case in cases.cases}
    ):
        raise ValueError("reference suite differs from frozen cases")
    development = tuple(
        sorted(
            (case for case in cases.cases if case.partition == "development"),
            key=lambda case: case.case_id,
        )
    )
    checkpoint = tuple(
        sorted(
            (case for case in cases.cases if case.partition == "checkpoint"),
            key=lambda case: case.case_id,
        )
    )
    if (
        len(development) != 80
        or len(checkpoint) != 20
        or any(case.business_id not in policy.development for case in development)
        or any(case.business_id not in policy.checkpoint for case in checkpoint)
    ):
        raise ValueError("campaign partition differs from pilot policy")
    _validate_sources(sources, split_root)
    return development, checkpoint


def _spec(
    *,
    phase: Literal["baseline", "final"],
    run_id: str,
    candidate_id: str,
    artifact_dir: Path,
    policy: PilotPolicy,
    suite: ReferenceSuite,
    cases: CaseManifest,
    sources: SourceSnapshot,
    config_hashes: dict[str, str],
    execution_mode: str,
    level2_status: str,
    level2_required: bool,
    mandatory_capabilities_met: bool,
    development: Sequence[NormalizationCase],
    checkpoint: Sequence[NormalizationCase],
) -> CampaignRunManifest:
    return CampaignRunManifest(
        record_kind="frozen_spec",
        phase=phase,
        run_id=run_id,
        candidate_id=candidate_id,
        artifact_dir=str(artifact_dir.resolve()),
        cache_root=str((artifact_dir.parent / "prepared" / phase).resolve()),
        policy_digest=_digest(policy.model_dump(mode="json")),
        source_policy_digest=freeze_selection_policy(policy, sources),
        case_manifest_digest=cases.digest,
        reference_suite_digest=suite.digest,
        config_hashes=dict(config_hashes),
        all_case_ids=tuple(sorted(case.case_id for case in cases.cases)),
        development_case_ids=tuple(case.case_id for case in development),
        checkpoint_case_ids=tuple(case.case_id for case in checkpoint),
        execution_mode=execution_mode,
        level2_status=level2_status,
        level2_required=level2_required,
        mandatory_capabilities_met=mandatory_capabilities_met,
    )


def _bind(
    spec: CampaignRunManifest,
    policy: PilotPolicy,
    suite: ReferenceSuite,
    cases: CaseManifest,
    sources: SourceSnapshot,
    split_root: Path,
    config_hashes: dict[str, str],
) -> tuple[tuple[NormalizationCase, ...], tuple[NormalizationCase, ...]]:
    development, checkpoint = _preflight(policy, suite, cases, sources, split_root, config_hashes)
    if spec.record_kind != "frozen_spec" or any(
        (
            spec.policy_digest != _digest(policy.model_dump(mode="json")),
            spec.source_policy_digest != freeze_selection_policy(policy, sources),
            spec.case_manifest_digest != cases.digest,
            spec.reference_suite_digest != suite.digest,
            spec.config_hashes != config_hashes,
            spec.all_case_ids != tuple(sorted(case.case_id for case in cases.cases)),
            spec.development_case_ids != tuple(case.case_id for case in development),
            spec.checkpoint_case_ids != tuple(case.case_id for case in checkpoint),
        )
    ):
        raise ValueError("frozen policy/source/reference/config digest drift")
    return development, checkpoint


def _validated_business_output(
    output: BusinessPreparation,
    business: str,
    cases: Sequence[NormalizationCase],
) -> BusinessPreparation:
    output = BusinessPreparation.model_validate_json(output.model_dump_json())
    if (
        output.business_id != business
        or {case.case_id for case in output.case_results} != {case.case_id for case in cases}
        or len(output.case_results) != len(cases)
    ):
        raise ValueError("preparation results do not cover every frozen business case")
    return output


def _prepare(
    spec: CampaignRunManifest,
    cases: Sequence[NormalizationCase],
    businesses: Sequence[str],
    prepare: Callable[..., BusinessPreparation],
    suite: ReferenceSuite,
) -> tuple[BusinessPreparation, ...]:
    from datamate.preparation.pipeline import PreparedBusiness

    outputs = []
    cache_root = Path(spec.cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    for business in businesses:
        selected = tuple(case for case in cases if case.business_id == business)
        if len(selected) != 20:
            raise ValueError("business does not have 20 frozen cases")
        output = prepare(
            business_id=business,
            cases=selected,
            phase=spec.phase,
            cache_root=cache_root,
            candidate_digest=spec.config_hashes["code"],
        )
        if suite.contract_version is not None:
            if not isinstance(output, PreparedBusiness):
                raise ValueError("artifact evaluation requires a prepared artifact callback")
            output = _artifact_business_output(output, business, selected, suite)
        outputs.append(_validated_business_output(output, business, selected))
    return tuple(outputs)


def _artifact_business_output(prepared, business, cases, suite) -> BusinessPreparation:
    from datamate.evaluation.prepared_observations import evaluate_prepared_cases

    catalog = prepared.catalog
    if (
        catalog.get("artifact_version") != 2
        or catalog.get("business_id") != business
        or catalog.get("fingerprint") != prepared.fingerprint
    ):
        raise ValueError("prepared artifact identity or version differs from business")
    observations = evaluate_prepared_cases(prepared.database, cases, suite)
    resources = catalog.get("resources", {})
    attempts = catalog.get("api_attempts")
    # Closed registry/replay paths make no requests. Missing provider usage on a
    # future API-backed path stays unknown; it cannot borrow this known zero.
    usage = catalog.get("token_usage", {})
    input_tokens = 0 if attempts == 0 else usage.get("input_tokens")
    output_tokens = 0 if attempts == 0 else usage.get("output_tokens")
    complete = resources.get("measurements_complete") is True and all(
        value is not None for value in (attempts, input_tokens, output_tokens)
    )
    replay = catalog.get("mode") == "replay"
    return BusinessPreparation(
        business_id=business,
        case_results=observations.case_results,
        audit_status="PASS" if catalog.get("status") == "PASS" else "PARTIAL",
        full_cpu_audit=not prepared.cache_hit and bool(catalog.get("audit_digest")),
        cache_mode="READY_CACHE" if prepared.cache_hit else "REPLAY" if replay else "COLD",
        accepted_plan_reuse=replay,
        measurements_complete=complete,
        elapsed_seconds=resources.get("elapsed_seconds"),
        peak_rss_bytes=resources.get("peak_rss_bytes"),
        peak_disk_bytes=resources.get("peak_disk_bytes"),
        api_attempts=attempts,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _metrics(outputs: Sequence[BusinessPreparation], suite: ReferenceSuite) -> NormalizationMetrics:
    return score_normalization(
        CandidateResultSet(
            case_results=tuple(case for output in outputs for case in output.case_results)
        ),
        suite,
    )


def _qualification(
    outputs: Sequence[BusinessPreparation],
    metrics: Sequence[NormalizationMetrics],
    spec: CampaignRunManifest,
    minimum: int,
    business_groups: Sequence[Sequence[str]],
) -> Literal["PASS", "PARTIAL", "FAIL"]:
    if any(output.audit_status == "FAIL" for output in outputs) or any(
        score.unsupported_negative_commitments
        or (score.verified_determinate_successes < score.verified_determinate_commitments)
        for score in metrics
    ):
        return "FAIL"
    if (
        not spec.mandatory_capabilities_met
        or (spec.level2_required and spec.level2_status != "QUALIFIED")
        or spec.level2_status == "UNQUALIFIED"
        or any(
            output.audit_status != "PASS"
            or not output.full_cpu_audit
            or output.cache_mode != spec.execution_mode
            or not output.measurements_complete
            for output in outputs
        )
        or any(
            score.verified_determinate_successes != score.verified_determinate_denominator
            for score in metrics
        )
        or any(
            score.business_determinate.get(business, 0) < minimum
            for score, group in zip(metrics, business_groups, strict=True)
            for business in group
        )
    ):
        return "PARTIAL"
    return "PASS"


def _result(
    spec: CampaignRunManifest,
    outputs: Sequence[BusinessPreparation],
    development_metrics: NormalizationMetrics | None,
    checkpoint_metrics: NormalizationMetrics | None,
    qualification: str,
    failure_type: str | None = None,
) -> CampaignRunManifest:
    payload = spec.model_dump(mode="json")
    payload.update(
        record_kind="result",
        parent_spec_digest=spec.digest,
        observations=[output.model_dump(mode="json") for output in outputs],
        development_metrics=development_metrics.model_dump(mode="json")
        if development_metrics
        else None,
        checkpoint_metrics=checkpoint_metrics.model_dump(mode="json")
        if checkpoint_metrics
        else None,
        qualification=qualification,
        failure_type=failure_type,
    )
    return CampaignRunManifest.model_validate_json(_bytes(payload))


def run_development_baseline(
    policy: PilotPolicy,
    suite: ReferenceSuite,
    prepare: Callable[..., BusinessPreparation],
    *,
    cases: CaseManifest,
    sources: SourceSnapshot,
    split_root: Path,
    output_dir: Path,
    config_hashes: dict[str, str],
    candidate_id: str,
    run_id: str = "baseline",
    execution_mode: Literal["COLD", "REPLAY"] = "COLD",
    level2_status: Literal["DISABLED", "UNQUALIFIED", "QUALIFIED"] = "DISABLED",
    level2_required: bool = False,
    mandatory_capabilities_met: bool = True,
) -> CampaignRunManifest:
    """Freeze source/reference/config before preparing all four development businesses."""
    development, checkpoint = _preflight(policy, suite, cases, sources, split_root, config_hashes)
    spec = _spec(
        phase="baseline",
        run_id=run_id,
        candidate_id=candidate_id,
        artifact_dir=output_dir,
        policy=policy,
        suite=suite,
        cases=cases,
        sources=sources,
        config_hashes=config_hashes,
        execution_mode=execution_mode,
        level2_status=level2_status,
        level2_required=level2_required,
        mandatory_capabilities_met=mandatory_capabilities_met,
        development=development,
        checkpoint=checkpoint,
    )
    _save(Path(spec.artifact_dir) / "frozen-spec.json", spec)
    outputs: tuple[BusinessPreparation, ...] = ()
    try:
        outputs = _prepare(spec, development, policy.development, prepare, suite)
        score = _metrics(outputs, suite)
        qualification = _qualification(
            outputs, (score,), spec, policy.minimum_determinate, (policy.development,)
        )
        result = _result(spec, outputs, score, None, qualification)
        _save(Path(spec.artifact_dir) / "result.json", result)
        return result
    except Exception as exc:
        failure = _result(spec, outputs, None, None, "FAIL", type(exc).__name__)
        _save(Path(spec.artifact_dir) / "result.json", failure)
        raise


def freeze_candidate(manifest: CampaignRunManifest, output_dir: Path) -> CandidateFreeze:
    """Write a final frozen specification after verifying baseline spec/result bytes."""
    if manifest.record_kind != "result" or manifest.phase != "baseline":
        raise ValueError("candidate freeze requires a baseline result")
    baseline_dir = Path(manifest.artifact_dir)
    recorded = _load(baseline_dir / "result.json")
    if recorded.digest != manifest.digest:
        raise ValueError("baseline result digest changed")
    _load(baseline_dir / "frozen-spec.json", expected=manifest.parent_spec_digest)
    if recorded.failure_type is not None or len(recorded.observations) != 4:
        raise ValueError("candidate baseline did not complete")
    payload = recorded.model_dump(mode="json")
    payload.update(
        record_kind="frozen_spec",
        phase="final",
        run_id=f"{manifest.run_id}-final",
        artifact_dir=str(output_dir.resolve()),
        cache_root=str((output_dir.parent / "prepared" / "final").resolve()),
        parent_spec_digest=None,
        observations=[],
        development_metrics=None,
        checkpoint_metrics=None,
        qualification=None,
        failure_type=None,
    )
    spec = CampaignRunManifest.model_validate_json(_bytes(payload))
    path = output_dir.resolve() / "frozen-spec.json"
    _save(path, spec)
    return CandidateFreeze(manifest_path=path, sha256=spec.digest)


def _frozen(freeze: CandidateFreeze) -> CampaignRunManifest:
    spec = _load(freeze.manifest_path, expected=freeze.sha256)
    if (
        spec.record_kind != "frozen_spec"
        or spec.phase != "final"
        or (freeze.manifest_path.resolve() != Path(spec.artifact_dir) / "frozen-spec.json")
    ):
        raise ValueError("invalid final frozen specification")
    return spec


def _checkpoint_key(policy: PilotPolicy, sources: SourceSnapshot) -> str:
    business = policy.checkpoint[0]
    return _digest(
        {
            "split_id": policy.split_id,
            "business_id": business,
            "source_files": sources.files[business],
        }
    )


def reserve_checkpoint(
    freeze: CandidateFreeze,
    ledger_path: Path,
    *,
    policy: PilotPolicy,
    suite: ReferenceSuite,
    cases: CaseManifest,
    sources: SourceSnapshot,
    split_root: Path,
    config_hashes: dict[str, str],
) -> CheckpointReservation:
    """Atomically consume one source-bound checkpoint in the existing attempt ledger."""
    spec = _frozen(freeze)
    _bind(spec, policy, suite, cases, sources, split_root, config_hashes)
    if not ledger_path.is_file():
        raise ValueError("existing inference attempt ledger is required")
    key = _checkpoint_key(policy, sources)
    claim_id = uuid.uuid4().hex
    with sqlite3.connect(ledger_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        if (
            db.execute(
                "select 1 from sqlite_master where type='table' and name='attempts'"
            ).fetchone()
            is None
        ):
            raise ValueError("checkpoint must share the inference attempt ledger")
        db.execute("""CREATE TABLE IF NOT EXISTS normalization_checkpoint_claims(
            checkpoint_key TEXT PRIMARY KEY, claim_id TEXT UNIQUE NOT NULL,
            spec_digest TEXT NOT NULL, reference_digest TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('STARTED','SUCCEEDED','FAILED'))
        )""")
        try:
            db.execute(
                "INSERT INTO normalization_checkpoint_claims VALUES (?,?,?,?,?)",
                (key, claim_id, freeze.sha256, suite.digest, "STARTED"),
            )
        except sqlite3.IntegrityError as exc:
            raise CheckpointConsumed("source-bound checkpoint already claimed") from exc
    return CheckpointReservation(
        ledger_path=ledger_path.resolve(),
        checkpoint_key=key,
        claim_id=claim_id,
        spec_digest=freeze.sha256,
    )


def _receipt(reservation: CheckpointReservation, spec: CampaignRunManifest) -> None:
    with sqlite3.connect(reservation.ledger_path) as db:
        row = db.execute(
            "SELECT claim_id,spec_digest,reference_digest,state "
            "FROM normalization_checkpoint_claims WHERE checkpoint_key=?",
            (reservation.checkpoint_key,),
        ).fetchone()
    if row != (reservation.claim_id, spec.digest, spec.reference_suite_digest, "STARTED"):
        raise ValueError("checkpoint receipt does not match an active ledger claim")


def _finish_claim(reservation: CheckpointReservation, state: str) -> None:
    with sqlite3.connect(reservation.ledger_path) as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute(
            "UPDATE normalization_checkpoint_claims SET state=? "
            "WHERE checkpoint_key=? AND claim_id=? AND spec_digest=? AND state='STARTED'",
            (state, reservation.checkpoint_key, reservation.claim_id, reservation.spec_digest),
        ).rowcount
        if changed != 1:
            raise ValueError("checkpoint claim is no longer STARTED")


def run_frozen_verification(
    freeze: CandidateFreeze,
    reservation: CheckpointReservation,
    suite: ReferenceSuite,
    prepare: Callable[..., BusinessPreparation],
    *,
    policy: PilotPolicy,
    cases: CaseManifest,
    sources: SourceSnapshot,
    split_root: Path,
    config_hashes: dict[str, str],
) -> CampaignRunManifest:
    """Execute one frozen final pass; a failed or crashed STARTED claim stays consumed."""
    spec = _frozen(freeze)
    if reservation.spec_digest != freeze.sha256 or reservation.checkpoint_key != _checkpoint_key(
        policy, sources
    ):
        raise ValueError("checkpoint receipt differs from frozen source identity")
    _receipt(reservation, spec)
    result_path = Path(spec.artifact_dir) / "result.json"
    outputs: tuple[BusinessPreparation, ...] = ()
    try:
        development, checkpoint = _bind(
            spec, policy, suite, cases, sources, split_root, config_hashes
        )
        if result_path.exists():
            raise FileExistsError("frozen result already exists; checkpoint cannot be replayed")
        outputs = _prepare(spec, (*development, *checkpoint), policy.business_ids, prepare, suite)
        dev_outputs = [output for output in outputs if output.business_id in policy.development]
        cp_outputs = [output for output in outputs if output.business_id in policy.checkpoint]
        dev_score, cp_score = _metrics(dev_outputs, suite), _metrics(cp_outputs, suite)
        qualification = _qualification(
            outputs,
            (dev_score, cp_score),
            spec,
            policy.minimum_determinate,
            (policy.development, policy.checkpoint),
        )
        result = _result(spec, outputs, dev_score, cp_score, qualification)
        _save(result_path, result)
        _finish_claim(reservation, "SUCCEEDED")
        return result
    except Exception as exc:
        failure = _result(spec, outputs, None, None, "FAIL", type(exc).__name__)
        try:
            if not result_path.exists():
                _save(result_path, failure)
        finally:
            _finish_claim(reservation, "FAILED")
        raise
