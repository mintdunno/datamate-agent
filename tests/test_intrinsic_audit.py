"""Audit evidence is independent of parser outcomes and scoped by applicability."""

import json
from dataclasses import replace
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
