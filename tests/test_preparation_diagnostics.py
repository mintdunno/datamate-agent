"""Source-only diagnostics against real, deliberately incomplete DuckDB stores."""

import hashlib
import json
import socket

import duckdb
import pytest


def diagnose(db, resources=None):
    from datamate.preparation.diagnostics import diagnose_preparation

    return diagnose_preparation(db, resources={} if resources is None else resources)


def snapshot(db):
    rows = (
        db.execute("SELECT * FROM meta.audit_receipt ORDER BY report_digest").fetchall(),
        db.execute("SELECT * FROM meta.qualification ORDER BY scope").fetchall(),
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def test_missing_evidence_stays_unknown_instead_of_zero():
    with duckdb.connect() as db:
        result = diagnose(db)
    assert result["raw_records"] is None
    assert result["data_records"] is None
    assert result["dispositions"] is None
    assert result["bindings"] is None
    assert result["transform_events"] is None
    assert result["qualification"] is None
    assert result["audits"] is None
    assert result["declared_coverage"] == {"raw_records": None, "data_records": None}
    assert result["resources"] == {
        "elapsed_seconds": None,
        "peak_rss_bytes": None,
        "peak_disk_bytes": None,
        "measurements_complete": None,
    }
    assert result["unsupported_families"] is None
    assert result["parse_ambiguity_units"] is None


def test_diagnostic_preserves_denominators_and_persisted_qualification(monkeypatch, tmp_path):
    forbidden = tmp_path / "answers.jsonl"
    forbidden.write_text("private label must never be opened", encoding="utf-8")

    def deny(*args, **kwargs):
        pytest.fail("diagnostics accessed HTTP or a forbidden label reader")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(type(forbidden), "open", deny)
    with duckdb.connect() as db:
        db.execute("CREATE SCHEMA staging; CREATE SCHEMA meta")
        db.execute("CREATE TABLE staging.raw_rows(record_id VARCHAR,kind VARCHAR)")
        db.execute(
            "INSERT INTO staging.raw_rows VALUES ('r0','header'),('r1','data'),('r2','data')"
        )
        db.execute("CREATE TABLE meta.dispositions(record_id VARCHAR,kind VARCHAR)")
        db.execute(
            "INSERT INTO meta.dispositions VALUES ('r0','structural'),('r1',"
            "'retained'),('r2','quarantined')"
        )
        db.execute(
            "CREATE TABLE meta.bindings(binding_id VARCHAR,status VARCHAR,"
            "reason VARCHAR,binding_json JSON)"
        )
        db.execute(
            "INSERT INTO meta.bindings VALUES ('b1','accepted','secret','{}'),"
            "('b2','unresolved','secret','{}'),('b3','conflict','secret','{}')"
        )
        db.execute(
            "CREATE TABLE meta.transform_events(record_id VARCHAR,"
            "column_id VARCHAR,step_id VARCHAR,status VARCHAR,"
            "before_json JSON,after_json JSON,reason VARCHAR)"
        )
        db.execute(
            "INSERT INTO meta.transform_events VALUES ('r2','c1','s1','ambiguous',"
            "'{}','{}','secret')"
        )
        db.execute(
            "CREATE TABLE meta.qualification(scope VARCHAR,status VARCHAR,report_digest VARCHAR)"
        )
        db.execute("INSERT INTO meta.qualification VALUES ('sales','UNRESOLVED','audit1')")
        db.execute("CREATE TABLE meta.audit_receipt(report_digest VARCHAR,findings_json JSON)")
        findings = [
            {"check_id": "coverage", "status": "UNRESOLVED", "detail": "secret"},
            {"check_id": "scale", "status": "FAIL", "detail": "secret"},
            {"check_id": "lineage", "status": "PASS", "detail": "secret"},
        ]
        db.execute("INSERT INTO meta.audit_receipt VALUES (?,?)", ["audit1", json.dumps(findings)])
        before = snapshot(db)
        result = diagnose(
            db,
            {
                "elapsed_seconds": 0.5,
                "peak_disk_bytes": 0,
                "measurements_complete": False,
                "label_reader": deny,
                "label_path": forbidden,
            },
        )
        assert snapshot(db) == before
    assert result["raw_records"] == 3
    assert result["data_records"] == 2
    assert result["declared_coverage"] == {"raw_records": 3, "data_records": 2}
    assert result["dispositions"]["total"] == 3
    assert result["dispositions"]["counts"] == {"quarantined": 1, "retained": 1, "structural": 1}
    assert result["bindings"]["counts"] == {"accepted": 1, "conflict": 1, "unresolved": 1}
    assert result["transform_events"]["counts"] == {"ambiguous": 1}
    assert result["qualification"]["scopes"] == [{"scope": "sales", "status": "UNRESOLVED"}]
    assert result["audits"]["counts"] == {"FAIL": 1, "PASS": 1, "UNRESOLVED": 1}
    assert result["audits"]["failing_check_ids"] == ["scale"]
    assert result["parse_ambiguity_units"] is None
    assert result["unsupported_families"] is None
    assert result["resources"]["peak_rss_bytes"] is None
    assert result["resources"]["peak_disk_bytes"] == 0
    assert result["checkpoint_claims"] is result["http_attempts"] is None
    assert "secret" not in json.dumps(result)


def test_partial_columns_and_views_do_not_become_evidence():
    with duckdb.connect() as db:
        db.execute("CREATE SCHEMA staging; CREATE SCHEMA meta")
        db.execute("CREATE TABLE staging.raw_rows(record_id VARCHAR)")
        db.execute("INSERT INTO staging.raw_rows VALUES ('r1')")
        db.execute("CREATE TABLE meta.bindings(binding_id VARCHAR)")
        db.execute("CREATE VIEW meta.dispositions AS SELECT error('must not execute') AS kind")
        result = diagnose(db)
    assert result["raw_records"] == 1
    assert result["data_records"] is None
    assert result["bindings"] is None
    assert result["dispositions"] is None


def test_empty_persisted_tables_are_known_zero_and_resources_are_allowlisted():
    with duckdb.connect() as db:
        db.execute("CREATE SCHEMA meta")
        db.execute("CREATE TABLE meta.dispositions(kind VARCHAR)")
        result = diagnose(
            db,
            {
                "elapsed_seconds": float("nan"),
                "peak_rss_bytes": -1,
                "peak_disk_bytes": True,
                "secret": "excluded",
            },
        )
    assert result["dispositions"]["total"] == 0
    assert result["dispositions"]["counts"] == {}
    assert all(value is None for value in result["resources"].values())


def test_large_scope_and_failure_metadata_is_bounded_without_changing_totals():
    with duckdb.connect() as db:
        db.execute("CREATE SCHEMA meta")
        db.execute(
            "CREATE TABLE meta.qualification AS SELECT 'scope'||i AS scope,"
            "'FAIL' AS status FROM range(150) t(i)"
        )
        db.execute("CREATE TABLE meta.audit_receipt(findings_json JSON)")
        findings = [{"check_id": "check" + str(i), "status": "FAIL"} for i in range(150)]
        db.execute("INSERT INTO meta.audit_receipt VALUES (?)", [json.dumps(findings)])
        result = diagnose(db)
    assert result["qualification"]["total"] == 150
    assert len(result["qualification"]["scopes"]) == 100
    assert result["qualification"]["truncated"] is True
    assert result["audits"]["counts"] == {"FAIL": 150}
    assert len(result["audits"]["failing_check_ids"]) == 100
    assert result["audits"]["failing_checks_truncated"] is True


def test_diagnostic_runs_on_read_only_connection(tmp_path):
    path = tmp_path / "prepared.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE SCHEMA staging")
        db.execute("CREATE TABLE staging.raw_rows(kind VARCHAR)")
        db.execute("INSERT INTO staging.raw_rows VALUES ('data')")
    with duckdb.connect(str(path), read_only=True) as db:
        result = diagnose(db)
    assert result["declared_coverage"] == {"raw_records": 1, "data_records": 1}
