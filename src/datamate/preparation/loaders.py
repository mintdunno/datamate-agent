"""Read source tables without modifying or executing their contents."""

import csv
import sqlite3
from collections.abc import Iterator
from itertools import chain, islice
from pathlib import Path

from openpyxl import load_workbook

from datamate.preparation.models import SourceRow, SourceTable


def load_tables(path: Path) -> Iterator[SourceTable]:
    """Yield tables whose rows must be consumed before advancing the iterator."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield from _csv_tables(path)
    elif suffix == ".xlsx":
        yield from _excel_tables(path)
    elif suffix == ".sqlite":
        yield from _sqlite_tables(path)
    else:
        raise ValueError(f"Unsupported source suffix: {suffix}")


def _csv_tables(path: Path) -> Iterator[SourceTable]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        sample = source.read(65536)
        source.seek(0)
        if not sample:
            raise ValueError(f"Empty CSV file: {path}")
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
        try:
            headers = tuple(next(csv.reader(source, delimiter=delimiter, strict=True), ()))
        except csv.Error as exc:
            raise ValueError(f"Invalid CSV header in {path}: {exc}") from exc

    def rows() -> Iterator[SourceRow]:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, delimiter=delimiter, strict=True)
            next(reader, None)
            number = 1
            try:
                for number, values in enumerate(reader, 2):
                    if len(values) != len(headers):
                        raise ValueError(
                            f"Ragged CSV record {number} in {path}: expected "
                            f"{len(headers)} fields, got {len(values)}"
                        )
                    yield SourceRow(number, tuple(values))
            except csv.Error as exc:
                raise ValueError(f"Invalid CSV record {number + 1} in {path}: {exc}") from exc

    yield SourceTable(path.stem, headers, 1, rows())


def _excel_tables(path: Path) -> Iterator[SourceTable]:
    formulas = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        values = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in formulas.worksheets:
                cached = values[sheet.title]
                sheet.reset_dimensions()
                cached.reset_dimensions()

                def converted(sheet=sheet, cached=cached) -> Iterator[SourceRow]:
                    for number, (raw_row, value_row) in enumerate(
                        zip(sheet.iter_rows(), cached.iter_rows(), strict=True), 1
                    ):
                        yield SourceRow(
                            number,
                            tuple(v.value for v in value_row),
                            tuple(c.value if c.data_type == "f" else None for c in raw_row),
                            tuple(c.number_format for c in raw_row),
                        )

                iterator = converted()
                initial = list(islice(iterator, 20))

                def strings(row: SourceRow) -> list[str]:
                    return [v for v in row.values if isinstance(v, str) and v.strip()]

                candidates = [
                    i
                    for i, row in enumerate(initial)
                    if len(strings(row)) >= 2
                    and all(v is None or isinstance(v, str) for v in row.values)
                ]
                if not candidates:
                    candidates = [
                        i
                        for i, row in enumerate(initial)
                        if len(strings(row)) == 1
                        and all(v is None or isinstance(v, str) for v in row.values)
                    ]
                if not candidates:
                    raise ValueError(f"Unable to detect header in sheet {sheet.title!r}")
                index = candidates[0]
                header = initial[index]

                def padded(rows, width):
                    for row in rows:
                        missing = max(0, width - len(row.values))
                        yield SourceRow(
                            row.number,
                            row.values + (None,) * missing,
                            row.formulas + (None,) * missing,
                            row.formats + (None,) * missing,
                        )

                yield SourceTable(
                    sheet.title,
                    header.values,
                    header.number,
                    padded(chain(initial[index + 1 :], iterator), len(header.values)),
                    tuple(initial[:index]),
                )
        finally:
            values.close()
    finally:
        formulas.close()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sqlite_tables(path: Path) -> Iterator[SourceTable]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.enable_load_extension(False)
        schemas = sorted(
            (
                row
                for row in connection.execute("PRAGMA table_list")
                if row[0] == "main" and not row[1].startswith("sqlite_")
            ),
            key=lambda row: row[1],
        )
        for _, name, kind, _, _, _ in schemas:
            if kind == "virtual":
                raise ValueError(f"Unsupported SQLite virtual table: {name}")
        for _, name, kind, _, without_rowid, _ in schemas:
            if kind != "table":
                continue
            identifier = _quote_identifier(name)
            columns = [
                c for c in connection.execute(f"PRAGMA table_xinfo({identifier})") if c[6] != 1
            ]
            headers = tuple(column[1] for column in columns)
            names_lower = {column.lower() for column in headers}
            rowid = next((n for n in ("rowid", "_rowid_", "oid") if n not in names_lower), None)
            if rowid and not without_rowid:
                order = rowid
            else:
                primary = sorted((c for c in columns if c[5]), key=lambda c: c[5])
                # All columns provide a deterministic fallback if rowid is shadowed.
                order = ", ".join(_quote_identifier(c[1]) for c in (primary or columns))
            cursor = connection.execute(f"SELECT * FROM {identifier} ORDER BY {order}")

            def rows(cursor=cursor) -> Iterator[SourceRow]:
                number = 0
                while batch := cursor.fetchmany(512):
                    for record in batch:
                        number += 1
                        yield SourceRow(number, tuple(record))

            yield SourceTable(name, headers, None, rows())
    finally:
        connection.close()
