"""Repeated provider usage counters must not exhaust an otherwise valid solver run."""

import asyncio
import json

import duckdb
import httpx

from datamate.contracts.inputs import Question
from datamate.inference.budget import RequestBudget
from datamate.inference.client import inference_model, model_settings
from datamate.runtime.solver import solve_question
from datamate.runtime.tools import StructuredTools


def test_cumulative_stream_usage_agrees_with_actual_ledger(tmp_path, monkeypatch):
    from test_adaptive_normalization import adaptive_profile
    from test_streaming_transport import Chunks, sse

    monkeypatch.setenv("LLM_API_KEY", "fixture-key")
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE TABLE sales(amount INTEGER)")
        db.execute("INSERT INTO sales VALUES (2),(3)")
    tools = StructuredTools(
        database,
        {"tables": [{"table_id": "sales", "columns": []}], "rules": []},
        allow_legacy_fixture=True,
    )
    profile = adaptive_profile().model_copy(
        update={
            "streaming": True,
            "supports_streaming": True,
            "stream_usage_mode": "cumulative",
        }
    )
    budget = RequestBudget(tmp_path / "usage.sqlite", run_id="cumulative", run_limit=2)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body["stream"]
        assert "continuous_usage_stats" not in body.get("stream_options", {})
        requests.append(body)
        first = len(requests) == 1
        name = (
            "execute_sql"
            if first
            else next(
                t["function"]["name"]
                for t in body["tools"]
                if t["function"]["name"].endswith("answer_number")
            )
        )
        arguments = {"sql": "SELECT sum(amount) FROM sales"} if first else {"answer": 5}
        base = {"id": "stream", "created": 0, "model": "selected-model"}
        events = [
            sse(
                base
                | {
                    "choices": [{"index": 0, "delta": {"content": " "}, "finish_reason": None}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100},
                }
            )
            for _ in range(20)
        ]
        events.append(
            sse(
                base
                | {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(arguments),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
                }
            )
        )
        events.append(b"data: [DONE]\r\n\r\n")
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Chunks(events)
        )

    async def run():
        async with inference_model(
            profile, budget, transport=httpx.MockTransport(respond), max_retries=0
        ) as model:
            return await solve_question(
                Question(id="q", business_id="shop", question="Total?", as_of_date="2025-01-01"),
                tools,
                model,
                streaming=True,
                total_token_budget=300,
                max_requests=2,
                model_settings=model_settings(profile),
            )

    result = asyncio.run(run())
    assert result.error_code is None, (
        result.input_tokens,
        result.output_tokens,
        budget.snapshot()["run_input_tokens"],
        budget.snapshot()["run_output_tokens"],
        len(requests),
    )
    assert result.answer["answer"] == 5
    assert result.input_tokens == budget.snapshot()["run_input_tokens"] == 200
    assert result.output_tokens == budget.snapshot()["run_output_tokens"] == 10
    assert len(requests) == budget.snapshot()["run_attempts"] == 2
