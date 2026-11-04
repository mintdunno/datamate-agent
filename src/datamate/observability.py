"""Runtime trace/run contracts contain no reference models or file discovery."""

import json
from typing import Annotated, Literal, Self, TextIO

from pydantic import (
    AwareDatetime,
    Field,
    StringConstraints,
    TypeAdapter,
    field_serializer,
    model_validator,
)

from datamate.contracts.answers import SubmissionAnswer, serialize_submission
from datamate.contracts.base import ContractModel, DayString, NonblankString, Sha256

NonnegativeInt = Annotated[int, Field(ge=0)]
ElapsedMilliseconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
CodeRevision = Annotated[str, StringConstraints(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]


class ModelMetadata(ContractModel):
    """Model identity only; never include provider credentials or environment values."""

    provider: NonblankString
    model_id: NonblankString
    revision: NonblankString | None = None


class ModelUsage(ContractModel):
    input_tokens: NonnegativeInt | None = None
    output_tokens: NonnegativeInt | None = None
    requests: NonnegativeInt


class RunMetadata(ContractModel):
    """Fingerprints refer only to allowed runtime inputs, excluding reference labels."""

    schema_version: Literal["1"] = "1"
    run_id: NonblankString
    started_at: AwareDatetime
    split_id: NonblankString
    inputs_sha256: Sha256
    rulebook_sha256: Sha256
    calendar_sha256: Sha256
    configuration_sha256: Sha256
    code_revision: CodeRevision
    code_dirty: bool
    python_version: NonblankString
    platform: NonblankString
    dependency_versions: dict[NonblankString, NonblankString]
    prompt_sha256: Sha256 | None = None
    seed: NonnegativeInt | None = None
    model: ModelMetadata | None = None


class TraceBase(ContractModel):
    schema_version: Literal["1"] = "1"
    run_id: NonblankString
    question_id: NonblankString
    timestamp: AwareDatetime


class QuestionStarted(TraceBase):
    event: Literal["question_started"]
    business_id: NonblankString
    as_of_date: DayString


class ToolFinished(TraceBase):
    """Persist bounded execution metadata, SQL, and an argument digest instead of raw data."""

    event: Literal["tool_finished"]
    tool_name: NonblankString
    elapsed_ms: ElapsedMilliseconds
    succeeded: bool
    sql: str | None = None
    arguments_sha256: Sha256 | None = None
    result_row_count: NonnegativeInt | None = None
    returned_row_count: NonnegativeInt | None = None
    truncated: bool = False
    error_code: NonblankString | None = None

    @model_validator(mode="after")
    def consistent_execution(self) -> Self:
        if self.succeeded == (self.error_code is not None):
            raise ValueError("failed tools require an error code; successful tools cannot have one")
        if self.result_row_count is not None and self.returned_row_count is not None:
            if self.returned_row_count > self.result_row_count:
                raise ValueError("returned rows cannot exceed result rows")
            if self.returned_row_count < self.result_row_count and not self.truncated:
                raise ValueError("omitted result rows must be marked truncated")
        return self


class QuestionFinished(TraceBase):
    event: Literal["question_finished"]
    status: Literal["answered", "failed"]
    elapsed_ms: ElapsedMilliseconds
    final_answer: SubmissionAnswer
    evidence_ids: list[NonblankString] = Field(default_factory=list)
    usage: ModelUsage | None = None
    error_code: NonblankString | None = None

    @model_validator(mode="after")
    def consistent_answer(self) -> Self:
        if self.final_answer.id != self.question_id:
            raise ValueError("final answer ID must match trace question ID")
        if self.status == "failed":
            if self.final_answer.type != "abstain" or self.error_code is None:
                raise ValueError("runtime failures require abstain and an error code")
        elif self.error_code is not None:
            raise ValueError("answered questions cannot have a final error code")
        return self

    @field_serializer("final_answer")
    def answer_wire_value(self, answer: SubmissionAnswer) -> dict:
        return json.loads(serialize_submission(answer))


TraceEvent = Annotated[
    QuestionStarted | ToolFinished | QuestionFinished, Field(discriminator="event")
]
TRACE_ADAPTER = TypeAdapter(TraceEvent)


def serialize_run_metadata(metadata: RunMetadata) -> str:
    validated = RunMetadata.model_validate(metadata)
    return json.dumps(
        validated.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def write_trace(stream: TextIO, event: TraceEvent) -> None:
    """Validate, append one JSONL record, and flush before accepting another question."""
    validated = TRACE_ADAPTER.validate_python(event)
    stream.write(
        json.dumps(
            validated.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    stream.flush()
