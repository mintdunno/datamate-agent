"""Prepare one business without exposing source rows or evaluator references."""

import argparse
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
    args = parser.parse_args()
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
                "api_attempts": prepared.catalog.get("api_attempts"),
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
