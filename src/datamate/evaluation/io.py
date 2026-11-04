"""Evaluator-owned JSONL parsing with explicit, content-free diagnostics."""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import JsonValue

from datamate.contracts.answers import finite_json
from datamate.contracts.base import ContractModel


class RecordIssue(ContractModel):
    code: str
    line_number: int | None = None
    question_id: str | None = None


@dataclass
class JsonlRecords:
    """Duplicate IDs are quarantined; no implicit first/last-record-wins policy."""

    records: dict[str, dict[str, JsonValue]] = field(default_factory=dict)
    invalid_ids: set[str] = field(default_factory=set)
    issues: list[RecordIssue] = field(default_factory=list)

    @property
    def has_unattributed_errors(self) -> bool:
        return any(issue.question_id is None for issue in self.issues)


def _unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant")


def read_records(lines: Iterable[str]) -> JsonlRecords:
    """Parse records without deciding undocumented malformed-file scoring policy."""
    batch = JsonlRecords()
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(
                line, object_pairs_hook=_unique_keys, parse_constant=_reject_constant
            )
            finite_json(record)
        except (ValueError, RecursionError):
            batch.issues.append(RecordIssue(code="invalid_json", line_number=line_number))
            continue
        if (
            not isinstance(record, dict)
            or type(record.get("id")) is not str
            or not record["id"].strip()
        ):
            batch.issues.append(RecordIssue(code="invalid_envelope", line_number=line_number))
            continue
        identifier = record["id"]
        if identifier in batch.records or identifier in batch.invalid_ids:
            batch.records.pop(identifier, None)
            batch.invalid_ids.add(identifier)
            batch.issues.append(
                RecordIssue(code="duplicate_id", line_number=line_number, question_id=identifier)
            )
            continue
        batch.records[identifier] = record
    return batch


def load_reference_file(path: Path) -> JsonlRecords:
    """Only evaluation owns reference-file access; callers supply the path explicitly."""
    with path.open(encoding="utf-8-sig") as stream:
        return read_records(stream)
