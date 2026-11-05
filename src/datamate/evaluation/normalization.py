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
from datamate.preparation.models import TableProfile

SELECTOR_VERSION = "stratified-source-v1"
STRATUM_TARGETS = {"rare": 4, "common": 4, "identity": 4, "temporal": 4, "structure": 4}


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


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


class NormalizationCase(ContractModel):
    case_id: Sha256
    business_id: NonblankString
    partition: Literal["development", "checkpoint"]
    record_id: NonblankString
    column_id: NonblankString
    source: NonblankString
    source_sha256: Sha256
    table_id: NonblankString
    stratum: Literal["rare", "common", "identity", "temporal", "structure"]


class CaseManifest(ContractModel):
    source_policy_digest: Sha256
    profile_digests: tuple[Sha256, ...]
    selector_version: str
    stratum_targets: dict[str, int]
    missing_strata: dict[str, dict[str, int]]
    cases: tuple[NormalizationCase, ...] = Field(min_length=100, max_length=100)

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def select_cases(
    policy: PilotPolicy, sources: SourceSnapshot, profiles: Sequence[TableProfile]
) -> CaseManifest:
    source_digest = freeze_selection_policy(policy, sources)
    if len({profile.table_id for profile in profiles}) != len(profiles):
        raise ValueError("duplicate physical source profile")
    pools = {business: [] for business in policy.business_ids}
    for profile in profiles:
        matches = [
            business
            for business, files in sources.files.items()
            if files.get(profile.source) == profile.source_sha256
        ]
        if not matches:
            raise ValueError("profile does not match frozen source evidence")
        columns = {column.column_id: column for column in profile.columns}
        for sample in profile.samples:
            column = columns[sample.column_id]
            hints = {"rare" if sample.pattern_count <= 2 else "common"}
            if sample.column_id in profile.candidate_keys:
                hints.add("identity")
            header = str(column.source_header.get("value", "")).casefold()
            if set(column.raw_types) & {"date", "datetime", "time"} or any(
                token in header for token in ("date", "ngày", "ngay", "time", "calendar")
            ):
                hints.add("temporal")
            if profile.raw_record_count > profile.row_count:
                hints.add("structure")
            rank = _digest([policy.seed, profile.table_id, sample.record_id, sample.column_id])
            for business in matches:
                pools[business].append((rank, profile, sample, hints))
    cases = []
    deficits = {}
    for business in sorted(pools):
        pool = sorted(pools[business], key=lambda item: item[0])
        selected = []
        seen = set()
        counts = {stratum: 0 for stratum in STRATUM_TARGETS}

        def take(item, stratum, *, seen=seen, counts=counts, selected=selected, business=business):
            _, profile, sample, _ = item
            seen.add(sample.record_id)
            counts[stratum] += 1
            selected.append(
                NormalizationCase(
                    case_id=_digest(
                        [
                            source_digest,
                            SELECTOR_VERSION,
                            business,
                            sample.record_id,
                            sample.column_id,
                            stratum,
                        ]
                    ),
                    business_id=business,
                    partition="checkpoint" if business in policy.checkpoint else "development",
                    record_id=sample.record_id,
                    column_id=sample.column_id,
                    source=profile.source,
                    source_sha256=profile.source_sha256,
                    table_id=profile.table_id,
                    stratum=stratum,
                )
            )

        for stratum, target in STRATUM_TARGETS.items():
            for item in pool:
                if counts[stratum] >= target:
                    break
                if stratum in item[3] and item[2].record_id not in seen:
                    take(item, stratum)
        for item in pool:
            if len(selected) >= policy.cases_per_business:
                break
            if item[2].record_id not in seen:
                take(item, sorted(item[3])[0])
        if len(selected) != policy.cases_per_business:
            raise ValueError("insufficient distinct source records for frozen pilot cases")
        deficits[business] = {
            s: target - counts[s] for s, target in STRATUM_TARGETS.items() if counts[s] < target
        }
        cases.extend(selected)
    # Risk strata are source hints, not verified semantic/difficulty labels. Persist deficits.
    return CaseManifest(
        source_policy_digest=source_digest,
        profile_digests=tuple(sorted(profile.digest for profile in profiles)),
        selector_version=SELECTOR_VERSION,
        stratum_targets=dict(STRATUM_TARGETS),
        missing_strata=deficits,
        cases=tuple(sorted(cases, key=lambda case: case.case_id)),
    )


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
