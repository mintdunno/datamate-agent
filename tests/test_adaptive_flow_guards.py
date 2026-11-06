"""No preprocessing or HTTP starts with unsafe adaptive configuration."""

import asyncio
import importlib

import pytest

from datamate.inference.budget import RequestBudget


def test_adaptive_requires_explicit_profile_and_budget_before_io(tmp_path):
    api = importlib.import_module("datamate.preparation.orchestration")
    with pytest.raises(ValueError, match="profile.*budget"):
        asyncio.run(
            api.prepare_with_recovery(
                tmp_path / "missing",
                "shop",
                tmp_path / "cache",
                split_id="train",
                run_dir=tmp_path / "run",
                issue_selector=lambda _: None,
                verifier=lambda *_: False,
                allow_model_calls=True,
            )
        )
    assert not (tmp_path / "cache").exists()


def test_adaptive_rejects_over_provider_rate_before_io(tmp_path):
    api = importlib.import_module("datamate.preparation.orchestration")
    budget = RequestBudget(tmp_path / "ledger.sqlite", run_id="over-rate", rpm=41)
    with pytest.raises(ValueError, match="40"):
        asyncio.run(
            api.prepare_with_recovery(
                tmp_path / "missing",
                "shop",
                tmp_path / "cache",
                split_id="train",
                run_dir=tmp_path / "run",
                issue_selector=lambda _: None,
                verifier=lambda *_: False,
                budget=budget,
            )
        )
    assert budget.snapshot()["run_attempts"] == 0
