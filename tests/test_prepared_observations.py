"""Regression coverage for evaluator-only artifact observations and reference migration."""

import importlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from datamate.evaluation.normalization import (
    CaseManifest,
    NormalizationCase,
    ReferenceProposal,
    evaluate_reference_proposals,
)
from datamate.evaluation.normalization_campaign import score_normalization
from datamate.preparation.models import (
    CandidateFact,
    EvidenceRef,
    LineageEdge,
    RawDisposition,
    SourceFile,
    SourceRow,
    SourceTable,
)
from datamate.preparation.storage import (
    create_storage,
    stage_table,
    store_disposition,
    store_fact,
    store_lineage,
)


def api():
    return importlib.import_module("datamate.evaluation.prepared_observations")


def world(tmp_path, raw="2025-01-02", *, header="date", source="data/a/sales.csv"):
    path = tmp_path / "prepared.duckdb"
    with duckdb.connect(str(path)) as db:
        create_storage(db)
        staged = stage_table(
            db,
            SourceFile(Path(source), source, "a" * 64),
            SourceTable(
                "Sheet1",
                (header,) if isinstance(header, str) else header,
                1,
                [
                    SourceRow(i + 2, (raw,) if not isinstance(raw, tuple) else raw)
                    for i in range(100)
                ],
            ),
        )
    cases = tuple(
        NormalizationCase(
            case_id=f"{i:064x}",
            business_id="a",
            partition="development",
            record_id=f"{staged.table_id}:{i + 2}",
            column_id="c0001",
            source=source,
            source_sha256="a" * 64,
            table_id=staged.table_id,
            stratum="temporal",
        )
        for i in range(100)
    )
    manifest = CaseManifest(
        source_policy_digest="b" * 64,
        profile_digests=(),
        selector_version="synthetic",
        stratum_targets={},
        missing_strata={},
        cases=cases,
    )
    return path, manifest


def proposal_for(case, expected="2025-01-02", *, kind="iso_calendar_date", stage="parsed_cell"):
    return ReferenceProposal(
        case_id=case.case_id,
        revision=1,
        assertion_stage=stage,
        expected_behavior="PRESERVE" if stage == "raw" else "TRANSFORM",
        expected_value=expected,
        proof_kind=kind,
        evidence=(
            EvidenceRef(
                source=case.source,
                sha256=case.source_sha256,
                locator=f"raw:{case.record_id}/{case.column_id}",
            ),
        ),
    )


def reference(manifest, proposal, observed):
    def reader(evidence):
        return {"cell": observed}

    suite = evaluate_reference_proposals(manifest, [proposal], {"a": reader}).suite
    return suite, reader


def accepted(path, case, value, *, kind="retained", lineage=True):
    with duckdb.connect(str(path)) as db:
        store_disposition(db, RawDisposition(case.record_id, kind, "synthetic", ()))
        store_fact(
            db, CandidateFact("row:" + case.record_id, "physical_row", {case.column_id: value})
        )
        if lineage:
            store_lineage(
                db,
                LineageEdge(
                    "row:" + case.record_id, case.record_id, case.column_id, "synthetic-step"
                ),
            )


@pytest.mark.parametrize(
    "raw,expected,kind,actual,want",
    [
        (
            "2025-01-02",
            "2025-01-02",
            "iso_calendar_date",
            date(2025, 1, 2),
            {"type": "date", "value": "2025-01-02"},
        ),
        (
            "2025-01-02",
            {"date": "2025-01-02"},
            "iso_calendar_date",
            date(2025, 1, 2),
            {"type": "date", "value": "2025-01-02"},
        ),
        ("2025", 2025, "calendar_integer_field", 2025, {"type": "int", "value": "2025"}),
        (
            datetime(2025, 1, 2, 3, 4, 5),
            "2025-01-02 03:04:05",
            "native_order_timestamp",
            datetime(2025, 1, 2, 3, 4, 5),
            {"type": "datetime", "value": "2025-01-02T03:04:05"},
        ),
        (
            date(2025, 1, 2),
            "2025-01-02",
            "iso_date_native_cell",
            date(2025, 1, 2),
            {"type": "date", "value": "2025-01-02"},
        ),
        (
            "31/01/2025",
            "2025-01-31",
            "day_first_date_format",
            date(2025, 1, 31),
            {"type": "date", "value": "2025-01-31"},
        ),
    ],
)
def test_source_proof_migration_and_real_artifact_types_match(
    tmp_path, raw, expected, kind, actual, want
):
    path, manifest = world(tmp_path, raw)
    case = manifest.cases[0]
    proposal = proposal_for(case, expected, kind=kind)
    source_value = {
        "type": type(raw).__name__,
        "value": raw.isoformat() if hasattr(raw, "isoformat") else raw,
    }
    suite, reader = reference(manifest, proposal, source_value)
    assert suite.references[0].evidence_status == "VERIFIED"
    accepted(path, case, actual)
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].value == want
    assert migrated.references[0].expected_value == want
    assert score_normalization(result, migrated).verified_determinate_successes == 1
    assert migrated.references[0].revision == 2
    assert migrated.references[0].evidence == suite.references[0].evidence
    assert tuple(r.case_id for r in migrated.references) == tuple(
        r.case_id for r in suite.references
    )
    assert migrated.manifest_digest == suite.manifest_digest


def test_legacy_serialization_and_digest_are_byte_stable_and_require_migration(tmp_path):
    path, manifest = world(tmp_path)
    proposal = proposal_for(manifest.cases[0])
    suite, _ = reference(manifest, proposal, {"type": "str", "value": "2025-01-02"})
    before = suite.model_dump_json()
    digest = suite.digest
    assert "contract_version" not in before
    assert "observation_target" not in before
    restored = type(suite).model_validate_json(before)
    assert restored.model_dump_json() == before
    assert restored.digest == digest
    missing = tmp_path / "must-not-be-created.duckdb"
    with pytest.raises(ValueError, match="contract"):
        api().evaluate_prepared_cases(missing, [manifest.cases[0]], suite)
    assert not missing.exists()


@pytest.mark.parametrize(
    "mutation", ["wrong_type", "no_lineage", "no_disposition", "multiple", "unresolved"]
)
def test_missing_or_ambiguous_artifact_support_abstains_without_value_guessing(tmp_path, mutation):
    path, manifest = world(tmp_path)
    case = manifest.cases[0]
    proposal = proposal_for(case)
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "2025-01-02"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(
        path,
        case,
        "2025-01-02" if mutation == "wrong_type" else date(2025, 1, 2),
        lineage=mutation != "no_lineage",
    )
    with duckdb.connect(str(path)) as db:
        if mutation == "no_disposition":
            db.execute("DELETE FROM meta.dispositions")
        if mutation == "multiple":
            store_fact(db, CandidateFact("other", "physical_row", {"c0001": date(2025, 1, 3)}))
            store_lineage(db, LineageEdge("other", case.record_id, case.column_id, "step"))
        if mutation == "unresolved":
            db.execute(
                "CREATE TABLE meta.transform_events("
                "record_id VARCHAR,column_id VARCHAR,status VARCHAR)"
            )
            db.execute(
                "INSERT INTO meta.transform_events VALUES (?,?,'UNRESOLVED')",
                [case.record_id, case.column_id],
            )
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].status == "ABSTAINED"
    assert result.case_results[0].value is None
    assert score_normalization(result, migrated).verified_determinate_denominator == 1


def test_quarantine_is_observed_and_remains_in_determinate_denominator(tmp_path):
    path, manifest = world(tmp_path)
    case = manifest.cases[0]
    proposal = proposal_for(case)
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "2025-01-02"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(path, case, date(2025, 1, 2), kind="quarantined")
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].status == "QUARANTINED"
    assert score_normalization(result, migrated).verified_determinate_denominator == 1


@pytest.mark.parametrize("raw", ["00120", Decimal("1.2300")])
def test_raw_observation_preserves_identifier_and_decimal_scale_without_positive_coverage(
    tmp_path, raw
):
    path, manifest = world(tmp_path, raw)
    case = manifest.cases[0]
    encoded = {"type": type(raw).__name__, "value": str(raw)}
    proposal = proposal_for(case, encoded, kind="exact_observation", stage="raw")
    suite, reader = reference(manifest, proposal, encoded)
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].value == encoded
    assert score_normalization(result, migrated).verified_determinate_denominator == 0


def test_migration_rechecks_original_proof_and_rejects_substituted_proposal(tmp_path):
    _, manifest = world(tmp_path)
    case = manifest.cases[0]
    proposal = proposal_for(case)
    suite, _ = reference(manifest, proposal, {"type": "str", "value": "2025-01-02"})
    migrated = api().migrate_reference_suite(
        suite, manifest, [proposal], {"a": lambda evidence: {"type": "str", "value": "wrong"}}
    )
    assert migrated.references[0].evidence_status == "UNRESOLVED"
    changed = proposal.model_copy(update={"expected_value": "2025-02-02"})
    with pytest.raises(ValueError, match="original"):
        api().migrate_reference_suite(suite, manifest, [changed], {"a": lambda evidence: None})


def test_calendar_integer_uses_qualified_rows_and_known_physical_header(tmp_path):
    path, manifest = world(tmp_path, "2025", header="lunar_year", source="calendar.csv")
    case = manifest.cases[0]
    proposal = proposal_for(case, 2025, kind="calendar_integer_field")
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "2025"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    with duckdb.connect(str(path)) as db:
        db.execute(
            "CREATE TABLE clean.calendar_days("
            "date DATE,lunar_year INTEGER,holiday VARCHAR,record_id VARCHAR)"
        )
        db.execute(
            "INSERT INTO clean.calendar_days VALUES ('2025-01-02',2025,'Synthetic',?)",
            [case.record_id],
        )
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].value == {"type": "int", "value": "2025"}
    assert score_normalization(result, migrated).verified_determinate_successes == 1
    with duckdb.connect(str(path)) as db:
        db.execute(
            "UPDATE meta.source_tables SET headers_json=?",
            [json.dumps([{"type": "str", "value": "unknown"}])],
        )
    assert (
        api().evaluate_prepared_cases(path, [case], migrated).case_results[0].status == "ABSTAINED"
    )


def test_wrong_but_well_typed_artifact_value_is_committed_and_scored_wrong(tmp_path):
    path, manifest = world(tmp_path)
    case = manifest.cases[0]
    proposal = proposal_for(case)
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "2025-01-02"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(path, case, date(2025, 2, 3))
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert result.case_results[0].status == "COMMITTED"
    assert result.case_results[0].value == {"type": "date", "value": "2025-02-03"}
    metrics = score_normalization(result, migrated)
    assert metrics.verified_determinate_commitments == 1
    assert metrics.verified_determinate_successes == 0


def test_verified_negative_keeps_denominator_and_cannot_become_positive(tmp_path):
    path, manifest = world(tmp_path, "1.000")
    case = manifest.cases[0]
    proposal = proposal_for(case, {"interpretations": ["1", "1000"]}, kind="demonstrated_ambiguity")
    proposal = proposal.model_copy(update={"expected_behavior": "PROCESSING_AMBIGUITY"})
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "1.000"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(path, case, Decimal("1.000"))
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert migrated.references[0].evidence_status == "VERIFIED"
    assert result.case_results[0].status == "ABSTAINED"
    metrics = score_normalization(result, migrated)
    assert metrics.verified_negative_denominator == 1
    assert metrics.unsupported_negative_commitments == 0
    assert metrics.verified_determinate_denominator == 0


@pytest.mark.parametrize("mutation", [None, "missing", "duplicate", "wrong_date_type"])
def test_canonical_calendar_event_reads_fixed_typed_fields(tmp_path, mutation):
    path, manifest = world(
        tmp_path, ("Synthetic", "2025-01-02"), header=("holiday", "date"), source="calendar.csv"
    )
    case = manifest.cases[0]
    proposal = proposal_for(
        case,
        {"date": "2025-01-02", "holiday": "Synthetic"},
        kind="source_calendar_event",
        stage="canonical_fact",
    )
    date_ref = proposal.evidence[0].model_copy(update={"locator": f"raw:{case.record_id}/c0002"})
    proposal = proposal.model_copy(update={"evidence": (*proposal.evidence, date_ref)})

    def reader(evidence):
        return {"type": "str", "value": "2025-01-02" if evidence == date_ref else "Synthetic"}

    suite = evaluate_reference_proposals(manifest, [proposal], {"a": reader}).suite
    assert suite.references[0].evidence_status == "VERIFIED"
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    if mutation != "missing":
        with duckdb.connect(str(path)) as db:
            sql_type = "VARCHAR" if mutation == "wrong_date_type" else "DATE"
            db.execute(
                f"CREATE TABLE clean.calendar_days(date {sql_type},"
                "holiday VARCHAR,record_id VARCHAR)"
            )
            db.execute(
                "INSERT INTO clean.calendar_days VALUES ('2025-01-02','Synthetic',?)",
                [case.record_id],
            )
            if mutation == "duplicate":
                db.execute("INSERT INTO clean.calendar_days SELECT * FROM clean.calendar_days")
    result = api().evaluate_prepared_cases(path, [case], migrated)
    if mutation is None:
        assert result.case_results[0].value == {
            "date": {"type": "date", "value": "2025-01-02"},
            "holiday": {"type": "str", "value": "Synthetic"},
        }
        assert score_normalization(result, migrated).verified_determinate_successes == 1
    else:
        assert result.case_results[0].status == "ABSTAINED"
        assert score_normalization(result, migrated).verified_determinate_denominator == 1


def test_native_canonical_date_keeps_original_stage_and_supported_scalar_target(tmp_path):
    path, manifest = world(tmp_path, date(2025, 1, 2))
    case = manifest.cases[0]
    proposal = proposal_for(case, "2025-01-02", kind="iso_date_native_cell", stage="canonical_fact")
    suite, reader = reference(manifest, proposal, {"type": "date", "value": "2025-01-02"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(path, case, date(2025, 1, 2))
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert migrated.references[0].assertion_stage == "canonical_fact"
    assert score_normalization(result, migrated).verified_determinate_successes == 1


def test_malformed_artifact_shapes_abstain_instead_of_crashing(tmp_path):
    path, manifest = world(tmp_path, "2025", header="lunar_year", source="calendar.csv")
    case = manifest.cases[0]
    proposal = proposal_for(case, 2025, kind="calendar_integer_field")
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "2025"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE clean.calendar_days(lunar_year INTEGER,record_id VARCHAR)")
        db.execute("INSERT INTO clean.calendar_days VALUES (2025,?)", [case.record_id])
        db.execute("UPDATE meta.source_tables SET headers_json='null'")
    assert (
        api().evaluate_prepared_cases(path, [case], migrated).case_results[0].status == "ABSTAINED"
    )


def test_verified_relationship_truth_survives_missing_candidate_artifact(tmp_path):
    import hashlib

    from datamate.evaluation.normalization import SourceProofReader, SourceSnapshot

    path, manifest = world(tmp_path, "P01", header="product_id")
    root = tmp_path / "source"
    folder = root / "data" / "a"
    folder.mkdir(parents=True)
    contents = {
        "rulebook.md": "Synthetic rules\n",
        "calendar.csv": "date\n2025-01-02\n",
        "data/a/sales.csv": "product_id\n" + "P01\n" * 100,
        "data/a/catalog.csv": "product_id\nP01\n",
        "data/a/README.md": "Tables join by product code\n",
    }
    hashes = {}
    for name, content in contents.items():
        (root / name).write_bytes(content.encode())
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    cases = tuple(
        case.model_copy(update={"source_sha256": hashes[case.source]}) for case in manifest.cases
    )
    manifest = manifest.model_copy(update={"cases": cases})
    case = cases[0]
    with duckdb.connect(str(path)) as db:
        db.execute("UPDATE meta.source_tables SET source_sha256=?", [case.source_sha256])
        staged = stage_table(
            db,
            SourceFile(folder / "catalog.csv", "data/a/catalog.csv", hashes["data/a/catalog.csv"]),
            SourceTable("Sheet1", ("product_id",), 1, [SourceRow(2, ("P01",))]),
        )
        matched = f"{staged.table_id}:2"
        proposal = proposal_for(
            case,
            {"product_id": "P01", "matched_record_id": matched},
            kind="trusted_key_relationship",
            stage="relationship",
        )
        proposal = proposal.model_copy(
            update={
                "evidence": (
                    *proposal.evidence,
                    EvidenceRef(
                        source="data/a/catalog.csv",
                        sha256=hashes["data/a/catalog.csv"],
                        locator=f"raw:{matched}/c0001",
                    ),
                    EvidenceRef(
                        source="data/a/README.md",
                        sha256=hashes["data/a/README.md"],
                        locator="md:1-1",
                        line_start=1,
                        line_end=1,
                        quote_sha256=hashlib.sha256(b"Tables join by product code").hexdigest(),
                    ),
                )
            }
        )
        reader = SourceProofReader(db, SourceSnapshot(split_id="train", files={"a": hashes}), root)
        suite = evaluate_reference_proposals(manifest, [proposal], {"a": reader}).suite
        assert suite.references[0].evidence_status == "VERIFIED"
        migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    accepted(path, case, "P01")
    result = api().evaluate_prepared_cases(path, [case], migrated)
    assert migrated.references[0].evidence_status == "VERIFIED"
    assert migrated.references[0].observation_target.scope == "relationship"
    assert result.case_results[0].status == "ABSTAINED"
    assert score_normalization(result, migrated).verified_determinate_denominator == 1


def test_raw_target_abstains_for_invalid_tagged_payload(tmp_path):
    path, manifest = world(tmp_path, "00120")
    case = manifest.cases[0]
    proposal = proposal_for(
        case, {"type": "str", "value": "00120"}, kind="exact_observation", stage="raw"
    )
    suite, reader = reference(manifest, proposal, {"type": "str", "value": "00120"})
    migrated = api().migrate_reference_suite(suite, manifest, [proposal], {"a": reader})
    with duckdb.connect(str(path)) as db:
        db.execute(
            "UPDATE staging.raw_rows SET values_json=? WHERE record_id=?",
            [json.dumps([{"type": "str", "value": {"unexpected": "object"}}]), case.record_id],
        )
    assert (
        api().evaluate_prepared_cases(path, [case], migrated).case_results[0].status == "ABSTAINED"
    )
