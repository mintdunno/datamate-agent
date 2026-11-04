"""Strict trace/run contracts prevent accidental reference payload propagation."""

import json
from datetime import UTC, datetime
from io import StringIO

import pytest
from pydantic import ValidationError

from datamate.contracts.answers import parse_submission
from datamate.evaluation.references import ReferenceAnswer
from datamate.observability import (
    QuestionFinished,
    QuestionStarted,
    RunMetadata,
    ToolFinished,
    serialize_run_metadata,
    write_trace,
)


def envelope():
    return {
        "run_id": "run-demo",
        "question_id": "q-demo",
        "timestamp": datetime(2026, 11, 4, tzinfo=UTC),
    }


def metadata():
    return {
        "run_id": "run-demo",
        "started_at": datetime(2026, 11, 4, tzinfo=UTC),
        "split_id": "train",
        "inputs_sha256": "a" * 64,
        "rulebook_sha256": "b" * 64,
        "calendar_sha256": "c" * 64,
        "configuration_sha256": "d" * 64,
        "code_revision": "e" * 40,
        "code_dirty": False,
        "python_version": "3.13.7",
        "platform": "synthetic",
        "dependency_versions": {"pydantic": "2.14.2"},
    }


def test_run_metadata_preserves_reproducibility_without_environment_dump():
    run = RunMetadata.model_validate(metadata())
    value = json.loads(serialize_run_metadata(run))
    assert value["schema_version"] == "1"
    assert value["code_revision"] == "e" * 40
    assert value["dependency_versions"] == {"pydantic": "2.14.2"}
    assert value["started_at"] == "2026-11-04T00:00:00Z"
    assert "model" not in value


@pytest.mark.parametrize(
    "field,value",
    [
        ("started_at", datetime(2026, 11, 4)),
        ("inputs_sha256", "not-a-hash"),
        ("code_revision", "main"),
        ("code_dirty", "false"),
        ("api_key", "synthetic-secret"),
        ("reference_answers", []),
        ("seed", -1),
    ],
)
def test_run_metadata_rejects_ambiguous_or_forbidden_fields(field, value):
    with pytest.raises(ValidationError):
        RunMetadata.model_validate({**metadata(), field: value})


def test_trace_writes_one_jsonl_record_and_flushes_before_close(tmp_path):
    answer = parse_submission(
        {"id": "q-demo", "type": "unanswerable", "reason": "Thiếu\nthông tin"}
    )
    event = QuestionFinished(
        **envelope(),
        event="question_finished",
        status="answered",
        elapsed_ms=1.5,
        final_answer=answer,
    )
    path = tmp_path / "trace.jsonl"
    with path.open("w", encoding="utf-8") as stream:
        write_trace(stream, event)
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        value = json.loads(lines[0])
        assert value["final_answer"] == {
            "id": "q-demo",
            "type": "unanswerable",
            "reason": "Thiếu\nthông tin",
        }
        assert value["schema_version"] == "1"


@pytest.mark.parametrize(
    "final_answer,status",
    [
        ({"id": "other", "type": "number", "answer": 1}, "answered"),
        ({"id": "q-demo", "type": "unanswerable"}, "failed"),
        ({"id": "q-demo", "type": "number", "answer": 1}, "failed"),
    ],
)
def test_final_trace_checks_identity_and_failure_abstention(final_answer, status):
    with pytest.raises(ValidationError):
        QuestionFinished(
            **envelope(),
            event="question_finished",
            status=status,
            elapsed_ms=1,
            final_answer=final_answer,
        )


def test_trace_rejects_reference_objects_and_alias_fields():
    reference = ReferenceAnswer(id="q-demo", type="string", answer="A", alias=["CODE"])
    with pytest.raises(ValidationError):
        QuestionFinished(
            **envelope(),
            event="question_finished",
            status="answered",
            elapsed_ms=1,
            final_answer=reference,
        )
    with pytest.raises(ValidationError):
        QuestionStarted(
            **envelope(),
            event="question_started",
            business_id="shop-demo",
            as_of_date="2025-01-01",
            alias=["CODE"],
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"elapsed_ms": -1},
        {"elapsed_ms": float("nan")},
        {"succeeded": False},
        {"error_code": "error"},
        {"result_row_count": 2, "returned_row_count": 3},
        {"result_row_count": 3, "returned_row_count": 2, "truncated": False},
    ],
)
def test_tool_trace_validates_errors_resource_counts_and_truncation(changes):
    with pytest.raises(ValidationError):
        ToolFinished(
            **{
                **envelope(),
                "event": "tool_finished",
                "tool_name": "execute_sql",
                "elapsed_ms": 1,
                "succeeded": True,
                **changes,
            }
        )


def test_tool_trace_records_bounded_execution_metadata():
    event = ToolFinished(
        **envelope(),
        event="tool_finished",
        tool_name="execute_sql",
        elapsed_ms=1,
        succeeded=True,
        sql="SELECT 1",
        arguments_sha256="f" * 64,
        result_row_count=10,
        returned_row_count=2,
        truncated=True,
    )
    assert event.truncated
    assert event.sql == "SELECT 1"


def test_runtime_failure_trace_emits_abstain_and_error_code():
    event = QuestionFinished(
        **envelope(),
        event="question_finished",
        status="failed",
        elapsed_ms=2,
        final_answer={"id": "q-demo", "type": "abstain"},
        error_code="deadline_exceeded",
        usage={"requests": 1, "input_tokens": 20, "output_tokens": 2},
    )
    stream = StringIO()
    write_trace(stream, event)
    value = json.loads(stream.getvalue())
    assert value["final_answer"] == {"id": "q-demo", "type": "abstain"}
    assert value["error_code"] == "deadline_exceeded"
    assert value["usage"]["requests"] == 1


@pytest.mark.parametrize(
    "usage",
    [
        {"requests": -1},
        {"requests": True},
        {"requests": 1, "input_tokens": -1},
    ],
)
def test_model_usage_rejects_invalid_counters(usage):
    with pytest.raises(ValidationError):
        QuestionFinished(
            **envelope(),
            event="question_finished",
            status="answered",
            elapsed_ms=1,
            final_answer={"id": "q-demo", "type": "abstain"},
            usage=usage,
        )


def test_model_metadata_cannot_accept_provider_credentials():
    with pytest.raises(ValidationError):
        RunMetadata.model_validate(
            {
                **metadata(),
                "model": {"provider": "synthetic", "model_id": "demo", "api_key": "never-log"},
            }
        )


def test_trace_revalidates_mutated_payload_before_writing_any_bytes():
    event = QuestionFinished(
        **envelope(),
        event="question_finished",
        status="answered",
        elapsed_ms=1,
        final_answer={"id": "q-demo", "type": "list", "answer": [1]},
    )
    event.final_answer.answer.append(float("nan"))
    stream = StringIO()
    with pytest.raises(ValidationError):
        write_trace(stream, event)
    assert stream.getvalue() == ""
