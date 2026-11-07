"""Source-row unit proofs through registry preparation, with no model or QA labels."""

import json
from decimal import Decimal
from hashlib import sha256

import duckdb
import pytest

from datamate.preparation.models import Binding
from datamate.preparation.pipeline import prepare_business
from datamate.preparation.storage import _decoded
from datamate.preparation.v2 import PreparationPolicy


def native_world(tmp_path, *, names_only=False):
    root = tmp_path / "train"
    shop = root / "data" / "shop"
    shop.mkdir(parents=True)
    (root / "rulebook.md").write_text(
        "# Rules\nUse source product-specific conversions.\n", encoding="utf-8"
    )
    (root / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    (shop / "README.md").write_text(
        "# Shop\n## Cột trong products.csv\n"
        "- **product_id**: mã hàng\n- **product_name**: tên hàng\n"
        "- **base_unit**: đơn vị cơ bản\n"
        "## Cột trong units.csv\n- **product_id**: mã hàng\n"
        "- **unit**: đơn vị lớn (thùng, hộp, tấn...)\n"
        "- **factor**: hệ số: số đơn vị cơ bản trong 1 đơn vị lớn\n"
        "- **base_unit**: đơn vị cơ bản\n"
        "## Cột trong sales.csv\n- **order_id**: mã đơn\n"
        "- **product_name**: tên hàng\n- **quantity**: số lượng\n"
        "- **unit**: đơn vị tính\n- **line_net_amount**: thành tiền sau giảm\n"
        "## Quy ước\n- **Quy đổi đơn vị**: số lượng ghi theo đơn vị của dòng, "
        "hệ số quy đổi ở `units.csv`.\n",
        encoding="utf-8",
    )
    (shop / "products.csv").write_text(
        "product_id,product_name,base_unit\nP1,Alpha,viên\nP2,Beta,cái\n", encoding="utf-8"
    )
    (shop / "units.csv").write_text(
        "product_id,unit,factor,base_unit\nP1,vỉ,12,viên\nP2,hộp,6,cái\n", encoding="utf-8"
    )
    identity = "product_name" if names_only else "product_id"
    values = ("Alpha", "Beta") if names_only else ("P1", "P2")
    (shop / "sales.csv").write_text(
        f"order_id,{identity},quantity,unit,line_net_amount\nO1,{values[0]},1,vỉ,1200\nO2,{values[1]},2,hộp,500\n",
        encoding="utf-8",
    )
    return root


def prepare(root, tmp_path):
    return prepare_business(
        root, "shop", tmp_path / "cache", split_id="train", policy=PreparationPolicy()
    )


def facts(prepared):
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        values = db.execute(
            "SELECT f.values_json FROM meta.facts f JOIN staging.raw_rows r "
            "ON f.fact_id='row:'||r.record_id JOIN meta.source_tables t USING(table_id) "
            "WHERE t.source LIKE '%/sales.csv' AND r.kind='data' ORDER BY r.ordinal"
        ).fetchall()
    return [{k: _decoded(v) for k, v in json.loads(row[0]).items()} for row in values]


def test_native_conversion_rows_are_proof_and_raw_quantities_stay_separate(tmp_path):
    root = native_world(tmp_path)
    sources = {p: sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    result = prepare(root, tmp_path)
    rows = facts(result)
    assert [r.get("quantity_base") for r in rows] == [12, 12]
    assert [r["c0003"] for r in rows] == [1, 2]
    assert [r["quantity_base_unit"] for r in rows] == ["viên", "cái"]
    with duckdb.connect(str(result.database), read_only=True) as db:
        proofs = db.execute(
            "SELECT binding_json FROM meta.bindings WHERE status='ACCEPTED'"
        ).fetchall()
    units = [json.loads(r[0]) for r in proofs if json.loads(r[0])["kind"] == "unit"]
    assert units and any(e["locator"].startswith("raw:") for b in units for e in b["evidence"])
    assert all(sha256(p.read_bytes()).hexdigest() == h for p, h in sources.items())


def test_name_only_sales_use_verified_catalog_rows(tmp_path):
    root = native_world(tmp_path, names_only=True)
    assert [r.get("quantity_base") for r in facts(prepare(root, tmp_path))] == [12, 12]


@pytest.mark.parametrize("extra", ["P1,vỉ,12,viên\n", "P1,vỉ,10,viên\n"])
def test_duplicate_or_conflicting_conversion_blocks_only_affected_product(tmp_path, extra):
    root = native_world(tmp_path)
    path = root / "data/shop/units.csv"
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    rows = facts(prepare(root, tmp_path))
    assert rows[0].get("quantity_base") is None
    assert rows[1].get("quantity_base") == 12


def test_ambiguous_name_does_not_become_product_identity(tmp_path):
    root = native_world(tmp_path, names_only=True)
    path = root / "data/shop/products.csv"
    path.write_text(path.read_text(encoding="utf-8") + "P3,Alpha,viên\n", encoding="utf-8")
    rows = facts(prepare(root, tmp_path))
    assert rows[0].get("quantity_base") is None
    assert rows[1].get("quantity_base") == 12


def test_changed_conversion_invalidates_cache_and_recomputes(tmp_path):
    root = native_world(tmp_path)
    first = prepare(root, tmp_path)
    path = root / "data/shop/units.csv"
    path.write_text(
        path.read_text(encoding="utf-8").replace("P1,vỉ,12", "P1,vỉ,24"), encoding="utf-8"
    )
    second = prepare(root, tmp_path)
    assert second.fingerprint != first.fingerprint
    assert facts(first)[0].get("quantity_base") == 12
    assert facts(second)[0].get("quantity_base") == 24


def test_policy_cannot_forge_a_native_row_receipt(tmp_path):
    root = native_world(tmp_path)
    baseline = prepare(root, tmp_path)
    with duckdb.connect(str(baseline.database), read_only=True) as db:
        payloads = db.execute(
            "SELECT binding_json FROM meta.bindings WHERE status='ACCEPTED'"
        ).fetchall()
    unit = next(
        Binding.model_validate_json(row[0])
        for row in payloads
        if json.loads(row[0])["kind"] == "unit" and json.loads(row[0])["unit_factor"]
    )
    forged = unit.model_copy(update={"unit_factor": Decimal(999)})
    candidate = prepare_business(
        root,
        "shop",
        tmp_path / "forged",
        split_id="train",
        policy=PreparationPolicy(binding_proposals=(forged,)),
    )
    assert facts(candidate)[0]["quantity_base"] == 12
    with duckdb.connect(str(candidate.database), read_only=True) as db:
        assert (
            db.execute(
                "SELECT status FROM meta.bindings WHERE binding_id=?", [forged.binding_id]
            ).fetchone()[0]
            == "UNRESOLVED"
        )


def test_conversion_lineage_includes_the_foreign_source_factor(tmp_path):
    result = prepare(native_world(tmp_path), tmp_path)
    with duckdb.connect(str(result.database), read_only=True) as db:
        count = db.execute(
            "SELECT count(*) FROM meta.lineage l JOIN staging.raw_rows r USING(record_id) "
            "JOIN meta.source_tables t USING(table_id) WHERE t.source LIKE '%/units.csv' "
            "AND l.column_id='c0003'"
        ).fetchone()[0]
    assert count > 0


def test_already_base_quantity_is_not_multiplied_again(tmp_path):
    root = native_world(tmp_path)
    path = root / "data/shop/sales.csv"
    path.write_text(
        path.read_text(encoding="utf-8").replace("O1,P1,1,vỉ", "O1,P1,3,viên"), encoding="utf-8"
    )
    assert facts(prepare(root, tmp_path))[0]["quantity_base"] == 3


def test_sales_with_names_and_codes_are_not_mistaken_for_catalog_duplicates(tmp_path):
    root = native_world(tmp_path)
    path = root / "data/shop/sales.csv"
    path.write_text(
        "order_id,product_id,product_name,quantity,unit,line_net_amount\n"
        "O1,P1,Alpha,1,vỉ,1200\nO2,P1,Alpha,2,vỉ,2400\n",
        encoding="utf-8",
    )
    assert [r.get("quantity_base") for r in facts(prepare(root, tmp_path))] == [12, 24]


@pytest.mark.parametrize("kind", ["csv", "xlsx", "sqlite"])
def test_native_fractional_factor_has_equal_semantics_across_formats(tmp_path, kind):
    root = native_world(tmp_path)
    folder = root / "data/shop"
    sales = folder / "sales.csv"
    sales.write_text(
        sales.read_text(encoding="utf-8").replace("O1,P1,1", "O1,P1,2"), encoding="utf-8"
    )
    old = folder / "units.csv"
    rows = [("P1", "vỉ", 1.5, "viên"), ("P2", "hộp", 6, "cái")]
    headers = ["product_id", "unit", "unit_factor", "base_unit"]
    if kind == "csv":
        old.write_text(
            "product_id,unit,unit_factor,base_unit\nP1,vỉ,1.5,viên\nP2,hộp,6,cái\n",
            encoding="utf-8",
        )
    else:
        old.unlink()  # Only this test's synthetic fixture is replaced.
        if kind == "xlsx":
            from openpyxl import Workbook

            book = Workbook()
            book.active.append(headers)
            for row in rows:
                book.active.append(row)
            book.save(folder / "units.xlsx")
            book.close()
        else:
            import sqlite3

            with sqlite3.connect(folder / "units.sqlite") as db:
                db.execute(
                    "CREATE TABLE rates(product_id TEXT,unit TEXT,unit_factor REAL,base_unit TEXT)"
                )
                db.executemany("INSERT INTO rates VALUES (?,?,?,?)", rows)
        readme = folder / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8").replace("units.csv", "units." + kind),
            encoding="utf-8",
        )
    assert facts(prepare(root, tmp_path))[0]["quantity_base"] == 3


def test_temporal_conversion_is_not_broadcast_as_a_timeless_factor(tmp_path):
    root = native_world(tmp_path)
    path = root / "data/shop/units.csv"
    path.write_text(
        "product_id,unit,unit_factor,base_unit,valid_from\nP1,vỉ,12,viên,2025-02-01\n",
        encoding="utf-8",
    )
    assert facts(prepare(root, tmp_path))[0].get("quantity_base") is None


def test_unsupported_conditional_source_blocks_only_its_product_dependencies(tmp_path):
    root = native_world(tmp_path)
    (root / "data/shop/overrides.csv").write_text(
        "product_id,unit,unit_factor,base_unit,valid_from\nP1,vỉ,24,viên,2025-02-01\n",
        encoding="utf-8",
    )
    rows = facts(prepare(root, tmp_path))
    assert rows[0].get("quantity_base") is None
    assert rows[1].get("quantity_base") == 12
