"""Source-bound text repair reaches qualified SQL without mutating physical evidence."""

import json
import sqlite3
from hashlib import sha256

import duckdb
import pytest
from openpyxl import Workbook

from datamate.preparation.pipeline import PreparationPolicy, prepare_business
from datamate.runtime.tools import StructuredTools


def mojibake(text):
    return text.encode("utf-8").decode("cp1252")


def split(tmp_path):
    root = tmp_path / "split"
    business = root / "data/shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text("# Rules\nPreserve source evidence.\n", encoding="utf-8")
    (business / "README.md").write_text("# Native sales data\n", encoding="utf-8")
    (root / "calendar.csv").write_text(
        "date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n"
        "2025-01-01,3,2,12,2024,false,\n",
        encoding="utf-8",
    )
    return root, business


def prepare(root, tmp_path, **kwargs):
    return prepare_business(
        root, "shop", tmp_path / "cache", split_id="demo", policy=PreparationPolicy(**kwargs)
    )


def test_repaired_excel_header_preserves_raw_and_qualifies(tmp_path):
    root, business = split(tmp_path)
    book = Workbook()
    sheet = book.active
    sheet.title = mojibake("Tiếng Việt")
    sheet.append(["order_id", mojibake("mã hàng"), "quantity", "line_net_amount"])
    sheet.append(["A", "P1", 2, 100])
    book.save(business / "sales.xlsx")
    before = (business / "sales.xlsx").read_bytes()
    result = prepare(root, tmp_path)
    assert result.catalog["text_integrity"]["accepted"] >= 2
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert db.execute("SELECT sum(line_net_amount) FROM clean.order_lines").fetchone()[0] == 100
        header, name = db.execute(
            "SELECT headers_json,table_name FROM meta.source_tables WHERE source LIKE '%.xlsx'"
        ).fetchone()
        assert json.loads(header)[1]["value"] == mojibake("mã hàng")
        assert name == mojibake("Tiếng Việt")
    assert (business / "sales.xlsx").read_bytes() == before
    profile = next(t for t in result.catalog["tables"] if t["source"].endswith(".xlsx"))
    assert profile["columns"][1]["source_header"]["value"] == mojibake("mã hàng")
    assert profile["columns"][1]["header_repair"]["decision"]["candidate"] == "mã hàng"


def test_declared_sqlite_foreign_key_repair_reaches_sql_filter(tmp_path):
    root, business = split(tmp_path)
    damaged = mojibake("Tiếng Việt")
    with sqlite3.connect(business / "sales.sqlite") as db:
        db.execute("CREATE TABLE products(product_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO products VALUES (?)", ("Tiếng Việt",))
        db.execute(
            "CREATE TABLE sales(order_id TEXT, product_id TEXT REFERENCES "
            "products(product_id), quantity INTEGER,line_net_amount INTEGER)"
        )
        db.execute("INSERT INTO sales VALUES ('A',?,2,100)", (damaged,))
    result = prepare(root, tmp_path)
    tools = StructuredTools(result.database, result.catalog)
    response = tools.execute_sql(
        "SELECT sum(line_net_amount) AS amount FROM clean.order_lines WHERE product_id = ?",
        ["Tiếng Việt"],
    )
    assert response["rows"] == [["100"]]
    with duckdb.connect(str(result.database), read_only=True) as db:
        receipts = db.execute(
            "SELECT proof_json FROM meta.text_integrity WHERE status='ACCEPTED' AND "
            "column_id='c0002'"
        ).fetchall()
        assert any("foreign_key" in row[0] for row in receipts)
        raw = db.execute(
            "SELECT values_json FROM staging.raw_rows r JOIN meta.source_tables t "
            "USING(table_id) WHERE t.table_name='sales' AND r.kind='data'"
        ).fetchone()[0]
        assert json.loads(raw)[1]["value"] == damaged


@pytest.mark.parametrize("collision", [False, True])
def test_unproven_key_repair_keeps_candidate_and_other_scope_ready(tmp_path, collision):
    root, business = split(tmp_path)
    damaged = mojibake("Tiếng Việt")
    with sqlite3.connect(business / "sales.sqlite") as db:
        relation = " REFERENCES products(product_id)" if collision else ""
        if collision:
            db.execute("CREATE TABLE products(product_id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO products VALUES (?)", ("Tiếng Việt",))
        db.execute(
            "CREATE TABLE sales(order_id TEXT,product_id TEXT"
            + relation
            + ",quantity INTEGER,line_net_amount INTEGER)"
        )
        db.execute("INSERT INTO sales VALUES ('A',?,2,100)", (damaged,))
        if collision:
            db.execute("INSERT INTO sales VALUES ('B',?,1,20)", ("Tiếng Việt",))
        db.execute("CREATE TABLE payments(order_id TEXT,payment_at TEXT,payment_amount INTEGER)")
        db.execute("INSERT INTO payments VALUES ('A','2025-01-01T00:00:00',50)")
    result = prepare(root, tmp_path)
    assert result.catalog["qualification"]["order_lines"] == "UNRESOLVED"
    assert result.catalog["qualification"]["payment_events"] == "READY"
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert (
            db.execute("SELECT sum(payment_amount) FROM clean.payment_events").fetchone()[0] == 50
        )
        assert (
            db.execute(
                "SELECT count(*) FROM meta.text_integrity WHERE status='UNRESOLVED' AND "
                "candidate_json IS NOT NULL"
            ).fetchone()[0]
            > 0
        )
        assert db.execute("SELECT count(*) FROM staging.raw_rows WHERE kind='data'").fetchone()[
            0
        ] == (5 if collision else 3)


def test_clean_source_values_and_raw_ids_are_unchanged_when_repair_disabled(tmp_path):
    root, business = split(tmp_path)
    (business / "sales.csv").write_text(
        "order_id,product_id,quantity,line_net_amount\nA,P1,2,100\n", encoding="utf-8"
    )
    enabled = prepare(root, tmp_path)
    disabled = prepare(root, tmp_path, text_integrity={"enabled": False})
    assert enabled.fingerprint != disabled.fingerprint
    with (
        duckdb.connect(str(enabled.database), read_only=True) as a,
        duckdb.connect(str(disabled.database), read_only=True) as b,
    ):
        assert (
            a.execute("SELECT * FROM staging.raw_rows ORDER BY record_id").fetchall()
            == b.execute("SELECT * FROM staging.raw_rows ORDER BY record_id").fetchall()
        )
        assert (
            a.execute("SELECT * FROM meta.facts ORDER BY fact_id").fetchall()
            == b.execute("SELECT * FROM meta.facts ORDER BY fact_id").fetchall()
        )
    assert enabled.catalog["text_integrity"]["accepted"] == 0


def test_readme_repair_candidate_never_replaces_original_quote(tmp_path):
    root, business = split(tmp_path)
    damaged = mojibake("Tiếng Việt")
    readme = business / "README.md"
    readme.write_text("# " + damaged + "\n", encoding="utf-8")
    raw_hash = sha256(readme.read_bytes()).hexdigest()
    (business / "sales.csv").write_text(
        "order_id,product_id,quantity,line_net_amount\nA,P1,2,100\n", encoding="utf-8"
    )
    result = prepare(root, tmp_path)
    with duckdb.connect(str(result.database), read_only=True) as db:
        quotes = db.execute(
            "SELECT quote FROM meta.clauses WHERE source='data/shop/README.md'"
        ).fetchall()
        assert any(damaged in quote for (quote,) in quotes)
    assert sha256(readme.read_bytes()).hexdigest() == raw_hash
    assert result.catalog["text_integrity"]["unresolved"] >= 1


def test_text_receipt_mutation_invalidates_audit(tmp_path):
    from datamate.preparation.audit import read_report

    root, business = split(tmp_path)
    (business / "README.md").write_text("# " + mojibake("Tiếng Việt") + "\n", encoding="utf-8")
    (business / "sales.csv").write_text(
        "order_id,product_id,quantity,line_net_amount\nA,P1,2,100\n", encoding="utf-8"
    )
    result = prepare(root, tmp_path)
    with duckdb.connect(str(result.database)) as db:
        read_report(db)
        assert db.execute("SELECT count(*) FROM meta.text_integrity").fetchone()[0] > 0
        db.execute("UPDATE meta.text_integrity SET proof_json='{}'")
        with pytest.raises(ValueError, match="stale|receipt|text"):
            read_report(db)


def test_calendar_text_and_executable_metadata_have_separate_policies(tmp_path):
    root, business = split(tmp_path)
    calendar = root / "calendar.csv"
    calendar.write_text(
        calendar.read_text(encoding="utf-8").replace(
            "false,\n", "false," + mojibake("Tiếng Việt") + "\n"
        ),
        encoding="utf-8",
    )
    book = Workbook()
    sheet = book.active
    sheet.append(["order_id", "product_id", "quantity", "line_net_amount", "note"])
    sheet.append(["A", "P1", 2, 100, '="FranÃ§ois"'])
    book.save(business / "sales.xlsx")
    result = prepare(root, tmp_path)
    with duckdb.connect(str(result.database), read_only=True) as db:
        assert db.execute("SELECT holiday FROM clean.calendar_days").fetchone()[0] == "Tiếng Việt"
        formula = db.execute(
            "SELECT formulas_json FROM staging.raw_rows r JOIN meta.source_tables t "
            "USING(table_id) WHERE t.source LIKE '%.xlsx' AND r.kind='data'"
        ).fetchone()[0]
        assert json.loads(formula)[4] == '="FranÃ§ois"'
        assert (
            db.execute(
                "SELECT count(*) FROM meta.text_integrity WHERE locator LIKE 'formula:%' "
                "AND status='UNRESOLVED'"
            ).fetchone()[0]
            > 0
        )
        assert db.execute("SELECT sum(line_net_amount) FROM clean.order_lines").fetchone()[0] == 100


def test_unused_damaged_header_does_not_block_safe_scope(tmp_path):
    root, business = split(tmp_path)
    book = Workbook()
    sheet = book.active
    sheet.append(["order_id", "product_id", "quantity", "line_net_amount", "note�"])
    sheet.append(["A", "P1", 2, 100, "safe"])
    book.save(business / "sales.xlsx")
    result = prepare(root, tmp_path)
    assert result.catalog["qualification"]["order_lines"] == "READY"
    assert result.catalog["text_integrity"]["lossy"] >= 1
