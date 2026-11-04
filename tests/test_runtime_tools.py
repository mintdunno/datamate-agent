import duckdb
import pytest

from datamate.runtime.tools import StructuredTools, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH 'x' AS x",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM other",
        "SELECT 1;SELECT 2",
        "COPY (SELECT 1) TO 'x'",
        "SELECT getenv('SECRET')",
        "SELECT * FROM range(1000000000)",
        "SELECT * FROM 'secret.csv'",
        "WITH raw_records AS (SELECT * FROM raw_records) SELECT * FROM raw_records",
        "WITH sales AS (SELECT * FROM raw_records) SELECT * FROM sales",
        "SELECT * FROM (PIVOT raw_records ON x USING sum(y))",
        "SELECT * FROM (UNPIVOT raw_records ON x,y INTO NAME key VALUE val)",
        "SELECT * FROM (SUMMARIZE raw_records)",
        "SELECT * FROM (DESCRIBE raw_records)",
    ],
)
def test_sql_rejects_unsafe_statements_and_sources(sql):
    with pytest.raises(ValueError):
        validate_sql(sql, {"sales"})


def test_sql_allows_analytical_cte_and_worker_caps(tmp_path):
    path = tmp_path / "db.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(name VARCHAR,amount INTEGER)")
        db.execute("INSERT INTO sales VALUES ('A',10),('B',20),('C',30)")
    catalog = {"tables": [{"table_id": "sales", "columns": []}], "rules": []}
    tools = StructuredTools(path, catalog, row_limit=2)
    result = tools.execute_sql("WITH s AS (SELECT * FROM sales) SELECT * FROM s ORDER BY amount")
    assert result["rows"] == [["A", 10], ["B", 20]]
    assert result["truncated"] is True
    assert tools.execute_sql("SELECT sum(amount) AS total FROM sales")["rows"] == [[60]]
    with pytest.raises(ValueError):
        tools.execute_sql("DELETE FROM sales")


def test_value_search_quotes_and_rule_paging(tmp_path):
    path = tmp_path / "db.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(name VARCHAR)")
        db.execute("INSERT INTO sales VALUES ('alpha'),('beta'),('O''Brien')")
    columns = [{"column_id": "name", "source_header": {"value": "customer"}}]
    rules = [
        {
            "rule_id": str(n),
            "source": "rulebook.md",
            "line_start": n,
            "line_end": n,
            "text": "x" * 2500,
        }
        for n in range(4)
    ]
    tools = StructuredTools(
        path, {"tables": [{"table_id": "sales", "columns": columns}], "rules": rules}
    )
    assert tools.search_data("sales", "name", "O'Brien")["rows"][0] == ["O'Brien"]
    assert tools.search_data("sales", "name", "alpa", limit=1)["rows"] == [["alpha"]]
    assert tools.executions[-1]["elapsed_ms"] > 0
    page = tools.get_rules()
    assert page["next_offset"] == 2 and page["omitted"] == 2
    assert tools.get_rules(offset=2)["next_offset"] is None


def test_worker_deadline_and_oversized_column_metadata(tmp_path):
    path = tmp_path / "db.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(n INTEGER)")
    tools = StructuredTools(
        path, {"tables": [{"table_id": "sales", "columns": []}], "rules": []}, timeout=0.001
    )
    with pytest.raises(TimeoutError):
        tools.execute_sql("SELECT * FROM sales")
    tools.timeout = 15
    tools.byte_limit = 100
    assert "error" in tools.execute_sql('SELECT 1 AS "' + "a" * 200 + '" FROM sales')


@pytest.mark.parametrize(
    "expression",
    [
        "strptime('01/02/2026','%d/%m/%Y')",
        "strftime(DATE '2026-02-01','%Y-%m')",
        "date_trunc('month',DATE '2026-02-03')",
        "date_diff('day',DATE '2026-02-01',DATE '2026-02-03')",
    ],
)
def test_safe_date_functions_use_sqlglot_canonical_names(expression):
    validate_sql("SELECT " + expression, set())
