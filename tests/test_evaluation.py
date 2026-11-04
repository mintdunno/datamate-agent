"""Confirmed scoring behavior and explicitly unresolved matching cases."""

import pytest
from pydantic import ValidationError

from datamate.evaluation.references import ReferenceAnswer
from datamate.evaluation.scoring import (
    TRAIN_V1_RULEBOOK_SHA256,
    EvaluationSpec,
    evaluate_prediction,
)


def spec():
    return EvaluationSpec(
        split_id="train", profile="train_rulebook_v1", rulebook_sha256=TRAIN_V1_RULEBOOK_SHA256
    )


def reference(kind="number", value=10, aliases=None):
    return ReferenceAnswer.model_validate(
        {"id": "q-demo", "type": kind, "answer": value, "alias": aliases or []}
    )


@pytest.mark.parametrize(
    "prediction,outcome,code",
    [
        (None, "abstain", "missing_prediction"),
        ({"id": "q-demo", "type": "abstain"}, "abstain", "explicit_abstain"),
        ({"id": "q-demo", "type": "string", "answer": "10"}, "wrong", "type_mismatch"),
        ({"id": "q-demo", "type": "number", "answer": 10}, "correct", "value_match"),
        ({"id": "q-demo", "type": "number", "answer": 9}, "wrong", "value_mismatch"),
        ({"id": "q-demo", "type": "number", "answer": "10"}, "wrong", "invalid_answer_format"),
        ({"id": "q-demo", "type": "number", "answer": True}, "wrong", "invalid_answer_format"),
        (
            {"id": "q-demo", "type": "number", "answer": 10, "reason": 12},
            "abstain",
            "nonstring_reason",
        ),
        ({"id": "other", "type": "number", "answer": 10}, None, "prediction_id_mismatch"),
        ({"id": "q-demo", "type": "unknown", "answer": 10}, None, "invalid_prediction"),
        ({"id": "q-demo", "type": "number", "answer": 10, "alias": []}, None, "invalid_prediction"),
    ],
)
def test_prediction_outcomes_keep_format_errors_distinct(prediction, outcome, code):
    result = evaluate_prediction(reference(), prediction, spec())
    assert result.outcome == outcome
    assert result.code == code
    if code in {"nonstring_reason", "invalid_answer_format", "invalid_prediction"}:
        assert result.status == "invalid_prediction"


@pytest.mark.parametrize("reason", [None, False, 0, [], {}])
def test_nonstring_reason_is_abstain_under_train_rulebook(reason):
    result = evaluate_prediction(
        reference("unanswerable", None),
        {"id": "q-demo", "type": "number", "answer": 10, "reason": reason},
        spec(),
    )
    assert result.outcome == "abstain"


@pytest.mark.parametrize(
    "kind,value,prediction,outcome",
    [
        ("unanswerable", None, {"type": "unanswerable"}, "correct"),
        ("unanswerable", None, {"type": "number", "answer": 0}, "wrong"),
        ("number", 0, {"type": "unanswerable"}, "wrong"),
        ("boolean", False, {"type": "boolean", "answer": False, "reason": "ignored"}, "correct"),
        ("boolean", True, {"type": "boolean", "answer": False}, "wrong"),
        ("date", "2025-09", {"type": "date", "answer": "2025-09"}, "correct"),
        ("date", "2025-09", {"type": "date", "answer": "2025-09-01"}, "wrong"),
        ("percent", 12.4, {"type": "percent", "answer": 12.4}, "correct"),
        ("percent", 12.4, {"type": "percent", "answer": 12.4000000001}, None),
        ("string", "Trà đào!", {"type": "string", "answer": " TRÀ   ĐÀO "}, "correct"),
        ("string", "Trà đào", {"type": "string", "answer": "Tra dao"}, None),
        ("list", ["A", "B"], {"type": "list", "answer": ["B", "A"]}, "wrong"),
        ("list", [3, 10], {"type": "list", "answer": [3, 11]}, "wrong"),
        ("list", [3], {"type": "list", "answer": [3, 4]}, "wrong"),
    ],
)
def test_confirmed_matching_and_ambiguities(kind, value, prediction, outcome):
    result = evaluate_prediction(reference(kind, value), {"id": "q-demo", **prediction}, spec())
    assert result.outcome == outcome
    if outcome is None:
        assert result.status == "unresolved"


def test_scalar_alias_acceptance_does_not_flatten_nested_aliases():
    accepted = evaluate_prediction(
        reference("string", "Trà đào", ["ITEM-1"]),
        {"id": "q-demo", "type": "string", "answer": "item-1"},
        spec(),
    )
    assert accepted.outcome == "correct"
    ambiguous = evaluate_prediction(
        reference("string", "Trà đào", [["ITEM-1"]]),
        {"id": "q-demo", "type": "string", "answer": "ITEM-1"},
        spec(),
    )
    assert ambiguous.outcome is None


@pytest.mark.parametrize(
    "columns,rows,outcome",
    [
        (["name", "amount"], [["B", 2], ["A", 1]], "correct"),
        (["amount", "name"], [[2, "B"], [1, "A"]], "correct"),
        (["label", "total"], [["B", 2], ["A", 1]], "correct"),
        (["name", "total"], [["A", 1], ["B", 2]], None),
        (["name", "name"], [["A", 1], ["B", 2]], None),
        (["name", "amount"], [["A", 5], ["B", 2]], "wrong"),
        (["name", "amount"], [["A", 1]], "wrong"),
    ],
)
def test_table_row_order_columns_and_uncertainty(columns, rows, outcome):
    expected = {"columns": ["name", "amount"], "rows": [["A", 1], ["B", 2]]}
    result = evaluate_prediction(
        reference("table", expected),
        {"id": "q-demo", "type": "table", "answer": {"columns": columns, "rows": rows}},
        spec(),
    )
    assert result.outcome == outcome


def test_observed_table_aliases_do_not_create_global_cross_row_matches():
    result = evaluate_prediction(
        reference(
            "table",
            {"columns": ["name", "amount"], "rows": [["A", 1], ["B", 2]]},
            [["CODE-A"], ["CODE-B"]],
        ),
        {
            "id": "q-demo",
            "type": "table",
            "answer": {"columns": ["name", "amount"], "rows": [["CODE-B", 1], ["CODE-A", 2]]},
        },
        spec(),
    )
    assert result.status == "unresolved"
    assert result.outcome is None


def test_train_matching_requires_explicit_split_binding():
    with pytest.raises(ValidationError):
        EvaluationSpec(split_id="private", profile="train_rulebook_v1", rulebook_sha256="a" * 64)
    global_spec = EvaluationSpec(split_id="future", profile="global")
    result = evaluate_prediction(
        reference(), {"id": "q-demo", "type": "number", "answer": 10}, global_spec
    )
    assert result.outcome is None
    assert result.code == "matching_profile_unavailable"


def test_changed_rulebook_cannot_reuse_train_v1_matching():
    with pytest.raises(ValidationError):
        EvaluationSpec(split_id="train", profile="train_rulebook_v1", rulebook_sha256="0" * 64)


def test_global_profile_does_not_assume_unanswerable_matching():
    result = evaluate_prediction(
        reference("unanswerable", None),
        {"id": "q-demo", "type": "unanswerable"},
        EvaluationSpec(split_id="future"),
    )
    assert result.outcome is None
    assert result.status == "unresolved"


@pytest.mark.parametrize(
    "kind,expected,actual",
    [
        ("list", [{"value": "A"}], [{"value": "a!"}]),
        (
            "table",
            {"columns": ["name", "info"], "rows": [["A", {"value": "B"}]]},
            {"columns": ["name", "info"], "rows": [["A", {"value": "b!"}]]},
        ),
    ],
)
def test_nested_json_does_not_invent_recursive_string_matching(kind, expected, actual):
    result = evaluate_prediction(
        reference(kind, expected), {"id": "q-demo", "type": kind, "answer": actual}, spec()
    )
    assert result.outcome is None
    assert result.status == "unresolved"
