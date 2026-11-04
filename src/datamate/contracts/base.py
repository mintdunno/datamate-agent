"""Shared strict boundary validation without data access or side effects."""

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints


class ContractModel(BaseModel):
    """Reject coercion and unknown fields at internal application boundaries."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        allow_inf_nan=False,
        hide_input_in_errors=True,
    )


def nonblank(value: str) -> str:
    """Validate text without silently changing source spelling."""
    if not value.strip():
        raise ValueError("must contain non-whitespace characters")
    return value


NonblankString = Annotated[str, StringConstraints(strict=True), AfterValidator(nonblank)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


def validate_day(value: str) -> str:
    """Require a real Gregorian day in the documented question format."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, flags=re.ASCII):
        raise ValueError("expected YYYY-MM-DD")
    date.fromisoformat(value)
    return value


DayString = Annotated[str, AfterValidator(validate_day)]
