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
        assert (
            sum(c.business_id == business and c.stratum == "structure" for c in manifest.cases) == 1
        )
        assert manifest.missing_strata[business]["structure"] == 3
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
    assert not adjudicated.determinate  # Raw fidelity alone cannot qualify normalization.
    changed = proposal.model_copy(update={"expected_value": {"type": "str", "value": "1"}})
    with pytest.raises(ValueError, match="digest"):
        module.adjudicate_reference(changed, proof)
    wrong = module.check_reference_proof(changed, lambda ref: {"type": "str", "value": "001"})
    assert module.adjudicate_reference(changed, wrong).evidence_status == "UNRESOLVED"


def test_exact_raw_observation_cannot_self_promote_to_normalized_cell_truth():
    module = api()
    proposal = reference_proposal(assertion_stage="parsed_cell")
    proof = module.check_reference_proof(proposal, lambda ref: {"type": "str", "value": "001"})
    assert not proof.verified


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


def test_iso_calendar_date_requires_source_typed_value_and_matching_expected_date():
    module = api()
    proposal = reference_proposal(kind="iso_calendar_date", behavior="TRANSFORM").model_copy(
        update={"assertion_stage": "parsed_cell", "expected_value": {"date": "2025-01-31"}}
    )
    matching = module.check_reference_proof(
        proposal, lambda ref: {"type": "str", "value": "2025-01-31"}
    )
    assert matching.verified
    mismatched = module.check_reference_proof(
        proposal, lambda ref: {"type": "str", "value": "2025-02-01"}
    )
    assert not mismatched.verified


def test_structural_header_metadata_without_independent_schema_stays_unresolved(tmp_path):
    import duckdb

    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.models import EvidenceRef
    from datamate.preparation.storage import create_storage, stage_table

    module = api()
    root = synthetic_split(tmp_path)
    sources = module.snapshot_sources(root, policy().business_ids, split_id="train")
    source = discover_business(root, "a", split_id="train").sources[0]
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(db, source, next(load_tables(source.path)))
        evidence = EvidenceRef(
            source=source.relative_path,
            sha256=source.sha256,
            locator=f"raw:{table.table_id}:1/c0001",
        )
        proposal = reference_proposal(kind="structural_header").model_copy(
            update={
                "assertion_stage": "disposition",
                "expected_value": {"kind": "header", "include_as_data": False},
                "evidence": (evidence,),
            }
        )
        reader = module.SourceProofReader(db, sources, root)
        assert not module.check_reference_proof(proposal, reader).verified

        def false_reader(ref):
            return {"type": "str", "value": "id"}

        assert not module.check_reference_proof(proposal, false_reader).verified


def test_native_arithmetic_without_source_roles_stays_unresolved():
    from datamate.preparation.models import EvidenceRef

    module = api()
    proposal = reference_proposal(kind="explicit_sale_arithmetic", behavior="TRANSFORM").model_copy(
        update={
            "assertion_stage": "parsed_cell",
            "expected_value": 2000,
            "evidence": tuple(
                EvidenceRef(source="data/a/sales.csv", sha256="a" * 64, locator=f"raw:t:1/c{i:04d}")
                for i in range(1, 4)
            ),
        }
    )
    cells = [
        {"type": "int", "value": 2000},
        {"type": "int", "value": 2},
        {"type": "int", "value": 1000},
    ]
    assert not module.check_reference_proof(
        proposal, lambda ref: cells[int(ref.locator[-1]) - 1]
    ).verified
    text_cells = [
        {"type": "str", "value": "2.000"},
        {"type": "str", "value": "2"},
        {"type": "str", "value": "1.000"},
    ]
    assert not module.check_reference_proof(
        proposal, lambda ref: text_cells[int(ref.locator[-1]) - 1]
    ).verified


def test_evaluate_reference_proposals_preserves_unresolved_manifest_and_minima(tmp_path):
    from normalization_fixtures import reference_world

    module = api()
    selection, sources, profiles = reference_world(tmp_path)
    manifest = module.select_cases(selection, sources, profiles)
    evaluation = module.evaluate_reference_proposals(manifest, [], {}, minimum_determinate=10)
    assert len(evaluation.suite.references) == 100
    assert not evaluation.minimum_met
    assert set(evaluation.determinate_counts) == set(selection.business_ids)
    assert all(count == 0 for count in evaluation.determinate_counts.values())


def test_source_proof_reader_checks_document_quote_without_line_ending(tmp_path):
    import hashlib

    import duckdb

    from datamate.preparation.models import EvidenceRef

    module = api()
    root = synthetic_split(tmp_path)
    source = "data/a/README.md"
    (root / source).write_text("# Source grain\n", encoding="utf-8")
    sources = module.snapshot_sources(root, policy().business_ids, split_id="train")
    ref = EvidenceRef(
        source=source,
        sha256=sources.files["a"][source],
        locator="md:1-1",
        line_start=1,
        line_end=1,
        quote_sha256=hashlib.sha256(b"# Source grain").hexdigest(),
    )
    with duckdb.connect() as db:
        assert module.SourceProofReader(db, sources, root)(ref)["quote"] == "# Source grain"


def test_structural_disposition_label_alone_cannot_promote_loader_metadata():
    module = api()
    proposal = reference_proposal(kind="structural_sheet_header", behavior="TRANSFORM").model_copy(
        update={"assertion_stage": "disposition", "expected_value": "structural"}
    )
    metadata = {
        "cell": {"type": "str", "value": "id"},
        "record_kind": "header",
        "source_row": 1,
        "header_row": 1,
    }
    assert not module.check_reference_proof(proposal, lambda ref: metadata).verified
    assert not module.check_reference_proof(
        proposal, lambda ref: metadata | {"record_kind": "data"}
    ).verified


def test_native_order_timestamp_requires_exact_native_datetime():
    module = api()
    proposal = reference_proposal(kind="native_order_timestamp", behavior="PRESERVE").model_copy(
        update={"assertion_stage": "parsed_cell", "expected_value": "2025-02-03T09:30:00"}
    )
    assert module.check_reference_proof(
        proposal, lambda ref: {"type": "datetime", "value": "2025-02-03T09:30:00"}
    ).verified
    assert not module.check_reference_proof(
        proposal, lambda ref: {"type": "str", "value": "2025-02-03T09:30:00"}
    ).verified


@pytest.mark.parametrize(
    "collision,catalog_header", [(False, "product_id"), (True, "product_id"), (False, "note")]
)
@pytest.mark.parametrize(
    "authority_text,matched_business,authorized",
    [
        ("Tables join by product code", "a", True),
        ("các file nối với nhau qua mã", "a", True),
        ("Tables do not join by product code", "a", False),
        ("Do not join tables by product code", "a", False),
        ("Tables join by customer code", "a", False),
        ("Other tables join by product code", "a", False),
        ("The example says Tables join by product code", "a", False),
        ("Tables join by product code", "b", False),
    ],
)
def test_trusted_key_relationship_requires_unique_cited_destination(
    tmp_path, collision, catalog_header, authority_text, matched_business, authorized
):
    import duckdb

    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.models import EvidenceRef
    from datamate.preparation.storage import create_storage, stage_table

    module = api()
    root = synthetic_split(tmp_path)
    business = root / "data" / "a"
    (business / "README.md").write_text(authority_text + "\n", encoding="utf-8")
    (business / "sales.csv").write_text("product_id\nP01\n", encoding="utf-8")
    catalog_rows = f"{catalog_header}\nP01\n" + ("P01\n" if collision else "")
    (root / "data" / matched_business / "catalog.csv").write_text(catalog_rows, encoding="utf-8")
    snapshot = module.snapshot_sources(root, policy().business_ids, split_id="train")
    sources = [
        source
        for name in {"a", matched_business}
        for source in discover_business(root, name, split_id="train").sources
        if source.relative_path in {"data/a/sales.csv", f"data/{matched_business}/catalog.csv"}
    ]
    with duckdb.connect() as db:
        create_storage(db)
        staged = {
            source.relative_path: stage_table(db, source, next(load_tables(source.path)))
            for source in sources
        }
        refs = tuple(
            EvidenceRef(
                source=source.relative_path,
                sha256=source.sha256,
                locator=f"raw:{staged[source.relative_path].table_id}:2/c0001",
            )
            for source in sources
            if source.relative_path.endswith(("sales.csv", "catalog.csv"))
        )
        refs = tuple(sorted(refs, key=lambda ref: "sales" not in ref.source))
        matched = refs[1].locator[4:].split("/")[0]
        import hashlib

        authority = EvidenceRef(
            source="data/a/README.md",
            sha256=snapshot.files["a"]["data/a/README.md"],
            locator="md:1-1",
            line_start=1,
            line_end=1,
            quote_sha256=hashlib.sha256(authority_text.encode()).hexdigest(),
        )
        proposal = reference_proposal(
            kind="trusted_key_relationship", behavior="TRANSFORM"
        ).model_copy(
            update={
                "assertion_stage": "relationship",
                "expected_value": {"product_id": "P01", "matched_record_id": matched},
                "evidence": (*refs, authority),
            }
        )
        reader = module.SourceProofReader(db, snapshot, root)
        proof = module.check_reference_proof(proposal, reader)
        assert proof.verified is (authorized and not collision and catalog_header == "product_id")
        no_authority = proposal.model_copy(update={"evidence": refs})
        assert not module.check_reference_proof(no_authority, reader).verified


def test_calendar_event_requires_holiday_and_iso_date_in_same_source_row(tmp_path):
    import duckdb

    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.models import EvidenceRef
    from datamate.preparation.storage import create_storage, stage_table

    module = api()
    root = synthetic_split(tmp_path)
    source_path = root / "data" / "a" / "sales.csv"
    source_path.write_text(
        "date,holiday\n2025-01-01,New Year\n2025-01-02,Other\n", encoding="utf-8"
    )
    snapshot = module.snapshot_sources(root, policy().business_ids, split_id="train")
    source = discover_business(root, "a", split_id="train").sources[0]
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(db, source, next(load_tables(source.path)))
        refs = tuple(
            EvidenceRef(
                source=source.relative_path,
                sha256=source.sha256,
                locator=f"raw:{table.table_id}:{row}/c{column:04d}",
            )
            for row, column in ((2, 2), (2, 1))
        )
        proposal = reference_proposal(
            kind="source_calendar_event", behavior="TRANSFORM"
        ).model_copy(
            update={
                "assertion_stage": "canonical_fact",
                "expected_value": {"date": "2025-01-01", "holiday": "New Year"},
                "evidence": refs,
            }
        )
        reader = module.SourceProofReader(db, snapshot, root)
        assert module.check_reference_proof(proposal, reader).verified
        wrong = proposal.model_copy(
            update={
                "evidence": (
                    refs[0],
                    refs[1].model_copy(update={"locator": f"raw:{table.table_id}:3/c0001"}),
                )
            }
        )
        assert not module.check_reference_proof(wrong, reader).verified


def test_semantic_source_proof_does_not_verify_refusal_behavior():
    module = api()
    proposal = reference_proposal(kind="structural_header", behavior="SOURCE_ABSENCE").model_copy(
        update={"assertion_stage": "disposition", "expected_value": "structural"}
    )
    metadata = {
        "cell": {"type": "str", "value": "id"},
        "record_kind": "header",
        "source_row": 1,
        "header_row": 1,
    }
    assert not module.check_reference_proof(proposal, lambda ref: metadata).verified


def test_reference_suite_rejects_forged_verified_adjudication(tmp_path):
    from normalization_fixtures import reference_world

    from datamate.preparation.models import EvidenceRef

    module = api()
    selection, sources, profiles = reference_world(tmp_path)
    manifest = module.select_cases(selection, sources, profiles)
    case = manifest.cases[0]
    forged = module.AdjudicatedReference(
        case_id=case.case_id,
        assertion_stage="parsed_cell",
        revision=1,
        expected_behavior="TRANSFORM",
        expected_value="invented",
        evidence_status="VERIFIED",
        proposal_digest="a" * 64,
        checker_version="forged",
        reason="reviewer claim",
        evidence=(
            EvidenceRef(
                source=case.source,
                sha256=case.source_sha256,
                locator=f"raw:{case.record_id}/{case.column_id}",
            ),
        ),
    )
    with pytest.raises(ValueError, match="checker"):
        module.freeze_reference_suite(manifest, [forged])


def test_demonstrated_scale_ambiguity_verifies_two_distinct_parses_without_counting_determinate():
    module = api()
    proposal = reference_proposal(
        kind="demonstrated_ambiguity", behavior="PROCESSING_AMBIGUITY"
    ).model_copy(
        update={
            "assertion_stage": "parsed_cell",
            "expected_value": {"interpretations": ["1", "1000"]},
        }
    )
    proof = module.check_reference_proof(proposal, lambda ref: {"type": "str", "value": "1.000"})
    assert proof.verified
    assert not module.adjudicate_reference(proposal, proof).determinate
    wrong = proposal.model_copy(update={"expected_value": {"interpretations": ["1", "2000"]}})
    assert not module.check_reference_proof(
        wrong, lambda ref: {"type": "str", "value": "1.000"}
    ).verified


def test_declared_source_absence_requires_document_and_header_inventory(tmp_path):
    import hashlib

    import duckdb

    from datamate.preparation.discovery import discover_business
    from datamate.preparation.loaders import load_tables
    from datamate.preparation.models import EvidenceRef
    from datamate.preparation.storage import create_storage, stage_table

    module = api()
    root = synthetic_split(tmp_path)
    doc = root / "data" / "a" / "README.md"
    doc.write_text("No discount is recorded\n", encoding="utf-8")
    snapshot = module.snapshot_sources(root, policy().business_ids, split_id="train")
    source = discover_business(root, "a", split_id="train").sources[0]
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(db, source, next(load_tables(source.path)))
        raw = EvidenceRef(
            source=source.relative_path,
            sha256=source.sha256,
            locator=f"raw:{table.table_id}:2/c0001",
        )
        authority = EvidenceRef(
            source="data/a/README.md",
            sha256=snapshot.files["a"]["data/a/README.md"],
            locator="md:1-1",
            line_start=1,
            line_end=1,
            quote_sha256=hashlib.sha256(b"No discount is recorded").hexdigest(),
        )
        proposal = reference_proposal(
            kind="declared_source_absence", behavior="SOURCE_ABSENCE"
        ).model_copy(
            update={
                "assertion_stage": "capability",
                "expected_value": {"field": "discount"},
                "evidence": (raw, authority),
            }
        )
        reader = module.SourceProofReader(db, snapshot, root)
        assert module.check_reference_proof(proposal, reader).verified
        assert not module.check_reference_proof(
            proposal.model_copy(update={"evidence": (raw,)}), reader
        ).verified
        db.execute(
            "UPDATE meta.source_tables SET headers_json=? WHERE table_id=?",
            [
                '[{"type":"str","value":"discount"},{"type":"str","value":"amount"}]',
                table.table_id,
            ],
        )
        assert not module.check_reference_proof(proposal, reader).verified


def test_excel_header_metadata_alone_is_not_independent_disposition_proof():
    from datamate.preparation.models import EvidenceRef

    module = api()
    proposal = reference_proposal(kind="structural_header").model_copy(
        update={
            "assertion_stage": "disposition",
            "expected_value": {"kind": "header", "include_as_data": False},
            "evidence": (
                EvidenceRef(source="data/a/sheet.xlsx", sha256="a" * 64, locator="raw:t:1/c0001"),
            ),
        }
    )
    metadata = {
        "cell": {"type": "str", "value": "id"},
        "record_kind": "header",
        "source_row": 1,
        "header_row": 1,
    }
    assert not module.check_reference_proof(proposal, lambda ref: metadata).verified


def test_neighbor_day_first_witness_does_not_fix_ambiguous_target():
    from datamate.preparation.models import EvidenceRef

    module = api()
    refs = (
        EvidenceRef(source="data/a/sheet.xlsx", sha256="a" * 64, locator="raw:t:1/c0001"),
        EvidenceRef(source="data/a/sheet.xlsx", sha256="a" * 64, locator="raw:t:2/c0001"),
    )
    proposal = reference_proposal(kind="day_first_date_format", behavior="TRANSFORM").model_copy(
        update={
            "assertion_stage": "parsed_cell",
            "expected_value": {"date": "2025-06-03"},
            "evidence": refs,
        }
    )
    values = {
        refs[0].locator: {"type": "str", "value": "03/06/2025"},
        refs[1].locator: {"type": "str", "value": "13/06/2025"},
    }
    assert not module.check_reference_proof(proposal, lambda ref: values[ref.locator]).verified


def test_native_arithmetic_across_unrelated_rows_is_not_role_proof():
    from datamate.preparation.models import EvidenceRef

    module = api()
    refs = tuple(
        EvidenceRef(source="data/a/sales.csv", sha256="a" * 64, locator=f"raw:t:{row}/c0001")
        for row in (1, 2, 3)
    )
    proposal = reference_proposal(kind="explicit_sale_arithmetic", behavior="TRANSFORM").model_copy(
        update={"assertion_stage": "parsed_cell", "expected_value": 2000, "evidence": refs}
    )
    values = {
        ref.locator: {"type": "int", "value": number}
        for ref, number in zip(refs, (2000, 2, 1000), strict=True)
    }
    assert not module.check_reference_proof(proposal, lambda ref: values[ref.locator]).verified


def test_declared_day_first_format_resolves_ambiguous_target():
    from datamate.preparation.models import EvidenceRef

    module = api()
    raw = EvidenceRef(source="data/a/sheet.xlsx", sha256="a" * 64, locator="raw:t:1/c0001")
    import hashlib

    authority = EvidenceRef(
        source="data/a/README.md",
        sha256="b" * 64,
        locator="md:1-1",
        line_start=1,
        line_end=1,
        quote_sha256=hashlib.sha256(b"Dates are dd/mm/yyyy").hexdigest(),
    )
    proposal = reference_proposal(kind="day_first_date_format", behavior="TRANSFORM").model_copy(
        update={
            "assertion_stage": "parsed_cell",
            "expected_value": {"date": "2025-06-03"},
            "evidence": (raw, authority),
        }
    )

    def reader(ref):
        if ref == raw:
            return {"type": "str", "value": "03/06/2025"}
        return {"quote": "Dates are dd/mm/yyyy"}

    assert module.check_reference_proof(proposal, reader).verified
    assert not module.check_reference_proof(
        proposal,
        lambda ref: (
            {"type": "str", "value": "03/06/2025"}
            if ref == raw
            else {"quote": "Dates are mm/dd/yyyy"}
        ),
    ).verified


@pytest.mark.parametrize(
    "kind,raw_type,raw_value,expected",
    [
        ("iso_calendar_date", "str", "2025-01-31", {"date": "2025-01-31"}),
        ("iso_calendar_date", "str", "2025-01-31", {"type": "date", "value": "2025-01-31"}),
        ("iso_date_native_cell", "date", "2025-01-31", {"date": "2025-01-31"}),
        ("iso_date_native_cell", "date", "2025-01-31", {"type": "date", "value": "2025-01-31"}),
        ("day_first_date_format", "str", "31/01/2025", {"date": "2025-01-31"}),
        ("day_first_date_format", "str", "31/01/2025", {"type": "date", "value": "2025-01-31"}),
        ("typed_calendar_integer", "str", "2025", {"type": "int", "value": 2025}),
    ],
)
def test_normalized_scalar_proof_rejects_extra_expected_assertions(
    kind, raw_type, raw_value, expected
):
    module = api()
    proposal = reference_proposal(kind=kind, behavior="TRANSFORM").model_copy(
        update={"assertion_stage": "parsed_cell", "expected_value": expected}
    )

    def reader(ref):
        return {"type": raw_type, "value": raw_value}

    assert module.check_reference_proof(proposal, reader).verified
    extra = proposal.model_copy(update={"expected_value": expected | {"unproven": "invented"}})
    assert not module.check_reference_proof(extra, reader).verified


@pytest.mark.parametrize(
    "native,expected",
    [
        ("2025-02-03T09:30:00.1234567", "2025-02-03T09:30:00.1234568"),
        ("2025-02-03T09:30:00.1234567", "2025-02-03T09:30:00.123456"),
        ("2025-02-03T09:30:00.123456", "2025-02-03T09:30:00.1234567"),
        ("2025-02-03T09:30:00,1234567", "2025-02-03T09:30:00,1234568"),
    ],
)
def test_native_timestamp_proof_rejects_truncated_fractional_precision(native, expected):
    module = api()
    proposal = reference_proposal(kind="native_order_timestamp", behavior="PRESERVE").model_copy(
        update={"assertion_stage": "parsed_cell", "expected_value": expected}
    )
    assert not module.check_reference_proof(
        proposal, lambda ref: {"type": "datetime", "value": native}
    ).verified


@pytest.mark.parametrize(
    "declaration,authorized",
    [
        ("Dates are dd/mm/yyyy", True),
        ("date_format: dd/mm/yyyy", True),
        ("date_format = dd/mm/yyyy", True),
        ("Dates are not dd/mm/yyyy", False),
        ("Do not use dd/mm/yyyy", False),
        ("Customer birthdays are dd/mm/yyyy", False),
        ("An example of dd/mm/yyyy", False),
        ("date_format: dd/mm/yyyy is not used", False),
        ("Other files use dd/mm/yyyy", False),
    ],
)
def test_ambiguous_day_first_requires_positive_global_declaration(declaration, authorized):
    import hashlib

    from datamate.preparation.models import EvidenceRef

    module = api()
    raw = EvidenceRef(source="data/a/sales.csv", sha256="a" * 64, locator="raw:t:1/c0001")
    authority = EvidenceRef(
        source="data/a/README.md",
        sha256="b" * 64,
        locator="md:1-1",
        line_start=1,
        line_end=1,
        quote_sha256=hashlib.sha256(declaration.encode()).hexdigest(),
    )
    proposal = reference_proposal(kind="day_first_date_format", behavior="TRANSFORM").model_copy(
        update={
            "assertion_stage": "parsed_cell",
            "expected_value": {"date": "2025-06-03"},
            "evidence": (raw, authority),
        }
    )

    def reader(ref):
        if ref == raw:
            return {"type": "str", "value": "03/06/2025"}
        return {"quote": declaration}

    assert module.check_reference_proof(proposal, reader).verified is authorized
