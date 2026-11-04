import sqlite3
from datetime import datetime

import pytest
from openpyxl import Workbook

from datamate.preparation.loaders import load_tables


def test_csv_streams_bom_delimiter_and_newlines(tmp_path):
    path = tmp_path / "input.csv"
    path.write_text(
        "\ufeffname;note\n" + 'a;"hello\nworld"\n' + "x;\n" * 25, encoding="utf-8", newline=""
    )
    table = next(load_tables(path))
    rows = list(table.rows)
    assert table.headers == ("name", "note")
    assert table.header_row == 1
    assert len(rows) == 26
    assert rows[0].values == ("a", "hello\nworld")
    assert rows[1].number == 3 and rows[1].values == ("x", "")


def test_csv_rejects_ragged_record(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2,3\n", encoding="utf-8")
    table = next(load_tables(path))
    with pytest.raises(ValueError, match="2"):
        list(table.rows)


def test_excel_preamble_sheets_sparse_dates_and_formula(tmp_path):
    path = tmp_path / "book.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "First"
    sheet.append(["Title"])
    sheet.append([])
    sheet.append(["Name", "Value", "Flag"])
    sheet.append(["a", datetime(2024, 1, 2), True])
    sheet.append([])
    sheet.append(["b", "=1+1", None])
    second = book.create_sheet("Second")
    second.append(["Key", "Data"])
    second.append(["x", 7])
    book.save(path)
    tables = load_tables(path)
    first = next(tables)
    assert first.header_row == 3 and first.headers == ("Name", "Value", "Flag")
    assert [r.number for r in first.preamble] == [1, 2]
    rows = list(first.rows)
    assert rows[0].values == ("a", datetime(2024, 1, 2), True)
    assert rows[1].values == (None, None, None)
    assert rows[2].values[1] is None and rows[2].formulas[1] == "=1+1"
    assert rows[0].formats[1]
    assert list(next(tables).rows)[0].values == ("x", 7)


def test_sqlite_tables_read_only_and_quoted(tmp_path):
    path = tmp_path / "source.sqlite"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "odd""name" (id INTEGER PRIMARY KEY, value TEXT)')
    con.execute('INSERT INTO "odd""name" VALUES (2, ?)', ("two",))
    con.execute('INSERT INTO "odd""name" VALUES (1, ?)', ("one",))
    con.execute("CREATE TABLE empty (name TEXT)")
    con.commit()
    con.close()
    before = path.read_bytes()
    result = {}
    for table in load_tables(path):
        result[table.name] = (table.headers, table.header_row, list(table.rows))
    assert result["empty"] == (("name",), None, [])
    assert [r.values for r in result['odd"name'][2]] == [(1, "one"), (2, "two")]
    assert path.read_bytes() == before


def test_unknown_suffix_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsupported|Unsupported"):
        list(load_tables(tmp_path / "x.txt"))


def test_sqlite_generated_columns_and_without_rowid_order(tmp_path):
    path = tmp_path / "generated.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE t (k TEXT PRIMARY KEY, v INT, "
        "doubled INT GENERATED ALWAYS AS (v*2)) WITHOUT ROWID"
    )
    con.executemany("INSERT INTO t(k,v) VALUES (?,?)", [("z", 3), ("a", 2)])
    con.commit()
    con.close()
    tables = load_tables(path)
    table = next(tables)
    assert table.headers == ("k", "v", "doubled")
    assert [row.values for row in table.rows] == [("a", 2, 4), ("z", 3, 6)]


def test_sqlite_virtual_table_rejected(tmp_path):
    path = tmp_path / "virtual.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE /* comment */ VIRTUAL TABLE t USING fts5(content)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="virtual"):
        list(load_tables(path))


def test_excel_all_rows_1904_epoch_and_wrong_dimensions(tmp_path):
    import zipfile

    from openpyxl.utils.datetime import CALENDAR_MAC_1904

    path = tmp_path / "dimension.xlsx"
    book = Workbook()
    book.epoch = CALENDAR_MAC_1904
    sheet = book.active
    sheet.append(["Name", "Date"])
    for i in range(25):
        sheet.append([str(i), datetime(2020, 1, 2)])
    book.save(path)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["xl/worksheets/sheet1.xml"] = files["xl/worksheets/sheet1.xml"].replace(
        b'<dimension ref="A1:B26"', b'<dimension ref="A1:A1"'
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    tables = load_tables(path)
    table = next(tables)
    rows = list(table.rows)
    assert len(rows) == 25
    assert rows[-1].values == ("24", datetime(2020, 1, 2))


def test_excel_single_column_and_invalid_sheets(tmp_path):
    path = tmp_path / "single.xlsx"
    book = Workbook()
    book.active.append(["Name"])
    book.active.append(["Alice"])
    book.save(path)
    tables = load_tables(path)
    table = next(tables)
    assert table.headers == ("Name",)
    assert list(table.rows)[0].values == ("Alice",)
    for value in (None, 42):
        book = Workbook()
        if value is not None:
            book.active.append([value])
        book.save(path)
        with pytest.raises(ValueError, match="header"):
            list(load_tables(path))


def test_csv_rejects_empty_and_unterminated_quotes(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        list(load_tables(path))
    path.write_text('a,b\n1,"broken', encoding="utf-8")
    tables = load_tables(path)
    table = next(tables)
    with pytest.raises(ValueError, match="record|CSV"):
        list(table.rows)
