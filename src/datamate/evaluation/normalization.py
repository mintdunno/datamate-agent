"""Evaluator-owned normalization selection; production preparation never imports this module."""

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.preparation.discovery import discover_business


def _safe_component(value: str) -> bool:
    return (
        bool(value.strip())
        and value not in {".", ".."}
        and not any(char in value for char in "/\\:\x00")
    )


class PilotPolicy(ContractModel):
    version: int = Field(default=1, ge=1)
    split_id: Literal["train"] = "train"
    development: tuple[NonblankString, ...]
    checkpoint: tuple[NonblankString, ...]
    seed: int = Field(default=42, ge=0)
    cases_per_business: Literal[20] = 20
    minimum_determinate: int = Field(default=10, ge=10)
    campaign_limit: int = Field(default=120, ge=1, le=120)
    phase_limits: dict[str, int] = Field(
        default_factory=lambda: {"smoke": 20, "development": 60, "final": 40}
    )

    @property
    def business_ids(self) -> tuple[str, ...]:
        return self.development + self.checkpoint

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        ids = self.business_ids
        if (
            len(self.development) != 4
            or len(self.checkpoint) != 1
            or len(set(ids)) != 5
            or not all(_safe_component(value) for value in ids)
        ):
            raise ValueError("pilot requires four distinct development and one checkpoint business")
        if self.minimum_determinate > self.cases_per_business:
            raise ValueError("determinate minimum exceeds case count")
        ceilings = {"smoke": 20, "development": 60, "final": 40}
        if set(self.phase_limits) != set(ceilings) or any(
            type(value) is not int or not 1 <= value <= ceilings[key]
            for key, value in self.phase_limits.items()
        ):
            raise ValueError("invalid campaign phase ceilings")
        if sum(self.phase_limits.values()) > self.campaign_limit:
            raise ValueError("phase ceilings exceed campaign total")
        return self


class SourceSnapshot(ContractModel):
    split_id: NonblankString
    files: dict[NonblankString, dict[NonblankString, Sha256]]

    @model_validator(mode="after")
    def allowlisted_sources(self) -> Self:
        for business, files in self.files.items():
            if not _safe_component(business):
                raise ValueError("invalid snapshot business")
            if not {"rulebook.md", "calendar.csv"} <= files.keys():
                raise ValueError("snapshot requires active split rules/calendar")
            for name in files:
                parts = name.split("/")
                if name in {"rulebook.md", "calendar.csv"}:
                    continue
                if (
                    len(parts) < 3
                    or parts[:2] != ["data", business]
                    or not all(_safe_component(part) for part in parts)
                    or Path(name).suffix.lower() not in {".csv", ".xlsx", ".sqlite", ".md"}
                ):
                    raise ValueError("snapshot contains non-source path")
        return self


class CatalogCase(ContractModel):
    case_id: int = Field(ge=1, le=44)
    owner: Literal["N", "M", "Q"]
    task: int = Field(ge=1, le=24)
    test_modules: tuple[NonblankString, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def test_paths(self) -> Self:
        if any(
            not path.startswith("tests/")
            or not path.endswith(".py")
            or not all(_safe_component(part) for part in path.split("/"))
            for path in self.test_modules
        ):
            raise ValueError("catalog tests must be repository test modules")
        return self


def load_case_catalog(path: Path) -> tuple[CatalogCase, ...]:
    cases = TypeAdapter(tuple[CatalogCase, ...]).validate_json(path.read_bytes())
    if len(cases) != 44 or {case.case_id for case in cases} != set(range(1, 45)):
        raise ValueError("catalog must contain all 44 cases exactly once")
    return tuple(sorted(cases, key=lambda case: case.case_id))


def load_pilot_policy(path: Path) -> PilotPolicy:
    return PilotPolicy.model_validate_json(path.read_bytes())


def snapshot_sources(
    split_root: Path, business_ids: Sequence[str], *, split_id: str
) -> SourceSnapshot:
    if len(set(business_ids)) != len(business_ids):
        raise ValueError("duplicate snapshot business")
    files = {}
    for business in sorted(business_ids):
        inputs = discover_business(split_root, business, split_id=split_id)
        files[business] = {
            item.relative_path: item.sha256 for item in (*inputs.sources, *inputs.documents)
        }
    return SourceSnapshot(split_id=split_id, files=files)


def _selection_payload(policy: PilotPolicy, sources: SourceSnapshot) -> bytes:
    # Revalidate nested mappings: frozen Pydantic models do not freeze their dict values.
    policy = PilotPolicy.model_validate_json(policy.model_dump_json())
    sources = SourceSnapshot.model_validate_json(sources.model_dump_json())
    if policy.split_id != sources.split_id:
        raise ValueError("selection split does not match source split")
    if set(policy.business_ids) != set(sources.files):
        raise ValueError("selection businesses do not match snapshot businesses")
    for name in ("rulebook.md", "calendar.csv"):
        if len({files[name] for files in sources.files.values()}) != 1:
            raise ValueError("inconsistent active split source hashes")
    return json.dumps(
        {"policy": policy.model_dump(mode="json"), "sources": sources.model_dump(mode="json")},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def freeze_selection_policy(policy: PilotPolicy, sources: SourceSnapshot) -> str:
    return hashlib.sha256(_selection_payload(policy, sources)).hexdigest()


def write_frozen_selection(path: Path, policy: PilotPolicy, sources: SourceSnapshot) -> str:
    payload = _selection_payload(policy, sources)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".selection-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publishes complete bytes atomically without replacing another writer.
        # Unsupported filesystems fail closed rather than falling back to overwrite/rename.
        os.link(temporary, path)
    finally:
        temporary.unlink()
    return hashlib.sha256(payload).hexdigest()
