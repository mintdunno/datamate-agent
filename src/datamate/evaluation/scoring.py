"""Per-question outcomes distinguish confirmed rules from unresolved specifications."""

from typing import Annotated, Literal, Self

from pydantic import StringConstraints, ValidationError, model_validator

from datamate.contracts.answers import parse_submission
from datamate.contracts.base import ContractModel, NonblankString
from datamate.evaluation.matching import match_train_value
from datamate.evaluation.references import ReferenceAnswer

Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Outcome = Literal["correct", "wrong", "abstain"]
TRAIN_V1_RULEBOOK_SHA256 = "740267bccba29df70f745bc39968bbec5b3e62dada2c858c45314f3ec723d113"


class EvaluationSpec(ContractModel):
    """Explicit source binding prevents training defaults leaking to future splits."""

    split_id: NonblankString
    profile: Literal["global", "train_rulebook_v1"] = "global"
    rulebook_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def train_binding(self) -> Self:
        if self.profile == "train_rulebook_v1":
            if self.split_id != "train" or self.rulebook_sha256 != TRAIN_V1_RULEBOOK_SHA256:
                raise ValueError(
                    "train profile requires the reviewed train-v1 rulebook fingerprint"
                )
        return self


class QuestionEvaluation(ContractModel):
    question_id: NonblankString
    status: Literal["scored", "invalid_prediction", "invalid_reference", "unresolved"]
    outcome: Outcome | None = None
    code: NonblankString


def evaluate_prediction(
    reference: ReferenceAnswer, prediction: object | None, spec: EvaluationSpec
) -> QuestionEvaluation:
    """Evaluate known semantics without assigning undocumented malformed-record outcomes."""
    reference = ReferenceAnswer.model_validate(reference)
    spec = EvaluationSpec.model_validate(spec)

    def result(outcome: Outcome | None, code: str, status: str = "scored") -> QuestionEvaluation:
        return QuestionEvaluation(
            question_id=reference.id, status=status, outcome=outcome, code=code
        )

    if prediction is None:
        return result("abstain", "missing_prediction")
    if not isinstance(prediction, dict) or prediction.get("id") != reference.id:
        return result(None, "prediction_id_mismatch", "invalid_prediction")
    if "reason" in prediction and type(prediction["reason"]) is not str:
        if spec.profile == "train_rulebook_v1":
            return result("abstain", "nonstring_reason", "invalid_prediction")
        return result(None, "invalid_prediction", "invalid_prediction")
    # A string reason is ignored by automated scoring; it is not restricted to one type.
    payload = {key: value for key, value in prediction.items() if key != "reason"}
    try:
        answer = parse_submission(payload)
    except ValidationError as error:
        locations = [
            item["loc"] for item in error.errors(include_input=False, include_context=False)
        ]
        documented_format_error = all(len(loc) >= 2 and loc[1] == "answer" for loc in locations)
        if documented_format_error and spec.profile == "train_rulebook_v1":
            return result("wrong", "invalid_answer_format", "invalid_prediction")
        return result(None, "invalid_prediction", "invalid_prediction")
    if answer.type == "abstain":
        return result("abstain", "explicit_abstain")
    if answer.type != reference.type:
        return result("wrong", "type_mismatch")
    if spec.profile != "train_rulebook_v1":
        return result(None, "matching_profile_unavailable", "unresolved")
    if answer.type == "unanswerable":
        return result("correct", "unanswerable_match")
    actual = answer.model_dump(mode="json")["answer"]
    matched, code = match_train_value(reference, actual)
    if matched is None:
        return result(None, code, "unresolved")
    return result("correct" if matched else "wrong", code)
