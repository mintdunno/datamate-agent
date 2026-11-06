"""Synthetic campaign and checkpoint contracts; no real pilot runs or labels."""

import json
import sqlite3

import pytest
from normalization_fixtures import reference_world

from datamate.evaluation.normalization import (
    AdjudicatedReference,
    ReferenceSuite,
    select_cases,
)
from datamate.inference.budget import RequestBudget


def api():
    from datamate.evaluation import normalization_campaign

    return normalization_campaign


def world(tmp_path, *, verified_per_business=0):
    policy, sources, profiles = reference_world(tmp_path)
    manifest = select_cases(policy, sources, profiles)
    indexed = {}
    for case in manifest.cases:
        indexed.setdefault(case.business_id, []).append(case)
    chosen = {case.case_id for cases in indexed.values() for case in cases[:verified_per_business]}
    refs = tuple(
        AdjudicatedReference(
            case_id=case.case_id,
            assertion_stage="parsed_cell",
            revision=1,
            expected_behavior="PRESERVE" if case.case_id in chosen else None,
            expected_value=7 if case.case_id in chosen else "PRIVATE_SENTINEL",
            evidence_status="VERIFIED" if case.case_id in chosen else "UNRESOLVED",
            proposal_digest="a" * 64 if case.case_id in chosen else None,
            checker_version="synthetic",
            reason="synthetic fixture",
        )
        for case in manifest.cases
    )
    suite = ReferenceSuite(manifest_digest=manifest.digest, references=refs)
    return policy, sources, manifest, suite, tmp_path / "train"


def hashes():
    return {
        name: format(n, "064x")
        for n, name in enumerate(
            ("code", "config", "prompt", "profile", "operator", "validator", "audit"), 1
        )
    }


def artifact_suite(manifest, suite):
    from datamate.evaluation.normalization import (
        ARTIFACT_OBSERVATION_VERSION,
        ArtifactObservationTarget,
    )
    from datamate.preparation.models import EvidenceRef

    cases = {case.case_id: case for case in manifest.cases}
    references = []
    for reference in suite.references:
        case = cases[reference.case_id]
        references.append(
            reference.model_copy(
                update={
                    "expected_value": {"type": "int", "value": "7"},
                    "observation_target": ArtifactObservationTarget(
                        stage="parsed_cell",
                        scope="source_cell",
                        fields=("value",),
                        value_type="int",
                    ),
                    "evidence": (
                        EvidenceRef(
                            source=case.source,
                            sha256=case.source_sha256,
                            locator=f"raw:{case.record_id}/{case.column_id}",
                        ),
                    ),
                }
            )
        )
    return ReferenceSuite(
        contract_version=ARTIFACT_OBSERVATION_VERSION,
        manifest_digest=manifest.digest,
        references=tuple(references),
    )


def test_artifact_campaign_prepares_without_private_assertion_targets(tmp_path):
    from datamate.preparation.pipeline import PreparationPolicy, prepare_business

    policy, sources, manifest, legacy, split_root = world(tmp_path, verified_per_business=1)
    suite = artifact_suite(manifest, legacy)
    calls = []

    def prepare(**kwargs):
        assert set(kwargs) == {"business_id", "cases", "phase", "cache_root", "candidate_digest"}
        assert all(not hasattr(case, "observation_target") for case in kwargs["cases"])
        assert "expected_value" not in repr(kwargs)
        calls.append(kwargs["business_id"])
        return prepare_business(
            split_root,
            kwargs["business_id"],
            kwargs["cache_root"],
            split_id="train",
            policy=PreparationPolicy(),
        )

    result = api().run_development_baseline(
        policy,
        suite,
        prepare,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        output_dir=tmp_path / "artifact-baseline",
        config_hashes=hashes(),
        candidate_id="artifact",
    )
    assert set(calls) == set(policy.development)
    assert len(result.observations) == 4
    assert all(len(output.case_results) == 20 for output in result.observations)
    assert all(output.api_attempts == 0 and output.full_cpu_audit for output in result.observations)
    assert result.qualification == "PARTIAL"


def test_artifact_campaign_rejects_legacy_label_shaped_callback(tmp_path):
    policy, sources, manifest, legacy, split_root = world(tmp_path)
    with pytest.raises(ValueError, match="artifact"):
        api().run_development_baseline(
            policy,
            artifact_suite(manifest, legacy),
            abstaining_prepare([]),
            cases=manifest,
            sources=sources,
            split_root=split_root,
            output_dir=tmp_path / "legacy-output",
            config_hashes=hashes(),
            candidate_id="artifact",
        )


def abstaining_prepare(seen):
    def prepare(*, business_id, cases, phase, cache_root, candidate_digest):
        assert "PRIVATE_SENTINEL" not in repr(
            (business_id, cases, phase, cache_root, candidate_digest)
        )
        seen.append((business_id, phase, tuple(case.case_id for case in cases), cache_root))
        return api().BusinessPreparation(
            business_id=business_id,
            audit_status="PASS",
            full_cpu_audit=True,
            cache_mode="COLD",
            accepted_plan_reuse=False,
            measurements_complete=True,
            elapsed_seconds=1.0,
            peak_rss_bytes=1024,
            peak_disk_bytes=2048,
            api_attempts=0,
            input_tokens=0,
            output_tokens=0,
            case_results=tuple(
                api().CandidateCaseResult(
                    case_id=case.case_id, business_id=business_id, status="ABSTAINED", value=None
                )
                for case in cases
            ),
        )

    return prepare


def baseline(tmp_path, *, verified_per_business=0):
    policy, sources, manifest, suite, split_root = world(
        tmp_path, verified_per_business=verified_per_business
    )
    seen = []
    run = api().run_development_baseline(
        policy,
        suite,
        abstaining_prepare(seen),
        cases=manifest,
        sources=sources,
        split_root=split_root,
        output_dir=tmp_path / "baseline",
        config_hashes=hashes(),
        candidate_id="candidate-1",
    )
    return policy, sources, manifest, suite, split_root, seen, run


def test_metrics_keep_abstentions_quarantine_and_zero_commitment_undefined(tmp_path):
    _, _, manifest, suite, _ = world(tmp_path, verified_per_business=12)
    cases = [case for case in manifest.cases if case.business_id == "a"][:12]
    results = api().CandidateResultSet(
        case_results=tuple(
            api().CandidateCaseResult(
                case_id=case.case_id,
                business_id="a",
                status="COMMITTED" if i < 10 else "ABSTAINED" if i == 10 else "QUARANTINED",
                value=7 if i < 10 else None,
            )
            for i, case in enumerate(cases)
        )
    )
    metrics = api().score_normalization(results, suite)
    assert metrics.verified_determinate_denominator == 12
    assert metrics.verified_determinate_successes == 10
    assert metrics.abstentions == 1
    assert metrics.quarantines == 1
    assert metrics.committed_correctness == 1.0
    zero = api().score_normalization(
        api().CandidateResultSet(
            case_results=tuple(
                api().CandidateCaseResult(
                    case_id=case.case_id, business_id="a", status="ABSTAINED", value=None
                )
                for case in cases
            )
        ),
        suite,
    )
    assert zero.committed_correctness is None


def test_verified_refusal_is_not_positive_coverage(tmp_path):
    _, _, manifest, suite, _ = world(tmp_path)
    case = manifest.cases[0]
    refs = list(suite.references)
    refs[0] = refs[0].model_copy(
        update={
            "expected_behavior": "SOURCE_ABSENCE",
            "expected_value": None,
            "evidence_status": "VERIFIED",
            "proposal_digest": "a" * 64,
        }
    )
    suite = ReferenceSuite(manifest_digest=manifest.digest, references=tuple(refs))
    result = api().CandidateResultSet(
        case_results=(
            api().CandidateCaseResult(
                case_id=case.case_id, business_id=case.business_id, status="ABSTAINED", value=None
            ),
        )
    )
    metrics = api().score_normalization(result, suite)
    assert metrics.verified_determinate_denominator == 0
    assert metrics.verified_negative_denominator == 1
    assert metrics.unsupported_negative_commitments == 0


def test_baseline_final_share_dev_ids_and_keep_checkpoint_private(tmp_path):
    policy, sources, manifest, suite, split_root, seen, run = baseline(tmp_path)
    assert run.record_kind == "result"
    assert len(run.development_case_ids) == 80
    assert len(run.checkpoint_case_ids) == 20
    assert {business for business, _, _, _ in seen} == set(policy.development)
    assert all("PRIVATE_SENTINEL" not in repr(item) for item in seen)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    receipt = api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    final = api().run_frozen_verification(
        freeze,
        receipt,
        suite,
        abstaining_prepare(seen),
        policy=policy,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    assert final.parent_spec_digest == freeze.sha256
    assert final.development_case_ids == run.development_case_ids
    assert len([item for item in seen if item[1] == "final"]) == 5
    assert final.qualification != "PASS"
    assert final.level2_status == "DISABLED"
    assert len({str(item[3]) for item in seen if item[1] == "final"}) == 1
    assert all(item[3] != seen[0][3] for item in seen if item[1] == "final")
    assert (tmp_path / "final" / "frozen-spec.json").read_bytes()
    assert (tmp_path / "final" / "result.json").read_bytes()


def test_checkpoint_claim_is_single_use_across_candidate_and_reference_revisions(tmp_path):
    policy, sources, manifest, suite, split_root, _, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    with pytest.raises(api().CheckpointConsumed):
        api().reserve_checkpoint(
            freeze,
            ledger,
            policy=policy,
            suite=suite,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    with sqlite3.connect(ledger) as db:
        assert db.execute("select state from normalization_checkpoint_claims").fetchone() == (
            "STARTED",
        )
        assert db.execute("select count(*) from normalization_checkpoint_claims").fetchone()[0] == 1


def test_source_or_reference_drift_aborts_before_checkpoint_callback(tmp_path):
    policy, sources, manifest, suite, split_root, seen, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    changed = suite.model_copy(update={"references": tuple(reversed(suite.references))})
    with pytest.raises(ValueError, match="reference"):
        api().reserve_checkpoint(
            freeze,
            ledger,
            policy=policy,
            suite=changed,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    (split_root / "data" / "a" / "sales.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source"):
        api().reserve_checkpoint(
            freeze,
            ledger,
            policy=policy,
            suite=suite,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    assert seen and len(seen) == 4
    with sqlite3.connect(ledger) as db:
        assert (
            db.execute(
                "select count(*) from sqlite_master where name='normalization_checkpoint_claims'"
            ).fetchone()[0]
            == 0
        )


def test_frozen_spec_and_result_are_separate_write_once_records(tmp_path):
    _, _, _, _, _, _, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    assert (
        freeze.sha256
        == api().CampaignRunManifest.model_validate_json(freeze.manifest_path.read_bytes()).digest
    )
    with pytest.raises(FileExistsError):
        api().freeze_candidate(run, tmp_path / "final")
    run.config_hashes["code"] = "f" * 64
    with pytest.raises(ValueError, match="digest"):
        api().freeze_candidate(run, tmp_path / "other")


def test_level2_disabled_does_not_mask_required_capability_failure(tmp_path):
    _, _, _, _, _, _, run = baseline(tmp_path)
    assert run.level2_status == "DISABLED"
    assert run.qualification != "PASS"


def committing_prepare(*, business_id, cases, phase, cache_root, candidate_digest):
    return api().BusinessPreparation(
        business_id=business_id,
        audit_status="PASS",
        full_cpu_audit=True,
        cache_mode="COLD",
        accepted_plan_reuse=False,
        measurements_complete=True,
        elapsed_seconds=1.0,
        peak_rss_bytes=1024,
        peak_disk_bytes=2048,
        api_attempts=0,
        input_tokens=0,
        output_tokens=0,
        case_results=tuple(
            api().CandidateCaseResult(
                case_id=case.case_id, business_id=business_id, status="COMMITTED", value=7
            )
            for case in cases
        ),
    )


def test_qualified_final_checks_each_business_in_its_partition(tmp_path):
    policy, sources, manifest, suite, split_root = world(tmp_path, verified_per_business=10)
    run = api().run_development_baseline(
        policy,
        suite,
        committing_prepare,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        output_dir=tmp_path / "baseline",
        config_hashes=hashes(),
        candidate_id="candidate-1",
    )
    assert run.qualification == "PASS"
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    receipt = api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    final = api().run_frozen_verification(
        freeze,
        receipt,
        suite,
        committing_prepare,
        policy=policy,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    assert final.qualification == "PASS"


def test_failed_started_checkpoint_persists_failure_and_cannot_retry(tmp_path):
    policy, sources, manifest, suite, split_root, _, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    receipt = api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )

    def failing_prepare(**kwargs):
        raise RuntimeError("synthetic failure")

    with pytest.raises(RuntimeError, match="synthetic failure"):
        api().run_frozen_verification(
            freeze,
            receipt,
            suite,
            failing_prepare,
            policy=policy,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    with sqlite3.connect(ledger) as db:
        assert db.execute("select state from normalization_checkpoint_claims").fetchone() == (
            "FAILED",
        )
    result = api().CampaignRunManifest.model_validate_json(
        (tmp_path / "final" / "result.json").read_bytes()
    )
    assert result.parent_spec_digest == freeze.sha256
    assert result.failure_type == "RuntimeError"
    with pytest.raises(api().CheckpointConsumed):
        api().reserve_checkpoint(
            freeze,
            ledger,
            policy=policy,
            suite=suite,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )


def test_new_candidate_and_reference_revision_cannot_rearm_source_checkpoint(tmp_path):
    policy, sources, manifest, suite, split_root, _, run = baseline(tmp_path)
    first = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    api().reserve_checkpoint(
        first,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    revised = suite.model_copy(update={"references": tuple(reversed(suite.references))})
    second_run = api().run_development_baseline(
        policy,
        revised,
        abstaining_prepare([]),
        cases=manifest,
        sources=sources,
        split_root=split_root,
        output_dir=tmp_path / "baseline-2",
        config_hashes=hashes(),
        candidate_id="candidate-2",
        run_id="baseline-2",
    )
    second = api().freeze_candidate(second_run, tmp_path / "final-2")
    with pytest.raises(api().CheckpointConsumed):
        api().reserve_checkpoint(
            second,
            ledger,
            policy=policy,
            suite=revised,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    with sqlite3.connect(ledger) as db:
        assert db.execute("select count(*) from normalization_checkpoint_claims").fetchone()[0] == 1


def test_typed_numeric_mismatch_does_not_count_as_correct(tmp_path):
    _, _, manifest, suite, _ = world(tmp_path, verified_per_business=1)
    case = next(case for case in manifest.cases if case.business_id == "a")
    result = api().CandidateResultSet(
        case_results=(
            api().CandidateCaseResult(
                case_id=case.case_id, business_id="a", status="COMMITTED", value=7.0
            ),
        )
    )
    metrics = api().score_normalization(result, suite)
    assert metrics.verified_determinate_denominator == 1
    assert metrics.verified_determinate_successes == 0


def test_cold_spec_cannot_pass_with_replay_or_ready_cache(tmp_path):
    policy, sources, manifest, suite, split_root = world(tmp_path, verified_per_business=10)

    def replay_prepare(**kwargs):
        return committing_prepare(**kwargs).model_copy(update={"cache_mode": "REPLAY"})

    run = api().run_development_baseline(
        policy,
        suite,
        replay_prepare,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        output_dir=tmp_path / "baseline",
        config_hashes=hashes(),
        candidate_id="candidate-1",
        execution_mode="COLD",
    )
    assert run.qualification == "PARTIAL"


def test_result_manifest_cannot_claim_pass_without_measurements(tmp_path):
    _, _, _, _, _, _, run = baseline(tmp_path)
    payload = (
        api()
        .CampaignRunManifest.model_validate_json(
            (tmp_path / "baseline" / "frozen-spec.json").read_bytes()
        )
        .model_dump(mode="json")
    )
    payload.update(
        record_kind="result",
        parent_spec_digest="f" * 64,
        qualification="PASS",
        observations=[],
        development_metrics=None,
    )
    with pytest.raises(ValueError, match="result"):
        api().CampaignRunManifest.model_validate_json(json.dumps(payload))


def test_forged_checkpoint_receipt_cannot_consume_legitimate_result_path(tmp_path):
    policy, sources, manifest, suite, split_root, seen, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    receipt = api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    forged = receipt.model_copy(update={"claim_id": "forged"})
    with pytest.raises(ValueError, match="receipt"):
        api().run_frozen_verification(
            freeze,
            forged,
            suite,
            abstaining_prepare(seen),
            policy=policy,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    assert not (tmp_path / "final" / "result.json").exists()
    final = api().run_frozen_verification(
        freeze,
        receipt,
        suite,
        abstaining_prepare(seen),
        policy=policy,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    assert final.parent_spec_digest == freeze.sha256


def test_started_claim_with_existing_result_never_reexecutes_callback(tmp_path):
    policy, sources, manifest, suite, split_root, _, run = baseline(tmp_path)
    freeze = api().freeze_candidate(run, tmp_path / "final")
    ledger = tmp_path / "inference.sqlite"
    RequestBudget(ledger, run_id="synthetic")
    receipt = api().reserve_checkpoint(
        freeze,
        ledger,
        policy=policy,
        suite=suite,
        cases=manifest,
        sources=sources,
        split_root=split_root,
        config_hashes=hashes(),
    )
    result_path = tmp_path / "final" / "result.json"
    result_path.write_bytes(b"interrupted prior result")
    called = []

    def should_not_prepare(**kwargs):
        called.append(True)
        raise AssertionError("callback was reexecuted")

    with pytest.raises(FileExistsError):
        api().run_frozen_verification(
            freeze,
            receipt,
            suite,
            should_not_prepare,
            policy=policy,
            cases=manifest,
            sources=sources,
            split_root=split_root,
            config_hashes=hashes(),
        )
    assert called == []
    assert result_path.read_bytes() == b"interrupted prior result"
    with sqlite3.connect(ledger) as db:
        assert db.execute("select state from normalization_checkpoint_claims").fetchone() == (
            "FAILED",
        )
