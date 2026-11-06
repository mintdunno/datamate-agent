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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("532.000đ", Decimal("532000")),
        ("4,544tr", Decimal("4544000")),
        ("294,7703tr", Decimal("294770300")),
        ("8tr399", Decimal("8399000")),
        ("16 076", Decimal("16076")),
        ("500k", Decimal("500000")),
        ("(532.000đ)", Decimal("-532000")),
        ("-4,544tr", Decimal("-4544000")),
    ],
)
def test_money_candidates_preserve_literal_scale_and_sign(raw, expected):
    from datamate.preparation.normalization import parse_candidates

    candidates = parse_candidates(raw, "money", (), {})
    assert expected in [item.value for item in candidates if item.precision_supported]


def test_unsuffixed_grouped_spelling_keeps_both_scale_hypotheses():
    from datamate.preparation.normalization import parse_candidates

    candidates = parse_candidates("1.000", "money", (), {"source_total": "1000"})
    assert {item.value for item in candidates} == {Decimal("1.000"), Decimal("1000")}
    assert len(candidates) == 2
    assert all(item.grammar for item in candidates)


def test_quantity_units_native_decimal_and_precision_are_lossless():
    from datamate.preparation.normalization import parse_candidates

    quantity = parse_candidates("-2 kg", "quantity", (), {})
    assert [(item.value, item.unit) for item in quantity] == [(Decimal("-2"), "kg")]
    native = Decimal("1E-7")
    assert parse_candidates(native, "money", (), {})[0].value.as_tuple().exponent == -7
    unsupported = parse_candidates("1." + "0" * 38, "money", (), {})
    assert unsupported and all(not item.precision_supported for item in unsupported)
    assert all(item.value == "1." + "0" * 38 for item in unsupported)
    assert parse_candidates(True, "money", (), {}) == ()
    assert parse_candidates(float("inf"), "money", (), {}) == ()
    assert parse_candidates(Decimal("NaN"), "money", (), {}) == ()
    assert parse_candidates(None, "money", (), {})[0].value is None
    assert parse_candidates("   ", "quantity", (), {})[0].value == "   "


def test_id_candidates_keep_raw_and_trimmed_leading_zero_forms():
    from datamate.preparation.normalization import parse_candidates

    candidates = parse_candidates(" 001 ", "id", (), {})
    assert [item.value for item in candidates] == [" 001 ", "001"]
    assert "collision_check" in candidates[1].context_requirements
    assert parse_candidates("001", "id", (), {})[0].value == "001"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2025-06-03", date(2025, 6, 3)),
        ("3/6/2025", date(2025, 6, 3)),
        ("3 thg 6 2025", date(2025, 6, 3)),
    ],
)
def test_date_candidates_parse_iso_and_vietnamese_dmy(raw, expected):
    from datamate.preparation.normalization import parse_candidates

    assert [item.value for item in parse_candidates(raw, "date", (), {})] == [expected]


def test_incomplete_source_date_uses_only_sheet_year():
    from datamate.preparation.normalization import parse_candidates

    assert parse_candidates("3/6", "date", (), {}) == ()
    assert parse_candidates("3/6", "date", (), {"sheet_year": 2025})[0].value == date(2025, 6, 3)
    with pytest.raises(ValueError, match="context"):
        parse_candidates("3/6", "date", (), {"question_year": 2026})


def test_excel_serials_require_epoch_and_reject_1900_phantom_day():
    from datetime import datetime

    from datamate.preparation.normalization import parse_candidates

    windows = {"workbook_epoch": datetime(1899, 12, 30)}
    mac = {"workbook_epoch": datetime(1904, 1, 1)}
    assert parse_candidates(61, "date", (), {}) == ()
    assert parse_candidates(61, "date", (), windows)[0].value == date(1900, 3, 1)
    assert parse_candidates(0, "date", (), mac)[0].value == date(1904, 1, 1)
    assert parse_candidates(60, "date", (), windows) == ()
    assert parse_candidates(Decimal("60.5"), "datetime", (), windows) == ()
    assert parse_candidates(Decimal("61.5"), "datetime", (), windows)[0].value == datetime(
        1900, 3, 1, 12
    )
    assert parse_candidates("1.25", "datetime", (), mac)[0].value == datetime(1904, 1, 2, 6)


def test_date_only_input_never_fabricates_midnight_timestamp():
    from datetime import datetime

    from datamate.preparation.normalization import parse_candidates

    native = date(2025, 6, 3)
    candidates = parse_candidates(native, "datetime", (), {})
    assert not any(isinstance(item.value, datetime) for item in candidates)
    assert not any(
        isinstance(item.value, datetime)
        for item in parse_candidates("2025-06-03", "datetime", (), {})
    )
    timestamp = datetime(2025, 6, 3, 14, 5)
    assert parse_candidates(timestamp, "datetime", (), {})[0].value == timestamp


def test_vietnamese_datetime_text_keeps_time_and_sheet_year():
    from datetime import datetime

    from datamate.preparation.normalization import parse_candidates

    assert parse_candidates("3/6/2025 14:05:09", "datetime", (), {})[0].value == datetime(
        2025, 6, 3, 14, 5, 9
    )
    assert parse_candidates("3/6 14:05", "datetime", (), {"sheet_year": 2025})[0].value == datetime(
        2025, 6, 3, 14, 5
    )
    assert parse_candidates("31/2/2025", "date", (), {}) == ()


def test_excel_serial_fraction_beyond_supported_precision_stays_raw():
    from datetime import datetime

    from datamate.preparation.normalization import parse_candidates

    raw = Decimal("61." + "0" * 37 + "5")
    candidate = parse_candidates(raw, "datetime", (), {"workbook_epoch": datetime(1899, 12, 30)})[0]
    assert candidate.value is raw
    assert not candidate.precision_supported
    assert "datetime_precision" in candidate.context_requirements


def test_very_large_native_and_compact_numbers_stay_raw_without_int_conversion_errors():
    from datamate.preparation.normalization import parse_candidates

    native = 10**5000
    compact = "9" * 5000 + "tr001"
    for raw in (native, compact):
        candidate = parse_candidates(raw, "money", (), {})[0]
        assert candidate.value == raw
        assert not candidate.precision_supported


def test_debt_note_k_candidate_requires_note_scope():
    from datamate.preparation.normalization import parse_candidates

    candidates = parse_candidates("nợ 500k", "money", (), {})
    assert [candidate.value for candidate in candidates] == [Decimal("500000")]
    assert "debt_note_scope" in candidates[0].context_requirements


@pytest.mark.parametrize("value", ["1.234.567", "1,234,567"])
def test_multiple_group_separators_have_no_decimal_interpretation(value):
    from datamate.preparation.normalization import parse_candidates

    assert [c.value for c in parse_candidates(value, "money", (), {})] == [Decimal("1234567")]


def test_k_suffix_does_not_hide_ambiguous_base_scale():
    from datamate.preparation.normalization import parse_candidates

    assert {c.value for c in parse_candidates("1.000k", "money", (), {})} == {
        Decimal("1000"),
        Decimal("1000000"),
    }


def test_iso_submicrosecond_precision_stays_raw_instead_of_being_truncated():
    from datamate.preparation.normalization import parse_candidates

    raw = "2025-06-03T14:05:09.1234567"
    candidate = parse_candidates(raw, "datetime", (), {})[0]
    assert candidate.value == raw
    assert not candidate.precision_supported


def test_parse_selection_requires_accepted_binding_and_never_selects_by_matching_total():
    from datamate.preparation.models import Binding, EvidenceRef, ResolvedBinding
    from datamate.preparation.normalization import parse_candidates, select_parse

    ref = EvidenceRef(source="x.csv", sha256="a" * 64, locator="header:t/c0001")
    binding = Binding(
        kind="role",
        value="line_net_amount",
        table_id="t",
        columns=("c0001",),
        profile_digest="b" * 64,
        evidence=(ref,),
    )
    accepted = ResolvedBinding(binding, "ACCEPTED", "fixture", (), "v1")
    ambiguous = parse_candidates("1.000", "money", (), {"source_total": 2000})
    assert select_parse(ambiguous, accepted).status == "UNRESOLVED"
    clear = parse_candidates("8tr399", "money", (), {})
    assert select_parse(clear, accepted).value == Decimal("8399000")
    assert (
        select_parse(clear, ResolvedBinding(binding, "UNRESOLVED", "no proof")).status
        == "UNRESOLVED"
    )


def test_native_time_does_not_require_fabricating_a_date():
    from datetime import time

    from datamate.preparation.normalization import parse_candidates

    value = time(13, 4, 5)
    assert parse_candidates(value, "datetime", (), {})[0].value is value
