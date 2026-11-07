"""Exact line arithmetic for formulas already justified by a trusted caller."""

from decimal import Context, Decimal, Inexact, Rounded, localcontext
from typing import Literal

LineAmountFormula = Literal[
    "quantity_times_price", "minus_line_discount", "after_line_discount_percent"
]


def _number(value: Decimal | int, name: str) -> Decimal:
    if type(value) is int:
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    else:
        raise TypeError(f"{name} must be a Decimal or integer")
    if not number.is_finite():
        raise ValueError(f"{name} must be finite")
    parts = number.as_tuple()
    scale = max(-parts.exponent, 0)
    integer_digits = max(len(parts.digits) + parts.exponent, 0)
    if max(integer_digits + scale, 1) > 38:
        raise ValueError(f"{name} exceeds supported Decimal precision")
    return number


def compute_line_amount(
    quantity: Decimal | int,
    unit_price: Decimal | int,
    *,
    formula: LineAmountFormula,
    discount: Decimal | int | None = None,
) -> Decimal:
    """Apply one explicit formula without rounding, clipping, or unit inference.

    The caller must establish matching quantity/price units and source authority
    for the exact formula. Amount discounts subtract from the signed gross amount;
    percentage discounts multiply it by (100 - discount) / 100. Operands and the
    exact result must fit precision 38 at their existing scale, including zeros.
    """
    if formula not in (
        "quantity_times_price",
        "minus_line_discount",
        "after_line_discount_percent",
    ):
        raise ValueError("unsupported line amount formula")
    quantity = _number(quantity, "quantity")
    unit_price = _number(unit_price, "unit_price")
    if unit_price < 0:
        raise ValueError("unit_price must be nonnegative")
    if formula == "quantity_times_price":
        if discount is not None:
            raise ValueError("quantity_times_price requires discount=None")
    else:
        if discount is None:
            raise ValueError("discount must be explicitly supplied")
        discount = _number(discount, "discount")
        if discount < 0:
            raise ValueError("discount must be nonnegative")
        if formula == "after_line_discount_percent" and discount > 100:
            raise ValueError("percentage discount must be at most 100")

    # Three bounded operands need at most 117 coefficient digits here. Use a
    # fresh context so caller precision, exponent limits and rounding cannot leak.
    context = Context(prec=160)
    context.traps[Inexact] = context.traps[Rounded] = True
    with localcontext(context):
        result = quantity * unit_price
        if formula == "minus_line_discount":
            result -= discount
        elif formula == "after_line_discount_percent":
            result = result * (Decimal(100) - discount) / Decimal(100)
    return _number(result, "result")


FORMULA_ROLES = {
    "quantity_times_price": ("quantity", "unit_price"),
    "minus_line_discount": ("quantity", "unit_price", "line_discount_amount"),
    "after_line_discount_percent": ("quantity", "unit_price", "line_discount_percent"),
}
FORMULA_DECLARATIONS = {
    "quantity_times_price": (
        "line_net_amount = quantity * unit_price; price_basis: source_quantity_unit; rounding: none"
    ),
    "minus_line_discount": (
        "line_net_amount = quantity * unit_price - line_discount_amount; "
        "price_basis: source_quantity_unit; rounding: none"
    ),
    "after_line_discount_percent": (
        "line_net_amount = quantity * unit_price * (1 - line_discount_percent / 100); "
        "price_basis: source_quantity_unit; rounding: none"
    ),
}
