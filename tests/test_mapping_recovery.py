"""Source-only schema gaps and evidence-checked mapping proposals."""

import csv

import duckdb
import pytest

from datamate.preparation.catalog import compile_clauses, validate_bindings
from datamate.preparation.discovery import sha256_file
from datamate.preparation.loaders import load_tables
from datamate.preparation.models import Binding, EvidenceRef, SourceFile
from datamate.preparation.profiling import profile_table
from datamate.preparation.storage import create_storage, stage_table


def mapping_world(
    tmp_path, header="Montant", description="thành tiền sau giảm", section="Cột trong `sales.csv`"
):
    business = tmp_path / "data/shop"
    business.mkdir(parents=True)
    rules = tmp_path / "rulebook.md"
    rules.write_text("# Rules\nKeep unknown meanings unresolved.\n", encoding="utf-8")
    readme = business / "README.md"
    readme.write_text(f"# Sales\n## {section}\n- **{header}**: {description}\n", encoding="utf-8")
    path = business / "sales.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([[header], ["1.600đ"], ["69k"]])
    clauses = compile_clauses(
        tuple(
            SourceFile(p, n, sha256_file(p))
            for p, n in [(rules, "rulebook.md"), (readme, "data/shop/README.md")]
        ),
        split_id="train",
    )
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db, SourceFile(path, "data/shop/sales.csv", sha256_file(path)), next(load_tables(path))
        )
        profile = profile_table(db, table.table_id)
    return clauses, profile


def candidate(clauses, profile, role="line_net_amount"):
    clause = next(c for c in clauses if "**" in c.quote)
    return Binding(
        kind="role",
        table_id=profile.table_id,
        columns=("c0001",),
        value=role,
        profile_digest=profile.digest,
        evidence=(
            EvidenceRef(
                source=profile.source,
                sha256=profile.source_sha256,
                locator=f"header:{profile.table_id}/c0001",
            ),
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


@pytest.mark.parametrize(
    ("description", "role"),
    [
        ("thành tiền sau giảm", "line_net_amount"),
        ("mã đơn", "order_id"),
        ("mã hàng", "product_id"),
        ("số lượng", "quantity"),
        ("đơn vị tính", "unit"),
        ("ngày bán", "ordered_at"),
        ("tên hàng", "product_name"),
    ],
)
def test_colon_declaration_proves_a_role_for_an_unfamiliar_header(tmp_path, description, role):
    clauses, profile = mapping_world(tmp_path, description=description)
    proposal = candidate(clauses, profile, role)
    assert proposal in validate_bindings(clauses, [profile], [proposal]).accepted


@pytest.mark.parametrize(
    ("description", "section", "role"),
    [
        ("tổng tiền cả đơn", "Cột trong `sales.csv`", "line_net_amount"),
        ("thành tiền sau giảm", "Cột trong `other.csv`", "line_net_amount"),
        ("có lẽ là tiền", "Cột trong `sales.csv`", "line_net_amount"),
    ],
)
def test_uncertain_or_different_file_declarations_do_not_authorize_role(
    tmp_path, description, section, role
):
    clauses, profile = mapping_world(tmp_path, description=description, section=section)
    assert not validate_bindings(clauses, [profile], [candidate(clauses, profile, role)]).accepted


def test_rejected_supplied_mapping_does_not_hide_native_role(tmp_path):
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path, header="order_id", description="mã đơn")
    invalid = candidate(clauses, profile, "unit_cost")
    result = _source_roles([profile], clauses, (invalid,))
    assert any(b.kind == "role" and b.value == "order_id" for b in result.accepted)


def test_unknown_column_is_a_source_issue_not_an_unmarked_clean_column(tmp_path):
    from datamate.preparation.mapping import discover_mapping_issues
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path)
    bindings = _source_roles([profile], clauses, ())
    issues = discover_mapping_issues([profile], bindings)
    assert len(issues) == 1
    assert issues[0].kind == "unmapped_role"
    assert issues[0].scope == profile.table_id
    assert "c0001" in issues[0].detail
    assert issues == discover_mapping_issues([profile], bindings)
    accepted = _source_roles([profile], clauses, (candidate(clauses, profile),))
    assert discover_mapping_issues([profile], accepted) == ()


def test_parent_file_section_still_applies_under_nested_heading(tmp_path):
    clauses, profile = mapping_world(tmp_path, section="Cột trong `other.csv`\n### Columns")
    assert not validate_bindings(clauses, [profile], [candidate(clauses, profile)]).accepted


def test_generic_name_does_not_prove_product_name(tmp_path):
    clauses, profile = mapping_world(tmp_path, description="tên")
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "product_name")]
    ).accepted


def test_supplied_grain_can_depend_on_native_roles_without_duplicate_ids(tmp_path):
    from test_semantic_bindings import binding_world

    from datamate.preparation.v2 import _source_roles

    clauses, profile = binding_world(tmp_path)
    native = _source_roles([profile], clauses, ())
    grain = next(b for b in native.accepted if b.kind == "grain" and b.value == "order_line")
    result = _source_roles([profile], clauses, (grain,))
    assert grain in result.accepted
    assert len(result.entries) == len({e.binding.binding_id for e in result.entries})


def test_invalid_competing_role_does_not_duplicate_native_id(tmp_path):
    from datamate.preparation.v2 import _source_roles

    clauses, profile = mapping_world(tmp_path, header="order_id", description="mã đơn")
    native = next(b for b in _source_roles([profile], clauses, ()).accepted if b.kind == "role")
    result = _source_roles([profile], clauses, (native, candidate(clauses, profile, "unit_cost")))
    assert native in result.accepted
    assert len(result.entries) == len({e.binding.binding_id for e in result.entries})
