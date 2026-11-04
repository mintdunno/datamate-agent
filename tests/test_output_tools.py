import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from datamate.contracts.answers import serialize_submission
from datamate.runtime.outputs import output_functions


@pytest.mark.parametrize(
    "name,invalid,valid,kind",
    [
        ("answer_number", {"answer": "5"}, {"answer": 5}, "number"),
        ("answer_percent", {"answer": False}, {"answer": 12.5}, "percent"),
        ("answer_boolean", {"answer": "true"}, {"answer": True}, "boolean"),
        ("answer_date", {"answer": "2025-02-30"}, {"answer": "2025-02-28"}, "date"),
        (
            "TableValue",
            {"columns": ["x"], "rows": [[1, 2]]},
            {"columns": ["x"], "rows": [[1]]},
            "table",
        ),
    ],
)
def test_output_tools_retry_invalid_values_and_bind_id_type(name, invalid, valid, kind):
    calls = []

    def respond(messages, info):
        calls.append(1)
        tool = next(tool for tool in info.output_tools if tool.name.endswith("_" + name))
        return ModelResponse(parts=[ToolCallPart(tool.name, invalid if len(calls) == 1 else valid)])

    result = asyncio.run(
        Agent(FunctionModel(respond), output_type=output_functions("bound")).run("test")
    )
    assert len(calls) == 2
    assert result.output.id == "bound"
    assert result.output.type == kind
    assert '"id":"bound"' in serialize_submission(result.output)


@pytest.mark.parametrize(
    "name,arguments,kind",
    [
        ("answer_string", {"answer": "evidence"}, "string"),
        ("answer_list", {"answer": ["A", None, 1]}, "list"),
        ("answer_unanswerable", {"reason": "documented absence"}, "unanswerable"),
        ("abstain", {}, "abstain"),
    ],
)
def test_remaining_output_shapes_serialize(name, arguments, kind):
    function = next(fn for fn in output_functions("bound") if fn.__name__ == name)
    result = function(**arguments)
    assert result.id == "bound" and result.type == kind
    serialize_submission(result)
