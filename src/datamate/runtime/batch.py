"""Sequential, durable batch execution over explicit solver inputs only."""

import hashlib
import json
import os
import platform
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from datamate.contracts.answers import parse_submission, serialize_submission
from datamate.contracts.inputs import Question
from datamate.inference.budget import BudgetExceeded, RequestBudget
from datamate.inference.client import model_settings
from datamate.observability import ModelUsage, QuestionFinished, QuestionStarted, ToolFinished
from datamate.preparation.discovery import discover_business
from datamate.preparation.pipeline import prepare_business
from datamate.runtime.metadata import collect_metadata
from datamate.runtime.solver import INSTRUCTIONS, solve_question
from datamate.runtime.tools import StructuredTools


class _ArtifactChanged(Exception):
    pass


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _sources(split_root: Path, business_ids: set[str], split_id: str) -> dict:
    fingerprints = {}
    for business_id in sorted(business_ids):
        inputs = discover_business(split_root, business_id, split_id=split_id)
        fingerprints[business_id] = {
            item.relative_path: item.sha256 for item in (*inputs.sources, *inputs.documents)
        }
    return fingerprints


def _write_run(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        stream.write(_json(data) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _append(stream, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _trace(stream, event) -> None:
    _append(stream, event.model_dump_json(exclude_none=True))


def _error_category(name: str) -> str:
    categories = {
        "BudgetExceeded": "budget_exhausted",
        "TimeoutError": "timeout",
        "ContextBudgetExceeded": "context_budget_exceeded",
        "UsageLimitExceeded": "usage_limit_exceeded",
        "UnexpectedModelBehavior": "model_behavior_error",
        "ModelHTTPError": "model_http_error",
        "ValueError": "validation_error",
        "RuntimeError": "runtime_error",
    }
    return categories.get(name, "question_failed")


def _dependencies() -> dict:
    result = {}
    for name in (
        "pydantic",
        "pydantic-ai-slim",
        "openai",
        "httpx",
        "duckdb",
        "sqlglot",
        "polars",
        "openpyxl",
        "pytz",
        "rapidfuzz",
    ):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            continue
    return result


def _completed(path: Path, questions: list[Question]) -> set[str]:
    if not path.exists():
        return set()
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("incomplete prediction record; repair required before resume")
    completed = set()
    for index, line in enumerate(data.splitlines()):
        answer = parse_submission(json.loads(line))
        if index >= len(questions) or answer.id != questions[index].id or answer.id in completed:
            raise ValueError("prediction records do not match question sequence")
        completed.add(answer.id)
    return completed


async def run_batch(
    questions: list[Question],
    *,
    split_root: Path,
    state_root: Path,
    output_dir: Path,
    model,
    profile,
    budget: RequestBudget,
    split_id: str = "train",
    max_requests_per_question: int = 8,
    deadline: float = 120,
    max_questions: int = 100,
    resume: bool = False,
    inference_settings: dict | None = None,
) -> dict:
    if not questions or not 1 <= max_questions <= 500 or len(questions) > max_questions:
        raise ValueError("question count exceeds configured limit (maximum 500)")
    if max_requests_per_question < 1 or deadline <= 0:
        raise ValueError("question request limit and deadline must be positive")
    if any(not isinstance(question, Question) for question in questions):
        raise ValueError("questions must be validated Question instances")
    if len({q.id for q in questions}) != len(questions):
        raise ValueError("duplicate question IDs")
    split_root = Path(split_root).resolve(strict=True)
    output_dir = Path(output_dir).resolve()
    state_root = Path(state_root).resolve()
    if output_dir.is_relative_to(split_root) or state_root.is_relative_to(split_root):
        raise ValueError("outputs and state must be outside source split")
    sources = _sources(split_root, {q.business_id for q in questions}, split_id)
    implementation = {
        str(path.relative_to(Path(__file__).parents[1])): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for directory in (
            Path(__file__).parent,
            Path(__file__).parents[1] / "preparation",
            Path(__file__).parents[1] / "inference",
            Path(__file__).parents[1] / "contracts",
        )
        for path in sorted(directory.glob("*.py"))
    }
    lock = Path(__file__).resolve().parents[3] / "uv.lock"
    dependencies = _dependencies()
    config = {
        "suite_sha256": _hash([q.model_dump(mode="json") for q in questions]),
        "profile_sha256": _hash(profile.model_dump(mode="json")),
        "sources": sources,
        "implementation": implementation,
        "dependencies": dependencies,
        "python_version": platform.python_version(),
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None,
        "inference_settings": inference_settings or {},
        "split_id": split_id,
        "budget": {
            "run_id": budget.run_id,
            "path": str(budget.path.resolve()),
            "rpm": budget.rpm,
            "daily_limit": budget.daily_limit,
            "run_limit": budget.run_limit,
            "max_wait_seconds": budget.max_wait_seconds,
        },
        "max_requests_per_question": max_requests_per_question,
        "deadline": deadline,
        "max_questions": max_questions,
    }
    fingerprint = _hash(config)
    run_path = output_dir / "run.json"
    old = {}
    if output_dir.exists() and any(output_dir.iterdir()):
        if not resume:
            raise ValueError("output directory is not empty; explicit resume required")
        if not run_path.is_file():
            raise ValueError("missing run metadata")
        old = json.loads(run_path.read_text(encoding="utf-8"))
        if old.get("configuration_sha256") != fingerprint:
            raise ValueError("resume configuration or source fingerprints changed")
    elif resume:
        raise ValueError("cannot resume without existing run metadata")
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "predictions.jsonl"
    done = _completed(prediction_path, questions) if resume else set()
    metadata = {
        "configuration_sha256": fingerprint,
        "configuration": config,
        "implementation": implementation,
        "budget_policy": {
            key: value for key, value in config["budget"].items() if key not in {"path", "run_id"}
        },
        "source_fingerprints": sources,
        "artifact_fingerprints": old.get("artifact_fingerprints", {}),
        "profile": profile.model_dump(mode="json"),
        "run_id": budget.run_id,
        "status": "running",
        "completed": len(done),
        "total": len(questions),
        "elapsed_seconds": old.get("elapsed_seconds", 0),
        "tool_errors": old.get("tool_errors", 0),
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "dependency_versions": dependencies,
    }
    _write_run(run_path, metadata)
    if not resume:
        observed = collect_metadata(
            run_id=budget.run_id,
            split_id=split_id,
            sources=sources,
            configuration_sha256=fingerprint,
            prompt_sha256=metadata["prompt_sha256"],
            profile=profile,
            dependency_versions=dependencies,
        )
        if observed is not None:
            _write_run(
                output_dir / "metadata.json", observed.model_dump(mode="json", exclude_none=True)
            )
    prepared = {}
    settings = model_settings(profile)
    with (
        prediction_path.open("a", encoding="utf-8", newline="\n") as predictions,
        (output_dir / "traces.jsonl").open("a", encoding="utf-8", newline="\n") as traces,
    ):
        os.chmod(prediction_path, 0o600)
        os.chmod(output_dir / "traces.jsonl", 0o600)
        for question in questions:
            if question.id in done:
                continue
            snapshot = budget.snapshot()
            if (
                snapshot["run_attempts"] >= budget.run_limit
                or snapshot["day_attempts"] >= budget.daily_limit
            ):
                metadata["status"] = "budget_exhausted"
                break
            error = None
            started = time.monotonic()
            result = None
            if (
                _sources(split_root, {question.business_id}, split_id)[question.business_id]
                != sources[question.business_id]
            ):
                metadata["status"] = "source_changed"
                _write_run(run_path, metadata)
                break
            _trace(
                traces,
                QuestionStarted(
                    event="question_started",
                    run_id=budget.run_id,
                    question_id=question.id,
                    timestamp=datetime.now(UTC),
                    business_id=question.business_id,
                    as_of_date=question.as_of_date,
                ),
            )
            try:
                if question.business_id not in prepared:
                    prepared[question.business_id] = prepare_business(
                        split_root, question.business_id, state_root, split_id=split_id
                    )
                artifact = prepared[question.business_id]
                previous = metadata["artifact_fingerprints"].get(question.business_id)
                if previous is not None and previous != artifact.fingerprint:
                    raise _ArtifactChanged("prepared artifact changed")
                metadata["artifact_fingerprints"][question.business_id] = artifact.fingerprint
                tools = StructuredTools(artifact.database, artifact.catalog)
                result = await solve_question(
                    question,
                    tools,
                    model,
                    deadline=deadline,
                    max_requests=max_requests_per_question,
                    model_settings=settings,
                )
                answer = parse_submission(result.answer)
                if answer.id != question.id:
                    raise ValueError("solver returned mismatched ID")
                if result.error_code:
                    error = _error_category(result.error_code)
                    answer = parse_submission({"id": question.id, "type": "abstain"})
            except _ArtifactChanged:
                metadata["status"] = "artifact_changed"
                break
            except BudgetExceeded:
                answer = parse_submission({"id": question.id, "type": "abstain"})
                error = "budget_exhausted"
            except Exception as exc:
                answer = parse_submission({"id": question.id, "type": "abstain"})
                error = _error_category(type(exc).__name__)
            _append(predictions, serialize_submission(answer))
            elapsed = time.monotonic() - started
            metadata["elapsed_seconds"] += elapsed
            events = getattr(result, "tool_events", None)
            for execution in (
                events if events is not None else getattr(result, "tool_executions", [])
            ):
                output = execution.get("result", {})
                failed = bool(execution.get("error_code")) or "error" in output
                metadata["tool_errors"] += int(failed)
                sql = execution.get("sql", "")
                _trace(
                    traces,
                    ToolFinished(
                        event="tool_finished",
                        run_id=budget.run_id,
                        question_id=question.id,
                        timestamp=datetime.now(UTC),
                        tool_name=execution.get("tool_name", "execute_sql"),
                        elapsed_ms=execution.get("elapsed_ms", 0),
                        succeeded=not failed,
                        arguments_sha256=execution.get("arguments_sha256")
                        or _hash({"sql": sql, "parameters": execution.get("parameters", [])}),
                        returned_row_count=execution.get(
                            "returned_row_count", len(output.get("rows", []))
                        ),
                        truncated=execution.get("truncated", output.get("truncated", False)),
                        error_code=execution.get("error_code")
                        or ("query_execution_failed" if failed else None),
                    ),
                )
            after = budget.snapshot()
            usage = ModelUsage(
                requests=after["run_attempts"] - snapshot["run_attempts"],
                input_tokens=(after["run_input_tokens"] - snapshot["run_input_tokens"])
                if after["run_usage_responses"] > snapshot["run_usage_responses"]
                else None,
                output_tokens=(after["run_output_tokens"] - snapshot["run_output_tokens"])
                if after["run_usage_responses"] > snapshot["run_usage_responses"]
                else None,
            )
            _trace(
                traces,
                QuestionFinished(
                    event="question_finished",
                    run_id=budget.run_id,
                    question_id=question.id,
                    timestamp=datetime.now(UTC),
                    status="failed" if error else "answered",
                    elapsed_ms=elapsed * 1000,
                    final_answer=answer,
                    usage=usage,
                    error_code=error,
                ),
            )
            metadata["completed"] += 1
            _write_run(run_path, metadata)
            if error == "budget_exhausted":
                metadata["status"] = "budget_exhausted"
                break
        else:
            metadata["status"] = "completed"
    _write_run(run_path, metadata)
    return metadata
