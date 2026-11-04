"""Transparent, provisional summaries for evaluator-owned benchmark runs."""

import hashlib
import json
import math

from datamate.evaluation.batch import evaluate_batch


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _wilson(successes, count):
    if not count:
        return None
    z = 1.959963984540054
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    margin = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_run(
    questions,
    references,
    predictions,
    spec,
    *,
    suite_fingerprint,
    configuration,
    latency_seconds,
    tool_errors,
    budgets,
    elapsed_seconds=None,
    error_categories=None,
):
    """Summarize outcomes; budgets describe policy limits, not usage or consumption.

    Throughput uses explicitly measured wall time; latency percentiles use linear interpolation.
    """
    evaluation = evaluate_batch(questions, references, predictions, spec)
    if len(latency_seconds) > evaluation.total_questions:
        raise ValueError("latency count exceeds question count")
    if any(not math.isfinite(x) or x < 0 for x in latency_seconds) or tool_errors < 0:
        raise ValueError("latencies and tool error count must be nonnegative")
    if elapsed_seconds is not None and (not math.isfinite(elapsed_seconds) or elapsed_seconds <= 0):
        raise ValueError("elapsed wall time must be positive and finite")
    errors = dict(error_categories or {})
    if (
        any(type(count) is not int or count < 0 for count in errors.values())
        or sum(errors.values()) > evaluation.total_questions
    ):
        raise ValueError("error categories must be nonnegative question failure counts")
    denominator = evaluation.correct + evaluation.wrong
    total = evaluation.total_questions
    config = dict(configuration)
    return {
        "suite_fingerprint": suite_fingerprint,
        "configuration": config,
        "configuration_fingerprint": _fingerprint(config),
        "scoring_spec": evaluation.spec.model_dump(mode="json"),
        "outcomes": {
            "correct": evaluation.correct,
            "wrong": evaluation.wrong,
            "abstain": evaluation.abstain,
        },
        "coverage": {
            "total": total,
            "resolved": total - evaluation.unresolved,
            "unresolved": evaluation.unresolved,
            "invalid_records": evaluation.invalid_records,
        },
        "abstention_rate": evaluation.abstain / total if total else None,
        "coverage_bound_accuracy": {
            "numerator": evaluation.correct,
            "denominator": total,
            "value": evaluation.correct / total if total else None,
            "label": "provisional known correct / total; unresolved outcomes not credited",
        },
        "conditional_accuracy": {
            "numerator": evaluation.correct,
            "denominator": denominator,
            "denominator_label": (
                "resolved correct + wrong answers; excludes abstentions and unresolved"
            ),
            "value": evaluation.correct / denominator if denominator else None,
            "wilson_95": _wilson(evaluation.correct, denominator),
            "uncertainty_note": "IID interval; business-cluster correlation not modeled",
        },
        "metric_status": evaluation.metric_status,
        "provisional_pa": evaluation.provisional_pa,
        "latency_seconds": {
            "count": len(latency_seconds),
            "total": sum(latency_seconds),
            "mean": sum(latency_seconds) / len(latency_seconds) if latency_seconds else None,
            "p50": _percentile(latency_seconds, 0.50),
            "p95": _percentile(latency_seconds, 0.95),
        },
        "throughput": {
            "elapsed_wall_seconds": elapsed_seconds,
            "completed_traced_questions": len(latency_seconds),
            "questions_per_second": len(latency_seconds) / elapsed_seconds
            if elapsed_seconds is not None
            else None,
        },
        "tool_errors": tool_errors,
        "error_categories": errors,
        "budgets": budgets,
    }


def compare_reports(left, right, *, allow_model_difference=False, allow_harness_difference=False):
    """Require matching suite, scoring, budget policy and config; model may differ explicitly."""
    if (
        left["suite_fingerprint"] != right["suite_fingerprint"]
        or left["scoring_spec"] != right["scoring_spec"]
        or left["budgets"] != right["budgets"]
    ):
        raise ValueError("incomparable suite, scoring, or budget")
    a, b = dict(left["configuration"]), dict(right["configuration"])
    if allow_model_difference:
        a.pop("model", None)
        b.pop("model", None)
    if allow_model_difference and allow_harness_difference:
        raise ValueError("choose one comparison axis to preserve attribution")
    if allow_harness_difference:
        for key in ("harness", "prompt_sha256", "dependencies"):
            a.pop(key, None)
            b.pop(key, None)
    if a != b:
        raise ValueError("incomparable configuration")
    return {
        "comparable": True,
        "comparison_axis": "model"
        if allow_model_difference
        else "harness"
        if allow_harness_difference
        else "identical_configuration",
        "correct_delta": right["outcomes"]["correct"] - left["outcomes"]["correct"],
        "unresolved_delta": right["coverage"]["unresolved"] - left["coverage"]["unresolved"],
    }
