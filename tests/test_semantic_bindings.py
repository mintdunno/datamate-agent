"""Synthetic source authority and conservative declaration checks."""

import pytest

from datamate.preparation.catalog import compile_clauses, resolve_declaration
from datamate.preparation.discovery import sha256_file
from datamate.preparation.models import ClauseDeclaration, SourceFile


def test_question_industry_general_readme_precedence(tmp_path):
    root = tmp_path / "private"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    rulebook = root / "rulebook.md"
    rulebook.write_text(
        "# General\nweek_start: monday\n# Industry: bakery\nweek_start: sunday\n",
        encoding="utf-8",
    )
    readme = business / "README.md"
    readme.write_text("week_start: tuesday\n", encoding="utf-8")
    clauses = compile_clauses(
        (
            SourceFile(rulebook, "rulebook.md", sha256_file(rulebook)),
            SourceFile(readme, "data/shop/README.md", sha256_file(readme)),
        )
    )
    assert [c.authority for c in clauses if c.declaration] == ["general", "industry", "readme"]
    assert (
        resolve_declaration(
            clauses, "week_start", split_id="private", industry="bakery"
        ).declaration.value
        == "sunday"
    )
    assert (
        resolve_declaration(clauses, "week_start", split_id="private").declaration.value == "monday"
    )
    readme_clause = next(c for c in clauses if c.authority == "readme" and c.declaration)
    general_clause = next(c for c in clauses if c.authority == "general" and c.declaration)
    assert readme_clause.conflicts_with == (general_clause.clause_id,)
    question = ClauseDeclaration("week_start", "friday")
    with pytest.raises(ValueError, match="permitted"):
        resolve_declaration(clauses, "week_start", split_id="private", question_override=question)
    assert (
        resolve_declaration(
            clauses,
            "week_start",
            split_id="private",
            industry="bakery",
            question_override=question,
            override_permitted=True,
        )
        == question
    )


def test_equal_authority_conflicts_and_unfamiliar_semantics_remain_unresolved(tmp_path):
    root = tmp_path / "train"
    root.mkdir()
    rulebook = root / "rulebook.md"
    rulebook.write_text(
        "week_start: monday\n\nweek_start: sunday\n\nrevenue: accept every row\n",
        encoding="utf-8",
    )
    clauses = compile_clauses((SourceFile(rulebook, "rulebook.md", sha256_file(rulebook)),))
    assert [clause.status for clause in clauses] == ["CONFLICT", "CONFLICT", "UNMAPPED"]
    assert clauses[0].conflicts_with == (clauses[1].clause_id,)
    assert clauses[1].conflicts_with == (clauses[0].clause_id,)
    assert resolve_declaration(clauses, "week_start", split_id="train") is None


def test_resolver_rejects_clauses_from_another_split(tmp_path):
    roots = []
    for split_id, day in (("train", "monday"), ("private", "sunday")):
        root = tmp_path / split_id
        root.mkdir()
        path = root / "rulebook.md"
        path.write_text(f"week_start: {day}\n", encoding="utf-8")
        roots.append(compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),)))
    with pytest.raises(ValueError, match="split"):
        resolve_declaration(roots[0], "week_start", split_id="private")
    with pytest.raises(ValueError, match="split"):
        resolve_declaration(roots[0] + roots[1], "week_start", split_id="private")
