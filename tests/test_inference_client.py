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


def test_campaign_counts_retry_repair_and_reference_calls(tmp_path):
    from datamate.inference.budget import BudgetExceeded, CampaignPolicy, register_campaign

    path = tmp_path / "accounting.sqlite"
    register_campaign(path, CampaignPolicy("pilot", total_limit=3, phase_limits={"development": 3}))
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={
                "id": f"response-{len(calls)}",
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
        proposal = RequestBudget(
            path, run_id="proposal", campaign_id="pilot", phase="development", rpm=100
        )
        async with inference_model(
            profile(), proposal, transport=httpx.MockTransport(respond), max_retries=1
        ) as model:
            assert (await Agent(model).run("proposal")).output == "ok"
            assert (await Agent(model).run("format repair")).output == "ok"
        reference = RequestBudget(
            path, run_id="reference", campaign_id="pilot", phase="development", rpm=100
        )
        async with inference_model(
            profile(), reference, transport=httpx.MockTransport(respond), max_retries=1
        ) as model:
            with pytest.raises(BudgetExceeded):
                await Agent(model).run("reference judgement")
        return proposal.snapshot(), reference.snapshot()

    proposal, reference = asyncio.run(run())
    assert len(calls) == 3
    assert proposal["campaign_attempts"] == 3
    assert proposal["run_attempts"] == 3
    assert reference["campaign_remaining"] == 0
    assert proposal["response_metadata_complete"]
    assert proposal["token_usage_status"] == "partial_or_unavailable"


def test_campaign_context_rejection_does_not_consume_attempt(tmp_path):
    from datamate.inference.budget import BudgetTransport, CampaignPolicy, register_campaign

    path = tmp_path / "accounting.sqlite"
    register_campaign(path, CampaignPolicy("pilot"))
    budget = RequestBudget(path, run_id="proposal", campaign_id="pilot", phase="smoke")
    calls = []

    async def run():
        transport = GuardedTransport(
            BudgetTransport(
                httpx.MockTransport(lambda req: calls.append(req)), budget, max_retries=1
            ),
            profile(input_byte_limit=1000),
        )
        with pytest.raises(ContextBudgetExceeded):
            await transport.handle_async_request(
                httpx.Request("POST", "http://localhost", content=b"x" * 1001)
            )
        await transport.aclose()

    asyncio.run(run())
    assert calls == []
    assert budget.snapshot()["run_attempts"] == 0
    assert budget.snapshot()["campaign_attempts"] == 0


def test_streaming_requires_explicit_backend_support():
    with pytest.raises(ValueError, match="stream"):
        profile(streaming=True)
    configured = profile(streaming=True, supports_streaming=True)
    assert configured.read_timeout_seconds == 300
    assert configured.request_deadline_seconds == 360


def test_typed_stream_drains_usage_and_uses_configured_timeouts(tmp_path):
    from pydantic_ai import UsageLimits
    from test_streaming_transport import Chunks, sse

    from datamate.preparation.mapping import MappingProposal

    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="typed-stream", run_limit=1)
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.extensions["timeout"]["connect"] == 15
        assert request.extensions["timeout"]["read"] == 300
        name = body["tools"][0]["function"]["name"]
        chunks = [
            sse(
                {
                    "id": "stream",
                    "created": 0,
                    "model": "selected-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call1",
                                        "type": "function",
                                        "function": {"name": name, "arguments": '{"mappings":'},
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            sse(
                {
                    "id": "stream",
                    "created": 0,
                    "model": "selected-model",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": "[]}"}}]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            ),
            sse(
                {
                    "id": "stream",
                    "created": 0,
                    "model": "selected-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                }
            ),
            sse(
                {
                    "id": "stream",
                    "created": 0,
                    "model": "selected-model",
                    "choices": [],
                    "usage": {"prompt_tokens": 14, "completion_tokens": 8},
                }
            ),
            b"data: [DONE]\n\n",
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Chunks(chunks)
        )

    async def run():
        async def drain(ctx, events):
            async for _event in events:
                pass

        async with inference_model(
            profile(streaming=True, supports_streaming=True, force_output_tool=True),
            budget,
            transport=httpx.MockTransport(respond),
            max_retries=0,
        ) as model:
            result = await Agent(model, output_type=MappingProposal, retries=0).run(
                "Return empty mappings",
                event_stream_handler=drain,
                usage_limits=UsageLimits(request_limit=1),
            )
            assert not result.output.mappings

    asyncio.run(run())
    assert len(requests) == 1 and requests[0]["stream"] is True
    assert requests[0]["stream_options"]["include_usage"] is True
    assert requests[0]["tool_choice"] == "required"
    assert budget.snapshot()["run_output_tokens"] == 8
    assert budget.snapshot()["request_traces"][0]["completed"]
    assert budget.snapshot()["request_traces"][0]["timeouts"]["read"] == 300
    assert budget.snapshot()["request_traces"][0]["tool_choice_requested"] == "required"
