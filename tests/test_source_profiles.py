"""Source-only profiling precedes references and never publishes qualified facts."""

import json

import pytest

from datamate.preparation.discovery import discover_business


def source_world(tmp_path):
    root = tmp_path / "train"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text(
        "# Rules\nKeep unknown semantics visible.\n", encoding="utf-8"
    )
    (root / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    (root / "answers.jsonl").write_text("PRIVATE_REFERENCE_SENTINEL", encoding="utf-8")
    (business / "README.md").write_text("# Notes\nSource coverage is fixed.\n", encoding="utf-8")
    (business / "sales.csv").write_text(
        "id,amount\n" + "".join(f"{i:04d},{i}\n" for i in range(1003)), encoding="utf-8"
    )
    return root


def test_source_profile_worker_keeps_all_records_and_active_split_without_labels(tmp_path):
    from datamate.preparation.pipeline import profile_business_sources
    from datamate.preparation.resources import PreparationLimits

    root = source_world(tmp_path)
    output = tmp_path / "profile"
    profiles, resources = profile_business_sources(
        root,
        "shop",
        output,
        split_id="active-train-v1",
        limits=PreparationLimits(timeout_seconds=30.0),
    )
    assert sum(profile.row_count for profile in profiles) == 1004
    assert resources.measurements_complete
    manifest = json.loads((output / "profile-manifest.json").read_text())
    assert manifest["status"] == "RAW_PROFILE_ONLY_UNQUALIFIED"
    assert manifest["split_id"] == "active-train-v1"
    assert "PRIVATE_REFERENCE_SENTINEL" not in json.dumps(manifest)
    assert not (output / "inputs" / "answers.jsonl").exists()
    assert not (output / "catalog.json").exists()
    import duckdb

    with duckdb.connect(str(output / "raw.duckdb"), read_only=True) as db:
        assert db.execute("SELECT DISTINCT split_id FROM meta.source_documents").fetchall() == [
            ("active-train-v1",)
        ]
        assert db.execute("SELECT count(*) FROM meta.facts").fetchone()[0] == 0


def test_source_profile_rejects_frozen_source_drift_before_starting_worker(tmp_path):
    from datamate.preparation.pipeline import profile_business_sources

    root = source_world(tmp_path)
    inputs = discover_business(root, "shop", split_id="train")
    expected = {s.relative_path: s.sha256 for s in (*inputs.sources, *inputs.documents)}
    (root / "data" / "shop" / "sales.csv").write_text("id,amount\nchanged,9\n")
    output = tmp_path / "profile"
    with pytest.raises(ValueError, match="source"):
        profile_business_sources(root, "shop", output, split_id="train", expected_sources=expected)
    assert not output.exists()


def test_failed_source_profile_keeps_diagnostic_without_success_manifest(tmp_path):
    from datamate.preparation.pipeline import profile_business_sources
    from datamate.preparation.resources import PreparationResourceError

    root = source_world(tmp_path)
    (root / "data" / "shop" / "sales.csv").write_text("id,amount\n001,1,extra\n")
    output = tmp_path / "failed"
    with pytest.raises(PreparationResourceError):
        profile_business_sources(root, "shop", output, split_id="train")
    assert not (output / "profile-manifest.json").exists()
    assert json.loads((output / "resources.json").read_text())["status"] == "FAILED"
