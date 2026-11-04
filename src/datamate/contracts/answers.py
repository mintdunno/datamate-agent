"""Strict submission shapes; matching and reference aliases belong to evaluation."""

import json
import math
import re
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from datamate.contracts.base import ContractModel, NonblankString, validate_day


def finite_json(value: object) -> object:
    """Reject non-JSON Python values and non-finite numbers at any nesting level."""
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        for item in value:
            finite_json(item)
        return value
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            finite_json(item)
        return value
    raise ValueError("expected a finite JSON value")


def json_number(value: object) -> object:
    """JSON booleans are not numbers, even though bool subclasses int in Python."""
    if type(value) not in (int, float):
        raise ValueError("expected a JSON number")
    return finite_json(value)


NumberValue = Annotated[int | float, BeforeValidator(json_number)]
FiniteJsonValue = Annotated[JsonValue, BeforeValidator(finite_json)]


def answer_date(value: str) -> str:
    """Train rulebook section 9 allows full dates and month-precision dates."""
    if re.fullmatch(r"\d{4}-\d{2}", value, flags=re.ASCII):
        year, month = map(int, value.split("-"))
        date(year, month, 1)
        return value
    return validate_day(value)


class TableValue(ContractModel):
    """Validate rectangular JSON rows without guessing column or cell semantics."""

    columns: list[str]
    rows: list[list[FiniteJsonValue]]

    @model_validator(mode="after")
    def rectangular_rows(self) -> Self:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row must have the same width as columns")
        return self


class AnswerBase(ContractModel):
    id: NonblankString


class NumberAnswer(AnswerBase):
    type: Literal["number"]
    answer: NumberValue


class PercentAnswer(AnswerBase):
    type: Literal["percent"]
    answer: NumberValue


class StringAnswer(AnswerBase):
    type: Literal["string"]
    answer: str


class DateAnswer(AnswerBase):
    type: Literal["date"]
    answer: Annotated[str, AfterValidator(answer_date)]


class BooleanAnswer(AnswerBase):
    type: Literal["boolean"]
    answer: bool


class ListAnswer(AnswerBase):
    type: Literal["list"]
    answer: list[FiniteJsonValue]


class TableAnswer(AnswerBase):
    type: Literal["table"]
    answer: TableValue


class UnanswerableAnswer(AnswerBase):
    type: Literal["unanswerable"]
    answer: None = None
    reason: str = ""

    @field_validator("reason", mode="before")
    @classmethod
    def provided_reason_is_string(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("reason, when present, must be a string")
        return value


class AbstainAnswer(AnswerBase):
    type: Literal["abstain"]


SubmissionAnswer = Annotated[
    NumberAnswer
    | PercentAnswer
    | StringAnswer
    | DateAnswer
    | BooleanAnswer
    | ListAnswer
    | TableAnswer
    | UnanswerableAnswer
    | AbstainAnswer,
    Field(discriminator="type"),
]
SUBMISSION_ADAPTER = TypeAdapter(SubmissionAnswer)


def parse_submission(record: object) -> SubmissionAnswer:
    """Reject unknown fields, coercion, and unsupported answer discriminators."""
    return SUBMISSION_ADAPTER.validate_python(record)


def serialize_submission(answer: SubmissionAnswer) -> str:
    """Revalidate at the wire boundary; never serialize a reference record."""
    validated = parse_submission(answer)
    return json.dumps(
        validated.model_dump(mode="json", exclude_none=True, exclude_unset=True),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
