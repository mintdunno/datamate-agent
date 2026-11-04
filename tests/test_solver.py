import asyncio

import duckdb
import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from datamate.contracts.inputs import Question
from datamate.runtime.solver import solve_question
from datamate.runtime.tools import StructuredTools


def test_mock_agent_executes_sql_and_returns_valid_answer(tmp_path):
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

    tools = StructuredTools(path, {"tables": [{"table_id": "sales", "columns": []}], "rules": []})
    question = Question(id="q", business_id="shop", question="Tổng?", as_of_date="2025-01-01")
    result = asyncio.run(solve_question(question, tools, FunctionModel(respond)))
    assert result.answer["answer"] == 5
    assert result.error_code is None


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

    tools = StructuredTools(path, {"tables": [{"table_id": "sales", "columns": []}], "rules": []})
    question = Question(id="q", business_id="shop", question="Tổng?", as_of_date="2025-01-01")
    result = asyncio.run(solve_question(question, tools, FunctionModel(respond), max_requests=4))
    assert result.answer["type"] == "abstain"
    assert result.error_code
    assert len(calls) <= 4
