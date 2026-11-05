import asyncio
from concurrent.futures import ThreadPoolExecutor

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


def _acquire_result(budget):
    try:
        asyncio.run(budget.acquire())
    except BudgetExceeded as exc:
        return exc
    return None


def test_campaign_cap_is_atomic_across_run_ids_and_phases(tmp_path):
    from datamate.inference.budget import CampaignPolicy, register_campaign

    path = tmp_path / "budget.sqlite"
    register_campaign(path, CampaignPolicy("pilot"))

    phases = ("smoke", "development", "final")
    budgets = [
        RequestBudget(
            path,
            run_id=f"run-{i}",
            campaign_id="pilot",
            phase=phases[i % 3],
            rpm=1000,
            daily_limit=1000,
            run_limit=1000,
            max_wait_seconds=5,
        )
        for i in range(180)
    ]
    with ThreadPoolExecutor(max_workers=12) as workers:
        results = list(workers.map(lambda budget: _acquire_result(budget), budgets))
    assert sum(result is None for result in results) == 120
    assert sum(isinstance(result, BudgetExceeded) for result in results) == 60
    snapshot = RequestBudget(
        path, run_id="observer", campaign_id="pilot", phase="smoke", rpm=1000
    ).snapshot()
    assert snapshot["campaign_attempts"] == 120
    assert snapshot["phase_attempts"] == 20
    assert snapshot["campaign_remaining"] == 0
    assert snapshot["phase_remaining"] == 0
    for phase, expected in (("development", 60), ("final", 40)):
        other = RequestBudget(
            path, run_id=f"observer-{phase}", campaign_id="pilot", phase=phase, rpm=1000
        ).snapshot()
        assert other["phase_attempts"] == expected
        assert other["phase_remaining"] == 0


def test_reopening_cannot_enlarge_campaign(tmp_path):
    from datamate.inference.budget import CampaignPolicy, register_campaign

    path = tmp_path / "budget.sqlite"
    register_campaign(path, CampaignPolicy("pilot"))
    with pytest.raises(ValueError, match="campaign policy"):
        register_campaign(path, CampaignPolicy("pilot", total_limit=121))
    with pytest.raises(ValueError, match="campaign policy"):
        register_campaign(
            path,
            CampaignPolicy("pilot", phase_limits={"smoke": 21, "development": 60, "final": 40}),
        )
    budget = RequestBudget(path, run_id="a", campaign_id="pilot", phase="smoke")
    asyncio.run(budget.acquire())
    with pytest.raises(ValueError, match="run"):
        RequestBudget(path, run_id="a", campaign_id="pilot", phase="final")


def test_old_attempts_still_count_toward_utc_day(tmp_path):
    from datamate.inference.budget import CampaignPolicy, register_campaign

    path = tmp_path / "budget.sqlite"
    old = RequestBudget(path, run_id="legacy", rpm=100, daily_limit=3)
    asyncio.run(old.acquire())
    asyncio.run(old.acquire())
    register_campaign(path, CampaignPolicy("pilot"))
    new = RequestBudget(
        path, run_id="new", campaign_id="pilot", phase="smoke", rpm=100, daily_limit=3
    )
    asyncio.run(new.acquire())
    assert new.snapshot()["day_attempts"] == 3
    assert new.snapshot()["day_remaining"] == 0
    with pytest.raises(BudgetExceeded):
        asyncio.run(
            RequestBudget(
                path, run_id="newer", campaign_id="pilot", phase="smoke", rpm=100, daily_limit=3
            ).acquire()
        )
    assert new.snapshot()["campaign_attempts"] == 1


def test_campaign_transport_limits_retry_count(tmp_path):
    from datamate.inference.budget import CampaignPolicy, register_campaign

    path = tmp_path / "budget.sqlite"
    register_campaign(path, CampaignPolicy("pilot"))
    budget = RequestBudget(path, run_id="run", campaign_id="pilot", phase="smoke")
    with pytest.raises(ValueError, match="one transport retry"):
        BudgetTransport(httpx.MockTransport(lambda request: httpx.Response(200)), budget)


def test_stale_noncampaign_instance_cannot_bypass_run_binding(tmp_path):
    from datamate.inference.budget import CampaignPolicy, register_campaign

    path = tmp_path / "budget.sqlite"
    stale = RequestBudget(path, run_id="shared")
    register_campaign(path, CampaignPolicy("pilot"))
    bound = RequestBudget(path, run_id="shared", campaign_id="pilot", phase="smoke")

    with pytest.raises(ValueError, match="run is bound"):
        asyncio.run(stale.acquire())
    assert bound.snapshot()["day_attempts"] == 0
    asyncio.run(bound.acquire())
    assert bound.snapshot()["campaign_attempts"] == 1
    assert bound.snapshot()["day_attempts"] == 1


@pytest.mark.parametrize(
    ("campaign_id", "total_limit", "phase_limits"),
    [
        (12, 120, {"smoke": 20}),
        ("pilot", 1.5, {"smoke": 20}),
        ("pilot", True, {"smoke": 20}),
        ("pilot", 120, {1: 20}),
    ],
)
def test_campaign_policy_rejects_noninteger_caps_and_nonstrings(
    campaign_id, total_limit, phase_limits
):
    from datamate.inference.budget import CampaignPolicy

    with pytest.raises(ValueError):
        CampaignPolicy(campaign_id, total_limit=total_limit, phase_limits=phase_limits)


def test_request_budget_rejects_nonstrings_for_campaign_binding(tmp_path):
    path = tmp_path / "budget.sqlite"
    with pytest.raises(ValueError, match="campaign_id and phase must be strings"):
        RequestBudget(path, run_id="a", campaign_id=1, phase="smoke")
    with pytest.raises(ValueError, match="campaign_id and phase must be strings"):
        RequestBudget(path, run_id="a", campaign_id="pilot", phase=1)
