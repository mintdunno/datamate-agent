"""Raw accounting stays independent from canonical fact cardinality."""

import importlib
from datetime import date, datetime, time
from decimal import Decimal

import duckdb
import pytest

from datamate.preparation.models import SourceFile, SourceRow, SourceTable


def storage():
    return importlib.import_module("datamate.preparation.storage")


def test_staging_is_lossless_and_identical_rows_have_distinct_identity(tmp_path):
    module = storage()
    source = SourceFile(tmp_path / "source.xlsx", "data/shop/source.xlsx", "a" * 64)
    values = (
        "001",
        None,
        "",
        Decimal("0.10"),
        b"\x00",
        date(2025, 1, 2),
        datetime(2025, 1, 2, 13, 4),
        time(13, 4),
        1.25,
        True,
    )
    rows = [SourceRow(i, values, ("=A1+B1",), ("0.00",), {"pk": i}) for i in (3, 4)]
    table = SourceTable(
        "Sales",
        tuple(f"c{i}" for i in range(len(values))),
        2,
        iter(rows),
        preamble=(SourceRow(1, ("Title",)),),
    )
    with duckdb.connect() as db:
        module.create_storage(db)
        staged = module.stage_table(db, source, table)
        records = [
            row for batch in module.iter_raw_batches(db, staged.table_id, 1) for row in batch
        ]
        assert len(records) == staged.raw_record_count == 4
        assert staged.row_count == 2
        data = [row for row in records if row.kind == "data"]
        assert data[0].record_id != data[1].record_id
        assert data[0].source == rows[0]
        assert data[0].source.values[3].as_tuple().exponent == -2
        assert [type(v) for v in data[0].source.values] == [type(v) for v in values]
        assert {row.kind for row in records} == {"preamble", "header", "data"}
        with pytest.raises(ValueError, match="batch"):
            list(module.iter_raw_batches(db, staged.table_id, 0))


def test_single_raw_record_supports_two_facts_one_disposition(tmp_path):
    module = storage()
    models = importlib.import_module("datamate.preparation.models")
    source = SourceFile(tmp_path / "source.csv", "data/shop/source.csv", "a" * 64)
    with duckdb.connect() as db:
        module.create_storage(db)
        staged = module.stage_table(
            db, source, SourceTable("devices", ("imei",), None, iter([SourceRow(1, ("X / Y",))]))
        )
        row = next(module.iter_raw_batches(db, staged.table_id, 10))[0]
        module.store_disposition(db, models.RawDisposition(row.record_id, "retained", "split"))
        for index, imei in enumerate(("X", "Y")):
            fact = models.CandidateFact(f"device-{index}", "device", {"imei": imei})
            module.store_fact(db, fact)
            module.store_lineage(
                db, models.LineageEdge(fact.fact_id, row.record_id, "c0001", "split")
            )
        assert db.execute("SELECT count(*) FROM staging.raw_rows").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM meta.dispositions").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM meta.facts").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM meta.lineage").fetchone()[0] == 2
        with pytest.raises(duckdb.ConstraintException):
            module.store_disposition(db, models.RawDisposition(row.record_id, "quarantined", "bad"))


def test_changed_source_digest_changes_record_identity(tmp_path):
    module = storage()
    with duckdb.connect() as db:
        module.create_storage(db)
        ids = []
        for digest in ("a" * 64, "b" * 64):
            staged = module.stage_table(
                db,
                SourceFile(tmp_path / "x.csv", "x.csv", digest),
                SourceTable("sales", ("id",), None, [SourceRow(1, ("same",))]),
            )
            ids.append(next(module.iter_raw_batches(db, staged.table_id, 10))[0].record_id)
        assert ids[0] != ids[1]


def test_batches_share_build_transaction_and_survive_interleaved_queries(tmp_path):
    module = storage()
    with duckdb.connect() as db:
        module.create_storage(db)
        db.execute("BEGIN")
        staged = module.stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "x.csv", "a" * 64),
            SourceTable("sales", ("id",), None, [SourceRow(i, (i,)) for i in range(1, 4)]),
        )
        batches = module.iter_raw_batches(db, staged.table_id, 1)
        assert next(batches)[0].source.values == (1,)
        db.execute("SELECT 42").fetchone()
        assert [batch[0].source.values for batch in batches] == [(2,), (3,)]
        db.execute("ROLLBACK")
        assert db.execute("SELECT count(*) FROM staging.raw_rows").fetchone()[0] == 0


def test_lineage_cannot_reference_nonexistent_source_column(tmp_path):
    module = storage()
    models = importlib.import_module("datamate.preparation.models")
    with duckdb.connect() as db:
        module.create_storage(db)
        staged = module.stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "x.csv", "a" * 64),
            SourceTable("sales", ("id",), None, [SourceRow(1, (1,))]),
        )
        record = next(module.iter_raw_batches(db, staged.table_id, 1))[0]
        module.store_fact(db, models.CandidateFact("fact", "sale", {"id": 1}))
        with pytest.raises(ValueError, match="column"):
            module.store_lineage(db, models.LineageEdge("fact", record.record_id, "c9999", "test"))


def test_evidence_alias_rejects_reversed_line_span():
    from datamate.preparation.normalization import Evidence

    with pytest.raises(ValueError, match="span"):
        Evidence(source="rulebook.md", sha256="a" * 64, locator="rules", line_start=10, line_end=2)


def test_schema_without_physical_header_retains_source_names_and_declared_types(tmp_path):
    import json

    module = storage()
    with duckdb.connect() as db:
        module.create_storage(db)
        staged = module.stage_table(
            db,
            SourceFile(tmp_path / "x.sqlite", "x.sqlite", "a" * 64),
            SourceTable(
                "sales",
                ("Customer Code", " Amount "),
                None,
                [SourceRow(1, ("001", 10))],
                declared_types=("TEXT", "INTEGER"),
            ),
        )
        assert staged.headers == ("Customer Code", " Amount ")
        assert staged.declared_types == ("TEXT", "INTEGER")
        metadata = db.execute(
            "SELECT headers_json,declared_types_json FROM meta.source_tables"
        ).fetchone()
        assert json.loads(metadata[0]) == [
            {"type": "str", "value": "Customer Code"},
            {"type": "str", "value": " Amount "},
        ]
        assert json.loads(metadata[1]) == ["TEXT", "INTEGER"]


def test_staging_persists_excel_epoch_merges_and_uncached_header_formula(tmp_path):
    import json

    module = storage()
    with duckdb.connect() as db:
        module.create_storage(db)
        staged = module.stage_table(
            db,
            SourceFile(tmp_path / "x.xlsx", "x.xlsx", "a" * 64),
            SourceTable(
                "sheet",
                ("id", None),
                1,
                [SourceRow(2, ("001", 1))],
                workbook_epoch=datetime(1904, 1, 1),
                merged_ranges=((1, 2, 1, 3),),
                header_formulas=(None, "=1+1"),
                header_formats=("@", "0.00"),
            ),
        )
        header = next(module.iter_raw_batches(db, staged.table_id, 1))[0]
        assert header.source.formulas == (None, "=1+1")
        assert header.source.formats == ("@", "0.00")
        epoch, merges = db.execute(
            "SELECT workbook_epoch,merged_ranges_json FROM meta.source_tables"
        ).fetchone()
        assert epoch == datetime(1904, 1, 1)
        assert json.loads(merges) == [[1, 2, 1, 3]]
