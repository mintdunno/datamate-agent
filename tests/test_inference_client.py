import asyncio
import json

import httpx
import pytest
from pydantic_ai import Agent

from datamate.inference.budget import RequestBudget
from datamate.inference.client import ContextBudgetExceeded, GuardedTransport, inference_model
from datamate.inference.config import ModelProfile


def profile(**changes):
    return ModelProfile(
        name="test", model_id="selected-model", base_url="http://localhost:8000/v1", **changes
    )


def test_factory_uses_selected_model_and_accounts_provider_usage(tmp_path):
    budget = RequestBudget(tmp_path / "accounting.sqlite", run_id="test")
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "response",
                "object": "chat.completion",
                "created": 0,
                "model": "selected-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            },
        )

    async def run():
        async with inference_model(
            profile(), budget, transport=httpx.MockTransport(respond)
        ) as model:
            result = await Agent(model).run("hello")
            assert result.output == "ok"

    asyncio.run(run())
    assert requests[0]["model"] == "selected-model"
    assert requests[0]["max_tokens"] == 1024
    assert "temperature" not in requests[0]
    assert "response_format" not in requests[0]
    assert budget.snapshot()["run_attempts"] == 1
    assert budget.snapshot()["run_input_tokens"] == 11
    assert budget.snapshot()["run_output_tokens"] == 2
    assert budget.snapshot()["finish_reasons"] == {"stop": 1}
    assert budget.snapshot()["response_metadata_complete"]


def test_context_guard_rejects_before_network(tmp_path):
    calls = []

    async def run():
        transport = GuardedTransport(
            httpx.MockTransport(lambda req: calls.append(req)), profile(input_byte_limit=1000)
        )
        with pytest.raises(ContextBudgetExceeded):
            await transport.handle_async_request(
                httpx.Request("POST", "http://localhost", content=b"x" * 1001)
            )
        await transport.aclose()

    asyncio.run(run())
    assert not calls


def test_profile_rejects_control_overrides_and_unsupported_tools(tmp_path):
    with pytest.raises(ValueError):
        profile(extra_body={"model": "another"})

    async def run():
        budget = RequestBudget(tmp_path / "accounting.sqlite", run_id="test")
        with pytest.raises(ValueError, match="native tool"):
            async with inference_model(profile(native_tools=False), budget):
                pass
        assert budget.snapshot()["run_attempts"] == 0

    asyncio.run(run())
