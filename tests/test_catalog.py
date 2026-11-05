"""Rule retrieval preserves evidence text and source coordinates."""

import pytest

from datamate.preparation.catalog import compile_clauses, rule_entries, search_rules
from datamate.preparation.discovery import sha256_file
from datamate.preparation.models import SourceFile


def test_catalog_preserves_rule_sections_scope_and_lexical_lookup(tmp_path):
    path = tmp_path / "rulebook.md"
    path.write_text(
        "# Doanh thu\nLoại đơn trả hàng.\n## Thời gian\nDùng as_of_date.\n", encoding="utf-8"
    )
    entries = rule_entries((SourceFile(path, "rulebook.md", sha256_file(path)),))
    assert len(entries) == 2
    assert entries[0]["line_start"] == 1 and entries[0]["line_end"] == 2
    assert entries[0]["scope"] == "split" and entries[0]["untrusted"] is True
    assert entries[0]["source_sha256"] == sha256_file(path)
    assert search_rules(entries, "doanh thu", limit=1)[0]["title"] == "Doanh thu"
    assert search_rules(entries, "absent") == []
    with pytest.raises(ValueError):
        search_rules(entries, "doanh thu", limit=0)


def test_all_clauses_preserved_even_when_unmapped(tmp_path):
    from datamate.preparation.catalog import compile_clauses

    root = tmp_path / "train"
    root.mkdir()
    path = root / "rulebook.md"
    path.write_text(
        "# Calendar\n"
        "- week_start: monday\n"
        "- unfamiliar ritual decides payment aging\n"
        "\n"
        "A wrapped statement about\n"
        "a new business concept.\n"
        "\n"
        "| Scope | Rule |\n"
        "| --- | --- |\n"
        "| bakery | week_start: friday |\n",
        encoding="utf-8",
        newline="\n",
    )
    clauses = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    assert [clause.quote for clause in clauses] == [
        "# Calendar\n",
        "- week_start: monday\n",
        "- unfamiliar ritual decides payment aging\n",
        "A wrapped statement about\na new business concept.\n",
        "| Scope | Rule |\n",
        "| --- | --- |\n",
        "| bakery | week_start: friday |\n",
    ]
    assert len({clause.clause_id for clause in clauses}) == 7
    assert [clause.status for clause in clauses] == [
        "UNMAPPED",
        "MAPPED",
        "UNMAPPED",
        "UNMAPPED",
        "UNMAPPED",
        "NOT_APPLICABLE",
        "UNMAPPED",
    ]
    assert clauses[1].declaration.value == "monday"
    assert clauses[2].declaration is None
    assert all(clause.source_document is clauses[0].source_document for clause in clauses)


def test_source_quote_hash_and_split_are_bound(tmp_path):
    from dataclasses import replace
    from hashlib import sha256

    from datamate.preparation.catalog import compile_clauses

    root = tmp_path / "private"
    root.mkdir()
    path = root / "rulebook.md"
    original = "\ufeff# Calendar\r\nweek_start: sunday\r\n"
    path.write_bytes(original.encode("utf-8"))
    source = SourceFile(path, "rulebook.md", sha256_file(path))
    clause = compile_clauses((source,))[1]
    assert clause.source_document.split_id == "private"
    assert clause.source_document.text == original
    assert clause.source_document.sha256 == source.sha256
    assert (clause.line_start, clause.line_end) == (2, 2)
    assert clause.quote == "week_start: sunday\r\n"
    assert clause.quote_sha256 == sha256(clause.quote.encode("utf-8")).hexdigest()
    for mutation in (
        {"quote": "week_start: monday\r\n"},
        {"line_start": 1},
        {"quote_sha256": "0" * 64},
    ):
        with pytest.raises(ValueError):
            replace(clause, **mutation)

    path.write_bytes(b"week_start: monday\n")
    with pytest.raises(ValueError, match="hash"):
        compile_clauses((source,))

    other_root = tmp_path / "train"
    other_root.mkdir()
    other_readme = other_root / "README.md"
    other_readme.write_text("week_start: tuesday\n", encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        compile_clauses(
            (
                SourceFile(path, "rulebook.md", sha256_file(path)),
                SourceFile(other_readme, "README.md", sha256_file(other_readme)),
            )
        )
    with pytest.raises(ValueError, match="rulebook"):
        compile_clauses((SourceFile(other_readme, "README.md", sha256_file(other_readme)),))


def test_bm25_retrieval_is_deterministic_but_does_not_drop_unmatched_rules(tmp_path):
    from datamate.preparation.catalog import retrieve_clauses

    root = tmp_path / "train"
    root.mkdir()
    path = root / "rulebook.md"
    path.write_text(
        "# Rules\n"
        "- Tổng doanh thu loại trả hàng.\n"
        "- Tổng doanh thu loại trả hàng.\n"
        "- Công nợ dùng ngày đến hạn.\n"
        "- Unfamiliar generic accounting ritual remains normative.\n",
        encoding="utf-8",
        newline="\n",
    )
    clauses = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    first = retrieve_clauses("ＴỔＮＧ DOANH THU", clauses, limit=2)
    second = retrieve_clauses("ＴỔＮＧ DOANH THU", tuple(reversed(clauses)), limit=2)

    assert [match.clause.clause_id for match in first] == [
        clauses[1].clause_id,
        clauses[2].clause_id,
    ]
    assert [match.clause.clause_id for match in first] == [
        match.clause.clause_id for match in second
    ]
    assert len(first) == 2
    assert first[0].bm25_score > 0
    assert first[0].fuzzy_score > 0
    assert first[0].score > first[0].bm25_score
    assert first[0].index_digest == second[0].index_digest
    assert first[0].tokenizer_version == second[0].tokenizer_version
    assert len(first[0].index_digest) == 64
    assert len(clauses) == 5
    assert clauses[-1].clause_id not in {match.clause.clause_id for match in first}
    assert all(clause.status == "UNMAPPED" for clause in clauses[1:])
    assert all(match.clause.declaration is None for match in first)


def test_retrieval_index_digest_changes_with_source_and_limits_are_bounded(tmp_path):
    from datamate.preparation.catalog import retrieve_clauses

    root = tmp_path / "train"
    root.mkdir()
    path = root / "rulebook.md"
    path.write_text("# Rules\n- Revenue excludes returns.\n", encoding="utf-8")
    original = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    original_match = retrieve_clauses("revenue returns", original, limit=1)[0]
    path.write_text("# Rules\n- Revenue includes returns.\n", encoding="utf-8")
    changed = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    changed_match = retrieve_clauses("revenue returns", changed, limit=1)[0]
    assert original_match.index_digest != changed_match.index_digest
    assert retrieve_clauses("unrelated", changed, limit=1) == []
    with pytest.raises(ValueError):
        retrieve_clauses("revenue", changed, limit=0)
    with pytest.raises(ValueError):
        retrieve_clauses("revenue", changed, limit=101)
    with pytest.raises(ValueError):
        retrieve_clauses("revenue", changed, limit=1.5)
    with pytest.raises(ValueError):
        retrieve_clauses("revenue", changed, limit=True)
