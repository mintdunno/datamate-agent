"""Pure formula arithmetic rejects unsupported inputs without implicit rounding."""

from decimal import (
    ROUND_UP,
    Decimal,
    Inexact,
    Overflow,
    Rounded,
    Underflow,
    localcontext,
)

import pytest

from datamate.preparation.financial import compute_line_amount


@pytest.mark.parametrize(
    ("quantity", "price", "formula", "discount", "expected"),
    [
        (3, Decimal("12.50"), "quantity_times_price", None, Decimal("37.50")),
        (3, Decimal("12.50"), "minus_line_discount", Decimal("2.25"), Decimal("35.25")),
        (3, Decimal("12.50"), "after_line_discount_percent", 10, Decimal("33.75")),
        (1, 10, "minus_line_discount", 12, Decimal("-2")),
        (3, 10, "after_line_discount_percent", 0, Decimal("30")),
        (3, 10, "after_line_discount_percent", 100, Decimal("0")),
    ],
)
def test_exact_declared_equations(quantity, price, formula, discount, expected):
    result = compute_line_amount(quantity, price, formula=formula, discount=discount)
    assert isinstance(result, Decimal)
    assert result == expected


@pytest.mark.parametrize(
    ("quantity", "formula", "discount", "expected"),
    [
        (-2, "quantity_times_price", None, Decimal("-20")),
        (-2, "minus_line_discount", 3, Decimal("-23")),
        (-2, "after_line_discount_percent", 25, Decimal("-15")),
        (Decimal("-0"), "quantity_times_price", None, Decimal("-0")),
    ],
)
def test_signed_quantities(quantity, formula, discount, expected):
    result = compute_line_amount(quantity, 10, formula=formula, discount=discount)
    assert result == expected
    assert result.is_signed() == expected.is_signed()


@pytest.mark.parametrize(
    ("formula", "discount"),
    [
        ("quantity_times_price", 0),
        ("minus_line_discount", None),
        ("after_line_discount_percent", None),
    ],
)
def test_discount_must_match_formula(formula, discount):
    with pytest.raises(ValueError):
        compute_line_amount(1, 10, formula=formula, discount=discount)


@pytest.mark.parametrize("invalid", [True, False, 1.0, "1"])
@pytest.mark.parametrize("operand", ["quantity", "price", "discount"])
def test_rejects_unsupported_operand_types(invalid, operand):
    values = {"quantity": 1, "price": 10, "discount": 0} | {operand: invalid}
    with pytest.raises((TypeError, ValueError)):
        compute_line_amount(
            values["quantity"],
            values["price"],
            formula="minus_line_discount",
            discount=values["discount"],
        )


@pytest.mark.parametrize(
    "invalid", [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
@pytest.mark.parametrize("operand", ["quantity", "price", "discount"])
def test_nonfinite_operands(invalid, operand):
    values = {"quantity": 1, "price": 10, "discount": 0} | {operand: invalid}
    with pytest.raises(ValueError):
        compute_line_amount(
            values["quantity"],
            values["price"],
            formula="minus_line_discount",
            discount=values["discount"],
        )


def test_negative_price_rejected():
    with pytest.raises(ValueError):
        compute_line_amount(-2, Decimal("-0.01"), formula="quantity_times_price")


@pytest.mark.parametrize("discount", [Decimal("-0.01"), Decimal("100.01")])
def test_percent_discount_range(discount):
    with pytest.raises(ValueError):
        compute_line_amount(2, 10, formula="after_line_discount_percent", discount=discount)


def test_amount_discount_must_be_nonnegative_but_explicit_zero_is_valid():
    with pytest.raises(ValueError):
        compute_line_amount(2, 10, formula="minus_line_discount", discount=Decimal("-0.01"))
    assert compute_line_amount(2, 10, formula="minus_line_discount", discount=0) == 20


@pytest.mark.parametrize(
    "value", [Decimal("1E38"), Decimal("1E-39"), Decimal("1." + "0" * 38), 10**38]
)
@pytest.mark.parametrize("operand", ["quantity", "price", "discount"])
def test_operand_precision_rejected_before_arithmetic(value, operand):
    values = {"quantity": 1, "price": 1, "discount": 0} | {operand: value}
    with pytest.raises(ValueError):
        compute_line_amount(
            values["quantity"],
            values["price"],
            formula="minus_line_discount",
            discount=values["discount"],
        )


@pytest.mark.parametrize(
    ("quantity", "price", "formula", "discount"),
    [
        (Decimal("1E37"), 10, "quantity_times_price", None),
        (Decimal("1E-38"), Decimal("0.1"), "quantity_times_price", None),
        (10**38 - 1, 2, "quantity_times_price", None),
        (Decimal("-1E37"), 1, "minus_line_discount", Decimal("0.1")),
        (Decimal("1E-38"), 1, "after_line_discount_percent", 1),
    ],
)
def test_unrepresentable_exact_results_rejected(quantity, price, formula, discount):
    with pytest.raises(ValueError):
        compute_line_amount(quantity, price, formula=formula, discount=discount)


def test_extreme_context_does_not_round_or_mutate_inputs_or_caller_context():
    operands = (Decimal("1234.5"), Decimal("2"), Decimal("12.5"))
    original = tuple(value.as_tuple() for value in operands)
    with localcontext() as context:
        context.prec, context.Emax, context.Emin, context.clamp = 1, 1, -1, 1
        context.rounding = ROUND_UP
        for signal in (Inexact, Rounded, Overflow, Underflow):
            context.traps[signal] = True
        context.clear_flags()
        settings = repr(context)
        assert compute_line_amount(
            operands[0], operands[1], formula="after_line_discount_percent", discount=operands[2]
        ) == Decimal("2160.375")
        for value in (Decimal("1E37"), Decimal("1E-38"), Decimal(10**38 - 1)):
            assert compute_line_amount(value, 1, formula="quantity_times_price") == value
        assert repr(context) == settings
    assert tuple(value.as_tuple() for value in operands) == original


@pytest.mark.parametrize("formula", ["unknown", "", None])
def test_unsupported_formula_rejected(formula):
    with pytest.raises(ValueError):
        compute_line_amount(1, 10, formula=formula)
