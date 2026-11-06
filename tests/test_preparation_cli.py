"""CLI integration emits metadata only and reuses completed preparation."""

import json
import subprocess
import sys


def test_prepare_cli_outputs_bounded_metadata_and_cache_status(tmp_path):
    root = tmp_path / "split"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text("# Rules\nNo guessed semantics.\n", encoding="utf-8")
    (root / "calendar.csv").write_text("date,lunar_day\n2025-01-01,2\n", encoding="utf-8")
    (business / "sales.csv").write_text("id,amount\nPRIVATE-NAME,12\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "datamate.preparation",
        "--split-root",
        str(root),
        "--split-id",
        "synthetic",
        "--business-id",
        "shop",
        "--cache-root",
        str(tmp_path / "cache"),
    ]
    one = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    two = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    first, second = json.loads(one.stdout), json.loads(two.stdout)
    assert first["input_rows"] == first["output_rows"] == 1
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert "PRIVATE-NAME" not in one.stdout


def test_prepare_cli_defaults_to_v2_and_exports_source_only_diagnostic(tmp_path):
    root = tmp_path / "split"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    (root / "rulebook.md").write_text("# Rules\nUnknown semantics.\n")
    (root / "calendar.csv").write_text("date,lunar_day\n2025-01-01,2\n")
    (business / "sales.csv").write_text("id,amount\nPRIVATE-NAME,12\n")
    diagnostic = tmp_path / "diagnostic.json"
    command = [
        sys.executable,
        "-m",
        "datamate.preparation",
        "--split-root",
        str(root),
        "--split-id",
        "synthetic",
        "--business-id",
        "shop",
        "--cache-root",
        str(tmp_path / "cache"),
        "--diagnostic-path",
        str(diagnostic),
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    metadata = json.loads(run.stdout)
    assert metadata["artifact_version"] == 2
    assert metadata["status"] == "PARTIAL"
    assert metadata["api_attempts"] == 0
    summary = json.loads(diagnostic.read_text())
    assert summary["raw_records"] == 4
    assert summary["declared_coverage"]["data_records"] == 2
    assert "PRIVATE-NAME" not in diagnostic.read_text()
