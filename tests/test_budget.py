import asyncio

import httpx
import pytest

from datamate.inference.budget import BudgetExceeded, BudgetTransport, RequestBudget


def test_persistent_limits_and_usage(tmp_path):
    path = tmp_path / "budget.sqlite"
    budget = RequestBudget(path, run_id="a", rpm=10, daily_limit=2, run_limit=2)
    asyncio.run(budget.acquire())
    budget.record_usage(12, 4)
    restored = RequestBudget(path, run_id="a", rpm=10, daily_limit=2, run_limit=2)
    assert restored.snapshot()["run_attempts"] == 1
    assert restored.snapshot()["day_attempts"] == 1
    assert restored.snapshot()["run_input_tokens"] == 12
    asyncio.run(restored.acquire())
    with pytest.raises(BudgetExceeded):
        asyncio.run(restored.acquire())
    with pytest.raises(BudgetExceeded):
        asyncio.run(RequestBudget(path, run_id="b", rpm=10, daily_limit=2).acquire())


def test_concurrent_reservations_do_not_overshoot(tmp_path):
    path = tmp_path / "budget.sqlite"

    async def reserve():
        budgets = [RequestBudget(path, run_id="a", run_limit=3) for _ in range(12)]
        return await asyncio.gather(*(b.acquire() for b in budgets), return_exceptions=True)

    results = asyncio.run(reserve())
    assert sum(result is None for result in results) == 3
    assert sum(isinstance(result, BudgetExceeded) for result in results) == 9


def test_sliding_minute_has_bounded_wait(tmp_path):
    budget = RequestBudget(tmp_path / "budget.sqlite", run_id="a", rpm=1, max_wait_seconds=0)
    asyncio.run(budget.acquire())
    with pytest.raises(BudgetExceeded):
        asyncio.run(budget.acquire())


def test_transport_counts_retries_and_closes_response(tmp_path):
    calls = []
    responses = []

    def handler(request):
        calls.append(request)
        response = httpx.Response(429 if len(calls) == 1 else 200, headers={"Retry-After": "0"})
        responses.append(response)
        return response

    budget = RequestBudget(tmp_path / "budget.sqlite", run_id="a")

    async def run():
        async with httpx.AsyncClient(
            transport=BudgetTransport(httpx.MockTransport(handler), budget)
        ) as client:
            return await client.get("https://example.test")

    assert asyncio.run(run()).status_code == 200
    assert len(calls) == 2
    assert responses[0].is_closed
    assert budget.snapshot()["run_attempts"] == 2


def test_transport_budget_prevents_retry(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, headers={"Retry-After": "0"})

    budget = RequestBudget(tmp_path / "budget.sqlite", run_id="a", run_limit=1)

    async def run():
        async with httpx.AsyncClient(
            transport=BudgetTransport(httpx.MockTransport(handler), budget)
        ) as client:
            await client.get("https://example.test")

    with pytest.raises(BudgetExceeded):
        asyncio.run(run())
    assert len(calls) == 1
