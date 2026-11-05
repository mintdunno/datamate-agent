"""Immutable raw staging and separate candidate accounting, owned by the build transaction."""

import hashlib
import json
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from itertools import islice

from duckdb import DuckDBPyConnection

from datamate.preparation.models import (
    CandidateFact,
    LineageEdge,
    RawDisposition,
    RawRecord,
    SourceFile,
    SourceRow,
    SourceTable,
    StagedTable,
)
from datamate.preparation.normalization import encoded

RAW_STORAGE_VERSION = "raw-v2-1"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _decoded(value: dict):
    kind, raw = value["type"], value["value"]
    converters = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "Decimal": Decimal,
        "bytes": bytes.fromhex,
        "date": date.fromisoformat,
        "datetime": datetime.fromisoformat,
        "time": time.fromisoformat,
    }
    if kind == "NoneType":
        return None
    if kind not in converters:
        raise ValueError("unsupported raw type tag")
    return converters[kind](raw)


def create_storage(db: DuckDBPyConnection) -> None:
    db.execute("CREATE SCHEMA staging")
    db.execute("CREATE SCHEMA meta")
    db.execute("CREATE SCHEMA clean")
    db.execute("CREATE TABLE meta.storage_version(version VARCHAR PRIMARY KEY)")
    db.execute("INSERT INTO meta.storage_version VALUES (?)", [RAW_STORAGE_VERSION])
    db.execute("""CREATE TABLE meta.source_tables(
        table_id VARCHAR PRIMARY KEY, source VARCHAR NOT NULL, source_sha256 VARCHAR NOT NULL,
        table_name VARCHAR NOT NULL, header_row BIGINT, columns_json JSON NOT NULL,
        row_count BIGINT NOT NULL, raw_record_count BIGINT NOT NULL)""")
    db.execute("""CREATE TABLE staging.raw_rows(
        record_id VARCHAR PRIMARY KEY, table_id VARCHAR NOT NULL, ordinal BIGINT NOT NULL,
        source_row BIGINT NOT NULL, kind VARCHAR NOT NULL,
        values_json JSON NOT NULL, formulas_json JSON NOT NULL, formats_json JSON NOT NULL,
        record_key_json JSON, UNIQUE(table_id,ordinal))""")
    db.execute("""CREATE TABLE meta.dispositions(
        record_id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL
        CHECK(kind IN ('retained','structural','duplicate','quarantined','unsupported')),
        reason VARCHAR NOT NULL, evidence_json JSON NOT NULL)""")
    # Candidate payloads are build metadata, never query-ready clean factual storage.
    # Task14 materializes typed canonical tables before any qualified publication.
    db.execute("""CREATE TABLE meta.facts(
        fact_id VARCHAR PRIMARY KEY, table_role VARCHAR NOT NULL, values_json JSON NOT NULL)""")
    db.execute("""CREATE TABLE meta.lineage(
        fact_id VARCHAR, record_id VARCHAR, column_id VARCHAR, step_id VARCHAR,
        PRIMARY KEY(fact_id,record_id,column_id,step_id))""")


def stage_table(db: DuckDBPyConnection, source: SourceFile, table: SourceTable) -> StagedTable:
    columns = tuple(f"c{i + 1:04d}" for i in range(len(table.headers)))
    if not columns:
        raise ValueError("source has no columns")
    identity = _json(
        [
            RAW_STORAGE_VERSION,
            source.relative_path,
            source.sha256,
            table.name,
            [encoded(v) for v in table.headers],
        ]
    )
    table_id = "t_" + hashlib.sha256(identity.encode()).hexdigest()

    def records():
        for row in table.preamble:
            yield "preamble", row
        if table.header_row is not None:
            yield "header", SourceRow(table.header_row, table.headers)
        for row in table.rows:
            if len(row.values) != len(columns):
                raise ValueError(f"ragged source row {row.number}")
            yield "data", row

    raw_count = data_count = 0
    iterator = iter(records())
    while batch := list(islice(iterator, 1000)):
        payloads = []
        for kind, row in batch:
            raw_count += 1
            data_count += kind == "data"
            record_id = f"{table_id}:{raw_count}"
            payloads.append(
                (
                    record_id,
                    table_id,
                    raw_count,
                    row.number,
                    kind,
                    _json([encoded(v) for v in row.values]),
                    _json(row.formulas),
                    _json(row.formats),
                    None
                    if row.record_key is None
                    else _json({key: encoded(v) for key, v in row.record_key.items()}),
                )
            )
        db.executemany("INSERT INTO staging.raw_rows VALUES (?,?,?,?,?,?,?,?,?)", payloads)
    db.execute(
        "INSERT INTO meta.source_tables VALUES (?,?,?,?,?,?,?,?)",
        [
            table_id,
            source.relative_path,
            source.sha256,
            table.name,
            table.header_row,
            _json(columns),
            data_count,
            raw_count,
        ],
    )
    return StagedTable(
        table_id,
        source,
        source.sha256,
        table.name,
        table.header_row,
        columns,
        data_count,
        raw_count,
    )


def iter_raw_batches(
    db: DuckDBPyConnection, table_id: str, batch_size: int
) -> Iterator[list[RawRecord]]:
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch size must be a positive integer")
    # Keyset batches share the caller's transaction and tolerate queries between yields.
    # DuckDB cursor() creates a separate connection that cannot see uncommitted staging.
    ordinal = 0
    while True:
        rows = db.execute(
            "SELECT record_id,source_row,kind,values_json,formulas_json,"
            "formats_json,record_key_json,ordinal FROM staging.raw_rows "
            "WHERE table_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
            [table_id, ordinal, batch_size],
        ).fetchall()
        if not rows:
            return
        ordinal = rows[-1][-1]
        yield [
            RawRecord(
                identifier,
                table_id,
                SourceRow(
                    number,
                    tuple(_decoded(v) for v in json.loads(values)),
                    tuple(json.loads(formulas)),
                    tuple(json.loads(formats)),
                    None if key is None else {k: _decoded(v) for k, v in json.loads(key).items()},
                ),
                kind,
            )
            for identifier, number, kind, values, formulas, formats, key, _ in rows
        ]


def store_disposition(db: DuckDBPyConnection, disposition: RawDisposition) -> None:
    if not db.execute(
        "SELECT 1 FROM staging.raw_rows WHERE record_id=?", [disposition.record_id]
    ).fetchone():
        raise ValueError("disposition references unknown raw record")
    db.execute(
        "INSERT INTO meta.dispositions VALUES (?,?,?,?)",
        [
            disposition.record_id,
            disposition.kind,
            disposition.reason,
            _json([e.model_dump(mode="json") for e in disposition.evidence_refs]),
        ],
    )


def store_fact(db: DuckDBPyConnection, fact: CandidateFact) -> None:
    db.execute(
        "INSERT INTO meta.facts VALUES (?,?,?)",
        [fact.fact_id, fact.table_role, _json({key: encoded(v) for key, v in fact.values.items()})],
    )


def store_lineage(db: DuckDBPyConnection, edge: LineageEdge) -> None:
    if not db.execute("SELECT 1 FROM meta.facts WHERE fact_id=?", [edge.fact_id]).fetchone():
        raise ValueError("lineage references unknown fact")
    raw = db.execute(
        "SELECT r.values_json,t.columns_json FROM staging.raw_rows r "
        "JOIN meta.source_tables t ON r.table_id=t.table_id WHERE r.record_id=?",
        [edge.record_id],
    ).fetchone()
    if raw is None:
        raise ValueError("lineage references unknown raw record")
    columns = json.loads(raw[1])
    if edge.column_id not in columns or columns.index(edge.column_id) >= len(json.loads(raw[0])):
        raise ValueError("lineage references unknown source column")
    db.execute(
        "INSERT INTO meta.lineage VALUES (?,?,?,?)",
        [edge.fact_id, edge.record_id, edge.column_id, edge.step_id],
    )
