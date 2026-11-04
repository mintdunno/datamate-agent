"""Preparation discovers only explicitly allowed business inputs."""

import pytest

from datamate.preparation.discovery import discover_business


def split_fixture(tmp_path):
    split = tmp_path / "split"
    business = split / "data" / "shop"
    business.mkdir(parents=True)
    (split / "rulebook.md").write_text("# Rules\nRevenue rules.", encoding="utf-8")
    (split / "calendar.csv").write_text("date,lunar_day\n2025-01-01,2\n", encoding="utf-8")
    (business / "README.md").write_text("# Shop\nSource notes.", encoding="utf-8")
    (business / "sales.csv").write_text("id,amount\nA,10\n", encoding="utf-8")
    return split, business


def test_discovery_excludes_labels_questions_and_secrets(tmp_path):
    split, _ = split_fixture(tmp_path)
    (split / "answers.jsonl").write_text("DO NOT READ", encoding="utf-8")
    (split / ".env").write_text("DO NOT READ", encoding="utf-8")
    package = discover_business(split, "shop", split_id="synthetic")
    assert [source.relative_path for source in package.sources] == ["data/shop/sales.csv"]
    assert {source.relative_path for source in package.documents} == {
        "rulebook.md",
        "calendar.csv",
        "data/shop/README.md",
    }
    assert all(len(source.sha256) == 64 for source in package.sources)


@pytest.mark.parametrize("identifier", ["..", "../shop", "a/b", r"a\b", "", "."])
def test_discovery_rejects_path_traversal(tmp_path, identifier):
    split, _ = split_fixture(tmp_path)
    with pytest.raises(ValueError):
        discover_business(split, identifier, split_id="synthetic")


def test_discovery_rejects_unrecognized_business_files_without_reading(tmp_path):
    split, business = split_fixture(tmp_path)
    (business / "answers.jsonl").write_text("DO NOT READ", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        discover_business(split, "shop", split_id="synthetic")


def test_active_split_must_supply_its_own_rules(tmp_path):
    split, _ = split_fixture(tmp_path)
    (split / "rulebook.md").unlink()
    with pytest.raises(FileNotFoundError):
        discover_business(split, "shop", split_id="synthetic")
