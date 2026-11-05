"""Evaluator selection must freeze source evidence without reading QA labels."""

import importlib
import json
from pathlib import Path

import pytest


def api():
    return importlib.import_module("datamate.evaluation.normalization")


def synthetic_split(tmp_path):
    root = tmp_path / "train"
    root.mkdir()
    (root / "rulebook.md").write_text("# Active rules", encoding="utf-8")
    (root / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    for name in ("a", "b", "c", "d", "e"):
        folder = root / "data" / name
        folder.mkdir(parents=True)
        (folder / "sales.csv").write_text("id,amount\n001,10\n", encoding="utf-8")
        (folder / "README.md").write_text("# Source grain", encoding="utf-8")
    return root


def policy():
    return api().PilotPolicy(development=("a", "b", "c", "d"), checkpoint=("e",))


def test_snapshot_excludes_labels_and_is_order_independent(tmp_path, monkeypatch):
    module = api()
    root = synthetic_split(tmp_path)
    forbidden = root / "answers.jsonl"
    forbidden.write_text("REFERENCE_SENTINEL", encoding="utf-8")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == forbidden:
            raise AssertionError("reference file read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    first = module.snapshot_sources(root, ("a", "b", "c", "d", "e"), split_id="train")
    second = module.snapshot_sources(root, ("e", "d", "c", "b", "a"), split_id="train")
    assert module.freeze_selection_policy(policy(), first) == module.freeze_selection_policy(
        policy(), second
    )
    assert set(first.files["a"]) == {
        "rulebook.md",
        "calendar.csv",
        "data/a/README.md",
        "data/a/sales.csv",
    }
    assert "REFERENCE_SENTINEL" not in first.model_dump_json()


def test_source_drift_changes_freeze_and_existing_freeze_cannot_be_overwritten(tmp_path):
    module = api()
    root = synthetic_split(tmp_path)
    selection = policy()
    snapshot = module.snapshot_sources(root, selection.business_ids, split_id="train")
    target = tmp_path / "frozen.json"
    digest = module.write_frozen_selection(target, selection, snapshot)
    original = target.read_bytes()
    (root / "data/a/sales.csv").write_text("id,amount\n001,20\n", encoding="utf-8")
    changed = module.snapshot_sources(root, selection.business_ids, split_id="train")
    assert module.freeze_selection_policy(selection, changed) != digest
    with pytest.raises(FileExistsError):
        module.write_frozen_selection(target, selection, changed)
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"development": ("a", "b", "c", "e")},
        {"development": ("a", "a", "c", "d")},
        {"development": ("../a", "b", "c", "d")},
        {"answers_path": "answers.jsonl"},
        {"campaign_limit": 121},
        {"phase_limits": {"smoke": 21, "development": 60, "final": 40}},
        {"minimum_determinate": 21},
        {"cases_per_business": 21},
        {"split_id": "public_test"},
    ],
)
def test_policy_rejects_unsafe_selection_or_budget_increase(changes):
    module = api()
    values = {"development": ("a", "b", "c", "d"), "checkpoint": ("e",)}
    with pytest.raises(ValueError):
        module.PilotPolicy(**(values | changes))


def test_freeze_rejects_missing_business_and_split_mismatch(tmp_path):
    module = api()
    root = synthetic_split(tmp_path)
    snapshot = module.snapshot_sources(root, ("a", "b", "c", "d"), split_id="train")
    with pytest.raises(ValueError, match="business"):
        module.freeze_selection_policy(policy(), snapshot)
    other = module.snapshot_sources(root, policy().business_ids, split_id="public_test")
    with pytest.raises(ValueError, match="split"):
        module.freeze_selection_policy(policy(), other)


def test_real_pilot_policy_has_expected_fixed_selection():
    module = api()
    value = module.load_pilot_policy(Path("config/normalization/pilot.json"))
    assert set(value.development) == {"biz_124", "biz_106", "biz_120", "biz_122"}
    assert value.checkpoint == ("biz_123",)
    assert value.cases_per_business == 20
    assert value.minimum_determinate == 10
    assert value.campaign_limit == 120
    assert value.phase_limits == {"smoke": 20, "development": 60, "final": 40}
    assert value.seed == 42
    assert "answers" not in json.dumps(value.model_dump())


def test_failed_publication_does_not_expose_partial_freeze(tmp_path, monkeypatch):
    import os

    module = api()
    root = synthetic_split(tmp_path)
    selection = policy()
    sources = module.snapshot_sources(root, selection.business_ids, split_id="train")
    target = tmp_path / "artifacts" / "selection.json"

    def fail_publication(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(os, "link", fail_publication)
    with pytest.raises(OSError, match="publication"):
        module.write_frozen_selection(target, selection, sources)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_catalog_requires_every_owned_case_exactly_once(tmp_path):
    module = api()
    path = Path("tests/fixtures/normalization/catalog_cases.json")
    cases = module.load_case_catalog(path)
    assert {case.case_id for case in cases} == set(range(1, 45))
    assert {case.owner for case in cases} == {"N", "M", "Q"}
    duplicate = [case.model_dump() for case in cases]
    duplicate[-1] = duplicate[0]
    bad = tmp_path / "bad-catalog.json"
    bad.write_text(json.dumps(duplicate), encoding="utf-8")
    with pytest.raises(ValueError, match="44"):
        module.load_case_catalog(bad)


def test_case_selection_is_seeded_source_bound_and_independent_of_profile_order(tmp_path):
    from normalization_fixtures import reference_world

    module = api()
    selection, sources, profiles = reference_world(tmp_path)
    manifest = module.select_cases(selection, sources, profiles)
    reversed_manifest = module.select_cases(selection, sources, list(reversed(profiles)))
    assert len(manifest.cases) == len({case.case_id for case in manifest.cases}) == 100
    assert manifest.digest == reversed_manifest.digest
    assert sum(case.business_id in selection.development for case in manifest.cases) == 80
    assert sum(case.business_id in selection.checkpoint for case in manifest.cases) == 20
    for business in selection.business_ids:
        assert (
            len({case.record_id for case in manifest.cases if case.business_id == business}) == 20
        )
    assert all(not hasattr(case, "expected_value") for case in manifest.cases)
    with pytest.raises(ValueError, match="source"):
        module.select_cases(
            selection,
            sources,
            [profiles[0].model_copy(update={"source_sha256": "b" * 64}), *profiles[1:]],
        )


def reference_proposal(*, kind="exact_observation", behavior="PRESERVE", **changes):
    module = api()
    return module.ReferenceProposal(
        case_id="c" * 64,
        revision=1,
        expected_behavior=behavior,
        expected_value={"type": "str", "value": "001"},
        proof_kind=kind,
        evidence=({"source": "data/a/sales.csv", "sha256": "a" * 64, "locator": "raw:t:1/c0001"},),
        **changes,
    )


@pytest.mark.parametrize("field", ["evidence_status", "checker_result", "verified", "confidence"])
def test_proposal_rejects_authoritative_verification_fields(field):
    with pytest.raises(ValueError):
        reference_proposal(**{field: "VERIFIED"})


def test_checker_rejects_guesses_and_proof_reuse_after_revision():
    module = api()
    proposal = reference_proposal()
    proof = module.check_reference_proof(proposal, lambda ref: {"type": "str", "value": "001"})
    adjudicated = module.adjudicate_reference(proposal, proof)
    assert adjudicated.evidence_status == "VERIFIED"
    changed = proposal.model_copy(update={"expected_value": {"type": "str", "value": "1"}})
    with pytest.raises(ValueError, match="digest"):
        module.adjudicate_reference(changed, proof)
    wrong = module.check_reference_proof(changed, lambda ref: {"type": "str", "value": "001"})
    assert module.adjudicate_reference(changed, wrong).evidence_status == "UNRESOLVED"


@pytest.mark.parametrize("kind", ["unknown_plugin", "arithmetic_total", "trusted_keys"])
def test_unknown_or_insufficient_proof_stays_unresolved(kind):
    module = api()
    proposal = reference_proposal(kind=kind, behavior="TRANSFORM")
    proof = module.check_reference_proof(proposal, lambda ref: "price1.000 qty2 total2.000")
    assert module.adjudicate_reference(proposal, proof).evidence_status == "UNRESOLVED"


def test_verified_refusal_does_not_count_as_determinate():
    module = api()
    reference = module.AdjudicatedReference(
        case_id="c" * 64,
        revision=1,
        expected_behavior="SOURCE_ABSENCE",
        expected_value=None,
        evidence_status="VERIFIED",
        proposal_digest="a" * 64,
        checker_version="synthetic-only",
        reason="independent fixture",
    )
    assert not reference.determinate


def test_unresolved_reference_is_not_replaced_and_revision_changes_digest(tmp_path):
    from normalization_fixtures import reference_world

    module = api()
    selection, sources, profiles = reference_world(tmp_path)
    manifest = module.select_cases(selection, sources, profiles)
    case = manifest.cases[0]
    from datamate.preparation.models import EvidenceRef

    proposal = reference_proposal().model_copy(
        update={
            "case_id": case.case_id,
            "evidence": (
                EvidenceRef(
                    source=case.source,
                    sha256=case.source_sha256,
                    locator=f"raw:{case.record_id}/{case.column_id}",
                ),
            ),
        }
    )
    proof = module.check_reference_proof(proposal, lambda ref: {"type": "str", "value": "001"})
    ref = module.adjudicate_reference(proposal, proof)
    suite = module.freeze_reference_suite(manifest, [ref])
    assert len(suite.references) == 100
    assert sum(r.evidence_status == "UNRESOLVED" for r in suite.references) == 99
    assert {r.case_id for r in suite.references} == {c.case_id for c in manifest.cases}
    revised = proposal.model_copy(
        update={"revision": 2, "revision_reason": "source evidence corrected"}
    )
    new_ref = module.adjudicate_reference(
        revised, module.check_reference_proof(revised, lambda ref: {"type": "str", "value": "001"})
    )
    assert module.freeze_reference_suite(manifest, [new_ref]).digest != suite.digest
    assert suite.manifest_digest == manifest.digest


def test_proof_requires_checker_origin_and_source_coordinates(tmp_path):
    import duckdb

    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.models import EvidenceRef
    from datamate.preparation.storage import create_storage, stage_table

    module = api()
    root = synthetic_split(tmp_path)
    sources = module.snapshot_sources(root, policy().business_ids, split_id="train")
    inputs = discover_business(root, "a", split_id="train")
    source = inputs.sources[0]
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(db, source, next(load_tables(source.path)))
        evidence = EvidenceRef(
            source=source.relative_path,
            sha256=source.sha256,
            locator=f"raw:{table.table_id}:2/c0001",
        )

        def reader(ref):
            return module.read_source_observation(db, sources, ref)

        assert reader(evidence) == {"type": "str", "value": "001"}
        proposal = reference_proposal().model_copy(update={"evidence": (evidence,)})
        forged = module.ProofCheckResult(proposal.digest, True, "model says so")
        with pytest.raises(ValueError, match="checker"):
            module.adjudicate_reference(proposal, forged)
        stale = proposal.model_copy(
            update={"evidence": (evidence.model_copy(update={"sha256": "f" * 64}),)}
        )
        proof = module.check_reference_proof(stale, reader)
        assert not proof.verified
        other_cell = evidence.model_copy(update={"locator": f"raw:{table.table_id}:2/c9999"})
        with pytest.raises(ValueError):
            reader(other_cell)


def test_reference_freeze_is_atomic_and_cannot_be_overwritten(tmp_path):
    from normalization_fixtures import reference_world

    module = api()
    selection, sources, profiles = reference_world(tmp_path)
    manifest = module.select_cases(selection, sources, profiles)
    suite = module.freeze_reference_suite(manifest, [])
    path = tmp_path / "references.json"
    assert module.write_frozen_reference_suite(path, suite) == suite.digest
    saved = path.read_bytes()
    with pytest.raises(FileExistsError):
        module.write_frozen_reference_suite(path, suite)
    assert path.read_bytes() == saved


def test_reference_sentinel_never_enters_preparation_catalog_or_request(tmp_path):
    from datamate.preparation.pipeline import prepare_business

    root = synthetic_split(tmp_path)
    sentinel = "EVALUATOR_PRIVATE_EXPECTATION_583102"
    (root / "answers.jsonl").write_text(sentinel, encoding="utf-8")
    evaluator_artifact = tmp_path / "suites" / "normalization-v1" / "references.json"
    evaluator_artifact.parent.mkdir(parents=True)
    evaluator_artifact.write_text(sentinel, encoding="utf-8")
    prepared = prepare_business(root, "a", tmp_path / "prepared", split_id="train")
    assert sentinel not in json.dumps(prepared.catalog)
    assert sentinel not in prepared.manifest_path.read_text(encoding="utf-8")
    # Production request inputs are source catalog + question, not evaluator artifacts.
    from datamate.contracts.inputs import Question

    question = Question(id="q", business_id="a", question="Count?", as_of_date="2025-01-01")
    assert sentinel not in json.dumps(
        {"catalog": prepared.catalog, "question": question.model_dump()}
    )
