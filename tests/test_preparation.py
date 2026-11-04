"""End-to-end preparation uses synthetic businesses and never opens labels."""

import json
from pathlib import Path

import duckdb
import pytest

from datamate.preparation.discovery import sha256_file
from datamate.preparation.normalization import ColumnPlan, Evidence, TablePlan
from datamate.preparation.pipeline import prepare_business


def fixture(tmp_path):
    root = tmp_path / "split"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text(
        "# Revenue\nUse line amounts.\n## Dates\nUse as_of_date.\n", encoding="utf-8"
    )
    (root / "calendar.csv").write_text("date,lunar_day\n2025-01-01,2\n", encoding="utf-8")
    (business / "README.md").write_text(
        "# Notes\namount is VND, comma thousands.\n", encoding="utf-8"
    )
    (business / "sales.csv").write_text(
        'id,amount\nA,"75,000"\nB,"198k"\nC,invalid\n', encoding="utf-8"
    )
    return root, business, tmp_path / "cache"


def policy(root):
    return {
        "data/shop/sales.csv::sales": TablePlan(
            columns={
                "c0002": ColumnPlan(
                    kind="integer",
                    number_style="comma_thousands",
                    allow_k_suffix=True,
                    evidence=Evidence(
                        source="data/shop/README.md",
                        sha256=sha256_file(root / "data/shop/README.md"),
                        locator="line 2",
                    ),
                )
            }
        )
    }


def test_ingestion_stores_raw_provenance_typed_values_profiles_and_rules(tmp_path):
    root, business, cache = fixture(tmp_path)
    before = {path: sha256_file(path) for path in [business / "sales.csv", root / "rulebook.md"]}
    prepared = prepare_business(root, "shop", cache, split_id="demo", plans=policy(root))
    table = prepared.catalog["tables"][0]
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        values = db.execute(
            f'SELECT c0001,c0002 FROM "{table["table_id"]}" ORDER BY _source_row'
        ).fetchall()
        assert values == [("A", 75000), ("B", 198000), ("C", None)]
        raw = db.execute(
            "SELECT source_row, raw_json, events_json FROM raw_records "
            "WHERE record_kind='data' ORDER BY source_row"
        ).fetchall()
        assert [row[0] for row in raw] == [2, 3, 4]
        assert json.loads(raw[0][1])[1] == {"type": "str", "value": "75,000"}
        assert "parse_failed" in raw[-1][2]
        assert db.execute("SELECT count(*) FROM rule_catalog").fetchone()[0] >= 3
        assert db.execute("SELECT count(*) FROM calendar_data").fetchone()[0] == 1
    assert table["input_rows"] == table["output_rows"] == 3
    assert table["columns"][1]["parse_failures"] == 1
    assert all(sha256_file(path) == digest for path, digest in before.items())


def test_cache_reuses_only_complete_same_inputs_and_ignores_labels(tmp_path):
    root, _, cache = fixture(tmp_path)
    (root / "answers.jsonl").write_text("NEVER READ", encoding="utf-8")
    one = prepare_business(root, "shop", cache, split_id="demo")
    two = prepare_business(root, "shop", cache, split_id="demo")
    assert not one.cache_hit and two.cache_hit
    assert one.fingerprint == two.fingerprint
    (root / "answers.jsonl").write_text("CHANGED LABELS", encoding="utf-8")
    assert prepare_business(root, "shop", cache, split_id="demo").fingerprint == one.fingerprint
    assert "answers.jsonl" not in one.catalog_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "target", ["rulebook.md", "calendar.csv", "data/shop/README.md", "data/shop/sales.csv"]
)
def test_cache_invalidates_every_allowed_source_dependency(tmp_path, target):
    root, _, cache = fixture(tmp_path)
    before = prepare_business(root, "shop", cache, split_id="demo")
    with (root / target).open("a", encoding="utf-8") as stream:
        stream.write("\n")
    # Changing CSV via a blank record may be a format error; use valid record content instead.
    if target.endswith("sales.csv"):
        (root / target).write_text("id,amount\nA,1\n", encoding="utf-8")
    elif target == "calendar.csv":
        (root / target).write_text("date,lunar_day\n2025-01-01,3\n", encoding="utf-8")
    after = prepare_business(root, "shop", cache, split_id="demo")
    assert after.fingerprint != before.fingerprint
    assert not after.cache_hit


def test_cache_invalidates_policy_and_rejects_stale_evidence(tmp_path):
    root, business, cache = fixture(tmp_path)
    one = prepare_business(root, "shop", cache, split_id="demo")
    two = prepare_business(root, "shop", cache, split_id="demo", plans=policy(root))
    assert one.fingerprint != two.fingerprint
    plans = policy(root)
    (business / "README.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="evidence"):
        prepare_business(root, "shop", cache, split_id="demo", plans=plans)


def test_cache_corruption_is_not_a_silent_hit(tmp_path):
    root, _, cache = fixture(tmp_path)
    result = prepare_business(root, "shop", cache, split_id="demo")
    result.database.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        prepare_business(root, "shop", cache, split_id="demo")


def test_output_cannot_write_into_source_tree(tmp_path):
    root, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="output"):
        prepare_business(root, "shop", root / "cache", split_id="demo")


def test_reference_open_is_blocked_and_runtime_has_no_evaluator_dependency(tmp_path, monkeypatch):
    root, _, cache = fixture(tmp_path)
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.name in {"answers.jsonl", ".env", "questions.jsonl"}:
            raise AssertionError("forbidden input")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = prepare_business(root, "shop", cache, split_id="demo")
    assert result.catalog["business_id"] == "shop"


def test_failed_preparation_never_publishes_complete_cache(tmp_path):
    root, business, cache = fixture(tmp_path)
    (business / "sales.csv").write_text("id,amount\nA,1,extra\n", encoding="utf-8")
    with pytest.raises(ValueError):
        prepare_business(root, "shop", cache, split_id="demo")
    assert not list(cache.glob("*/manifest.json"))


def test_aware_datetime_normalization_preserves_instant(tmp_path):
    from datetime import datetime

    root, business, cache = fixture(tmp_path)
    (business / "sales.csv").write_text("id,when\nA,2025-01-01T07:00:00+07:00\n", encoding="utf-8")
    evidence = Evidence(
        source="data/shop/README.md",
        sha256=sha256_file(business / "README.md"),
        locator="ISO timestamp",
    )
    result = prepare_business(
        root,
        "shop",
        cache,
        split_id="demo",
        plans={
            "data/shop/sales.csv::sales": TablePlan(
                columns={"c0002": ColumnPlan(kind="datetime", evidence=evidence)}
            )
        },
    )
    table = result.catalog["tables"][0]
    with duckdb.connect(str(result.database), read_only=True) as db:
        actual = db.execute(f'SELECT c0002 FROM "{table["table_id"]}"').fetchone()[0]
    assert actual == datetime.fromisoformat("2025-01-01T07:00:00+07:00")


def test_all_rows_and_profile_cardinality_cap_are_explicit(tmp_path):
    root, business, cache = fixture(tmp_path)
    (business / "sales.csv").write_text(
        "id,amount\n" + "".join(f"X{i},{i}\n" for i in range(1101)), encoding="utf-8"
    )
    result = prepare_business(root, "shop", cache, split_id="demo")
    table = result.catalog["tables"][0]
    assert table["input_rows"] == table["output_rows"] == 1101
    assert table["columns"][0]["unique_count_lower_bound"] == 1000
    assert table["columns"][0]["unique_count_exact"] is False


def test_parsed_bytes_are_bound_to_manifest_even_if_source_changes_and_reverts(
    tmp_path, monkeypatch
):
    import datamate.preparation.pipeline as pipeline

    root, business, cache = fixture(tmp_path)
    original_bytes = (business / "sales.csv").read_bytes()
    original_build = pipeline._build_database

    def change_and_revert(*args, **kwargs):
        (business / "sales.csv").write_text("id,amount\nA,999\n", encoding="utf-8")
        try:
            return original_build(*args, **kwargs)
        finally:
            (business / "sales.csv").write_bytes(original_bytes)

    monkeypatch.setattr(pipeline, "_build_database", change_and_revert)
    result = pipeline.prepare_business(root, "shop", cache, split_id="demo")
    table = result.catalog["tables"][0]
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert db.execute(
            f'SELECT c0002 FROM "{table["table_id"]}" ORDER BY _source_row'
        ).fetchall() == [("75,000",), ("198k",), ("invalid",)]


def test_cache_lock_is_not_removed_by_a_competing_builder(tmp_path):
    root, _, cache = fixture(tmp_path)
    first = prepare_business(root, "shop", cache, split_id="demo")
    other = tmp_path / "other-cache"
    other.mkdir()
    lock = other / (first.fingerprint + ".lock")
    lock.write_text("other owner", encoding="utf-8")
    with pytest.raises(ValueError, match="already being built"):
        prepare_business(root, "shop", other, split_id="demo")
    assert lock.read_text(encoding="utf-8") == "other owner"


def test_cache_invalidates_pipeline_version(tmp_path, monkeypatch):
    import datamate.preparation.pipeline as pipeline

    root, _, cache = fixture(tmp_path)
    first = prepare_business(root, "shop", cache, split_id="demo")
    monkeypatch.setattr(pipeline, "PIPELINE_VERSION", "synthetic-new-version")
    second = prepare_business(root, "shop", cache, split_id="demo")
    assert first.fingerprint != second.fingerprint
    assert second.cache_hit is False


def test_persistent_source_changes_during_build_do_not_publish(tmp_path, monkeypatch):
    import datamate.preparation.pipeline as pipeline

    root, business, cache = fixture(tmp_path)
    build = pipeline._build_database

    def changed(*args, **kwargs):
        result = build(*args, **kwargs)
        (business / "sales.csv").write_text("id,amount\nNEW,10\n", encoding="utf-8")
        return result

    monkeypatch.setattr(pipeline, "_build_database", changed)
    with pytest.raises(ValueError, match="changed during"):
        prepare_business(root, "shop", cache, split_id="demo")
    assert not list(cache.glob("*/manifest.json"))


def test_mixed_format_storage_preserves_blobs_dates_formulas_and_empty_tables(tmp_path):
    import sqlite3
    from datetime import datetime

    from openpyxl import Workbook

    root, business, cache = fixture(tmp_path)
    with sqlite3.connect(business / "pos.sqlite") as db:
        db.execute("CREATE TABLE blobs(id INTEGER, payload BLOB)")
        db.execute("INSERT INTO blobs VALUES (?, ?)", [1, b"\x00\xff"])
        db.execute("CREATE TABLE empty(id INTEGER)")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Title"])
    sheet.append([])
    sheet.append(["name", "when", "active"])
    sheet.append(["X", datetime(2025, 1, 2), True])
    sheet.append(["Y", "=1+1", False])
    workbook.save(business / "book.xlsx")
    result = prepare_business(root, "shop", cache, split_id="demo")
    assert len(result.catalog["tables"]) == 4
    blob_table = next(t for t in result.catalog["tables"] if t["source_table"] == "blobs")
    excel_table = next(t for t in result.catalog["tables"] if t["source"].endswith("book.xlsx"))
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert (
            db.execute(f'SELECT c0002 FROM "{blob_table["table_id"]}"').fetchone()[0] == b"\x00\xff"
        )
        assert db.execute(
            f'SELECT c0002 FROM "{excel_table["table_id"]}" ORDER BY _source_row'
        ).fetchall() == [(datetime(2025, 1, 2),), (None,)]
        events = db.execute(
            "SELECT events_json FROM raw_records WHERE table_id=? AND source_row=5",
            [excel_table["table_id"]],
        ).fetchone()[0]
        assert "formula_cache_missing" in events
    assert excel_table["header_row"] == 3 and excel_table["preamble_rows"] == 2
