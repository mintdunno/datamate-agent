import json

import pytest

from datamate.evaluation.suites import build_suites


def fixture_split(path):
    path.mkdir()
    qs = [
        dict(id=f"q{i}", business_id=f"b{i // 3}", question="total sales", as_of_date="2025-01-01")
        for i in range(60)
    ]
    refs = [dict(id=q["id"], type="number", answer=987654321, alias=[]) for q in qs]
    for name, rows in [("questions.jsonl", qs), ("answers.jsonl", refs)]:
        (path / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return qs


def test_suites_deterministic_disjoint_and_no_answers(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    a = build_suites(
        split, tmp_path / "a", smoke_size=3, development_size=12, hard_size=4, holdout_size=8
    )
    b = build_suites(
        split, tmp_path / "b", smoke_size=3, development_size=12, hard_size=4, holdout_size=8
    )
    assert a["manifest"] == b["manifest"]
    for name in ("smoke", "development", "hard", "holdout"):
        assert a["paths"][name].read_text() == b["paths"][name].read_text()
        assert "987654321" not in a["paths"][name].read_text()

    def businesses(name):
        return {
            json.loads(line)["business_id"] for line in a["paths"][name].read_text().splitlines()
        }

    assert businesses("development").isdisjoint(businesses("holdout"))


def test_missing_or_duplicate_reference_rejected(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    p = split / "answers.jsonl"
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ValueError):
        build_suites(split, tmp_path / "out")
    p.write_text("\n".join(lines + [lines[0]]) + "\n")
    with pytest.raises(ValueError):
        build_suites(split, tmp_path / "out")


def test_suite_limit_and_shortage_are_explicit(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    with pytest.raises(ValueError, match="above 500"):
        build_suites(split, tmp_path / "out", smoke_size=501)
    result = build_suites(split, tmp_path / "out", development_size=100)
    coverage = result["manifest"]["suites"]["development"]
    assert coverage["selected"] + coverage["shortage"] == 100
    assert coverage["shortage"] > 0


def test_business_and_domain_coverage_and_all_source_fingerprints(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    (split / "records.csv").write_text("source-one", encoding="utf-8")
    domains = {f"b{i}": ("retail" if i % 2 else "services") for i in range(20)}
    first = build_suites(split, tmp_path / "out1", development_size=12, business_domains=domains)
    coverage = first["manifest"]["suites"]["development"]
    assert len(coverage["business_ids"]) == 12
    assert sum(coverage["domains"].values()) == 12
    assert set(coverage["domains"]) <= {"retail", "services"}
    assert "records.csv" in first["manifest"]["source_sha256"]
    (split / "records.csv").write_text("source-two", encoding="utf-8")
    second = build_suites(split, tmp_path / "out2", development_size=12, business_domains=domains)
    assert first["manifest"]["fingerprint"] != second["manifest"]["fingerprint"]


def test_output_cannot_be_inside_split(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    with pytest.raises(ValueError, match="outside"):
        build_suites(split, split / "out")


def test_type_strata_are_balanced_across_businesses(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    path = split / "answers.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for index, row in enumerate(rows):
        if index % 3 == 0:
            row["type"] = "string"
            row["answer"] = "secret"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = build_suites(split, tmp_path / "out", development_size=12)
    counts = result["manifest"]["suites"]["development"]["answer_types"]
    assert counts == {"number": 6, "string": 6}


def test_rare_types_are_covered_before_balanced_filling():
    from datamate.contracts.inputs import Question
    from datamate.evaluation.suites import _select

    rows = []
    for business in range(12):
        kinds = ["number", "string"]
        if business == 0:
            kinds += ["table", "unanswerable"]
        elif business == 1:
            kinds += ["table"]
        for kind in kinds:
            rows.append(
                dict(
                    question=Question(
                        id=f"q-{business}-{kind}",
                        business_id=f"b{business}",
                        question="total sales",
                        as_of_date="2025-01-01",
                    ),
                    type=kind,
                    category="aggregation",
                    difficulty=0,
                    domain="retail" if business % 2 else "services",
                )
            )
    chosen = _select(rows, 12, 42, "smoke")
    assert {row["type"] for row in chosen} == {"number", "string", "table", "unanswerable"}
    assert len({row["question"].business_id for row in chosen}) == 12
    assert {row["domain"] for row in chosen} == {"retail", "services"}
    assert chosen == _select(list(reversed(rows)), 12, 42, "smoke")
    small = _select(rows, 1, 42, "smoke")
    assert small[0]["type"] == "unanswerable"


def test_available_type_metadata_and_short_suite_missing_types(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    answers = split / "answers.jsonl"
    rows = [json.loads(line) for line in answers.read_text().splitlines()]
    for index, row in enumerate(rows):
        row["type"] = ["number", "string", "table"][index % 3]
    answers.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = build_suites(split, tmp_path / "out", smoke_size=1)
    for name, coverage in result["manifest"]["suites"].items():
        assert set(coverage["available_answer_types"]) == {"number", "string", "table"}
        assert sum(coverage["available_answer_types"].values()) == coverage["available"]
        assert coverage["missing_available_types"] == sorted(
            set(coverage["available_answer_types"]) - set(coverage["answer_types"])
        )
        assert len(coverage["missing_available_types"]) == (2 if name == "smoke" else 0)


def test_snapshot_only_binds_solver_safe_sources_for_chosen_businesses(tmp_path):
    import hashlib

    split = tmp_path / "split"
    fixture_split(split)
    (split / "rulebook.md").write_text("rules", encoding="utf-8")
    (split / "calendar.csv").write_text("date\n2025-01-01\n", encoding="utf-8")
    (split / "evaluator_notes.md").write_text("reference metadata", encoding="utf-8")
    for index in range(20):
        business = split / "data" / f"b{index}"
        (business / "nested").mkdir(parents=True)
        for name in ("README.md", "nested/a.csv", "nested/b.xlsx", "nested/c.sqlite"):
            (business / name).write_text(f"input {index} {name}", encoding="utf-8")
        (business / "private.json").write_text("not allowlisted", encoding="utf-8")
    result = build_suites(split, tmp_path / "out", smoke_size=3)
    for name, path in result["snapshots"].items():
        snapshot = json.loads(path.read_text())
        assert set(snapshot) == {"schema_version", "questions_sha256", "sources"}
        assert snapshot["schema_version"] == "1"
        assert (
            snapshot["questions_sha256"]
            == hashlib.sha256(result["paths"][name].read_bytes()).hexdigest()
        )
        coverage = result["manifest"]["suites"][name]
        assert set(snapshot["sources"]) == set(coverage["business_ids"])
        for business, hashes in snapshot["sources"].items():
            assert set(hashes) == {
                "rulebook.md",
                "calendar.csv",
                f"data/{business}/README.md",
                f"data/{business}/nested/a.csv",
                f"data/{business}/nested/b.xlsx",
                f"data/{business}/nested/c.sqlite",
            }
            for relative, digest in hashes.items():
                assert digest == hashlib.sha256((split / relative).read_bytes()).hexdigest()
        assert "answers.jsonl" not in path.read_text()
        assert "reference metadata" not in path.read_text()
        assert "987654321" not in path.read_text()
        assert "answer_types" not in path.read_text()


def test_reference_values_and_aliases_cannot_influence_selection_or_snapshot(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    first = build_suites(split, tmp_path / "first")
    answers = split / "answers.jsonl"
    rows = [json.loads(line) for line in answers.read_text().splitlines()]
    for row in rows:
        row["answer"] = -123
        row["alias"] = ["never export this alias"]
    answers.write_text("".join(json.dumps(row) + "\n" for row in rows))
    second = build_suites(split, tmp_path / "second")
    for name in first["paths"]:
        assert first["paths"][name].read_bytes() == second["paths"][name].read_bytes()
        assert first["snapshots"][name].read_bytes() == second["snapshots"][name].read_bytes()


def test_changed_sources_cannot_overwrite_existing_suite(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    source = split / "calendar.csv"
    source.write_text("old", encoding="utf-8")
    output = tmp_path / "out"
    build_suites(split, output)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    source.write_text("new", encoding="utf-8")
    with pytest.raises(ValueError, match="source hashes differ"):
        build_suites(split, output)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


def test_changed_selection_cannot_overwrite_existing_suite(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    output = tmp_path / "out"
    build_suites(split, output, development_size=12)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    with pytest.raises(ValueError, match="existing suite artifact differs"):
        build_suites(split, output, development_size=11)
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


def test_identical_build_can_restore_missing_safe_snapshot(tmp_path):
    split = tmp_path / "split"
    fixture_split(split)
    output = tmp_path / "out"
    first = build_suites(split, output)
    snapshot = first["snapshots"]["smoke"]
    original = snapshot.read_bytes()
    snapshot.unlink()
    second = build_suites(split, output)
    assert second["manifest"] == first["manifest"]
    assert snapshot.read_bytes() == original


@pytest.mark.parametrize("link_method", ["is_junction", "is_symlink"])
def test_source_links_rejected_before_traversal(tmp_path, monkeypatch, link_method):
    from pathlib import Path

    split = tmp_path / "split"
    fixture_split(split)
    junction = split / "junction"
    junction.mkdir()
    original = getattr(Path, link_method)
    monkeypatch.setattr(Path, link_method, lambda self: self == junction or original(self))
    with pytest.raises(ValueError, match="junctions"):
        build_suites(split, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("size", [0, 4, 8, 12])
def test_selector_covers_every_available_reference_type_when_size_permits(size):
    from datamate.contracts.inputs import Question
    from datamate.evaluation.suites import _select

    kinds = ["number", "percent", "string", "date", "boolean", "list", "table", "unanswerable"]
    rows = [
        dict(
            question=Question(
                id=f"q{index}",
                business_id=f"b{index % 12}",
                question="source question",
                as_of_date="2025-01-01",
            ),
            type=kind,
            category="other",
            difficulty=0,
            domain="retail",
        )
        for index, kind in enumerate(kinds + ["number"] * 20)
    ]
    selected = _select(rows, size, 42, "smoke")
    assert len(selected) == size
    if size >= len(kinds):
        assert {row["type"] for row in selected} == set(kinds)
    else:
        assert len({row["type"] for row in selected}) == size
