"""Qualified source observations preserve rows while withholding uncertain fields."""

import csv

import pytest

from datamate.preparation.pipeline import prepare_business
from datamate.preparation.v2 import PreparationPolicy
from datamate.runtime.tools import StructuredTools


@pytest.fixture(scope="module")
def source_world(tmp_path_factory):
    folder = tmp_path_factory.mktemp("source-fields")
    split = folder / "train"
    shop = split / "data/shop"
    shop.mkdir(parents=True)
    (split / "rulebook.md").write_text("# Rules\nPreserve uncertain values.\n", encoding="utf-8")
    (split / "calendar.csv").write_text(
        "date,weekday,lunar_day,lunar_month,lunar_year,lunar_leap,holiday\n"
        "2025-01-01,3,2,12,2024,false,\n",
        encoding="utf-8",
    )
    (shop / "README.md").write_text(
        "# Source\nEach row is one recorded sales line.\n", encoding="utf-8"
    )
    with (shop / "sales.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(
            [
                ["order_id", "product_name", "quantity", "unit", "line_net_amount", "unit_price"],
                ["O1", "Alpha", "2", "viên", "200", "100000"],
                ["O2", "Beta", "3", "hộp", "", "100,000"],
            ]
        )
    prepared = prepare_business(
        split,
        "shop",
        folder / "cache",
        split_id="train",
        policy=PreparationPolicy(readme_roles=True),
    )
    return prepared


def source_projection(prepared):
    return next(p for p in prepared.catalog["projections"] if p.get("kind") == "source_fields")


def test_source_fields_are_usable_without_a_product_identity(source_world):
    projection = source_projection(source_world)
    assert source_world.catalog["qualification"]["order_lines"] == "UNRESOLVED"
    assert projection["row_count"] == 2
    tools = StructuredTools(source_world.database, source_world.catalog)
    name = "clean." + projection["table_name"]
    assert tools.execute_sql(f"SELECT count(*) FROM {name}")["rows"] == [[2]]
    rows = tools.execute_sql(f"SELECT c0004,sum(c0003) FROM {name} GROUP BY c0004 ORDER BY c0004")[
        "rows"
    ]
    assert rows == [["hộp", "3"], ["viên", "2"]]


def test_null_or_ambiguous_money_is_wholly_withheld_without_losing_rows(source_world):
    projection = source_projection(source_world)
    assert "c0005" not in projection["columns"]
    assert "c0006" not in projection["columns"]
    assert {"c0005", "c0006"} <= set(projection["source_field_policy"]["withheld_fields"])
    assert projection["source_field_policy"]["declared_data_rows"] == 2
    tools = StructuredTools(source_world.database, source_world.catalog)
    response = tools.execute_sql(f"SELECT * FROM clean.{projection['table_name']}")
    assert len(response["rows"]) == 2


@pytest.mark.parametrize(
    "template",
    [
        "SELECT sum(c0003) FROM {t}",
        "SELECT sum(TRY_CAST(REPLACE(c0002,'A','1') AS INTEGER)) FROM {t}",
        "SELECT a.c0003 FROM {t} a JOIN {t} b ON a.fact_id=b.fact_id",
        "SELECT a.c0003 FROM {t} a, {t} b",
        "WITH q AS (SELECT c0003 FROM {t}) SELECT sum(c0003) FROM q",
        "SELECT sum(x) FROM (SELECT c0003 AS x FROM {t}) q",
        "SELECT c0006 FROM {t}",
        "SELECT sum(c0003) FROM {t} WHERE c0004='viên' OR c0003>0",
        "SELECT sum(c0003) FROM {t} WHERE c0004=1",
        "SELECT sum(c0003) FROM {t} WHERE c0004=?",
        "SELECT sum(c0003) FROM {t} GROUP BY lower(c0004)",
        "SELECT sum(c0002 * 1) FROM {t}",
        "SELECT sum(c0002::DECIMAL) FROM {t}",
        "SELECT sum(coalesce(c0002,'0')) FROM {t}",
        "SELECT sum(c0003) OVER () FROM {t}",
        "SELECT sum(c0003) FILTER (WHERE c0004='viên') FROM {t}",
    ],
)
def test_source_sql_cannot_bypass_field_or_relationship_guards(source_world, template):
    projection = source_projection(source_world)
    tools = StructuredTools(source_world.database, source_world.catalog)
    with pytest.raises(ValueError):
        tools.execute_sql(template.format(t="clean." + projection["table_name"]))


def test_explicit_single_recorded_unit_filter_is_accepted(source_world):
    projection = source_projection(source_world)
    tools = StructuredTools(source_world.database, source_world.catalog)
    name = "clean." + projection["table_name"]
    assert tools.execute_sql(f"SELECT sum(c0003) FROM {name} WHERE c0004='viên'")["rows"] == [["2"]]


@pytest.mark.parametrize("change", ["candidate", "policy", "profile", "view"])
def test_source_receipt_and_view_tampering_is_rejected(source_world, tmp_path, change):
    import json
    import shutil

    import duckdb

    projection = source_projection(source_world)
    database = tmp_path / "tampered.duckdb"
    shutil.copy2(source_world.database, database)
    scope = projection["scope"]
    with duckdb.connect(str(database)) as db:
        if change == "candidate":
            db.execute(f'UPDATE meta."candidate_{scope}" SET c0003=999')
        elif change == "policy":
            row = db.execute(
                "SELECT metadata_json FROM meta.source_field_descriptors WHERE scope=?", [scope]
            ).fetchone()
            metadata = json.loads(row[0])
            metadata["joins"] = "allowed"
            db.execute(
                "UPDATE meta.source_field_descriptors SET metadata_json=? WHERE scope=?",
                [json.dumps(metadata), scope],
            )
        elif change == "profile":
            # Change an actual persisted profile property, independent of sampled text.
            db.execute(
                "UPDATE meta.profiles SET profile=json_merge_patch(profile, "
                '\'{"profile_version":"corrupted"}\')'
            )
        else:
            db.execute(f'DROP VIEW clean."{scope}"')
            db.execute(
                f'CREATE VIEW clean."{scope}" AS SELECT * FROM meta."candidate_{scope}" '
                "WHERE c0003>2"
            )
    with pytest.raises(ValueError):
        StructuredTools(database, source_world.catalog)


def test_model_metadata_keeps_proofs_private_and_headers_readable(source_world):
    import json

    projection = source_projection(source_world)
    tools = StructuredTools(source_world.database, source_world.catalog)
    description = tools.describe_table("clean." + projection["scope"])
    quantity = next(c for c in description["columns"] if c["column_id"] == "c0003")
    assert quantity["role"] == "quantity"
    assert quantity["header"] == "quantity"
    public = json.dumps([tools.list_tables(), description])
    assert "binding_id" not in public
    assert "_source_fields" not in public
    assert "profile_digest" not in public


@pytest.mark.parametrize("change", ["preamble", "orphan_edge", "missing_raw_edge"])
def test_independent_source_audit_rejects_coverage_and_lineage_gaps(source_world, tmp_path, change):
    import shutil

    import duckdb

    from datamate.preparation.models import Binding, ResolvedBinding, SemanticBindings
    from datamate.preparation.source_fields import audit_source_fields

    database = tmp_path / "audit-input.duckdb"
    shutil.copy2(source_world.database, database)
    scope = source_projection(source_world)["scope"]
    with duckdb.connect(str(database)) as db:
        entries = tuple(
            ResolvedBinding(Binding.model_validate_json(row[0]), "ACCEPTED", "fixture source role")
            for row in db.execute(
                "SELECT binding_json FROM meta.bindings WHERE status='ACCEPTED'"
            ).fetchall()
        )
        bindings = SemanticBindings(entries, (), ())
        if change == "preamble":
            db.execute(
                "UPDATE meta.dispositions SET kind='quarantined' WHERE record_id IN "
                "(SELECT record_id FROM staging.raw_rows WHERE kind='header')"
            )
        elif change == "orphan_edge":
            db.execute(
                "INSERT INTO meta.projection_lineage "
                "SELECT scope,'orphan',role,column_id,record_id "
                "FROM meta.projection_lineage WHERE scope=? LIMIT 1",
                [scope],
            )
        else:
            db.execute("DELETE FROM meta.lineage WHERE column_id='c0003'")
        findings = audit_source_fields(db, bindings, {})
        assert any(f.scope == scope and f.status in {"FAIL", "UNRESOLVED"} for f in findings)


@pytest.mark.parametrize("prefetch", [False, True])
def test_solver_traces_qualified_schema_without_treating_row_count_as_rows(source_world, prefetch):
    import asyncio
    import json

    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from datamate.contracts.inputs import Question
    from datamate.runtime.solver import solve_question

    projection = source_projection(source_world)
    table = "clean." + projection["scope"]
    tools = StructuredTools(source_world.database, source_world.catalog)
    assert tools.describe_table(table)["rows"] == 2
    calls = []

    def respond(messages, info):
        calls.append(1)
        if prefetch:
            prompt = next(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            schema = json.loads(prompt)["source_context"]["tables"][0]
            assert "rows" not in schema
            assert "declared_data_rows" not in schema["policy"]
        elif len(calls) <= 2:
            scope = "split" if len(calls) == 1 else "business"
            return ModelResponse(parts=[ToolCallPart("get_rules", {"scope": scope})])
        elif len(calls) == 3:
            return ModelResponse(parts=[ToolCallPart("describe_table", {"table_id": table})])
        if len(calls) == (1 if prefetch else 4):
            return ModelResponse(
                parts=[ToolCallPart("execute_sql", {"sql": f"SELECT count(*) FROM {table}"})]
            )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"answer": 2})])

    result = asyncio.run(
        solve_question(
            Question(
                id="q",
                business_id="shop",
                question=f"Count rows in {table}?",
                as_of_date="2025-01-01",
            ),
            tools,
            FunctionModel(respond),
            prefetch_source_context=prefetch,
        )
    )
    assert result.error_code is None, result.error_code
    assert result.answer["answer"] == 2
    description = next(
        event for event in result.tool_events if event["tool_name"] == "describe_table"
    )
    assert description["error_code"] is None
    assert description["returned_row_count"] == 0
    query = next(event for event in result.tool_events if event["tool_name"] == "execute_sql")
    assert query["returned_row_count"] == 1
