"""Incremental provider accounting without buffering SSE or exposing source text."""

import asyncio
import json

import httpx
import pytest

from datamate.inference.budget import BudgetTransport, RequestBudget


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error
        self.seen = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.seen += 1
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def sse(value):
    return ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()


def test_stream_is_incremental_and_accounts_final_usage_once(tmp_path):
    body = sse({"choices": [{"delta": {"content": "Tiếng Việt"}, "finish_reason": None}]})
    body += sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    body += sse(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }
    )
    body += b"data: [DONE]\r\n\r\n"
    stream = Chunks([body[i : i + 7] for i in range(0, len(body), 7)])
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="stream")
    calls = []

    async def run():
        def respond(request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        transport = BudgetTransport(httpx.MockTransport(respond), budget, max_retries=0)
        response = await transport.handle_async_request(
            httpx.Request("POST", "https://provider", json={"stream": True})
        )
        assert stream.seen == 0, "SSE must not be consumed before returning headers"
        chunks = [chunk async for chunk in response.aiter_raw()]
        assert b"".join(chunks) == body
        await response.aclose()
        await transport.aclose()

    asyncio.run(run())
    snap = budget.snapshot()
    assert len(calls) == snap["run_attempts"] == 1
    assert (snap["run_input_tokens"], snap["run_output_tokens"]) == (11, 7)
    assert snap["finish_reasons"] == {"stop": 1}
    trace = snap["request_traces"][0]
    assert trace["completed"] and trace["stream_done"]
    assert trace["first_delta_seconds"] is not None
    assert trace["reasoning_tokens"] == 3
    assert "Tiếng Việt" not in json.dumps(snap, ensure_ascii=False)
    assert stream.closed


def test_partial_stream_timeout_never_retries_or_invents_zero_usage(tmp_path):
    stream = Chunks(
        [sse({"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})],
        httpx.ReadTimeout("private payload must not be recorded"),
    )
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="partial")
    calls = []

    async def run():
        def respond(request):
            calls.append(request)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

        transport = BudgetTransport(httpx.MockTransport(respond), budget, max_retries=1)
        response = await transport.handle_async_request(
            httpx.Request("POST", "https://provider", json={"stream": True})
        )
        with pytest.raises(httpx.ReadTimeout):
            await response.aread()
        await response.aclose()
        await transport.aclose()

    asyncio.run(run())
    snap = budget.snapshot()
    assert len(calls) == snap["run_attempts"] == 1
    assert snap["token_usage_status"] == "partial_or_unavailable"
    assert snap["run_usage_responses"] == 0
    trace = snap["request_traces"][0]
    assert trace["failure_code"] == "TIMEOUT_READ" and not trace["completed"]
    assert "private payload" not in json.dumps(snap)


def test_absolute_request_deadline_bounds_wait_for_headers(tmp_path):
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="deadline")

    async def run():
        async def respond(request):
            await asyncio.Event().wait()

        transport = BudgetTransport(
            httpx.MockTransport(respond), budget, max_retries=0, deadline_seconds=0.02
        )
        with pytest.raises(TimeoutError):
            await transport.handle_async_request(httpx.Request("POST", "https://provider"))
        await transport.aclose()

    asyncio.run(run())
    assert budget.snapshot()["run_attempts"] == 1
    assert budget.snapshot()["request_traces"][0]["failure_code"] == "DEADLINE_EXCEEDED"


def test_early_close_records_incomplete_stream_without_usage(tmp_path):
    stream = Chunks([sse({"choices": [{"delta": {"content": "one"}, "finish_reason": None}]})])
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="close")

    async def run():
        transport = BudgetTransport(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=stream
                )
            ),
            budget,
            max_retries=0,
        )
        response = await transport.handle_async_request(
            httpx.Request("POST", "https://provider", json={"stream": True})
        )
        await response.aclose()
        await transport.aclose()

    asyncio.run(run())
    snap = budget.snapshot()
    assert not snap["request_traces"][0]["completed"]
    assert snap["request_traces"][0]["failure_code"] == "STREAM_CLOSED_EARLY"
    assert snap["run_usage_responses"] == 0


@pytest.mark.parametrize("failure", ["read", "deadline", "cancel"])
def test_nonstreaming_failed_body_is_closed(tmp_path, failure):
    ready = asyncio.Event()

    class Body(Chunks):
        async def __aiter__(self):
            yield b"{"
            ready.set()
            if failure == "read":
                raise httpx.ReadTimeout("unreported body")
            await asyncio.Event().wait()

    stream = Body([])
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="close-body")

    async def run():
        transport = BudgetTransport(
            httpx.MockTransport(lambda req: httpx.Response(200, stream=stream)),
            budget,
            max_retries=0,
            deadline_seconds=0.02 if failure == "deadline" else 1,
        )
        task = asyncio.create_task(
            transport.handle_async_request(httpx.Request("POST", "https://provider"))
        )
        if failure == "cancel":
            await ready.wait()
            task.cancel()
        with pytest.raises((httpx.ReadTimeout, TimeoutError, asyncio.CancelledError)):
            await task
        assert stream.closed
        await transport.aclose()

    asyncio.run(run())
    assert budget.snapshot()["run_attempts"] == 1


def test_gzip_sse_is_decoded_for_observation_without_corrupting_forwarded_body(tmp_path):
    import gzip

    body = (
        sse(
            {
                "choices": [{"delta": {"content": "xin chào"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        )
        + b"data: [DONE]\n\n"
    )
    wire = gzip.compress(body)
    stream = Chunks([wire[:11], wire[11:]])
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="gzip")

    async def run():
        transport = BudgetTransport(
            httpx.MockTransport(
                lambda req: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
                    stream=stream,
                )
            ),
            budget,
            max_retries=0,
        )
        response = await transport.handle_async_request(
            httpx.Request("POST", "https://provider", json={"stream": True})
        )
        assert await response.aread() == body
        await response.aclose()
        await transport.aclose()

    asyncio.run(run())
    assert stream.closed
    assert budget.snapshot()["run_output_tokens"] == 2
    assert budget.snapshot()["request_traces"][-1]["completed"]
