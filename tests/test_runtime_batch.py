import asyncio
import json
from types import SimpleNamespace

import pytest

from datamate.contracts.inputs import Question
from datamate.inference.budget import RequestBudget
from datamate.runtime import batch


def question(id):
    return Question(id=id, business_id="biz", question="question", as_of_date="2024-01-01")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    split = tmp_path / "split"
    (split / "data" / "biz").mkdir(parents=True)
    (split / "data" / "biz" / "data.csv").write_text("a\n1\n")
    (split / "rulebook.md").write_text("rules")
    (split / "calendar.csv").write_text("day\n")
    profile = SimpleNamespace(
        name="test",
        model_id="local",
        runtime="compatible",
        revision=None,
        max_tokens=1024,
        temperature=None,
        top_p=None,
        extra_body={},
        model_dump=lambda **kwargs: {"name": "test", "model_id": "local"},
    )
    prepared = SimpleNamespace(database=tmp_path / "db", catalog={"tables": []}, fingerprint="abc")
    monkeypatch.setattr(batch, "prepare_business", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(batch, "StructuredTools", lambda *args, **kwargs: object())

    async def solve(q, tools, model, **kwargs):
        return SimpleNamespace(
            answer={"id": q.id, "type": "abstain"}, error_code=None, input_tokens=0, output_tokens=0
        )

    monkeypatch.setattr(batch, "solve_question", solve)
    budget = RequestBudget(tmp_path / "budget.sqlite", run_id="r")
    return dict(
        split_root=split,
        state_root=tmp_path / "state",
        output_dir=tmp_path / "out",
        model=object(),
        profile=profile,
        budget=budget,
    )


def test_run_progress_resume_and_config_change(setup):
    questions = [question("1"), question("2")]
    result = asyncio.run(batch.run_batch(questions, **setup))
    assert result["status"] == "completed"
    lines = (setup["output_dir"] / "submission.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["1", "2"]
    with pytest.raises(ValueError):
        asyncio.run(batch.run_batch(questions, **setup))
    assert asyncio.run(batch.run_batch(questions, **setup, resume=True))["status"] == "completed"
    with pytest.raises(ValueError):
        asyncio.run(batch.run_batch(questions, **setup, resume=True, deadline=5))
    (setup["split_root"] / "rulebook.md").write_text("changed")
    with pytest.raises(ValueError):
        asyncio.run(batch.run_batch(questions, **setup, resume=True))


def test_failures_abstain_and_budget_stops_remaining(setup, monkeypatch):
    async def solve(q, tools, model, **kwargs):
        await setup["budget"].acquire()
        raise RuntimeError("private secret")

    monkeypatch.setattr(batch, "solve_question", solve)
    setup["budget"].run_limit = 1
    result = asyncio.run(batch.run_batch([question("1"), question("2")], **setup))
    assert result["status"] == "budget_exhausted"
    assert len((setup["output_dir"] / "submission.jsonl").read_text().splitlines()) == 1
    assert "private secret" not in (setup["output_dir"] / "traces.jsonl").read_text()


def test_duplicate_and_maximum_rejected(setup):
    with pytest.raises(ValueError):
        asyncio.run(batch.run_batch([question("1"), question("1")], **setup))
    with pytest.raises(ValueError):
        asyncio.run(batch.run_batch([], **setup, max_questions=501))


def test_typed_trace_and_transport_usage_not_double_counted(setup, monkeypatch):
    from datamate.observability import TRACE_ADAPTER

    async def solve(q, tools, model, **kwargs):
        await setup["budget"].acquire()
        setup["budget"].record_usage(9, 3)
        return SimpleNamespace(
            answer={"id": q.id, "type": "abstain"},
            error_code=None,
            input_tokens=9,
            output_tokens=3,
            tool_executions=[{"sql": "SELECT 'private'", "result": {"rows": [["secret"]]}}],
        )

    monkeypatch.setattr(batch, "solve_question", solve)
    result = asyncio.run(batch.run_batch([question("1")], **setup))
    assert setup["budget"].snapshot()["run_input_tokens"] == 9
    text = (setup["output_dir"] / "traces.jsonl").read_text()
    events = [TRACE_ADAPTER.validate_json(line) for line in text.splitlines()]
    assert [event.event for event in events] == [
        "question_started",
        "tool_finished",
        "question_finished",
    ]
    assert events[-1].usage.requests == 1
    assert events[-1].usage.input_tokens == 9
    assert "private" not in text and "secret" not in text
    assert result["tool_errors"] == 0


def test_fixed_snapshot_rejects_data_drift_before_writes(setup):
    with pytest.raises(ValueError, match="snapshot"):
        asyncio.run(batch.run_batch([question("1")], **setup, expected_sources={"biz": {}}))
    assert not setup["output_dir"].exists()
    assert setup["budget"].snapshot()["run_attempts"] == 0


def test_resume_binds_dependency_versions(setup, monkeypatch):
    asyncio.run(batch.run_batch([question("1")], **setup))
    monkeypatch.setattr(batch, "_dependencies", lambda: {"pydantic-ai-slim": "different"})
    with pytest.raises(ValueError, match="resume"):
        asyncio.run(batch.run_batch([question("1")], **setup, resume=True))


def test_evidenced_normalization_plans_reach_preparation_and_bind_resume(setup, monkeypatch):
    from datamate.preparation.normalization import TablePlan
    from datamate.runtime.inputs import PreparationPlans

    original = batch.prepare_business
    received = []

    def prepare(*args, **kwargs):
        received.append(kwargs["plans"])
        return original(*args, **kwargs)

    monkeypatch.setattr(batch, "prepare_business", prepare)
    plans = PreparationPlans(businesses={"biz": {"data/biz/data.csv::data": TablePlan()}})
    asyncio.run(batch.run_batch([question("1")], **setup, normalization_plans=plans))
    assert isinstance(received[0]["data/biz/data.csv::data"], TablePlan)
    with pytest.raises(ValueError, match="resume"):
        asyncio.run(
            batch.run_batch(
                [question("1")],
                **setup,
                resume=True,
                normalization_plans=PreparationPlans(businesses={}),
            )
        )
