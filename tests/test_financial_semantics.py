"""Financial meanings stay distinct and percentages retain their source unit."""

from decimal import Decimal

import pytest
from test_mapping_recovery import candidate, mapping_world

from datamate.preparation.catalog import _ROLE_HEADERS, validate_bindings
from datamate.preparation.mapping import RoleMapping
from datamate.preparation.normalization import parse_candidates


@pytest.mark.parametrize(
    ("role", "meaning"),
    [
        ("unit_price", "đơn giá"),
        ("line_discount_amount", "giảm giá dòng bằng tiền"),
        ("line_discount_percent", "tỷ lệ giảm giá dòng (%)"),
        ("order_discount_amount", "giảm giá toàn đơn bằng tiền"),
        ("order_discount_percent", "tỷ lệ giảm giá toàn đơn (%)"),
    ],
)
def test_model_can_express_exact_source_scoped_financial_meaning(tmp_path, role, meaning):
    clauses, profile = mapping_world(tmp_path, description=meaning)
    clause = next(c for c in clauses if "**" in c.quote)
    mapping = RoleMapping(column_id="c0001", role=role, clause_ids=(clause.clause_id,))
    assert mapping.role == role
    assert (
        candidate(clauses, profile, role)
        in validate_bindings(clauses, [profile], [candidate(clauses, profile, role)]).accepted
    )


def test_price_is_not_cost_and_discount_scope_cannot_be_swapped(tmp_path):
    clauses, profile = mapping_world(tmp_path / "price", description="đơn giá")
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "unit_cost")]
    ).accepted
    clauses, profile = mapping_world(tmp_path / "discount", description="tỷ lệ giảm giá dòng (%)")
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "order_discount_percent")]
    ).accepted


def test_financial_declaration_from_another_file_remains_unresolved(tmp_path):
    clauses, profile = mapping_world(
        tmp_path, description="đơn giá", section="Cột trong `other.csv`"
    )
    assert not validate_bindings(
        clauses, [profile], [candidate(clauses, profile, "unit_price")]
    ).accepted


def test_model_role_schema_matches_registered_semantic_roles():
    assert set(RoleMapping.model_json_schema()["properties"]["role"]["enum"]) == set(_ROLE_HEADERS)


@pytest.mark.parametrize(
    ("raw", "context", "expected"),
    [
        ("20%", {}, Decimal(20)),
        (20, {}, Decimal(20)),
        (Decimal("0.2"), {"percentage_ratio": True}, Decimal(20)),
    ],
)
def test_percent_points_and_native_excel_ratios_are_explicit(raw, context, expected):
    choices = parse_candidates(raw, "percentage", (), context)
    assert len(choices) == 1 and choices[0].value == expected
    assert choices[0].unit == "percent"


@pytest.mark.parametrize("raw", [True, "120%", "-1%", float("inf")])
def test_invalid_discount_percent_does_not_become_a_valid_candidate(raw):
    assert parse_candidates(raw, "percentage", (), {}) == ()
