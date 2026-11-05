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


def test_snapshot_directory_name_does_not_replace_explicit_split_identity(tmp_path):
    root = tmp_path / "private-bytes"
    root.mkdir()
    path = root / "rulebook.md"
    path.write_text("# Exclude returned orders\nweek_start: monday\n", encoding="utf-8")
    clauses = compile_clauses(
        (SourceFile(path, "rulebook.md", sha256_file(path)),), split_id="train"
    )
    assert {c.source_document.split_id for c in clauses} == {"train"}
    assert clauses[0].status == "UNMAPPED"
    assert (
        resolve_declaration(clauses, "week_start", split_id="train").declaration.value == "monday"
    )


def test_clause_storage_preserves_full_documents_and_unresolved_normative_text(tmp_path):
    import duckdb

    from datamate.preparation.storage import create_storage, store_clauses

    path = tmp_path / "rulebook.md"
    text = "# Exclude returns\nUnfamiliar business condition.\n"
    path.write_bytes(text.encode())
    clauses = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    with duckdb.connect() as db:
        create_storage(db)
        store_clauses(db, clauses)
        assert db.execute("SELECT text FROM meta.source_documents").fetchall() == [(text,)]
        assert {row[0] for row in db.execute("SELECT clause_id FROM meta.clauses").fetchall()} == {
            clause.clause_id for clause in clauses
        }
        assert db.execute("SELECT DISTINCT status FROM meta.clauses").fetchall() == [("UNMAPPED",)]


def binding_world(tmp_path):
    import duckdb

    from datamate.preparation.loaders import load_tables
    from datamate.preparation.profiling import profile_table
    from datamate.preparation.storage import create_storage, stage_table

    root = tmp_path / "train"
    business = root / "data" / "shop"
    business.mkdir(parents=True)
    path = root / "rulebook.md"
    path.write_text(
        "# Active rules\nNever assume a universal valid-order flag.\n", encoding="utf-8"
    )
    clauses = compile_clauses((SourceFile(path, "rulebook.md", sha256_file(path)),))
    path = business / "sales.csv"
    path.write_text(
        "order_id,order_total,line_net_amount,product_id,quantity\n"
        "A,200,100,P1,1\nA,200,100,P2,1\n",
        encoding="utf-8",
    )
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db, SourceFile(path, "data/shop/sales.csv", sha256_file(path)), next(load_tables(path))
        )
        profile = profile_table(db, table.table_id)
    return clauses, profile


def role_binding(profile, role, column):
    from datamate.preparation.models import Binding, EvidenceRef

    return Binding(
        kind="role",
        table_id=profile.table_id,
        columns=(column,),
        value=role,
        profile_digest=profile.digest,
        evidence=(
            EvidenceRef(
                source=profile.source,
                sha256=profile.source_sha256,
                locator=f"header:{profile.table_id}/{column}",
            ),
        ),
    )


def test_combined_table_binds_line_and_order_grain_separately(tmp_path):
    from datamate.preparation.catalog import validate_bindings
    from datamate.preparation.models import Binding

    clauses, profile = binding_world(tmp_path)
    roles = [
        role_binding(profile, name, f"c{i:04d}")
        for i, name in enumerate(
            ("order_id", "order_total", "line_net_amount", "product_id", "quantity"), 1
        )
    ]
    grains = [
        Binding(
            kind="grain",
            table_id=profile.table_id,
            columns=tuple(role.columns[0] for role in roles),
            value=grain,
            profile_digest=profile.digest,
            evidence=roles[0].evidence,
            dependencies=tuple(role.binding_id for role in roles),
        )
        for grain in ("order_header", "order_line")
    ]
    bindings = validate_bindings(clauses, [profile], [*grains, *roles])
    assert {b.value for b in bindings.accepted if b.kind == "grain"} == {
        "order_header",
        "order_line",
    }
    assert len(bindings.accepted) == 7


def test_stale_conflicting_or_injected_bindings_are_not_accepted(tmp_path):
    from datamate.preparation.catalog import validate_bindings
    from datamate.preparation.models import Binding

    clauses, profile = binding_world(tmp_path)
    valid = role_binding(profile, "order_total", "c0002")
    stale = valid.model_copy(update={"profile_digest": "f" * 64})
    conflict = valid.model_copy(update={"value": "line_net_amount"})
    result = validate_bindings(clauses, [profile], [valid, stale, conflict])
    assert not result.accepted
    assert sum(entry.status == "CONFLICT" for entry in result.entries) == 2
    assert any("profile" in entry.reason for entry in result.entries)
    with pytest.raises(ValueError):
        Binding(**(valid.model_dump() | {"expression": "__import__('os').system('x')"}))


def test_measure_policies_keep_return_fee_and_warranty_predicates_separate():
    from datamate.preparation.models import MeasurePolicy

    revenue = MeasurePolicy(measure="revenue", projection_id="p_" + "a" * 16)
    debt = MeasurePolicy(measure="debt", projection_id="p_" + "a" * 16)
    visits = MeasurePolicy(measure="visit_count", projection_id="p_" + "b" * 16)
    items = MeasurePolicy(measure="item_quantity", projection_id="p_" + "c" * 16)
    assert revenue.digest != debt.digest
    assert "exclude_returned" in revenue.predicates
    assert "exclude_returned" not in debt.predicates
    assert "exclude_fee" not in revenue.predicates
    assert "exclude_fee" in items.predicates
    assert "exclude_zero_amount" not in visits.predicates


def test_measure_renderer_uses_only_explicitly_qualified_flat_projections(tmp_path):
    from dataclasses import replace

    from datamate.preparation.catalog import render_measure, validate_bindings
    from datamate.preparation.models import MeasurePolicy

    clauses, profile = binding_world(tmp_path)
    bindings = validate_bindings(clauses, [profile], [])
    policy = MeasurePolicy(measure="revenue", projection_id="p_" + "a" * 16)
    with pytest.raises(ValueError, match="qualified"):
        render_measure(policy, bindings)
    trusted = replace(
        bindings, qualified_measures=((policy.projection_id, policy.measure, policy.digest),)
    )
    sql = render_measure(policy, trusted)
    assert '"clean"."p_aaaaaaaaaaaaaaaa"' in sql
    assert 'SUM("line_net_amount")' in sql
    assert 'NOT "cancelled"' in sql and 'NOT "returned"' in sql
    with pytest.raises(ValueError):
        MeasurePolicy(measure="revenue", projection_id="x; DROP TABLE clean.x")


def test_required_rule_dependencies_ignore_top_k_truncation(tmp_path):
    from datamate.preparation.catalog import required_clauses, retrieve_clauses, validate_bindings

    clauses, profile = binding_world(tmp_path)
    bindings = validate_bindings(clauses, [profile], [])
    retrieved = retrieve_clauses("active", clauses, limit=1)
    assert len(retrieved) == 1
    required = required_clauses(bindings, profile.table_id)
    assert required == {clause.clause_id for clause in clauses}
    assert required - {match.clause.clause_id for match in retrieved}


def test_snapshot_is_not_event_history_and_reference_date_cannot_move_it(tmp_path):
    import duckdb

    from datamate.preparation.catalog import validate_bindings
    from datamate.preparation.models import Binding, EvidenceRef, SourceRow, SourceTable
    from datamate.preparation.profiling import profile_table
    from datamate.preparation.storage import create_storage, stage_table

    clauses, _ = binding_world(tmp_path)
    path = tmp_path / "train/data/shop/README.md"
    path.write_text("snapshot_at: 2025-03-01\n", encoding="utf-8")
    book = tmp_path / "train/rulebook.md"
    clauses = compile_clauses(
        (
            SourceFile(book, "rulebook.md", sha256_file(book)),
            SourceFile(path, "data/shop/README.md", sha256_file(path)),
        )
    )
    with duckdb.connect() as db:
        create_storage(db)
        staged = stage_table(
            db,
            SourceFile(tmp_path / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
            SourceTable(
                "stock", ("product_id", "inventory_quantity"), 1, [SourceRow(2, ("P1", 10))]
            ),
        )
        profile = profile_table(db, staged.table_id)
    roles = [
        role_binding(profile, "product_id", "c0001"),
        role_binding(profile, "inventory_quantity", "c0002"),
    ]
    clause = next(c for c in clauses if c.source_document.source.endswith("README.md"))
    ref = EvidenceRef(
        source=clause.source_document.source,
        sha256=clause.source_document.sha256,
        locator=f"clause:{clause.clause_id}",
        line_start=clause.line_start,
        line_end=clause.line_end,
        quote_sha256=clause.quote_sha256,
    )
    snapshot = Binding(
        kind="grain",
        value="inventory_snapshot",
        table_id=profile.table_id,
        columns=("c0001", "c0002"),
        profile_digest=profile.digest,
        evidence=(ref,),
        dependencies=tuple(r.binding_id for r in roles),
        snapshot_at="2025-03-01",
    )
    history = Binding(
        kind="capability",
        value="event_history",
        table_id=profile.table_id,
        columns=snapshot.columns,
        profile_digest=profile.digest,
        evidence=(ref,),
        available=False,
        dependencies=(snapshot.binding_id,),
    )
    bindings = validate_bindings(clauses, [profile], [history, snapshot, *roles])
    assert bindings.capability(profile.table_id, "event_history") is False
    assert next(b for b in bindings.accepted if b.kind == "grain").snapshot_at == "2025-03-01"
    moved = snapshot.model_copy(update={"snapshot_at": "2026-03-01"})
    assert moved not in validate_bindings(clauses, [profile], [moved, *roles]).accepted
    assert bindings.capability(profile.table_id, "hour") is None


def test_fixed_measure_queries_exclude_returns_only_for_revenue_and_keep_fee_revenue(tmp_path):
    from dataclasses import replace

    import duckdb

    from datamate.preparation.catalog import render_measure, validate_bindings
    from datamate.preparation.models import MeasurePolicy

    clauses, profile = binding_world(tmp_path)
    bindings = validate_bindings(clauses, [profile], [])
    projection = "p_" + "a" * 16
    revenue = MeasurePolicy(measure="revenue", projection_id=projection)
    debt = MeasurePolicy(measure="debt", projection_id=projection)
    bindings = replace(
        bindings,
        qualified_measures=tuple((projection, p.measure, p.digest) for p in (revenue, debt)),
    )
    with duckdb.connect() as db:
        db.execute("CREATE SCHEMA clean")
        db.execute(
            f'CREATE TABLE clean."{projection}"(line_net_amount INTEGER, debt_amount INTEGER,'
            "cancelled BOOLEAN, returned BOOLEAN, is_fee BOOLEAN,payment_method VARCHAR)"
        )
        db.executemany(
            f'INSERT INTO clean."{projection}" VALUES (?,?,?,?,?,?)',
            [
                (100, 100, False, False, False, "debt"),
                (30, 50, False, True, False, "debt"),
                (25, 0, False, False, True, "cash"),
                (0, 40, False, False, False, "cod"),
                (5, 70, True, False, False, "debt"),
            ],
        )
        assert db.execute(render_measure(revenue, bindings)).fetchone()[0] == 125
        assert db.execute(render_measure(debt, bindings)).fetchone()[0] == 150


@pytest.mark.parametrize(("header", "expected"), [("", False), ("trị", False), ("Giá trị", True)])
def test_header_must_match_the_whole_readme_field_declaration(tmp_path, header, expected):
    import duckdb

    from datamate.preparation.catalog import validate_bindings
    from datamate.preparation.models import Binding, EvidenceRef, SourceRow, SourceTable
    from datamate.preparation.profiling import profile_table
    from datamate.preparation.storage import create_storage, stage_table

    clauses, _ = binding_world(tmp_path)
    book = tmp_path / "train/rulebook.md"
    readme = tmp_path / "train/data/shop/README.md"
    readme.write_text("Giá trị là số tiền trả lần đó.\n", encoding="utf-8")
    clauses = compile_clauses(
        (
            SourceFile(book, "rulebook.md", sha256_file(book)),
            SourceFile(readme, "data/shop/README.md", sha256_file(readme)),
        )
    )
    with duckdb.connect() as db:
        create_storage(db)
        staged = stage_table(
            db,
            SourceFile(tmp_path / "x.xlsx", "data/shop/x.xlsx", "a" * 64),
            SourceTable("sheet", (header,), 1, [SourceRow(2, (10,))]),
        )
        profile = profile_table(db, staged.table_id)
    clause = next(c for c in clauses if c.source_document.source.endswith("README.md"))
    proposal = Binding(
        kind="role",
        table_id=profile.table_id,
        columns=("c0001",),
        value="payment_amount",
        profile_digest=profile.digest,
        evidence=(
            EvidenceRef(
                source=clause.source_document.source,
                sha256=clause.source_document.sha256,
                locator=f"clause:{clause.clause_id}",
                line_start=clause.line_start,
                line_end=clause.line_end,
                quote_sha256=clause.quote_sha256,
            ),
        ),
    )
    assert bool(validate_bindings(clauses, [profile], [proposal]).accepted) is expected


@pytest.mark.parametrize("native", [True, False])
def test_native_day_precision_is_distinct_from_unparsed_text_dates(tmp_path, native):
    from datetime import date

    import duckdb

    from datamate.preparation.catalog import validate_bindings
    from datamate.preparation.models import Binding, SourceRow, SourceTable
    from datamate.preparation.profiling import profile_table
    from datamate.preparation.storage import create_storage, stage_table

    clauses, _ = binding_world(tmp_path)
    with duckdb.connect() as db:
        create_storage(db)
        source = SourceFile(tmp_path / "dates.xlsx", "data/shop/dates.xlsx", "a" * 64)
        staged = stage_table(
            db,
            source,
            SourceTable(
                "dates",
                ("ordered_at",),
                1,
                [SourceRow(2, (date(2025, 1, 1) if native else "01/01/2025",))],
            ),
        )
        profile = profile_table(db, staged.table_id)
    witness = role_binding(profile, "ordered_at", "c0001").evidence
    proposal = Binding(
        kind="capability",
        value="hour",
        table_id=profile.table_id,
        columns=("c0001",),
        profile_digest=profile.digest,
        evidence=witness,
        available=False,
    )
    result = validate_bindings(clauses, [profile], [proposal])
    assert result.capability(profile.table_id, "hour") is (False if native else None)
