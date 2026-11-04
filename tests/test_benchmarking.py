import pytest

from datamate.contracts.inputs import Question
from datamate.evaluation.benchmarking import compare_reports, summarize_run
from datamate.evaluation.io import read_records
from datamate.evaluation.scoring import EvaluationSpec


def test_report_uncertainty_and_comparability():
    questions = [Question(id="q", business_id="b", question="x", as_of_date="2025-01-01")]
    refs = read_records(['{"id":"q","type":"unanswerable","answer":null}'])
    preds = read_records(['{"id":"q","type":"abstain"}'])
    report = summarize_run(
        questions,
        refs,
        preds,
        EvaluationSpec(split_id="x"),
        suite_fingerprint="abc",
        configuration={"model": "m"},
        latency_seconds=[1.0],
        tool_errors=0,
        budgets={},
    )
    assert report["outcomes"]["abstain"] == 1
    assert report["conditional_accuracy"]["denominator"] == 0
    assert report["conditional_accuracy"]["wilson_95"] is None
    assert report["coverage"]["unresolved"] == 0
    assert compare_reports(report, report)["comparable"]
    other = dict(report, suite_fingerprint="different")
    with pytest.raises(ValueError):
        compare_reports(report, other)


def test_wilson_interval_and_explicit_model_comparison():
    from datamate.evaluation.scoring import TRAIN_V1_RULEBOOK_SHA256

    questions = [Question(id="q", business_id="b", question="x", as_of_date="2025-01-01")]
    refs = read_records(['{"id":"q","type":"unanswerable","answer":null}'])
    preds = read_records(['{"id":"q","type":"unanswerable","answer":null}'])
    spec = EvaluationSpec(
        split_id="train", profile="train_rulebook_v1", rulebook_sha256=TRAIN_V1_RULEBOOK_SHA256
    )
    kwargs = dict(suite_fingerprint="abc", latency_seconds=[], tool_errors=0, budgets={})
    first = summarize_run(questions, refs, preds, spec, configuration={"model": "a"}, **kwargs)
    second = summarize_run(questions, refs, preds, spec, configuration={"model": "b"}, **kwargs)
    assert first["conditional_accuracy"]["denominator"] == 1
    assert 0 < first["conditional_accuracy"]["wilson_95"][0] < 1
    with pytest.raises(ValueError):
        compare_reports(first, second)
    assert compare_reports(first, second, allow_model_difference=True)["comparable"]


def test_runtime_metrics_use_elapsed_wall_time():
    questions = [
        Question(id=str(i), business_id="b", question="x", as_of_date="2025-01-01")
        for i in range(3)
    ]
    refs = read_records([f'{{"id":"{i}","type":"unanswerable","answer":null}}' for i in range(3)])
    preds = read_records([f'{{"id":"{i}","type":"abstain"}}' for i in range(3)])
    report = summarize_run(
        questions,
        refs,
        preds,
        EvaluationSpec(split_id="x"),
        suite_fingerprint="abc",
        configuration={"model": "m"},
        latency_seconds=[1, 2, 3],
        elapsed_seconds=2,
        tool_errors=1,
        error_categories={"timeout": 1},
        budgets={"calls": 5},
    )
    assert report["abstention_rate"] == 1
    assert report["coverage_bound_accuracy"]["value"] == 0
    assert report["latency_seconds"]["p50"] == 2
    assert report["latency_seconds"]["p95"] == pytest.approx(2.9)
    assert report["throughput"]["questions_per_second"] == 1.5
    assert report["error_categories"] == {"timeout": 1}
    with pytest.raises(ValueError):
        compare_reports(report, dict(report, budgets={"calls": 6}))


def test_partial_run_throughput_and_harness_comparison():
    questions = [
        Question(id=str(i), business_id="b", question="x", as_of_date="2025-01-01")
        for i in range(3)
    ]
    refs = read_records([f'{{"id":"{i}","type":"number","answer":1}}' for i in range(3)])
    first = summarize_run(
        questions,
        refs,
        read_records(['{"id":"0","type":"abstain"}']),
        EvaluationSpec(split_id="x"),
        suite_fingerprint="same",
        configuration={"model": "m", "harness": "a"},
        latency_seconds=[2],
        elapsed_seconds=2,
        tool_errors=0,
        budgets={},
        error_categories={"infrastructure": 1},
    )
    assert first["throughput"]["questions_per_second"] == 0.5
    assert first["outcomes"]["abstain"] == 3
    other = dict(first, configuration={"model": "m", "harness": "b"})
    assert compare_reports(first, other, allow_harness_difference=True)["comparable"]
    with pytest.raises(ValueError):
        compare_reports(first, other, allow_model_difference=True)
