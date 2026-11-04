"""Reference labels and accepted aliases are evaluator-only capabilities."""

from typing import Literal, Self

from pydantic import Field, model_validator

from datamate.contracts.answers import FiniteJsonValue, parse_submission
from datamate.contracts.base import ContractModel, NonblankString


class ReferenceAnswer(ContractModel):
    """Preserve documented scalar and observed nested alias encodings separately.

    Supporting an encoding does not establish its matching/row-association semantics.
    Missing aliases are accepted; references can never have type abstain.
    """

    id: NonblankString
    type: Literal["number", "percent", "string", "date", "boolean", "list", "table", "unanswerable"]
    answer: FiniteJsonValue
    alias: list[str | list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_answer_shape(self) -> Self:
        parse_submission({"id": self.id, "type": self.type, "answer": self.answer})
        return self
