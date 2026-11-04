"""Prepare one business without exposing source rows or evaluator references."""

import argparse
import json
from pathlib import Path

from datamate.preparation.normalization import TablePlan
from datamate.preparation.pipeline import prepare_business


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split-root", required=True, type=Path)
    parser.add_argument("--split-id", required=True)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--cache-root", required=True, type=Path)
    parser.add_argument("--plans", type=Path, help="Explicit table-key -> TablePlan JSON")
    args = parser.parse_args()
    plans = None
    if args.plans:
        configured = json.loads(args.plans.read_text(encoding="utf-8"))
        plans = {key: TablePlan.model_validate(value) for key, value in configured.items()}
    prepared = prepare_business(
        args.split_root, args.business_id, args.cache_root, split_id=args.split_id, plans=plans
    )
    tables = prepared.catalog["tables"]
    print(
        json.dumps(
            {
                "split_id": args.split_id,
                "business_id": args.business_id,
                "fingerprint": prepared.fingerprint,
                "cache_hit": prepared.cache_hit,
                "tables": len(tables),
                "input_rows": sum(table["input_rows"] for table in tables),
                "output_rows": sum(table["output_rows"] for table in tables),
                "excluded_rows": sum(table["excluded_rows"] for table in tables),
                "database": str(prepared.database),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
