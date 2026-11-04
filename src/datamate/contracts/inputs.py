"""Question input contracts contain no reference-answer capabilities."""

from datamate.contracts.base import ContractModel, DayString, NonblankString


class Question(ContractModel):
    """A question's as_of_date is its explicit temporal reference, not wall clock."""

    id: NonblankString
    business_id: NonblankString
    question: NonblankString
    as_of_date: DayString
