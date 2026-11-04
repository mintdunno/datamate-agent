"""Question-complete aggregation; partial results never fabricate an official score."""

from collections.abc import Sequence

from pydantic import ValidationError

from datamate.contracts.base import ContractModel
from datamate.contracts.inputs import Question
from datamate.evaluation.io import JsonlRecords, RecordIssue
from datamate.evaluation.references import ReferenceAnswer
from datamate.evaluation.scoring import EvaluationSpec, QuestionEvaluation, evaluate_prediction


class EvaluationReport(ContractModel):
    spec: EvaluationSpec
    results: list[QuestionEvaluation]
    issues: list[RecordIssue]
    total_questions: int
    correct: int
    wrong: int
    abstain: int
    unresolved: int
    invalid_records: int
    known_points: int
    complete: bool
    metric_status: str = "provisional"
    provisional_pa: float | None = None


def evaluate_batch(
    questions: Sequence[Question],
    references: JsonlRecords,
    predictions: JsonlRecords,
    spec: EvaluationSpec,
) -> EvaluationReport:
    """Evaluate IDs from questions, not merely the intersection with available labels."""
    spec = EvaluationSpec.model_validate(spec)
    validated_questions = [Question.model_validate(question) for question in questions]
    identifiers = [question.id for question in validated_questions]
    expected_ids = set(identifiers)
    if len(expected_ids) != len(identifiers):
        raise ValueError("duplicate question IDs")
    issues = [*references.issues, *predictions.issues]
    for records, kind in [(references, "reference"), (predictions, "prediction")]:
        for identifier in sorted((records.records.keys() | records.invalid_ids) - expected_ids):
            issues.append(RecordIssue(code=f"unexpected_{kind}_id", question_id=identifier))
    results = []
    for identifier in identifiers:
        try:
            if identifier in references.invalid_ids or identifier not in references.records:
                raise ValueError("missing or quarantined reference")
            reference = ReferenceAnswer.model_validate(references.records[identifier])
        except (ValidationError, ValueError):
            results.append(
                QuestionEvaluation(
                    question_id=identifier, status="invalid_reference", code="invalid_reference"
                )
            )
            continue
        if identifier in predictions.invalid_ids:
            results.append(
                QuestionEvaluation(
                    question_id=identifier, status="invalid_prediction", code="duplicate_prediction"
                )
            )
        elif identifier not in predictions.records and predictions.has_unattributed_errors:
            results.append(
                QuestionEvaluation(
                    question_id=identifier,
                    status="invalid_prediction",
                    code="unattributed_invalid_record",
                )
            )
        else:
            results.append(
                evaluate_prediction(reference, predictions.records.get(identifier), spec)
            )
    correct = sum(result.outcome == "correct" for result in results)
    wrong = sum(result.outcome == "wrong" for result in results)
    abstain = sum(result.outcome == "abstain" for result in results)
    unresolved = sum(result.outcome is None for result in results)
    complete = not issues and unresolved == 0
    return EvaluationReport(
        spec=spec,
        results=results,
        issues=issues,
        total_questions=len(identifiers),
        correct=correct,
        wrong=wrong,
        abstain=abstain,
        unresolved=unresolved,
        invalid_records=sum(result.status.startswith("invalid_") for result in results),
        known_points=correct - wrong,
        complete=complete,
        provisional_pa=(correct - wrong) / len(identifiers) if complete and identifiers else None,
    )
