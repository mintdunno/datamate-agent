import asyncio
import json

import duckdb
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from datamate.contracts.inputs import Question
from datamate.runtime.solver import solve_question
from datamate.runtime.tools import StructuredTools


@pytest.mark.parametrize("streaming", [False, True])
def test_mock_agent_executes_sql_and_returns_valid_answer(tmp_path, streaming):
    path = tmp_path / "test.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(amount INTEGER)")
        db.execute("INSERT INTO sales VALUES (2),(3)")
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) == 1:
            return ModelResponse(
                parts=[ToolCallPart("execute_sql", {"sql": "SELECT sum(amount) FROM sales"})]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": 5},
                )
            ]
        )

    streamed = []

    async def stream(messages, info):
        streamed.append(True)
        response = respond(messages, info)
        yield {
            index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))
            for index, part in enumerate(response.parts)
        }

    tools = StructuredTools(
        path,
        {"tables": [{"table_id": "sales", "columns": []}], "rules": []},
        allow_legacy_fixture=True,
    )
    question = Question(id="q", business_id="shop", question="Tổng?", as_of_date="2025-01-01")
    result = asyncio.run(
        solve_question(
            question, tools, FunctionModel(respond, stream_function=stream), streaming=streaming
        )
    )
    assert bool(streamed) == streaming
    assert result.answer["answer"] == 5
    assert result.error_code is None


def test_explicit_rule_reads_satisfy_source_gate_and_queries_are_traced(tmp_path):
    path = tmp_path / "db.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(amount INTEGER)")
        db.execute("INSERT INTO sales VALUES(5)")
    rules = [
        {
            "rule_id": str(i),
            "source": source,
            "source_sha256": "a" * 64,
            "line_start": 1,
            "line_end": 1,
            "title": "rules",
            "text": "All records are valid.",
        }
        for i, source in enumerate(["rulebook.md", "data/shop/README.md"])
    ]
    tools = StructuredTools(
        path,
        {
            "tables": [
                {
                    "table_id": "sales",
                    "source_table": "sales",
                    "columns": [
                        {
                            "column_id": "amount",
                            "source_header": {"value": "amount"},
                            "sql_type": "INTEGER",
                        }
                    ],
                }
            ],
            "rules": rules,
        },
        allow_legacy_fixture=True,
    )
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) == 1:
            assert "All records are valid" not in str(messages)
            return ModelResponse(parts=[ToolCallPart("get_rules", {"scope": "business"})])
        if len(calls) == 2:
            return ModelResponse(parts=[ToolCallPart("get_rules", {"scope": "split"})])
        if len(calls) == 3:
            return ModelResponse(
                parts=[ToolCallPart("execute_sql", {"sql": "SELECT sum(amount) FROM sales"})]
            )
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"answer": 5})])

    result = asyncio.run(
        solve_question(
            Question(id="q", business_id="shop", question="Total?", as_of_date="2025-01-01"),
            tools,
            FunctionModel(respond),
        )
    )
    assert result.answer["answer"] == 5
    assert {"0", "1"} <= set(result.evidence_ids)
    assert result.tool_events[2]["sql"] == "SELECT sum(amount) FROM sales"
    assert result.tool_events[2]["timestamp"].tzinfo is not None


@pytest.mark.parametrize("sql,value", [("SELECT 1", 999), ("SELECT sum(amount) FROM sales", 999)])
def test_ungrounded_scalar_is_abstained(tmp_path, sql, value):
    path = tmp_path / "data.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("CREATE TABLE sales(amount INTEGER)")
        db.execute("INSERT INTO sales VALUES (5)")
    calls = []

    def respond(messages, info):
        calls.append(1)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart("execute_sql", {"sql": sql})])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {"answer": value},
                )
            ]
        )

    tools = StructuredTools(
        path,
        {"tables": [{"table_id": "sales", "columns": []}], "rules": []},
        allow_legacy_fixture=True,
    )
    question = Question(id="q", business_id="shop", question="Tổng?", as_of_date="2025-01-01")
    result = asyncio.run(solve_question(question, tools, FunctionModel(respond), max_requests=4))
    assert result.answer["type"] == "abstain"
    assert result.error_code
    assert len(calls) <= 4
