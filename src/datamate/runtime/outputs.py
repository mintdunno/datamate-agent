"""Small tool schemas bind IDs/types in code and validate the official wire contracts."""

from pydantic import StrictBool, ValidationError
from pydantic_ai import ModelRetry

from datamate.contracts.answers import (
    FiniteJsonValue,
    NumberValue,
    SubmissionAnswer,
    TableValue,
    parse_submission,
)


def output_functions(question_id: str) -> list:
    def finish(kind: str, **fields) -> SubmissionAnswer:
        try:
            return parse_submission({"id": question_id, "type": kind, **fields})
        except ValidationError as error:
            raise ModelRetry("Invalid strict answer shape or value; correct the output.") from error

    def answer_number(answer: NumberValue) -> SubmissionAnswer:
        """Finish with a finite JSON number, in the unit requested by the question."""
        return finish("number", answer=answer)

    def answer_percent(answer: NumberValue) -> SubmissionAnswer:
        """Finish with percentage units, e.g. 23.5 means 23.5%, not 0.235."""
        return finish("percent", answer=answer)

    def answer_string(answer: str) -> SubmissionAnswer:
        """Finish with an exact evidence-backed string."""
        return finish("string", answer=answer)

    def answer_date(answer: str) -> SubmissionAnswer:
        """Finish with a valid ISO YYYY-MM-DD or YYYY-MM date."""
        return finish("date", answer=answer)

    def answer_boolean(answer: StrictBool) -> SubmissionAnswer:
        """Finish with JSON true/false, not text or a number."""
        return finish("boolean", answer=answer)

    def answer_list(answer: list[FiniteJsonValue]) -> SubmissionAnswer:
        """Finish with a list ordered exactly as the supporting SQL results."""
        return finish("list", answer=answer)

    def answer_table(answer: TableValue) -> SubmissionAnswer:
        """Finish with columns and rectangular rows matching the supporting SQL result."""
        return finish("table", answer=answer.model_dump(mode="json"))

    def answer_unanswerable(reason: str) -> SubmissionAnswer:
        """Facts are absent or outside coverage; explain evidence of absence."""
        return finish("unanswerable", reason=reason)

    def abstain() -> SubmissionAnswer:
        """Stop when reasoning, rules, or resource limits prevent a justified answer."""
        return finish("abstain")

    return [
        answer_number,
        answer_percent,
        answer_string,
        answer_date,
        answer_boolean,
        answer_list,
        answer_table,
        answer_unanswerable,
        abstain,
    ]
