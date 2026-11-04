import pytest
from openpyxl import Workbook

from datamate.preparation import pipeline
from datamate.preparation.loaders import load_tables


def test_excel_late_columns_preserve_data_and_width(tmp_path):
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    sheet = wb.active
    sheet.append(["Title"])
    sheet.merge_cells("A1:D1")
    sheet.append(["Name", "Value"])
    sheet.append(["a", 1])
    sheet.cell(row=30, column=5, value="late")
    wb.save(path)
    wb.close()
    tables = load_tables(path)
    table = next(tables)
    rows = list(table.rows)
    tables.close()
    assert table.header_row == 2
    assert table.headers == ("Name", "Value", None, None, None)
    assert all(len(row.values) == 5 for row in rows)
    assert rows[-1].values[-1] == "late"
    assert table.preamble[0].values[0] == "Title"


def test_exception_during_ingestion_closes_excel_streams(tmp_path, monkeypatch):
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    wb.active.append(["Name", "Value"])
    wb.active.append(["a", 1])
    wb.save(path)
    wb.close()
    from datamate.preparation import loaders

    original = loaders.load_workbook
    opened = []

    def load(*args, **kwargs):
        workbook = original(*args, **kwargs)
        opened.append(workbook)
        return workbook

    monkeypatch.setattr(loaders, "load_workbook", load)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(pipeline, "_ingest_table", fail)
    from types import SimpleNamespace

    source = SimpleNamespace(path=path, relative_path="data/source.xlsx", sha256="x")
    inputs = SimpleNamespace(sources=[source], documents=[])
    with pytest.raises(RuntimeError, match="synthetic failure"):
        pipeline._build_database(tmp_path / "test.duckdb", inputs, {})
    assert len(opened) == 2
    assert all(workbook._archive.fp is None for workbook in opened)
    path.rename(tmp_path / "renamed.xlsx")


def test_pipeline_closes_table_generator_on_ingestion_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    original = pipeline.load_tables
    retained = []

    def capture(path):
        stream = original(path)
        retained.append(stream)
        return stream

    monkeypatch.setattr(pipeline, "load_tables", capture)
    monkeypatch.setattr(
        pipeline,
        "_ingest_table",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("failure")),
    )
    path = tmp_path / "source.xlsx"
    wb = Workbook()
    wb.active.append(["A", "B"])
    wb.save(path)
    wb.close()
    inputs = SimpleNamespace(
        sources=[SimpleNamespace(path=path, relative_path="source.xlsx", sha256="x")], documents=[]
    )
    with pytest.raises(RuntimeError):
        pipeline._build_database(tmp_path / "test.duckdb", inputs, {})
    assert retained[0].gi_frame is None


def test_header_prefers_richest_text_row_and_preserves_metadata(tmp_path):
    path = tmp_path / "metadata.xlsx"
    wb = Workbook()
    sheet = wb.active
    sheet.append(["Order status", "Approved"])
    sheet.append([])
    headers = [f"Column {i}" for i in range(1, 10)]
    sheet.append(headers)
    sheet.append([f"value {i}" for i in range(1, 10)])
    wb.save(path)
    wb.close()
    tables = load_tables(path)
    table = next(tables)
    rows = list(table.rows)
    tables.close()
    assert table.header_row == 3
    assert table.headers == tuple(headers)
    assert [row.number for row in table.preamble] == [1, 2]
    assert table.preamble[0].values[:2] == ("Order status", "Approved")
    assert rows[0].number == 4
    assert rows[0].values == tuple(f"value {i}" for i in range(1, 10))
