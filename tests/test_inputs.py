"""Input boundary tests use synthetic questions only."""

import pytest
from pydantic import ValidationError

from datamate.contracts.inputs import Question


def test_question_preserves_explicit_reference_date():
    question = Question.model_validate(
        {
            "id": "q-demo",
            "business_id": "shop-demo",
            "question": "Doanh thu?",
            "as_of_date": "2025-02-28",
        }
    )
    assert question.as_of_date == "2025-02-28"
    assert question.model_dump()["question"] == "Doanh thu?"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 1),
        ("business_id", " "),
        ("question", ""),
        ("as_of_date", "2025-02-29"),
        ("as_of_date", "2025-02"),
        ("as_of_date", "28/02/2025"),
    ],
)
def test_question_rejects_invalid_inputs_without_coercion(field, value):
    record = {
        "id": "q-demo",
        "business_id": "shop-demo",
        "question": "Doanh thu?",
        "as_of_date": "2025-02-28",
    }
    record[field] = value
    with pytest.raises(ValidationError):
        Question.model_validate(record)


def test_question_rejects_reference_fields():
    with pytest.raises(ValidationError):
        Question.model_validate(
            {
                "id": "q-demo",
                "business_id": "shop-demo",
                "question": "Doanh thu?",
                "as_of_date": "2025-02-28",
                "answer": 10,
                "alias": ["secret"],
            }
        )
