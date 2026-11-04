"""Deterministic normalization requires explicit interpretation and evidence."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from datamate.preparation.models import SourceRow
from datamate.preparation.normalization import (
    ColumnPlan,
    Evidence,
    TablePlan,
    normalize_cell,
    normalize_rows,
)


def evidence():
    return Evidence(source="rulebook.md", sha256="a" * 64, locator="section 8")


@pytest.mark.parametrize(
    "raw,settings,expected",
    [
        ("75,000", {"kind": "integer", "number_style": "comma_thousands"}, 75000),
        ("198k", {"kind": "integer", "allow_k_suffix": True}, 198000),
        ("1.234,50", {"kind": "decimal", "number_style": "vi"}, Decimal("1234.50")),
        ("1 thg 2 2025", {"kind": "date", "date_format": "dmy"}, date(2025, 2, 1)),
        ("02/03", {"kind": "date", "date_format": "dmy", "year": 2025}, date(2025, 3, 2)),
        ("true", {"kind": "boolean"}, True),
    ],
)
def test_explicit_normalization(raw, settings, expected):
    value, codes = normalize_cell(raw, ColumnPlan(**settings, evidence=evidence()))
    assert value == expected
    assert codes == ("converted",)


@pytest.mark.parametrize(
    "raw,settings",
    [
        ("1,234", {"kind": "integer"}),
        ("198k", {"kind": "integer"}),
        ("01/02/2025", {"kind": "date"}),
        ("02/03", {"kind": "date", "date_format": "dmy"}),
        ("bad", {"kind": "decimal"}),
        (True, {"kind": "integer"}),
    ],
)
def test_ambiguous_or_invalid_values_are_null_with_warning_not_zero(raw, settings):
    value, codes = normalize_cell(raw, ColumnPlan(**settings, evidence=evidence()))
    assert value is None
    assert codes == ("parse_failed",)


def test_auto_preserves_text_and_decimal_precision():
    assert normalize_cell("75,000", ColumnPlan()) == ("75,000", ())
    assert normalize_cell(0.1, ColumnPlan())[0] == Decimal("0.1")
    assert normalize_cell("", ColumnPlan()) == ("", ())


def test_transformations_require_evidence_and_group_contract():
    with pytest.raises(ValidationError):
        ColumnPlan(kind="integer")
    with pytest.raises(ValidationError):
        TablePlan(deduplicate_order_lines=True, evidence=evidence())


def test_scoped_fill_and_exact_order_dedup_preserve_raw_rows():
    rows = [
        SourceRow(2, ("A", "Lan", 1)),
        SourceRow(3, (None, None, 2)),
        SourceRow(4, ("B", None, 1)),
        SourceRow(5, ("B", None, 1)),
    ]
    plan = TablePlan(
        group_key="c0001", fill_columns=["c0002"], deduplicate_order_lines=True, evidence=evidence()
    )
    result = list(normalize_rows(rows, 3, plan))
    assert result[1].values == ("A", "Lan", 2)
    assert result[1].source.values == (None, None, 2)
    assert result[2].values == ("B", None, 1)
    assert not result[2].excluded
    assert result[3].excluded and result[3].duplicate_of == 4
    assert result[3].source.number == 5


def test_non_order_duplicate_rows_are_retained():
    result = list(normalize_rows([SourceRow(2, ("A",)), SourceRow(3, ("A",))], 1, TablePlan()))
    assert len(result) == 2
    assert not any(row.excluded for row in result)


def test_same_values_in_different_orders_are_not_deduplicated():
    plan = TablePlan(group_key="c0001", deduplicate_order_lines=True, evidence=evidence())
    result = list(normalize_rows([SourceRow(1, ("A", 1)), SourceRow(2, ("B", 1))], 2, plan))
    assert not any(row.excluded for row in result)


def test_long_numbers_and_k_suffix_do_not_round_at_decimal_context_precision():
    raw = "12345678901234567890123456789"
    integer = ColumnPlan(kind="integer", evidence=evidence())
    suffix = ColumnPlan(kind="integer", allow_k_suffix=True, evidence=evidence())
    assert normalize_cell(raw, integer)[0] == 12345678901234567890123456789
    assert normalize_cell(raw + "k", suffix)[0] == 12345678901234567890123456789000


def test_fill_uses_first_group_value_and_records_conflicting_values():
    plan = TablePlan(group_key="c0001", fill_columns=["c0002"], evidence=evidence())
    result = list(
        normalize_rows(
            [SourceRow(1, ("A", "Lan")), SourceRow(2, ("A", "Hoa")), SourceRow(3, (None, None))],
            2,
            plan,
        )
    )
    assert result[2].values == ("A", "Lan")
    assert result[1].values == ("A", "Hoa")
    assert any(event["code"] == "group_conflict" for event in result[1].events)


def test_different_formula_cells_are_not_exact_duplicates():
    plan = TablePlan(group_key="c0001", deduplicate_order_lines=True, evidence=evidence())
    rows = [
        SourceRow(1, ("A", 2), formulas=(None, "=1+1")),
        SourceRow(2, ("A", 2), formulas=(None, "=2*1")),
    ]
    assert not any(row.excluded for row in normalize_rows(rows, 2, plan))


def test_uncached_formula_is_not_a_blank_group_field_or_order_key():
    plan = TablePlan(group_key="c0001", fill_columns=["c0002"], evidence=evidence())
    result = list(
        normalize_rows(
            [
                SourceRow(1, ("A", "Lan")),
                SourceRow(2, ("A", None), formulas=(None, '=CONCAT("H","oa")')),
                SourceRow(3, (None, "Hoa"), formulas=('="B"', None)),
                SourceRow(4, (None, None)),
            ],
            2,
            plan,
        )
    )
    assert result[1].values == ("A", None)
    assert result[2].values == (None, "Hoa")
    assert result[3].values == (None, None)
