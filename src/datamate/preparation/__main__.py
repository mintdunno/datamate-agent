"""Prepare one business without exposing source rows or evaluator references."""

import argparse
import asyncio
import json
from pathlib import Path

from datamate.preparation.normalization import TablePlan
from datamate.preparation.pipeline import PreparationPolicy, prepare_business


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-root", required=True, type=Path)
    parser.add_argument("--split-id", required=True)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--plans", type=Path, help="Explicit table-key -> TablePlan JSON")
    parser.add_argument("--policy", type=Path, help="Version-2 source-bound PreparationPolicy JSON")
    parser.add_argument("--mode", choices=("registry", "replay"), default="registry")
    parser.add_argument("--diagnostic-path", type=Path, help="Source-only diagnostic JSON output")
    parser.add_argument(
        "--adaptive-mapping",
        action="store_true",
        help="One bounded model mapping proposal for a source-marked schema gap",
    )
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--table-id", help="Optional physical table scope; otherwise select the first marked table"
    )
    parser.add_argument("--request-budget", type=int, default=4)
    parser.add_argument("--rpm", type=int, default=25)
    parser.add_argument("--daily-budget", type=int, default=1200)
    parser.add_argument("--campaign-id", default="adaptive-normalization-v1")
    parser.add_argument("--phase", choices=("smoke", "development"), default="development")
    args = parser.parse_args()
    if args.adaptive_mapping:
        if not all((args.profile, args.ledger, args.run_id, args.run_dir)):
            parser.error("--adaptive-mapping requires --profile, --ledger, --run-id and --run-dir")
        if args.ledger.resolve().is_relative_to(args.split_root.resolve()):
            parser.error("ledger must remain outside the source split")
        if args.plans or args.mode != "registry":
            parser.error("adaptive mapping requires version-2 registry preparation")
        if not 1 <= args.request_budget <= 300 or not 1 <= args.rpm <= 40:
            parser.error("request budget must be 1..300 and provider RPM must be 1..40")
    plans = None
    if args.plans and args.policy:
        parser.error("legacy --plans and version-2 --policy cannot be combined")
    if args.diagnostic_path and args.diagnostic_path.resolve().is_relative_to(
        args.split_root.resolve()
    ):
        parser.error("diagnostics must remain outside the source split")
    if args.plans:
        configured = json.loads(args.plans.read_text(encoding="utf-8"))
        plans = {key: TablePlan.model_validate(value) for key, value in configured.items()}
    policy = (
        None
        if plans is not None
        else (
            PreparationPolicy.model_validate_json(args.policy.read_bytes())
            if args.policy
            else PreparationPolicy(mode=args.mode)
        )
    )
    adaptive_result = None
    if args.adaptive_mapping:
        from datamate.inference.budget import CampaignPolicy, RequestBudget, register_campaign
        from datamate.inference.config import load_profile
        from datamate.preparation.orchestration import prepare_with_mapping_recovery

        selected_profile = load_profile(args.profile)
        register_campaign(args.ledger, CampaignPolicy(args.campaign_id))
        budget = RequestBudget(
            args.ledger,
            run_id=args.run_id,
            rpm=args.rpm,
            daily_limit=args.daily_budget,
            run_limit=args.request_budget,
            campaign_id=args.campaign_id,
            phase=args.phase,
        )
        adaptive_result = asyncio.run(
            prepare_with_mapping_recovery(
                args.split_root,
                args.business_id,
                args.cache_root,
                split_id=args.split_id,
                run_dir=args.run_dir,
                table_id=args.table_id,
                allow_model_calls=True,
                profile=selected_profile,
                budget=budget,
                policy=policy,
                env_file=args.env_file,
            )
        )
        prepared = adaptive_result.prepared
    else:
        prepared = prepare_business(
            args.split_root,
            args.business_id,
            args.cache_root,
            split_id=args.split_id,
            plans=plans,
            policy=policy,
        )
    v2 = prepared.catalog.get("artifact_version") == 2
    tables = [t for t in prepared.catalog["tables"] if t.get("source") != "calendar.csv"]
    if args.diagnostic_path:
        if not v2:
            parser.error("diagnostics require version-2 preparation")
        args.diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostic_path.write_text(
            json.dumps(prepared.catalog["diagnostic"]), encoding="utf-8"
        )
    row_counts = prepared.catalog.get("physical_row_counts", {})
    print(
        json.dumps(
            {
                "split_id": args.split_id,
                "business_id": args.business_id,
                "fingerprint": prepared.fingerprint,
                "cache_hit": prepared.cache_hit,
                "tables": len(tables),
                "artifact_version": 2 if v2 else 1,
                "status": prepared.catalog.get("status", "LEGACY_UNQUALIFIED"),
                "api_attempts": (
                    budget.snapshot()["run_attempts"]
                    if adaptive_result
                    else prepared.catalog.get("api_attempts")
                ),
                "adaptive_status": adaptive_result.status if adaptive_result else None,
                "issues": len(prepared.catalog.get("issues", [])),
                "input_rows": sum(t["row_count"] if v2 else t["input_rows"] for t in tables),
                "output_rows": sum(
                    row_counts[t["table_id"]].get("retained", 0) if v2 else t["output_rows"]
                    for t in tables
                ),
                "excluded_rows": sum(
                    t["row_count"] - row_counts[t["table_id"]].get("retained", 0)
                    if v2
                    else t["excluded_rows"]
                    for t in tables
                ),
                "database": str(prepared.database),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
