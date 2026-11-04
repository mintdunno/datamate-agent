"""Question input contracts contain no reference-answer capabilities."""

from typing import Literal

from datamate.contracts.base import ContractModel, DayString, NonblankString, Sha256


class Question(ContractModel):
    """A question's as_of_date is its explicit temporal reference, not wall clock."""

    id: NonblankString
    business_id: NonblankString
    question: NonblankString
    as_of_date: DayString


class InputSnapshot(ContractModel):
    """Solver-safe data binding; contains no labels, reference hashes or evaluator strata."""

    schema_version: Literal["1"] = "1"
    questions_sha256: Sha256
    sources: dict[NonblankString, dict[NonblankString, Sha256]]
