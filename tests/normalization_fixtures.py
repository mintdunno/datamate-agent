"""Synthetic source worlds with literal truth; never imported by production code."""

import duckdb

from datamate.evaluation.normalization import PilotPolicy, snapshot_sources
from datamate.preparation.discovery import discover_business
from datamate.preparation.loaders import load_tables
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, stage_table


def reference_world(tmp_path):
    root = tmp_path / "train"
    root.mkdir()
    (root / "rulebook.md").write_text(
        "# Synthetic rules\nKeep source observations.\n", encoding="utf-8"
    )
    (root / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    policy = PilotPolicy(development=("a", "b", "c", "d"), checkpoint=("e",))
    for business in policy.business_ids:
        folder = root / "data" / business
        folder.mkdir(parents=True)
        (folder / "README.md").write_text("# Synthetic shop\n", encoding="utf-8")
        (folder / "sales.csv").write_text(
            "id,amount\n" + "".join(f"{n:03d},{n * 100}\n" for n in range(1, 41)), encoding="utf-8"
        )
    sources = snapshot_sources(root, policy.business_ids, split_id="train")
    profiles = []
    with duckdb.connect() as db:
        create_storage(db)
        for business in policy.business_ids:
            inputs = discover_business(root, business, split_id="train")
            for source in inputs.sources:
                for table in load_tables(source.path):
                    staged = stage_table(db, source, table)
                    profiles.append(profile_table(db, staged.table_id))
    return policy, sources, profiles
