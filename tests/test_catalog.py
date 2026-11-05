"""Rule retrieval preserves evidence text and source coordinates."""

import pytest

from datamate.preparation.catalog import rule_entries, search_rules
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
        "NOT_APPLICABLE",
        "MAPPED",
        "UNMAPPED",
        "UNMAPPED",
        "NOT_APPLICABLE",
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
