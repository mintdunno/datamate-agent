"""Audit evidence is independent of parser outcomes and scoped by applicability."""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from test_canonical_projection import prepared_world

from datamate.preparation.audit import audit_prepared, qualify
from datamate.preparation.models import (
    Binding,
    CandidateFact,
    EvidenceRef,
    LineageEdge,
    RawDisposition,
    ResolvedBinding,
    SemanticBindings,
    SourceFile,
    SourceRow,
    SourceTable,
)
from datamate.preparation.normalization import encoded
from datamate.preparation.projection import project_facts, publish_projections
from datamate.preparation.storage import (
    iter_raw_batches,
    stage_table,
    store_disposition,
    store_fact,
    store_lineage,
)


def _world(db, rows=(("A", Decimal("200.00")),)):
    plan, bindings = prepared_world(
        db,
        ("order_id", "order_total"),
        rows,
        grains=("order_header",),
    )
    project_facts(db, plan, bindings)
    return bindings


def _finding(report, check_id, scope):
    matches = [
        finding
        for finding in report.findings
        if finding.check_id == check_id and finding.scope == scope
    ]
    assert len(matches) == 1
    return matches[0]


def test_exhaustive_dispositions_and_fact_counts_are_separate():
    with duckdb.connect() as db:
        bindings = _world(db)
        report = audit_prepared(db, bindings)
        assert _finding(report, "raw_accounting", "*").status == "PASS"
        assert _finding(report, "field_lineage", "order_headers").status == "PASS"
        qualification = qualify(report, {})
        assert qualification.status("order_headers") == "READY"
        published = publish_projections(db, qualification)
        assert [(item.scope, item.row_count) for item in published] == [("order_headers", 1)]
        assert db.execute("SELECT order_total FROM clean.order_headers").fetchone() == (
            Decimal("200.00"),
        )
        assert db.execute("SELECT count(*) FROM staging.raw_rows").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM meta.dispositions").fetchone()[0] == 2


def test_missing_disposition_blocks_all_scopes_and_forged_ready_is_rejected():
    with duckdb.connect() as db:
        bindings = _world(db)
        db.execute(
            "DELETE FROM meta.dispositions WHERE record_id IN "
            "(SELECT record_id FROM staging.raw_rows WHERE kind='data')"
        )
        report = audit_prepared(db, bindings)
        assert _finding(report, "raw_accounting", "*").status == "FAIL"
        qualification = qualify(report, {})
        assert qualification.status("order_headers") != "READY"
        forged = replace(qualification, statuses=(("order_headers", "READY"),))
        with pytest.raises(ValueError, match="qualification"):
            publish_projections(db, forged)


def test_self_consistent_wrong_scale_fails_numeric_source_check():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("200.00"))],
            grains=("order_header",),
        )
        changed = {"c0001": encoded("A"), "c0002": encoded(Decimal("2.00"))}
        db.execute("UPDATE meta.facts SET values_json=?", [json.dumps(changed)])
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "source_numeric", "order_headers").status == "FAIL"
        assert qualify(report, {}).status("order_headers") != "READY"


def test_decimal_scale_change_is_detected_even_when_numeric_value_matches():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("200.00"))],
            grains=("order_header",),
        )
        changed = {"c0001": encoded("A"), "c0002": encoded(Decimal("200"))}
        db.execute("UPDATE meta.facts SET values_json=?", [json.dumps(changed)])
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "source_numeric", "order_headers").status == "FAIL"


def test_quarantine_does_not_shrink_declared_coverage():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("100")), ("B", Decimal("100"))],
            grains=("order_header",),
        )
        db.execute(
            "UPDATE meta.dispositions SET kind='quarantined' "
            "WHERE record_id IN (SELECT record_id FROM staging.raw_rows "
            "WHERE kind='data' ORDER BY ordinal DESC LIMIT 1)"
        )
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert report.declared_data_rows == 2
        assert _finding(report, "declared_coverage", "order_headers").status == "UNRESOLVED"
        assert qualify(report, {}).status("order_headers") != "READY"


def test_duplicate_disposition_without_identity_proof_blocks_coverage():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("100")), ("A", Decimal("100"))],
            grains=("order_header",),
        )
        db.execute(
            "UPDATE meta.dispositions SET kind='duplicate' "
            "WHERE record_id IN (SELECT record_id FROM staging.raw_rows "
            "WHERE kind='data' ORDER BY ordinal DESC LIMIT 1)"
        )
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", "order_headers").status == "UNRESOLVED"
        assert qualify(report, {}).status("order_headers") != "READY"


def test_unready_dependency_blocks_otherwise_ready_scope():
    with duckdb.connect() as db:
        bindings = _world(db)
        report = audit_prepared(db, bindings)
        qualification = qualify(report, {"order_headers": ("order_lines",)})
        assert qualification.status("order_headers") == "UNRESOLVED"
        assert publish_projections(db, qualification) == ()


def test_missing_applicability_proof_cannot_be_not_applicable():
    with duckdb.connect() as db:
        bindings = _world(db)
        report = audit_prepared(db, bindings)
        assert _finding(report, "applicability", "payment_events").status == "UNRESOLVED"
        assert qualify(report, {}).status("payment_events") != "READY"


def test_not_applicable_requires_accepted_source_capability():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("200"))],
            grains=("order_header",),
        )
        capability = Binding(
            kind="capability",
            table_id=bindings.table_ids[0],
            columns=("c0001",),
            value="payment_events",
            profile_digest="b" * 64,
            evidence=(
                EvidenceRef(source="data/shop/source.csv", sha256="a" * 64, locator="header"),
            ),
            available=False,
        )
        entries = bindings.entries + (ResolvedBinding(capability, "ACCEPTED", "source absence"),)
        updated = SemanticBindings(
            entries, bindings.clauses, bindings.profile_digests, bindings.table_ids
        )
        project_facts(db, plan.model_copy(update={"bindings_digest": updated.digest}), updated)
        report = audit_prepared(db, updated)
        assert _finding(report, "applicability", "payment_events").status == "NOT_APPLICABLE"
        assert qualify(report, {}).status("payment_events") == "NOT_APPLICABLE"


def test_candidate_mutation_after_audit_invalidates_publication_receipt():
    with duckdb.connect() as db:
        bindings = _world(db)
        qualification = qualify(audit_prepared(db, bindings), {})
        db.execute("UPDATE meta.candidate_order_headers SET order_total=1")
        with pytest.raises(ValueError, match="stale"):
            publish_projections(db, qualification)


def test_failed_field_lineage_blocks_publication():
    with duckdb.connect() as db:
        bindings = _world(db)
        db.execute("DELETE FROM meta.projection_lineage WHERE role='order_total'")
        report = audit_prepared(db, bindings)
        assert _finding(report, "field_lineage", "order_headers").status == "FAIL"
        assert qualify(report, {}).status("order_headers") != "READY"


def test_cross_source_field_lineage_is_rejected_even_with_matching_values():
    with duckdb.connect() as db:
        bindings = _world(db)
        second = stage_table(
            db,
            SourceFile(Path("other.csv"), "other.csv", "c" * 64),
            SourceTable(
                "other",
                ("order_id", "order_total"),
                1,
                [SourceRow(2, ("A", Decimal("200.00")))],
            ),
        )
        other_data = None
        for batch in iter_raw_batches(db, second.table_id, 100):
            for record in batch:
                store_disposition(db, RawDisposition(record.record_id, "retained", "source"))
                if record.kind != "data":
                    continue
                other_data = record.record_id
                fact = CandidateFact(
                    "row:" + record.record_id,
                    "physical_row",
                    {"c0001": "A", "c0002": Decimal("200.00")},
                )
                store_fact(db, fact)
                for column in fact.values:
                    store_lineage(db, LineageEdge(fact.fact_id, record.record_id, column, "source"))
        db.execute(
            "UPDATE meta.projection_lineage SET record_id=? WHERE role='order_total'",
            [other_data],
        )
        report = audit_prepared(db, bindings)
        assert _finding(report, "field_lineage", "order_headers").status == "FAIL"


def _duplicate_world(db, *, rows=None, grains=("order_line",)):
    if rows is None:
        rows = [("A", "P1", 1, Decimal("100.00"))] * 2
    plan, bindings = prepared_world(
        db,
        ("order_id", "product_id", "quantity", "line_net_amount"),
        rows,
        grains=grains,
    )
    records = [
        r[0]
        for r in db.execute(
            "SELECT record_id FROM staging.raw_rows WHERE kind='data' ORDER BY ordinal"
        ).fetchall()
    ]
    db.execute("ALTER TABLE meta.dispositions ADD COLUMN IF NOT EXISTS duplicate_of VARCHAR")
    db.execute(
        "UPDATE meta.dispositions SET kind='duplicate',duplicate_of=? WHERE record_id=?",
        [records[0], records[1]],
    )
    return plan, bindings, records


def test_exact_order_duplicate_is_independently_proven_without_shrinking_denominator():
    with duckdb.connect() as db:
        plan, bindings, _ = _duplicate_world(db)
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        coverage = _finding(report, "declared_coverage", "order_lines")
        assert report.declared_data_rows == 2
        assert coverage.status == "PASS"
        assert "proven=1" in coverage.detail
        assert qualify(report, {}).status("order_lines") == "READY"


@pytest.mark.parametrize(
    "mutation", ["order", "near", "formula", "format", "forward", "target_excluded", "cross_table"]
)
def test_destructive_duplicate_claim_fails_independent_source_audit(mutation):
    with duckdb.connect() as db:
        plan, bindings, records = _duplicate_world(db)
        if mutation in {"order", "near"}:
            values = [
                encoded("B" if mutation == "order" else "A"),
                encoded("P1"),
                encoded(1),
                encoded(Decimal("99.99") if mutation == "near" else Decimal("100.00")),
            ]
            db.execute(
                "UPDATE staging.raw_rows SET values_json=? WHERE record_id=?",
                [json.dumps(values), records[1]],
            )
        elif mutation in {"formula", "format"}:
            column = "formulas_json" if mutation == "formula" else "formats_json"
            db.execute(
                f"UPDATE staging.raw_rows SET {column}=? WHERE record_id=?",
                [
                    json.dumps([None, None, None, "=50*2" if mutation == "formula" else "0.00"]),
                    records[1],
                ],
            )
        elif mutation == "forward":
            db.execute(
                "UPDATE meta.dispositions SET kind='retained',duplicate_of=NULL WHERE record_id=?",
                [records[1]],
            )
            db.execute(
                "UPDATE meta.dispositions SET kind='duplicate',duplicate_of=? WHERE record_id=?",
                [records[1], records[0]],
            )
        elif mutation == "target_excluded":
            db.execute(
                "UPDATE meta.dispositions SET kind='quarantined' WHERE record_id=?", [records[0]]
            )
        else:
            other = stage_table(
                db,
                SourceFile(Path("other.csv"), "data/shop/other.csv", "c" * 64),
                SourceTable(
                    "other",
                    ("order_id", "product_id", "quantity", "line_net_amount"),
                    1,
                    [SourceRow(2, ("A", "P1", 1, Decimal("100.00")))],
                ),
            )
            other_id = db.execute(
                "SELECT record_id FROM staging.raw_rows WHERE table_id=? AND kind='data'",
                [other.table_id],
            ).fetchone()[0]
            for batch in iter_raw_batches(db, other.table_id, 100):
                for record in batch:
                    store_disposition(db, RawDisposition(record.record_id, "retained", "source"))
            db.execute(
                "UPDATE meta.dispositions SET duplicate_of=? WHERE record_id=?",
                [other_id, records[1]],
            )
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", "order_lines").status == "FAIL"
        assert qualify(report, {}).status("order_lines") == "FAIL"


@pytest.mark.parametrize(
    "grains,scope",
    [
        (("payment_event",), "payment_events"),
        (("inventory_snapshot",), "inventory_snapshots"),
        (("order_line", "payment_event"), "order_lines"),
        (("order_line", "inventory_snapshot"), "order_lines"),
    ],
)
def test_non_order_or_ambiguous_event_grain_duplicate_is_destructive(grains, scope):
    with duckdb.connect() as db:
        plan, bindings, _ = _duplicate_world(db, grains=grains)
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", scope).status == "FAIL"


def test_blank_inherited_order_key_has_no_independent_duplicate_scope_proof():
    with duckdb.connect() as db:
        plan, bindings, records = _duplicate_world(db, rows=[("", "P1", 1, Decimal("100.00"))] * 2)
        # A self-consistent transformed order key is insufficient source evidence.
        db.execute(
            "UPDATE meta.facts SET values_json=?",
            [
                json.dumps(
                    {
                        "c0001": encoded("A"),
                        "c0002": encoded("P1"),
                        "c0003": encoded(1),
                        "c0004": encoded(Decimal("100.00")),
                    }
                )
            ],
        )
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", "order_lines").status == "UNRESOLVED"
        assert qualify(report, {}).status("order_lines") == "UNRESOLVED"


def test_legacy_disposition_storage_with_no_duplicates_remains_valid():
    with duckdb.connect() as db:
        bindings = _world(db)
        db.execute("ALTER TABLE meta.dispositions DROP COLUMN IF EXISTS duplicate_of")
        assert qualify(audit_prepared(db, bindings), {}).status("order_headers") == "READY"


def _events(db):
    db.execute(
        "CREATE TABLE meta.transform_events(record_id VARCHAR,column_id VARCHAR,"
        "step_id VARCHAR,status VARCHAR,before_json JSON,after_json JSON,reason VARCHAR)"
    )


@pytest.mark.parametrize("status", ["UNRESOLVED", "CONFLICT"])
def test_transform_events_block_only_the_projection_using_the_affected_field(status):
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "device_imeis_slash", "payment_at", "payment_amount"),
            [("A", "IMEI1 / IMEI2", date(2026, 1, 1), Decimal("10.00"))],
            grains=("order_line_device", "payment_event"),
        )
        project_facts(db, plan, bindings)
        _events(db)
        record_id = db.execute(
            "SELECT record_id FROM staging.raw_rows WHERE kind='data'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO meta.transform_events VALUES (?,?,?,?,?,?,?)",
            [
                record_id,
                "c0002",
                "split",
                status,
                '"IMEI1/IMEI2"',
                '"IMEI1/IMEI2"',
                "ambiguous device",
            ],
        )
        report = audit_prepared(db, bindings)
        assert _finding(report, "transform_events", "order_line_devices").status == "UNRESOLVED"
        assert qualify(report, {}).status("order_line_devices") == "UNRESOLVED"
        assert _finding(report, "transform_events", "payment_events").status == "PASS"
        assert qualify(report, {}).status("payment_events") == "READY"


@pytest.mark.parametrize("metadata", ["events", "bindings", "new_events"])
def test_optional_audit_metadata_mutation_invalidates_receipt(metadata):
    with duckdb.connect() as db:
        bindings = _world(db)
        if metadata == "events":
            _events(db)
        if metadata == "bindings":
            db.execute(
                "CREATE TABLE meta.bindings(binding_id VARCHAR,status VARCHAR,"
                "reason VARCHAR,binding_json JSON)"
            )
            db.execute("INSERT INTO meta.bindings VALUES ('b','ACCEPTED','source','{}')")
        qualification = qualify(audit_prepared(db, bindings), {})
        if metadata == "bindings":
            db.execute("UPDATE meta.bindings SET status='CONFLICT'")
        else:
            if metadata == "new_events":
                _events(db)
            record_id = db.execute(
                "SELECT record_id FROM staging.raw_rows WHERE kind='data'"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO meta.transform_events VALUES (?,?,?,?,?,?,?)",
                [record_id, "c0001", "step", "CONFLICT", '"A"', '"A"', "conflicting identifier"],
            )
        with pytest.raises(ValueError, match="stale"):
            publish_projections(db, qualification)


@pytest.mark.parametrize("key", [None, "   ", 7])
def test_non_string_or_blank_source_order_key_cannot_prove_duplicate_scope(key):
    with duckdb.connect() as db:
        plan, bindings, _ = _duplicate_world(db, rows=[(key, "P1", 1, Decimal("100.00"))] * 2)
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", "order_lines").status == "UNRESOLVED"


def test_legacy_duplicate_without_link_column_stays_unresolved():
    with duckdb.connect() as db:
        plan, bindings, _ = _duplicate_world(db)
        db.execute("ALTER TABLE meta.dispositions DROP COLUMN duplicate_of")
        project_facts(db, plan, bindings)
        report = audit_prepared(db, bindings)
        assert _finding(report, "declared_coverage", "order_lines").status == "UNRESOLVED"


def test_duplicate_proof_streams_all_source_rows_in_bounded_batches():
    with duckdb.connect() as db:
        plan, bindings, records = _duplicate_world(
            db, rows=[("A", "P1", 1, Decimal("100.00"))] * 1003
        )
        db.execute(
            "UPDATE meta.dispositions SET kind='duplicate',duplicate_of=? "
            "WHERE record_id IN (SELECT record_id FROM staging.raw_rows WHERE kind='data') "
            "AND record_id<>?",
            [records[0], records[0]],
        )
        project_facts(db, plan, bindings)
        sizes = []

        class BoundedCursor:
            def fetchmany(self, size):
                assert size <= 1000
                rows = db.fetchmany(size)
                sizes.append(len(rows))
                return rows

        class BoundedConnection:
            def execute(self, statement, *args):
                result = db.execute(statement, *args)
                return BoundedCursor() if statement.startswith("SELECT d.duplicate_of,") else result

        report = audit_prepared(BoundedConnection(), bindings)
        coverage = _finding(report, "declared_coverage", "order_lines")
        assert coverage.status == "PASS"
        assert "proven=1002" in coverage.detail
        assert sizes == [1000, 2, 0]


@pytest.mark.parametrize("status", ["UNRESOLVED", "CONFLICT"])
def test_mapped_transform_event_blocks_an_otherwise_ready_numeric_scope(status):
    with duckdb.connect() as db:
        bindings = _world(db)
        assert qualify(audit_prepared(db, bindings), {}).status("order_headers") == "READY"
        _events(db)
        record_id = db.execute(
            "SELECT record_id FROM staging.raw_rows WHERE kind='data'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO meta.transform_events VALUES (?,?,?,?,?,?,?)",
            [record_id, "c0002", "parse", status, '"200.00"', '"200.00"', "unresolved parse"],
        )
        report = audit_prepared(db, bindings)
        assert _finding(report, "source_numeric", "order_headers").status == "PASS"
        assert _finding(report, "transform_events", "order_headers").status == "UNRESOLVED"
        assert qualify(report, {}).status("order_headers") == "UNRESOLVED"
