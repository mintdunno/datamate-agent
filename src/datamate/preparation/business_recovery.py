"""Bounded business-level scheduling around the audited mapping recovery API."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
from httpx import AsyncBaseTransport

from datamate.inference.budget import RequestBudget
from datamate.inference.config import ModelProfile
from datamate.preparation.discovery import discover_business
from datamate.preparation.orchestration import prepare_with_mapping_recovery
from datamate.preparation.pipeline import PreparedBusiness, prepare_business
from datamate.preparation.v2 import PreparationPolicy


@dataclass(frozen=True)
class BusinessRecoveryResult:
    baseline: PreparedBusiness
    prepared: PreparedBusiness
    status: str
    stages: tuple[dict, ...]


def _inventory(inputs) -> tuple:
    return tuple(sorted((s.relative_path, s.sha256) for s in (*inputs.sources, *inputs.documents)))


def _tables(prepared: PreparedBusiness, attempted: set[str]) -> list[str]:
    candidates = {
        i["scope"]
        for i in prepared.catalog.get("issues", ())
        if i["kind"] in {"unmapped_role", "unresolved_preheader_metadata"}
    } - attempted
    if not candidates:
        return []
    roles = {table: set() for table in candidates}
    # Read only accepted role metadata, never physical values or QA records.
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        rows = db.execute(
            "SELECT DISTINCT json_extract_string(binding_json, '$.table_id'), "
            "json_extract_string(binding_json, '$.value') FROM meta.bindings "
            "WHERE status = 'ACCEPTED' "
            "AND json_extract_string(binding_json, '$.kind') = 'role'"
        ).fetchall()
    for table, role in rows:
        if table in roles:
            roles[table].add(role)

    def priority(table):
        if any(
            i["scope"] == table and i["kind"] == "unresolved_preheader_metadata"
            for i in prepared.catalog.get("issues", ())
        ):
            return -1, table
        assigned = roles[table]
        transaction = "quantity" in assigned and assigned & {"order_id", "line_net_amount", "unit"}
        named_units = "unit" in assigned and assigned & {"product_id", "product_name"}
        if "unit_factor" not in assigned and (transaction or named_units):
            return 0, table
        if assigned & {"unit_factor", "base_unit", "quantity_base"}:
            return 1, table
        return 2, table

    return sorted(candidates, key=priority)


def _exhausted(snapshot: dict) -> bool:
    return any(
        snapshot.get(key, 1) <= 0
        for key in ("run_remaining", "day_remaining", "campaign_remaining", "phase_remaining")
    )


async def prepare_business_with_recovery(
    split_root: Path,
    business_id: str,
    cache_root: Path,
    *,
    split_id: str,
    run_dir: Path,
    allow_model_calls: bool = False,
    profile: ModelProfile | None = None,
    budget: RequestBudget | None = None,
    env_file: Path | None = None,
    max_tables: int = 3,
    policy: PreparationPolicy | None = None,
    transport: AsyncBaseTransport | None = None,
) -> BusinessRecoveryResult:
    """Try each selected table once, retaining the last audited mapping candidate.

    The mapping API owns evidence, validation and rebuilding and makes one actual
    request with zero format/transport retries. This wrapper never changes models,
    provider settings, or request budgets. Mapping acceptance is not readiness.
    """
    if type(max_tables) is not int or not 1 <= max_tables <= 8:
        raise ValueError("max_tables must be an integer from 1 through 8")
    if allow_model_calls and (profile is None or budget is None):
        raise ValueError("model calls require an explicit profile and budget")
    if budget is not None and budget.rpm > 40:
        raise ValueError("provider rate cannot exceed 40 RPM")
    policy = PreparationPolicy() if policy is None else PreparationPolicy.model_validate(policy)
    if policy.mode != "registry":
        raise ValueError("business recovery requires registry preparation")
    inputs = discover_business(split_root, business_id, split_id=split_id)
    inventory = _inventory(inputs)
    cache_root, run_dir = Path(cache_root).resolve(), Path(run_dir).resolve()
    if any(p.is_relative_to(inputs.split_root.resolve()) for p in (cache_root, run_dir)):
        raise ValueError("outputs must remain outside the source split")
    if budget is not None and hasattr(budget, "path"):
        if budget.path.resolve().is_relative_to(inputs.split_root.resolve()):
            raise ValueError("ledger must remain outside the source split")
    run_dir.mkdir(parents=True, exist_ok=False)
    before = budget.snapshot() if budget else {}

    def unchanged():
        if _inventory(discover_business(split_root, business_id, split_id=split_id)) != inventory:
            raise ValueError("source snapshot changed during business recovery")

    baseline = await asyncio.to_thread(
        prepare_business, split_root, business_id, cache_root, split_id=split_id, policy=policy
    )
    unchanged()
    selected = baseline
    stages = []
    attempted: set[str] = set()
    if allow_model_calls:
        for _ in range(max_tables):
            if _exhausted(budget.snapshot()):
                break
            tables = _tables(selected, attempted)
            if not tables:
                break
            table = tables[0]
            attempted.add(table)
            # The selected immutable artifact is authoritative for cumulative policy.
            current_policy = PreparationPolicy.model_validate_json(
                json.dumps(selected.catalog["inputs"]["policy"])
            )
            stage_dir = run_dir / f"stage-{len(stages) + 1:02d}"
            stage_before = budget.snapshot()
            stage = {"table_id": table, "status": "PARTIAL", "accepted": False}
            failed = False
            unchanged()
            try:
                result = await prepare_with_mapping_recovery(
                    split_root,
                    business_id,
                    cache_root,
                    split_id=split_id,
                    run_dir=stage_dir,
                    table_id=table,
                    allow_model_calls=True,
                    profile=profile,
                    budget=budget,
                    policy=current_policy,
                    transport=transport,
                    env_file=env_file,
                )
                report_path = stage_dir / "report.json"
                if report_path.stat().st_size > 1024 * 1024:
                    raise ValueError("mapping stage report exceeds wrapper bound")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                # Never copy proposal text, exception payloads, model configuration,
                # or arbitrary report fields into the business report.
                failure = report.get("proposal_failure")
                failed = bool(failure)
                stage["proposal_failed"] = failed
                stage["budget_exhausted"] = (
                    isinstance(failure, dict) and failure.get("code") == "BUDGET_EXCEEDED"
                )
                stage["reported_http_attempts"] = int(report.get("http_attempts", 0))
                if result.status == "MAPPING_ACCEPTED" and not failed:
                    selected = result.prepared
                    stage.update(status="MAPPING_ACCEPTED", accepted=True)
            except Exception as error:
                stage["error_type"] = type(error).__name__
                # Stage failures cannot replace the last successfully audited artifact.
                # Diagnostic payloads remain outside the wrapper's bounded report.
                stage["status"] = "STAGE_FAILED"
                failed = True
            unchanged()
            stage_after = budget.snapshot()
            stage["http_attempts"] = stage_after["run_attempts"] - stage_before["run_attempts"]
            stages.append(stage)
            # All proposal failures stop scheduling, including provider/budget failures.
            if failed or stage["http_attempts"] > 1:
                break
    unchanged()
    after = budget.snapshot() if budget else {}
    status = "PARTIAL"
    if not selected.catalog.get("issues") and selected.catalog.get("status") == "READY":
        status = "READY"
    report = {
        "status": status,
        "baseline_fingerprint": baseline.fingerprint,
        "selected_fingerprint": selected.fingerprint,
        "stages": stages,
        "http_attempts": after.get("run_attempts", 0) - before.get("run_attempts", 0),
        "input_tokens": after.get("run_input_tokens", 0) - before.get("run_input_tokens", 0),
        "output_tokens": after.get("run_output_tokens", 0) - before.get("run_output_tokens", 0),
        "accounting": {
            key: after[key]
            for key in (
                "run_attempts",
                "run_input_tokens",
                "run_output_tokens",
                "run_usage_responses",
                "run_remaining",
                "day_remaining",
                "campaign_remaining",
                "phase_remaining",
            )
            if key in after
        },
    }
    with (run_dir / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
    return BusinessRecoveryResult(baseline, selected, status, tuple(stages))
