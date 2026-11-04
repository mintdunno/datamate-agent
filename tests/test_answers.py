"""Synthetic wire-contract tests; no training labels are accessed."""

import json

import pytest
from pydantic import ValidationError

from datamate.contracts.answers import parse_submission, serialize_submission
from datamate.evaluation.references import ReferenceAnswer


@pytest.mark.parametrize(
    "kind,value",
    [
        ("number", 125000),
        ("percent", -12.45),
        ("string", "Trà đào"),
        ("date", "2024-02-29"),
        ("date", "2025-09"),
        ("boolean", True),
        ("list", ["Trà đào", 3, False, None]),
        ("table", {"columns": ["item", "amount"], "rows": [["Trà đào", 12]]}),
    ],
)
def test_answer_round_trips_without_coercion(kind, value):
    record = {"id": "q-demo", "type": kind, "answer": value}
    assert json.loads(serialize_submission(parse_submission(record))) == record


@pytest.mark.parametrize("kind", ["unanswerable", "abstain"])
def test_special_answers_serialize_without_fabricated_value(kind):
    record = {"id": "q-demo", "type": kind}
    assert json.loads(serialize_submission(parse_submission(record))) == record


@pytest.mark.parametrize(
    "kind,value",
    [
        ("number", True),
        ("number", "100"),
        ("number", float("nan")),
        ("number", float("inf")),
        ("percent", "12.4%"),
        ("percent", False),
        ("boolean", 1),
        ("boolean", "true"),
        ("string", ["A"]),
        ("date", "2025-02-29"),
        ("date", "2025-13"),
        ("date", "2025-2-01"),
        ("date", "2025-01-01T00:00:00"),
        ("list", "A"),
        ("list", [float("nan")]),
        ("list", (1, 2)),
        ("table", {"columns": ["x", "y"], "rows": [[1]]}),
        ("table", {"columns": ["x"], "rows": [[float("inf")]]}),
        ("unanswerable", "not null"),
        ("abstain", None),
    ],
)
def test_answer_rejects_invalid_wire_values(kind, value):
    with pytest.raises(ValidationError):
        parse_submission({"id": "q-demo", "type": kind, "answer": value})


def test_unanswerable_reason_is_optional_but_strict():
    result = parse_submission({"id": "q-demo", "type": "unanswerable", "reason": "Thiếu giá vốn"})
    assert json.loads(serialize_submission(result))["reason"] == "Thiếu giá vốn"
    for reason in [None, 1, False, [], {}]:
        with pytest.raises(ValidationError):
            parse_submission({"id": "q-demo", "type": "unanswerable", "reason": reason})


def test_submission_cannot_accept_reference_aliases():
    with pytest.raises(ValidationError):
        parse_submission({"id": "q-demo", "type": "string", "answer": "A", "alias": ["CODE"]})


def test_reference_preserves_scalar_and_observed_nested_aliases():
    scalar = ReferenceAnswer.model_validate(
        {"id": "q-demo", "type": "string", "answer": "A", "alias": ["CODE"]}
    )
    table = ReferenceAnswer.model_validate(
        {
            "id": "q-table",
            "type": "table",
            "answer": {"columns": ["name"], "rows": [["A"], ["B"]]},
            "alias": [["CODE-A"], ["CODE-B"]],
        }
    )
    assert scalar.alias == ["CODE"]
    assert table.alias == [["CODE-A"], ["CODE-B"]]
    with pytest.raises(ValidationError):
        serialize_submission(scalar)


def test_reference_requires_null_for_unanswerable_and_forbids_abstain():
    record = ReferenceAnswer.model_validate(
        {"id": "q-demo", "type": "unanswerable", "answer": None}
    )
    assert record.answer is None
    assert record.alias == []
    for invalid in [
        {"id": "q-demo", "type": "unanswerable", "answer": 0},
        {"id": "q-demo", "type": "abstain", "answer": None},
        {"id": "q-demo", "type": "number", "answer": True},
        {"id": "q-demo", "type": "string", "answer": "A", "alias": [12]},
    ]:
        with pytest.raises(ValidationError):
            ReferenceAnswer.model_validate(invalid)


def test_serialization_revalidates_mutated_nested_values():
    answer = parse_submission({"id": "q-demo", "type": "list", "answer": [1]})
    answer.answer.append(float("nan"))
    with pytest.raises(ValidationError):
        serialize_submission(answer)
