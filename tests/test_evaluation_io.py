"""Evaluator JSONL handling preserves uncertainty instead of fabricating outcomes."""

import json

import pytest

from datamate.contracts.inputs import Question
from datamate.evaluation.batch import evaluate_batch
from datamate.evaluation.io import read_records
from datamate.evaluation.scoring import TRAIN_V1_RULEBOOK_SHA256, EvaluationSpec


def question(identifier):
    return Question(
        id=identifier, business_id="shop-demo", question="Số lượng?", as_of_date="2025-01-01"
    )


def records(*values):
    return read_records(json.dumps(value) for value in values)


def policy():
    return EvaluationSpec(
        split_id="train", profile="train_rulebook_v1", rulebook_sha256=TRAIN_V1_RULEBOOK_SHA256
    )


def test_missing_predictions_use_expected_question_denominator():
    report = evaluate_batch(
        [question("a"), question("b"), question("c")],
        records(*({"id": key, "type": "number", "answer": 10} for key in "abc")),
        records(
            {"id": "a", "type": "number", "answer": 10}, {"id": "b", "type": "number", "answer": 9}
        ),
        policy(),
    )
    assert (report.correct, report.wrong, report.abstain, report.unresolved) == (1, 1, 1, 0)
    assert report.total_questions == 3
    assert report.known_points == 0
    assert report.provisional_pa == 0.0
    assert report.complete


def test_unresolved_float_blocks_aggregate_without_shrinking_denominator():
    report = evaluate_batch(
        [question("a"), question("b")],
        records(
            {"id": "a", "type": "percent", "answer": 12.4},
            {"id": "b", "type": "number", "answer": 1},
        ),
        records({"id": "a", "type": "percent", "answer": 12.400000001}),
        policy(),
    )
    assert report.total_questions == 2
    assert report.unresolved == 1
    assert report.abstain == 1
    assert report.provisional_pa is None
    assert not report.complete


@pytest.mark.parametrize(
    "line,code",
    [
        ("{broken", "invalid_json"),
        ('{"id":"a","id":"b"}', "invalid_json"),
        ('{"id":"a","type":"number","answer":NaN}', "invalid_json"),
        ('{"id":"a","type":"number","answer":1e999}', "invalid_json"),
        ("[]", "invalid_envelope"),
        ('{"id":1}', "invalid_envelope"),
        ("", "invalid_json"),
    ],
)
def test_malformed_records_have_content_free_diagnostics(line, code):
    batch = read_records([line])
    assert not batch.records
    assert batch.issues[0].code == code
    assert batch.issues[0].line_number == 1
    assert "answer" not in batch.issues[0].__dict__


def test_duplicate_ids_are_quarantined_not_first_or_last_wins():
    batch = records(
        {"id": "a", "type": "number", "answer": 1}, {"id": "a", "type": "number", "answer": 2}
    )
    assert "a" not in batch.records
    assert batch.invalid_ids == {"a"}
    report = evaluate_batch(
        [question("a")], records({"id": "a", "type": "number", "answer": 1}), batch, policy()
    )
    assert report.results[0].status == "invalid_prediction"
    assert report.results[0].outcome is None
    assert report.provisional_pa is None


def test_malformed_unattributed_line_is_not_silently_missing_prediction():
    report = evaluate_batch(
        [question("a")],
        records({"id": "a", "type": "number", "answer": 1}),
        read_records(["broken"]),
        policy(),
    )
    assert report.abstain == 0
    assert report.unresolved == 1
    assert report.results[0].code == "unattributed_invalid_record"


@pytest.mark.parametrize("reference_records", [[], [{"id": "a", "type": "number", "answer": True}]])
def test_invalid_or_missing_references_are_not_scored(reference_records):
    report = evaluate_batch([question("a")], records(*reference_records), records(), policy())
    assert report.results[0].status == "invalid_reference"
    assert report.provisional_pa is None


def test_unexpected_prediction_ids_block_complete_report():
    report = evaluate_batch(
        [question("a")],
        records({"id": "a", "type": "number", "answer": 1}),
        records({"id": "unexpected", "type": "number", "answer": 1}),
        policy(),
    )
    assert any(issue.code == "unexpected_prediction_id" for issue in report.issues)
    assert report.provisional_pa is None


def test_duplicate_questions_fail_before_evaluation():
    with pytest.raises(ValueError, match="duplicate question"):
        evaluate_batch([question("a"), question("a")], records(), records(), policy())


def test_empty_batch_has_no_division_or_fabricated_score():
    report = evaluate_batch([], records(), records(), policy())
    assert report.total_questions == 0
    assert report.provisional_pa is None
