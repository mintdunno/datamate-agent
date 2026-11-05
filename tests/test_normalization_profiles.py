"""Profiles cover every raw row with bounded, reproducible model evidence."""

import importlib

import duckdb

from datamate.preparation.models import SourceFile, SourceRow, SourceTable
from datamate.preparation.storage import create_storage, stage_table


def test_full_profiles_are_batch_invariant_and_rare_samples_are_bounded(tmp_path):
    module = importlib.import_module("datamate.preparation.profiling")
    with duckdb.connect() as db:
        create_storage(db)
        rows = [SourceRow(i + 1, (str(i), None if i == 0 else "1000")) for i in range(1002)]
        rows.append(SourceRow(1003, ("rare", "8tr399")))
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "x.csv", "a" * 64),
            SourceTable("sales", ("id", "amount"), None, rows),
        )
        profiles = [module.profile_table(db, table.table_id, batch_size=n) for n in (1, 7, 1000)]
        assert [p.row_count for p in profiles] == [1003, 1003, 1003]
        assert len({p.digest for p in profiles}) == 1
        assert profiles[0].columns[0].unique_count_lower_bound == 1000
        assert profiles[0].columns[0].unique_count_exact is False
        assert profiles[0].columns[1].null_count == 1
        assert profiles[0].columns[1].raw_types == {"NoneType": 1, "str": 1002}
        selected = module.select_evidence(profiles[0], seed=42, max_samples=4)
        assert len(selected) <= 4
        assert any(s.record_id.endswith(":1003") for s in selected)
        assert selected == module.select_evidence(profiles[0], seed=42, max_samples=4)
        assert not hasattr(profiles[0], "accepted_keys")


def test_structural_records_counted_separately_and_empty_values_not_collapsed(tmp_path):
    module = importlib.import_module("datamate.preparation.profiling")
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "x.csv", "a" * 64),
            SourceTable(
                "sales",
                ("id",),
                2,
                [SourceRow(3, (None,)), SourceRow(4, ("",))],
                preamble=(SourceRow(1, ("title",)),),
            ),
        )
        profile = module.profile_table(db, table.table_id, batch_size=1)
        assert profile.row_count == 2
        assert profile.raw_record_count == 4
        assert profile.record_kinds == {"data": 2, "header": 1, "preamble": 1}
        assert profile.columns[0].null_count == profile.columns[0].blank_count == 1
        assert profile.columns[0].unique_count_lower_bound == 2
        assert profile.columns[0].unique_count_exact is True
        assert module.select_evidence(profile, seed=42, max_samples=0) == []


def test_unique_columns_are_only_key_candidates_not_accepted_relationships(tmp_path):
    module = importlib.import_module("datamate.preparation.profiling")
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "x.csv", "a" * 64),
            SourceTable(
                "sales",
                ("unknown_code", "other"),
                None,
                [SourceRow(i + 1, row) for i, row in enumerate((("A", 1), ("B", 1), ("C", 2)))],
            ),
        )
        profile = module.profile_table(db, table.table_id)
        assert profile.candidate_keys == ("c0001",)
        assert profile.columns[0].source_header == {"type": "str", "value": "unknown_code"}
        assert not hasattr(profile, "accepted_relationships")


def test_uniform_tables_still_provide_distinct_representative_source_records(tmp_path):
    module = importlib.import_module("datamate.preparation.profiling")
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "data/shop/x.csv", "a" * 64),
            SourceTable("sales", ("id",), None, [SourceRow(i, ("same",)) for i in range(1, 41)]),
        )
        profile = module.profile_table(db, table.table_id)
        assert len({sample.record_id for sample in profile.samples}) >= 20
        assert len(profile.samples) <= 64
        assert profile.source == "data/shop/x.csv"
        assert profile.source_sha256 == "a" * 64
        assert profile.source_table == "sales"


def test_structure_samples_refer_to_actual_header_or_preamble_not_arbitrary_data(tmp_path):
    module = importlib.import_module("datamate.preparation.profiling")
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(tmp_path / "x.csv", "data/shop/x.csv", "a" * 64),
            SourceTable(
                "sales",
                ("id", "amount"),
                2,
                [SourceRow(i, (str(i), i)) for i in range(3, 43)],
                preamble=(SourceRow(1, ("Title",)),),
            ),
        )
        profile = module.profile_table(db, table.table_id)
        structural = [sample for sample in profile.samples if sample.kind != "data"]
        assert {sample.kind for sample in structural} == {"header", "preamble"}
        assert {sample.record_id for sample in structural} == {
            f"{table.table_id}:1",
            f"{table.table_id}:2",
        }
        assert len(profile.samples) <= 64
