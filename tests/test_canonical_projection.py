"""Canonical facts retain grain, exact values, and source lineage."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

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
from datamate.preparation.normalization import CleaningPlan, CleaningStep, PreserveRawOperation
from datamate.preparation.projection import project_facts
from datamate.preparation.storage import (
    create_storage,
    iter_raw_batches,
    stage_table,
    store_disposition,
    store_fact,
    store_lineage,
)


def prepared_world(db, names, rows, *, grains, snapshot_at=None):
    create_storage(db)
    staged = stage_table(
        db,
        SourceFile(Path("source.csv"), "data/shop/source.csv", "a" * 64),
        SourceTable(
            "source", tuple(names), 1, [SourceRow(i + 2, row) for i, row in enumerate(rows)]
        ),
    )
    evidence = (EvidenceRef(source="data/shop/source.csv", sha256="a" * 64, locator="header"),)
    roles = [
        Binding(
            kind="role",
            table_id=staged.table_id,
            columns=(f"c{i:04d}",),
            value=name,
            profile_digest="b" * 64,
            evidence=evidence,
        )
        for i, name in enumerate(names, 1)
    ]
    grain_bindings = [
        Binding(
            kind="grain",
            table_id=staged.table_id,
            columns=tuple(f"c{i:04d}" for i in range(1, len(names) + 1)),
            value=grain,
            profile_digest="b" * 64,
            evidence=evidence,
            snapshot_at=snapshot_at if grain == "inventory_snapshot" else None,
        )
        for grain in grains
    ]
    bindings = SemanticBindings(
        tuple(
            ResolvedBinding(binding, "ACCEPTED", "source proof")
            for binding in roles + grain_bindings
        ),
        (),
        ("b" * 64,),
        (staged.table_id,),
    )
    plan = CleaningPlan(
        bindings_digest=bindings.digest,
        profile_digests=bindings.profile_digests,
        steps=(CleaningStep(table_id=staged.table_id, operation=PreserveRawOperation()),),
    )
    for batch in iter_raw_batches(db, staged.table_id, 100):
        for record in batch:
            store_disposition(db, RawDisposition(record.record_id, "retained", "source"))
            if record.kind != "data":
                continue
            fact = CandidateFact(
                "row:" + record.record_id,
                "physical_row",
                {f"c{i:04d}": value for i, value in enumerate(record.source.values, 1)},
            )
            store_fact(db, fact)
            for column in fact.values:
                store_lineage(db, LineageEdge(fact.fact_id, record.record_id, column, "source"))
    return plan, bindings


def test_repeated_invoice_fields_project_once_per_order():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            (
                "order_id",
                "order_total",
                "product_id",
                "quantity",
                "line_net_amount",
                "shipping_fee",
            ),
            [
                ("A", Decimal("200.00"), "P1", Decimal("1"), Decimal("100.00"), Decimal("8.25")),
                ("A", Decimal("200.00"), "P2", Decimal("1"), Decimal("100.00"), Decimal("8.25")),
            ],
            grains=("order_header", "order_line", "shipping_component"),
        )
        result = project_facts(db, plan, bindings)
        assert result.count("order_headers") == 1
        assert result.count("order_lines") == 2
        assert result.count("shipping_components") == 1
        assert db.execute("SELECT order_total FROM meta.candidate_order_headers").fetchone() == (
            Decimal("200.00"),
        )
        columns = [
            row[1]
            for row in db.execute("PRAGMA table_info('meta.candidate_order_headers')").fetchall()
        ]
        assert "is_valid" not in columns and "revenue" not in columns
        assert (
            db.execute(
                "SELECT count(*) FROM meta.projection_lineage WHERE scope='order_headers'"
            ).fetchone()[0]
            == 4
        )


def test_declared_device_split_creates_two_facts_one_disposition():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "device_imeis_slash"),
            [("A", "IMEI1 / IMEI2")],
            grains=("order_line_device",),
        )
        result = project_facts(db, plan, bindings)
        assert result.count("order_line_devices") == 2
        assert db.execute(
            "SELECT imei FROM meta.candidate_order_line_devices ORDER BY imei"
        ).fetchall() == [
            ("IMEI1",),
            ("IMEI2",),
        ]
        assert (
            db.execute(
                "SELECT count(*) FROM meta.projection_lineage "
                "WHERE scope='order_line_devices' AND role='imei'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute("SELECT count(*) FROM meta.dispositions").fetchone()[0] == 2
        )  # header + data


def test_snapshot_is_date_only_and_does_not_create_history():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("product_id", "inventory_quantity"),
            [("P1", Decimal("3.00"))],
            grains=("inventory_snapshot",),
            snapshot_at="2025-01-31",
        )
        result = project_facts(db, plan, bindings)
        assert result.count("inventory_snapshots") == 1
        assert db.execute(
            "SELECT snapshot_at FROM meta.candidate_inventory_snapshots"
        ).fetchone() == (date(2025, 1, 31),)
        assert db.execute("SELECT count(*) FROM meta.projection_descriptors").fetchone()[0] == 1


def test_missing_order_id_and_unparsed_money_never_fabricate_or_publish():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total", "product_id", "quantity", "line_net_amount"),
            [(None, "1.200,00", "P1", Decimal("1"), Decimal("1200.00"))],
            grains=("order_header", "order_line"),
        )
        result = project_facts(db, plan, bindings)
        assert result.count("order_headers") == 0
        assert result.count("order_lines") == 0
        assert "order_id" in result.descriptor("order_headers").blocked_fields
        assert "order_total" in result.descriptor("order_headers").blocked_fields
        with pytest.raises(duckdb.CatalogException):
            db.execute("SELECT * FROM clean.order_headers")


def test_conflicting_repeated_amount_blocks_header_scope():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("200")), ("A", Decimal("201"))],
            grains=("order_header",),
        )
        result = project_facts(db, plan, bindings)
        assert result.count("order_headers") == 0
        assert "order_total" in result.descriptor("order_headers").blocked_fields


def test_same_order_key_in_distinct_source_tables_is_not_merged():
    with duckdb.connect() as db:
        plan, bindings = prepared_world(
            db,
            ("order_id", "order_total"),
            [("A", Decimal("100"))],
            grains=("order_header",),
        )
        second = stage_table(
            db,
            SourceFile(Path("second.csv"), "data/shop/second.csv", "c" * 64),
            SourceTable(
                "second", ("order_id", "order_total"), 1, [SourceRow(2, ("A", Decimal("300")))]
            ),
        )
        for batch in iter_raw_batches(db, second.table_id, 100):
            for record in batch:
                store_disposition(db, RawDisposition(record.record_id, "retained", "source"))
                if record.kind != "data":
                    continue
                fact = CandidateFact(
                    "row:" + record.record_id,
                    "physical_row",
                    {"c0001": "A", "c0002": Decimal("300")},
                )
                store_fact(db, fact)
                for column in fact.values:
                    store_lineage(db, LineageEdge(fact.fact_id, record.record_id, column, "source"))
        evidence = (EvidenceRef(source="data/shop/second.csv", sha256="c" * 64, locator="header"),)
        second_bindings = [
            Binding(
                kind="role",
                table_id=second.table_id,
                columns=(f"c{i:04d}",),
                value=role,
                profile_digest="b" * 64,
                evidence=evidence,
            )
            for i, role in enumerate(("order_id", "order_total"), 1)
        ]
        second_bindings.append(
            Binding(
                kind="grain",
                table_id=second.table_id,
                columns=("c0001", "c0002"),
                value="order_header",
                profile_digest="b" * 64,
                evidence=evidence,
            )
        )
        merged = SemanticBindings(
            bindings.entries
            + tuple(
                ResolvedBinding(binding, "ACCEPTED", "source proof") for binding in second_bindings
            ),
            bindings.clauses,
            bindings.profile_digests,
            bindings.table_ids + (second.table_id,),
        )
        two_table_plan = plan.model_copy(
            update={
                "bindings_digest": merged.digest,
                "steps": plan.steps
                + (CleaningStep(table_id=second.table_id, operation=PreserveRawOperation()),),
            }
        )
        result = project_facts(db, two_table_plan, merged)
        assert result.count("order_headers") == 2
        assert sorted(
            row[0]
            for row in db.execute("SELECT order_total FROM meta.candidate_order_headers").fetchall()
        ) == [Decimal("100"), Decimal("300")]
