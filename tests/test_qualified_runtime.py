"""Qualified artifacts expose only declared, complete factual projections."""

import copy
import json

import duckdb
import pytest

from datamate.runtime.tools import StructuredTools


@pytest.fixture
def qualified(tmp_path):
    database = tmp_path / "qualified.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE SCHEMA clean; CREATE SCHEMA meta; CREATE SCHEMA staging")
        db.execute("CREATE TABLE staging.raw_rows(record_id VARCHAR,table_id VARCHAR)")
        db.execute("INSERT INTO staging.raw_rows VALUES ('record-1','source-1')")
        db.execute("CREATE TABLE meta.bindings(secret VARCHAR)")
        db.execute("CREATE TABLE private(secret VARCHAR)")
        db.execute(
            "CREATE TABLE clean.order_lines(fact_id VARCHAR,table_id VARCHAR,"
            "order_id VARCHAR,quantity INTEGER,blocked_amount INTEGER)"
        )
        db.execute("INSERT INTO clean.order_lines VALUES ('fact-1','source-1','order-1',2,999)")
        db.execute(
            "CREATE TABLE meta.projection_descriptors(scope VARCHAR,table_name VARCHAR,"
            "columns_json JSON,blocked_json JSON,row_count BIGINT,source_tables_json JSON)"
        )
        db.execute(
            "INSERT INTO meta.projection_descriptors VALUES "
            "('order_lines','candidate_order_lines',?, '[]',1,?)",
            [json.dumps(["order_id", "quantity"]), json.dumps(["source-1"])],
        )
        db.execute(
            "CREATE TABLE meta.projection_lineage(scope VARCHAR,fact_id VARCHAR,"
            "role VARCHAR,column_id VARCHAR,record_id VARCHAR)"
        )
        db.execute(
            "INSERT INTO meta.projection_lineage VALUES "
            "('order_lines','fact-1','order_id','c1','record-1'),"
            "('order_lines','fact-1','quantity','c2','record-1')"
        )
        db.execute("CREATE TABLE meta.qualification(scope VARCHAR,status VARCHAR)")
        db.execute("INSERT INTO meta.qualification VALUES ('order_lines','READY')")
        db.execute("CREATE TABLE clean.calendar_days(date DATE,record_id VARCHAR)")
        db.execute("INSERT INTO clean.calendar_days VALUES ('2026-01-01','calendar-1')")
    catalog = {
        "artifact_version": 2,
        "projections": [
            {
                "scope": "order_lines",
                "table_name": "order_lines",
                "columns": ["fact_id", "table_id", "order_id", "quantity"],
                "provenance_columns": ["fact_id", "table_id"],
                "source_tables": ["source-1"],
                "row_count": 1,
            }
        ],
        "qualification": {"order_lines": "READY", "order_headers": "UNRESOLVED"},
        "calendar": {
            "status": "QUALIFIED",
            "table_name": "calendar_days",
            "columns": ["date", "record_id"],
            "provenance_columns": ["record_id"],
            "source_sha256": "a" * 64,
            "calendar_sha256": "b" * 64,
            "rows": 1,
            "declarations": {"tet_interval": "UNRESOLVED"},
        },
        "tables": [
            {
                "table_id": "source-1",
                "source": "data/shop/orders.csv",
                "source_sha256": "c" * 64,
                "source_table": "orders",
                "columns": [
                    {"column_id": "c1", "source_header": {"value": "Order"}},
                    {"column_id": "c2", "source_header": {"value": "Quantity"}},
                ],
            }
        ],
        "rules": [],
    }
    return database, catalog


def test_runtime_denies_legacy_by_default(qualified):
    database, _ = qualified
    with pytest.raises(ValueError, match="version 2"):
        StructuredTools(database, {"tables": [], "rules": []})


def test_ready_flat_projection_is_discoverable(qualified):
    database, catalog = qualified
    tools = StructuredTools(database, catalog)
    assert {table["table_id"] for table in tools.list_tables()} == {
        "clean.order_lines",
        "clean.calendar_days",
    }
    description = tools.describe_table("clean.order_lines")
    assert [c["column_id"] for c in description["columns"]] == [
        "fact_id",
        "table_id",
        "order_id",
        "quantity",
    ]
    assert description["source_tables"] == ["source-1"]
    assert description["provenance_columns"] == ["fact_id", "table_id"]
    assert description["policy"]["measure_qualification"] == "UNRESOLVED"
    assert tools.search_schema("quantity")[0]["table_id"] == "clean.order_lines"
    assert tools.inspect_values("clean.order_lines", "quantity")["rows"] == [[2]]
    assert tools.search_data("clean.order_lines", "order_id", "order1")["rows"] == [["order-1"]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM staging.raw_rows",
        "SELECT * FROM meta.bindings",
        "SELECT * FROM private",
        "SELECT * FROM main.private",
        "SELECT * FROM source_1",
        "SELECT * FROM order_lines",
        'SELECT * FROM "clean.order_lines"',
        "WITH private AS (SELECT * FROM private) SELECT * FROM private",
        "WITH order_lines AS (SELECT * FROM meta.bindings) SELECT * FROM order_lines",
        "SELECT * FROM duckdb_tables()",
        "SELECT * FROM read_csv('secret.csv')",
        "CREATE VIEW x AS SELECT * FROM clean.order_lines",
    ],
)
def test_runtime_denies_private_sources_and_runtime_ddl(qualified, sql):
    database, catalog = qualified
    tools = StructuredTools(database, catalog)
    with pytest.raises(ValueError):
        tools.execute_sql(sql)
    assert tools.executions == []


def test_star_cannot_expose_blocked_amount(qualified):
    database, catalog = qualified
    tools = StructuredTools(database, catalog)
    for sql in (
        "SELECT * FROM clean.order_lines",
        "WITH s AS (SELECT * FROM clean.order_lines) SELECT s.* FROM s",
    ):
        result = tools.execute_sql(sql)
        assert result["columns"] == ["fact_id", "table_id", "order_id", "quantity"]
        assert result["rows"] == [["fact-1", "source-1", "order-1", 2]]
    for sql in (
        "SELECT blocked_amount FROM clean.order_lines",
        "SELECT SUM(blocked_amount) FROM clean.order_lines WHERE quantity>0",
        "WITH s AS (SELECT * FROM clean.order_lines) SELECT blocked_amount FROM s",
    ):
        with pytest.raises(ValueError):
            tools.execute_sql(sql)


def test_partial_total_does_not_become_complete_after_view_filter(qualified):
    database, catalog = qualified
    catalog["qualification"]["order_lines"] = "UNRESOLVED"
    tools = StructuredTools(database, catalog)
    assert "clean.order_lines" not in tools.tables
    with pytest.raises(ValueError):
        tools.execute_sql("SELECT SUM(quantity) FROM clean.order_lines WHERE quantity IS NOT NULL")
    with pytest.raises(ValueError):
        tools.describe_table("clean.order_lines")


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_tables", []),
        ("source_tables", ["unknown-source"]),
        ("provenance_columns", []),
        ("columns", ["fact_id", "table_id", "blocked_amount"]),
        ("row_count", 10),
        ("table_name", "meta.bindings"),
    ],
)
def test_ready_catalog_requires_persisted_columns_and_source_provenance(qualified, field, value):
    database, catalog = qualified
    catalog["projections"][0][field] = value
    with pytest.raises(ValueError):
        StructuredTools(database, catalog)


def test_ready_receipt_cannot_disagree_with_database(qualified):
    database, catalog = qualified
    with duckdb.connect(str(database)) as db:
        db.execute("UPDATE meta.qualification SET status='UNRESOLVED'")
    with pytest.raises(ValueError):
        StructuredTools(database, catalog)


def test_missing_lineage_cannot_qualify_projection(qualified):
    database, catalog = qualified
    with duckdb.connect(str(database)) as db:
        db.execute("DELETE FROM meta.projection_lineage WHERE role='quantity'")
    with pytest.raises(ValueError):
        StructuredTools(database, catalog)


def test_calendar_access_requires_qualified_receipt(qualified):
    database, catalog = qualified
    catalog["calendar"]["status"] = "UNRESOLVED"
    tools = StructuredTools(database, catalog)
    assert "clean.calendar_days" not in tools.tables
    with pytest.raises(ValueError):
        tools.execute_sql("SELECT * FROM clean.calendar_days")
    assert tools.execute_sql("SELECT SUM(quantity) FROM clean.order_lines")["rows"] == [[2]]


def test_runtime_opens_artifact_read_only_without_creating_relations(qualified, monkeypatch):
    database, catalog = qualified
    connect = duckdb.connect
    opened = []

    def read_only(*args, **kwargs):
        opened.append(kwargs.get("read_only"))
        return connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", read_only)
    tools = StructuredTools(database, catalog)
    assert opened and all(opened)
    assert tools.execute_sql("SELECT quantity FROM clean.order_lines")["rows"] == [[2]]
    with connect(str(database), read_only=True) as db:
        assert db.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='clean'"
        ).fetchone() == (2,)


def test_v2_cannot_bypass_qualification_with_fixture_flag(qualified):
    database, catalog = qualified
    changed = copy.deepcopy(catalog)
    changed["projections"][0]["source_tables"] = []
    with pytest.raises(ValueError):
        StructuredTools(database, changed, allow_legacy_fixture=True)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT t FROM clean.order_lines t",
        "SELECT order_lines FROM clean.order_lines",
        "SELECT t.blocked_amount FROM (SELECT t FROM clean.order_lines t) q",
        "WITH s AS (SELECT t AS row_value FROM clean.order_lines t) SELECT * FROM s",
    ],
)
def test_whole_row_references_cannot_expose_withheld_columns(qualified, sql):
    database, catalog = qualified
    tools = StructuredTools(database, catalog)
    with pytest.raises(ValueError):
        tools.execute_sql(sql)


def test_ready_scope_cannot_be_rebound_to_another_clean_relation(qualified):
    database, catalog = qualified
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE clean.private_lines AS SELECT * FROM clean.order_lines")
    catalog["projections"][0]["table_name"] = "private_lines"
    with pytest.raises(ValueError):
        StructuredTools(database, catalog)


def test_qualified_calendar_query_is_not_business_data_evidence(qualified):
    database, catalog = qualified
    tools = StructuredTools(database, catalog)
    assert tools.execute_sql("SELECT date FROM clean.calendar_days")["rows"] == [["2026-01-01"]]
    assert tools.executions[-1]["business_data"] is False
    assert tools.execute_sql("SELECT quantity FROM clean.order_lines")["rows"] == [[2]]
    assert tools.executions[-1]["business_data"] is True
