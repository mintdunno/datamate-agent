"""Evaluator selection must freeze source evidence without reading QA labels."""

import importlib
import json
from pathlib import Path

import pytest


def api():
    return importlib.import_module("datamate.evaluation.normalization")


def synthetic_split(tmp_path):
    root = tmp_path / "train"
    root.mkdir()
    (root / "rulebook.md").write_text("# Active rules", encoding="utf-8")
    (root / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    for name in ("a", "b", "c", "d", "e"):
        folder = root / "data" / name
        folder.mkdir(parents=True)
        (folder / "sales.csv").write_text("id,amount\n001,10\n", encoding="utf-8")
        (folder / "README.md").write_text("# Source grain", encoding="utf-8")
    return root


def policy():
    return api().PilotPolicy(development=("a", "b", "c", "d"), checkpoint=("e",))


def test_snapshot_excludes_labels_and_is_order_independent(tmp_path, monkeypatch):
    module = api()
    root = synthetic_split(tmp_path)
    forbidden = root / "answers.jsonl"
    forbidden.write_text("REFERENCE_SENTINEL", encoding="utf-8")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == forbidden:
            raise AssertionError("reference file read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    first = module.snapshot_sources(root, ("a", "b", "c", "d", "e"), split_id="train")
    second = module.snapshot_sources(root, ("e", "d", "c", "b", "a"), split_id="train")
    assert module.freeze_selection_policy(policy(), first) == module.freeze_selection_policy(
        policy(), second
    )
    assert set(first.files["a"]) == {
        "rulebook.md",
        "calendar.csv",
        "data/a/README.md",
        "data/a/sales.csv",
    }
    assert "REFERENCE_SENTINEL" not in first.model_dump_json()


def test_source_drift_changes_freeze_and_existing_freeze_cannot_be_overwritten(tmp_path):
    module = api()
    root = synthetic_split(tmp_path)
    selection = policy()
    snapshot = module.snapshot_sources(root, selection.business_ids, split_id="train")
    target = tmp_path / "frozen.json"
    digest = module.write_frozen_selection(target, selection, snapshot)
    original = target.read_bytes()
    (root / "data/a/sales.csv").write_text("id,amount\n001,20\n", encoding="utf-8")
    changed = module.snapshot_sources(root, selection.business_ids, split_id="train")
    assert module.freeze_selection_policy(selection, changed) != digest
    with pytest.raises(FileExistsError):
        module.write_frozen_selection(target, selection, changed)
    assert target.read_bytes() == original


@pytest.mark.parametrize(
    "changes",
    [
        {"development": ("a", "b", "c", "e")},
        {"development": ("a", "a", "c", "d")},
        {"development": ("../a", "b", "c", "d")},
        {"answers_path": "answers.jsonl"},
        {"campaign_limit": 121},
        {"phase_limits": {"smoke": 21, "development": 60, "final": 40}},
        {"minimum_determinate": 21},
    ],
)
def test_policy_rejects_unsafe_selection_or_budget_increase(changes):
    module = api()
    values = {"development": ("a", "b", "c", "d"), "checkpoint": ("e",)}
    with pytest.raises(ValueError):
        module.PilotPolicy(**(values | changes))


def test_freeze_rejects_missing_business_and_split_mismatch(tmp_path):
    module = api()
    root = synthetic_split(tmp_path)
    snapshot = module.snapshot_sources(root, ("a", "b", "c", "d"), split_id="train")
    with pytest.raises(ValueError, match="business"):
        module.freeze_selection_policy(policy(), snapshot)
    other = module.snapshot_sources(root, policy().business_ids, split_id="public_test")
    with pytest.raises(ValueError, match="split"):
        module.freeze_selection_policy(policy(), other)


def test_real_pilot_policy_has_expected_fixed_selection():
    module = api()
    value = module.load_pilot_policy(Path("config/normalization/pilot.json"))
    assert set(value.development) == {"biz_124", "biz_106", "biz_120", "biz_122"}
    assert value.checkpoint == ("biz_123",)
    assert value.cases_per_business == 20
    assert value.minimum_determinate == 10
    assert value.campaign_limit == 120
    assert value.phase_limits == {"smoke": 20, "development": 60, "final": 40}
    assert value.seed == 42
    assert "answers" not in json.dumps(value.model_dump())


def test_failed_publication_does_not_expose_partial_freeze(tmp_path, monkeypatch):
    import os

    module = api()
    root = synthetic_split(tmp_path)
    selection = policy()
    sources = module.snapshot_sources(root, selection.business_ids, split_id="train")
    target = tmp_path / "artifacts" / "selection.json"

    def fail_publication(*args, **kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr(os, "link", fail_publication)
    with pytest.raises(OSError, match="publication"):
        module.write_frozen_selection(target, selection, sources)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []
