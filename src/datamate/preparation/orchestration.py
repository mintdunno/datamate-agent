"""Bounded adaptive recovery around immutable, independently audited preparation artifacts."""

import asyncio
import inspect
import json
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from time import monotonic

import duckdb
from httpx import AsyncBaseTransport

from datamate.inference.budget import RequestBudget
from datamate.inference.config import ModelProfile
from datamate.preparation.adaptive import (
    RecoveryPolicy,
    RecoveryResult,
    build_evidence_packet,
    cleaning_plan_proposer,
    recover_issue,
)
from datamate.preparation.catalog import compile_clauses
from datamate.preparation.discovery import discover_business
from datamate.preparation.models import EvidenceRef, NormalizationIssue, TableProfile
from datamate.preparation.normalization import CleaningPlan
from datamate.preparation.operations import OPERATION_ORDER, validate_plan
from datamate.preparation.pipeline import PreparedBusiness, _load_cache, prepare_business
from datamate.preparation.v2 import PreparationPolicy, _source_roles


@dataclass(frozen=True)
class IssueSelection:
    issue: NormalizationIssue
    references: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class AdaptivePreparationResult:
    baseline: PreparedBusiness
    prepared: PreparedBusiness
    recovery: RecoveryResult | None
    status: str


def _inventory(inputs):
    return tuple(
        sorted((item.relative_path, item.sha256) for item in (*inputs.sources, *inputs.documents))
    )


def _promote_candidate(
    candidate: PreparedBusiness, cache_root: Path, trials: Path
) -> PreparedBusiness:
    source = candidate.database.parent.resolve()
    _load_cache(source, candidate.fingerprint)
    target = (cache_root / candidate.fingerprint).resolve()
    if not source.is_relative_to(trials.resolve()) or not target.is_relative_to(cache_root):
        raise ValueError("candidate promotion escaped its owned workspace")
    if target.exists():
        return _load_cache(target, candidate.fingerprint)
    source.rename(target)
    try:
        return replace(_load_cache(target, candidate.fingerprint), cache_hit=False)
    except BaseException:
        if source.exists() or not target.resolve().is_relative_to(cache_root):
            raise RuntimeError("cannot restore failed owned promotion") from None
        target.rename(source)
        raise


def _raw_digest(prepared: PreparedBusiness) -> str:
    digest = sha256()
    with duckdb.connect(str(prepared.database), read_only=True) as db:
        cursor = db.execute("SELECT * FROM staging.raw_rows ORDER BY record_id")
        while batch := cursor.fetchmany(1000):
            for row in batch:
                digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
                digest.update(b"\n")
    return digest.hexdigest()


async def prepare_with_recovery(
    split_root: Path,
    business_id: str,
    cache_root: Path,
    *,
    split_id: str,
    run_dir: Path,
    issue_selector: Callable[[PreparedBusiness], IssueSelection | None],
    verifier: Callable[
        [NormalizationIssue, PreparedBusiness, PreparedBusiness], bool | Awaitable[bool]
    ],
    allow_model_calls: bool = False,
    profile: ModelProfile | None = None,
    budget: RequestBudget | None = None,
    policy: PreparationPolicy | None = None,
    transport: AsyncBaseTransport | None = None,
    max_rounds: int = 2,
    logical_requests_per_round: int = 2,
    transport_retries: int = 1,
) -> AdaptivePreparationResult:
    """Select one source issue, trial typed deltas, and publish only verified recovery.

    The selector/verifier are trusted source-only business logic. They never receive
    evaluator records. Evidence comes from the active split snapshot. Registry and
    replay artifacts retain deterministic cache identity; model usage belongs to the
    fresh adaptive run, not a misleading API count on a reused artifact.
    """
    if allow_model_calls and (profile is None or budget is None):
        raise ValueError("adaptive model calls require an explicit profile and budget")
    if budget is not None and budget.rpm > 40:
        raise ValueError("adaptive provider rate cannot exceed 40 RPM")
    policy = PreparationPolicy() if policy is None else PreparationPolicy.model_validate(policy)
    if policy.mode != "registry":
        raise ValueError("adaptive baseline must explicitly use registry preparation")
    bounds = RecoveryPolicy(
        max_rounds=max_rounds,
        logical_requests_per_round=logical_requests_per_round,
        transport_retries=transport_retries,
    )
    inputs = discover_business(split_root, business_id, split_id=split_id)
    initial_inventory = _inventory(inputs)
    cache_root, run_dir = Path(cache_root).resolve(), Path(run_dir).resolve()
    if run_dir.is_relative_to(inputs.split_root.resolve()) or cache_root.is_relative_to(
        inputs.split_root.resolve()
    ):
        raise ValueError("adaptive outputs must remain outside the source split")
    run_dir.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    before_attempts = int(budget.snapshot()["run_attempts"]) if budget else 0

    proposal_trace = []

    def unchanged():
        current = discover_business(split_root, business_id, split_id=split_id)
        if _inventory(current) != initial_inventory:
            raise ValueError("source snapshot changed during adaptive preparation")

    def finish(baseline, selected, recovery, status, reason):
        unchanged()
        result = AdaptivePreparationResult(baseline, selected, recovery, status)
        after_attempts = int(budget.snapshot()["run_attempts"]) if budget else 0
        recovery_record = asdict(recovery) if recovery else None
        if recovery_record is not None and recovery.accepted_plan is not None:
            recovery_record["accepted_plan"] = recovery.accepted_plan.model_dump(mode="json")
        report = {
            "mode": "adaptive",
            "status": status,
            "reason": reason,
            "baseline_fingerprint": baseline.fingerprint,
            "selected_fingerprint": selected.fingerprint,
            "split_id": split_id,
            "business_id": business_id,
            "profile_sha256": sha256(profile.model_dump_json().encode()).hexdigest()
            if profile
            else None,
            "model_id": profile.model_id if profile else None,
            "recovery": recovery_record,
            "proposals": proposal_trace,
            "http_attempts": after_attempts - before_attempts,
            "accounting": budget.snapshot() if budget else None,
            "elapsed_seconds": monotonic() - started,
            "baseline_resources": baseline.catalog.get("resources"),
            "selected_resources": selected.catalog.get("resources"),
        }
        with (run_dir / "report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return result

    try:
        baseline = await asyncio.to_thread(
            prepare_business, split_root, business_id, cache_root, split_id=split_id, policy=policy
        )
        unchanged()
        selection = issue_selector(baseline)
        if selection is None:
            return finish(
                baseline, baseline, None, "NO_ISSUE", "source selector found no recovery issue"
            )
        if not isinstance(selection, IssueSelection):
            raise TypeError("source selector must return IssueSelection or None")
        issue = NormalizationIssue.model_validate(selection.issue)
        profiles = tuple(
            TableProfile.model_validate_json(json.dumps(item))
            for item in baseline.catalog["tables"]
        )
        clauses = compile_clauses(inputs.documents, split_id=split_id)
        full_bindings = _source_roles(profiles, clauses, policy.binding_proposals)
        if full_bindings.digest != baseline.catalog["bindings_digest"]:
            raise ValueError("source bindings changed after registry preparation")
        if issue.scope not in full_bindings.table_ids:
            raise ValueError("issue scope is outside the active business")
        scoped_profiles = tuple(item for item in profiles if item.table_id == issue.scope)
        bindings = replace(
            full_bindings,
            entries=tuple(
                entry for entry in full_bindings.entries if entry.binding.table_id == issue.scope
            ),
            profile_digests=tuple(item.digest for item in scoped_profiles),
            table_ids=(issue.scope,),
            profile_sources=tuple(
                item for item in full_bindings.profile_sources if item[0] == issue.scope
            ),
        )
        packet = build_evidence_packet(
            issue,
            inputs,
            selection.references,
            Path.read_bytes,
            bindings_digest=bindings.digest,
            profile_digests=bindings.profile_digests,
            byte_limit=profile.input_byte_limit if profile is not None else None,
        )
        if not allow_model_calls:
            return finish(
                baseline, baseline, None, "PARTIAL", "model calls are explicitly disabled"
            )
        base_plan = CleaningPlan.model_validate_json(json.dumps(baseline.catalog["plan"]))
        selected = baseline

        @asynccontextmanager
        async def trial(delta):
            nonlocal selected
            unchanged()
            if any(step.table_id != issue.scope for step in delta.steps):
                raise ValueError("proposal escaped the selected source scope")
            existing = {
                (step.table_id, step.operation.model_dump_json()) for step in base_plan.steps
            }
            if all(
                (step.table_id, step.operation.model_dump_json()) in existing
                for step in delta.steps
            ):
                raise ValueError("proposal repeats only existing registry operations")
            additions = [
                step
                for step in delta.steps
                if (step.table_id, step.operation.model_dump_json()) not in existing
            ]
            table_order = {table: index for index, table in enumerate(full_bindings.table_ids)}
            steps = sorted(
                (*base_plan.steps, *additions),
                key=lambda step: (table_order[step.table_id], OPERATION_ORDER[step.operation.name]),
            )
            combined = CleaningPlan(
                bindings_digest=full_bindings.digest,
                profile_digests=full_bindings.profile_digests,
                steps=tuple(steps),
            )
            validate_plan(combined, full_bindings)
            replay = policy.model_copy(update={"mode": "replay", "frozen_plan": combined})
            # Isolated trial directories provide rollback without nesting DuckDB
            # transactions or exposing a rejected artifact in the ordinary cache.
            trials = cache_root / ".adaptive-trials"
            trials.mkdir(exist_ok=True)
            if (
                trials.is_symlink()
                or trials.is_junction()
                or not trials.resolve().is_relative_to(cache_root)
            ):
                raise ValueError("linked trial workspace is forbidden")
            with tempfile.TemporaryDirectory(prefix="candidate-", dir=trials) as temporary:
                candidate = await asyncio.to_thread(
                    prepare_business,
                    split_root,
                    business_id,
                    Path(temporary),
                    split_id=split_id,
                    policy=replay,
                )
                frozen_manifest = candidate.manifest_path.read_bytes()
                yield candidate
                unchanged()
                if candidate.manifest_path.read_bytes() != frozen_manifest:
                    raise ValueError("verifier modified the immutable trial manifest")
                selected = _promote_candidate(candidate, cache_root, trials)

        async def audit_candidate(current_issue, candidate, _delta):
            ready = {
                scope
                for scope, status in baseline.catalog.get("qualification", {}).items()
                if status == "READY"
            }
            if any(
                candidate.catalog.get("qualification", {}).get(scope) != "READY" for scope in ready
            ):
                return False
            # prepare_business already ran intrinsic accounting/lineage/audits.
            # Source verification must additionally demonstrate this issue's repair.
            verdict = verifier(current_issue, baseline, candidate)
            if inspect.isawaitable(verdict):
                verdict = await verdict
            return verdict is True

        recovery_policy = replace(bounds, budget=budget, bindings=bindings)
        async with cleaning_plan_proposer(
            profile, budget, bindings, transport=transport, transport_retries=transport_retries
        ) as proposer:

            async def recorded_proposer(current_issue, current_packet, round_index, repair_error):
                plan = await proposer(current_issue, current_packet, round_index, repair_error)
                proposal_trace.append({"round": round_index, "plan": plan.model_dump(mode="json")})
                return plan

            recovery = await recover_issue(
                issue, packet, recorded_proposer, trial, audit_candidate, recovery_policy
            )
        return finish(baseline, selected, recovery, recovery.status, recovery.reason)
    except BaseException as error:
        with (run_dir / "failure.json").open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                    "http_attempts": int(budget.snapshot()["run_attempts"]) - before_attempts
                    if budget
                    else 0,
                },
                stream,
            )
        raise


async def prepare_with_mapping_recovery(
    split_root: Path,
    business_id: str,
    cache_root: Path,
    *,
    split_id: str,
    run_dir: Path,
    table_id: str | None = None,
    allow_model_calls: bool = False,
    profile: ModelProfile | None = None,
    budget: RequestBudget | None = None,
    policy: PreparationPolicy | None = None,
    transport: AsyncBaseTransport | None = None,
    env_file: Path | None = None,
) -> AdaptivePreparationResult:
    """Discover gaps, propose roles once, then rebuild and audit an isolated candidate.

    Mapping acceptance is not business qualification. No issue-selector callback,
    evaluator knowledge, arbitrary code, model-authored evidence or hidden retry is used.
    """
    from datamate.preparation.adaptive import propose_role_mapping
    from datamate.preparation.mapping import mapping_bindings

    if allow_model_calls and (profile is None or budget is None):
        raise ValueError("adaptive model calls require an explicit profile and budget")
    if budget is not None and budget.rpm > 40:
        raise ValueError("adaptive provider rate cannot exceed 40 RPM")
    policy = PreparationPolicy() if policy is None else PreparationPolicy.model_validate(policy)
    if policy.mode != "registry":
        raise ValueError("mapping recovery requires registry preparation")
    inputs = discover_business(split_root, business_id, split_id=split_id)
    if budget is not None and budget.path.resolve().is_relative_to(inputs.split_root.resolve()):
        raise ValueError("ledger must remain outside the source split")
    inventory = _inventory(inputs)
    cache_root, run_dir = Path(cache_root).resolve(), Path(run_dir).resolve()
    if run_dir.is_relative_to(inputs.split_root.resolve()) or cache_root.is_relative_to(
        inputs.split_root.resolve()
    ):
        raise ValueError("adaptive outputs must remain outside the source split")
    run_dir.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    initial_attempts = int(budget.snapshot()["run_attempts"]) if budget else 0
    trace = {}

    def unchanged():
        if _inventory(discover_business(split_root, business_id, split_id=split_id)) != inventory:
            raise ValueError("source snapshot changed during mapping recovery")

    baseline = None
    try:
        baseline = await asyncio.to_thread(
            prepare_business, split_root, business_id, cache_root, split_id=split_id, policy=policy
        )

        def finish(status, reason, candidate=baseline):
            unchanged()
            report = {
                "mode": "adaptive_mapping",
                "status": status,
                "reason": reason,
                "split_id": split_id,
                "business_id": business_id,
                "table_id": table_id,
                "baseline_fingerprint": baseline.fingerprint,
                "selected_fingerprint": candidate.fingerprint,
                "model_id": profile.model_id if profile else None,
                "profile_sha256": sha256(profile.model_dump_json().encode()).hexdigest()
                if profile
                else None,
                "http_attempts": int(budget.snapshot()["run_attempts"]) - initial_attempts
                if budget
                else 0,
                "accounting": budget.snapshot() if budget else None,
                "elapsed_seconds": monotonic() - started,
                **trace,
            }
            with (run_dir / "report.json").open("x", encoding="utf8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            return AdaptivePreparationResult(baseline, candidate, None, status)

        profiles = tuple(
            TableProfile.model_validate_json(json.dumps(p)) for p in baseline.catalog["tables"]
        )
        clauses = compile_clauses(inputs.documents, split_id=split_id)
        bindings = _source_roles(profiles, clauses, policy.binding_proposals)
        if bindings.digest != baseline.catalog["bindings_digest"]:
            raise ValueError("source bindings changed after registry preparation")
        issues = tuple(
            NormalizationIssue.model_validate_json(json.dumps(i))
            for i in baseline.catalog["issues"]
        )
        candidates = [
            p
            for p in profiles
            if any(i.kind == "unmapped_role" and i.scope == p.table_id for i in issues)
        ]
        if table_id is not None:
            candidates = [p for p in candidates if p.table_id == table_id]
            if not any(p.table_id == table_id for p in profiles):
                raise ValueError("mapping scope is outside the active business")
        if not candidates:
            return finish(
                "NO_MAPPING_ISSUE",
                "No unmapped-role issue in the selected scope; other issue kinds may remain.",
            )
        selected_profile = candidates[0]
        table_id = selected_profile.table_id
        trace["issues"] = [i.model_dump(mode="json") for i in issues if i.scope == table_id]
        if not allow_model_calls:
            return finish("PARTIAL", "Model calls explicitly disabled; schema gaps remain marked.")
        try:
            proposal = await propose_role_mapping(
                profile,
                budget,
                selected_profile,
                clauses,
                bindings,
                transport=transport,
                env_file=env_file,
            )
        except Exception as error:
            # This is an explicit proposal boundary: retain results and record failure,
            # including provider errors and exhausted budgets. Never retry or switch models.
            trace["proposal_error_type"] = type(error).__name__
            if isinstance(error, ValueError):
                trace["proposal_error"] = str(error)[:240]
            return finish("PARTIAL", "Mapping proposal failed; baseline retained.")
        trace["proposal"] = proposal.model_dump(mode="json")
        unchanged()
        if not proposal.mappings:
            return finish("PARTIAL", "Model proposed no source-backed mapping.")
        try:
            proposed = mapping_bindings(proposal, selected_profile, clauses)
        except ValueError as error:
            trace["validation_error"] = str(error)
            return finish("PARTIAL", "Mapping references invalid source coordinates.")
        current = {(b.table_id, b.columns): b.value for b in bindings.accepted if b.kind == "role"}
        if any(
            (b.table_id, b.columns) in current and current[(b.table_id, b.columns)] != b.value
            for b in proposed
        ):
            return finish("PARTIAL", "Proposal would overwrite accepted source semantics.")
        proposed = tuple(b for b in proposed if (b.table_id, b.columns) not in current)
        reviewed = _source_roles(profiles, clauses, (*policy.binding_proposals, *proposed))
        accepted_ids = {b.binding_id for b in reviewed.accepted}
        accepted = tuple(b for b in proposed if b.binding_id in accepted_ids)
        trace["mapping_validation"] = [
            {"column_id": b.columns[0], "role": b.value, "accepted": b.binding_id in accepted_ids}
            for b in proposed
        ]
        if not accepted:
            return finish("PARTIAL", "No proposed mapping passed source validation.")
        next_policy = policy.model_copy(
            update={"binding_proposals": (*policy.binding_proposals, *accepted)}
        )
        trials = cache_root / ".adaptive-trials"
        trials.mkdir(exist_ok=True)
        if (
            trials.is_symlink()
            or trials.is_junction()
            or not trials.resolve().is_relative_to(cache_root)
        ):
            raise ValueError("linked trial workspace is forbidden")
        with tempfile.TemporaryDirectory(prefix="mapping-", dir=trials) as temporary:
            candidate = await asyncio.to_thread(
                prepare_business,
                split_root,
                business_id,
                Path(temporary),
                split_id=split_id,
                policy=next_policy,
            )
            unchanged()
            ready = {k for k, v in baseline.catalog["qualification"].items() if v == "READY"}
            if any(candidate.catalog["qualification"].get(k) != "READY" for k in ready):
                return finish("PARTIAL", "Candidate regressed an existing READY scope.")
            if _raw_digest(candidate) != _raw_digest(baseline):
                return finish("PARTIAL", "Candidate altered immutable raw staging.")
            # Each prepare already checks source stability, intrinsic audit and content hashes.
            selected = _promote_candidate(candidate, cache_root, trials)
        return finish(
            "MAPPING_ACCEPTED",
            "Source-validated mappings applied; inspect scoped qualification separately.",
            selected,
        )

    except BaseException as error:
        # Preserve a diagnostic even when preparation or promotion fails after a
        # paid proposal. The baseline cache remains available; no model retry occurs.
        with (run_dir / "failure.json").open("x", encoding="utf8") as handle:
            json.dump(
                {
                    "mode": "adaptive_mapping",
                    "status": "FAILED",
                    "error_type": type(error).__name__,
                    "split_id": split_id,
                    "business_id": business_id,
                    "baseline_fingerprint": baseline.fingerprint if baseline else None,
                    "http_attempts": int(budget.snapshot()["run_attempts"]) - initial_attempts
                    if budget
                    else 0,
                    "elapsed_seconds": monotonic() - started,
                },
                handle,
            )
        raise
