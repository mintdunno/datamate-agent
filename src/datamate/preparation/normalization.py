"""Explicit deterministic transformations; raw rows always remain available."""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Literal, Self

from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.preparation.models import RawValue, SourceRow


class Evidence(ContractModel):
    source: NonblankString
    sha256: Sha256
    locator: NonblankString


class ColumnPlan(ContractModel):
    kind: Literal["auto", "text", "integer", "decimal", "date", "datetime", "boolean"] = "auto"
    number_style: Literal["plain", "comma_thousands", "dot_thousands", "vi"] = "plain"
    allow_k_suffix: bool = False
    date_format: Literal["iso", "dmy"] = "iso"
    year: int | None = Field(default=None, ge=1, le=9999)
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> Self:
        if self.kind != "auto" and self.evidence is None:
            raise ValueError("explicit interpretation requires source evidence")
        return self


class TablePlan(ContractModel):
    columns: dict[str, ColumnPlan] = Field(default_factory=dict)
    group_key: str | None = None
    fill_columns: list[str] = Field(default_factory=list)
    deduplicate_order_lines: bool = False
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def grouped_transforms(self) -> Self:
        if self.group_key or self.fill_columns or self.deduplicate_order_lines:
            if not self.group_key or self.evidence is None:
                raise ValueError("group transforms require an order key and source evidence")
        return self


def encoded(value: object) -> dict:
    """Tagged values retain Python/native source types without lossy JSON coercion."""
    if value is None:
        payload = None
    elif type(value) in (str, bool):
        payload = value
    elif isinstance(value, (date, datetime, time)):
        payload = value.isoformat()
    elif isinstance(value, bytes):
        payload = value.hex()
    else:
        payload = str(value)
    return {"type": type(value).__name__, "value": payload}


def _number(value: RawValue, plan: ColumnPlan) -> Decimal:
    if type(value) is bool:
        raise ValueError("boolean is not numeric")
    if type(value) in (int, float) or isinstance(value, Decimal):
        result = Decimal(str(value))
    elif isinstance(value, str):
        token = value.strip()
        multiplier = 1
        if plan.allow_k_suffix and token.lower().endswith("k"):
            token, multiplier = token[:-1], 1000
        patterns = {
            "plain": r"[+-]?\d+(?:\.\d+)?",
            "comma_thousands": r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?",
            "dot_thousands": r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)",
            "vi": r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?",
        }
        if not re.fullmatch(patterns[plan.number_style], token, flags=re.ASCII):
            raise ValueError("ambiguous numeric spelling")
        if plan.number_style == "comma_thousands":
            token = token.replace(",", "")
        elif plan.number_style in {"dot_thousands", "vi"}:
            token = token.replace(".", "").replace(",", ".")
        result = Decimal(token)
        if multiplier != 1:
            parts = result.as_tuple()
            result = Decimal((parts.sign, parts.digits, parts.exponent + 3))
    else:
        raise ValueError("unsupported numeric value")
    if not result.is_finite():
        raise ValueError("non-finite numeric value")
    return result


def _day(value: RawValue, plan: ColumnPlan) -> date:
    if isinstance(value, datetime):
        return value.date()
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise ValueError("numeric serials require a workbook date style")
    token = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token, flags=re.ASCII):
        return date.fromisoformat(token)
    if plan.date_format == "dmy":
        match = re.fullmatch(r"(\d{1,2})(?:/|-| thg )(\d{1,2})(?:(?:/|-| )(\d{4}))?", token)
        if match:
            day, month, year = match.groups()
            resolved_year = int(year) if year else plan.year
            if resolved_year is not None:
                return date(resolved_year, int(month), int(day))
    raise ValueError("unsupported or ambiguous date")


def normalize_cell(value: RawValue, plan: ColumnPlan) -> tuple[object, tuple[str, ...]]:
    if value is None:
        return None, ()
    if plan.kind == "auto":
        if type(value) is float:
            return (
                (Decimal(str(value)), ("native_decimal",))
                if math.isfinite(value)
                else (None, ("nonfinite",))
            )
        if type(value) is int and len(str(abs(value))) > 38:
            return str(value), ("integer_out_of_range",)
        return value, ()
    try:
        if plan.kind in {"integer", "decimal"}:
            number = _number(value, plan)
            if plan.kind == "integer":
                if number != number.to_integral_value() or len(str(abs(int(number)))) > 38:
                    raise ValueError("integer precision loss")
                result = int(number)
            else:
                result = number
        elif plan.kind == "date":
            result = _day(value, plan)
        elif plan.kind == "datetime":
            if isinstance(value, datetime):
                result = value
            elif isinstance(value, str):
                result = datetime.fromisoformat(value)
            else:
                raise ValueError("unsupported timestamp")
        elif plan.kind == "boolean":
            if type(value) is bool:
                result = value
            elif type(value) is int and value in (0, 1):
                result = bool(value)
            elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                result = value.strip().lower() == "true"
            else:
                raise ValueError("ambiguous boolean")
        else:
            result = value if isinstance(value, str) else str(value)
        return result, () if encoded(result) == encoded(value) else ("converted",)
    except (ValueError, InvalidOperation, OverflowError):
        return None, ("parse_failed",)


@dataclass(frozen=True)
class PreparedRow:
    source: SourceRow
    values: tuple[object, ...]
    events: tuple[dict, ...]
    excluded: bool = False
    duplicate_of: int | None = None


def normalize_rows(rows: Iterable[SourceRow], width: int, plan: TablePlan) -> Iterator[PreparedRow]:
    columns = [f"c{index + 1:04d}" for index in range(width)]
    configured = (
        set(plan.columns) | set(plan.fill_columns) | ({plan.group_key} if plan.group_key else set())
    )
    if not configured <= set(columns):
        raise ValueError("normalization references unknown column IDs")
    group_index = columns.index(plan.group_key) if plan.group_key else None
    fill_indices = [columns.index(name) for name in plan.fill_columns]
    previous_group = None
    carried: dict[int, object] = {}
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        if len(row.values) != width:
            raise ValueError(f"ragged source row {row.number}")
        values = list(row.values)
        events = []
        group = None
        if group_index is not None:
            key = values[group_index]
            unknown_key = bool(row.formulas and row.formulas[group_index] and key is None)
            if unknown_key:
                group = None
            elif key is None or key == "":
                group = previous_group
                if group is not None:
                    values[group_index] = carried[group_index]
                    events.append({"column": columns[group_index], "code": "group_fill"})
            else:
                group = json.dumps(encoded(key), sort_keys=True)
            if group != previous_group:
                carried = {}
            if group is None:
                carried = {}
                events.append({"column": columns[group_index], "code": "group_key_unresolved"})
            for index in [group_index, *fill_indices]:
                if group is None or (
                    row.formulas and row.formulas[index] and values[index] is None
                ):
                    continue
                if index not in carried and group != previous_group:
                    carried[index] = values[index]
                if values[index] is None or values[index] == "":
                    if index in carried and group is not None:
                        values[index] = carried[index]
                        events.append({"column": columns[index], "code": "group_fill"})
                elif index in carried and encoded(values[index]) != encoded(carried[index]):
                    events.append({"column": columns[index], "code": "group_conflict"})
            previous_group = group
        duplicate_of = None
        if plan.deduplicate_order_lines and group is not None:
            signature = hashlib.sha256(
                json.dumps(
                    {"values": [encoded(value) for value in row.values], "formulas": row.formulas},
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            duplicate_of = seen.get((group, signature))
            seen.setdefault((group, signature), row.number)
        normalized = []
        for index, value in enumerate(values):
            result, codes = normalize_cell(value, plan.columns.get(columns[index], ColumnPlan()))
            normalized.append(result)
            events.extend({"column": columns[index], "code": code} for code in codes)
            if row.formulas and row.formulas[index] and row.values[index] is None:
                events.append({"column": columns[index], "code": "formula_cache_missing"})
        yield PreparedRow(
            row, tuple(normalized), tuple(events), duplicate_of is not None, duplicate_of
        )
