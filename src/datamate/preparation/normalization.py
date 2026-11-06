"""Explicit deterministic transformations; raw rows always remain available."""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, NonblankString, Sha256
from datamate.preparation.models import EvidenceRef as Evidence
from datamate.preparation.models import (
    ParseCandidate,
    ParseDecision,
    RawValue,
    ResolvedBinding,
    SourceRow,
    TypedValue,
)


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


class PreserveRawOperation(ContractModel):
    name: Literal["preserve_raw"] = "preserve_raw"
    version: Literal["1"] = "1"


class TrimTextOperation(ContractModel):
    name: Literal["trim_text"] = "trim_text"
    version: Literal["1"] = "1"
    columns: tuple[NonblankString, ...] = Field(min_length=1)


class ParseValuesOperation(ContractModel):
    name: Literal["parse_values"] = "parse_values"
    version: Literal["1"] = "1"
    columns: tuple[NonblankString, ...] = Field(min_length=1)
    role: Literal["money", "quantity", "id", "date", "datetime"]


class ClassifyStructureOperation(ContractModel):
    name: Literal["classify_structure"] = "classify_structure"
    version: Literal["1"] = "1"


class FillMergedOperation(ContractModel):
    name: Literal["fill_merged"] = "fill_merged"
    version: Literal["1"] = "1"
    columns: tuple[NonblankString, ...] = Field(min_length=1)


class InheritGroupOperation(ContractModel):
    name: Literal["inherit_group"] = "inherit_group"
    version: Literal["1"] = "1"
    group_key: NonblankString
    columns: tuple[NonblankString, ...] = Field(min_length=1)


class DeduplicateOrderOperation(ContractModel):
    name: Literal["deduplicate_order"] = "deduplicate_order"
    version: Literal["1"] = "1"
    group_key: NonblankString


class ResolveEntitiesOperation(ContractModel):
    name: Literal["resolve_entities"] = "resolve_entities"
    version: Literal["1"] = "1"
    columns: tuple[NonblankString, ...] = Field(min_length=1, max_length=1)
    scope: Literal["product", "customer"]
    namespace: NonblankString
    input_kind: Literal["id", "alias"] = "id"


class NormalizeQuantityOperation(ContractModel):
    name: Literal["normalize_quantity"] = "normalize_quantity"
    version: Literal["1"] = "1"
    product_column: NonblankString
    quantity_column: NonblankString
    unit_column: NonblankString
    preconverted_column: NonblankString | None = None

    @model_validator(mode="after")
    def distinct_fields(self) -> Self:
        columns = [self.product_column, self.quantity_column, self.unit_column]
        if self.preconverted_column is not None:
            columns.append(self.preconverted_column)
        if len(set(columns)) != len(columns):
            raise ValueError("quantity conversion requires distinct source fields")
        return self


class CleaningStep(ContractModel):
    table_id: NonblankString
    operation: Annotated[
        PreserveRawOperation
        | TrimTextOperation
        | ParseValuesOperation
        | ClassifyStructureOperation
        | FillMergedOperation
        | InheritGroupOperation
        | DeduplicateOrderOperation
        | ResolveEntitiesOperation
        | NormalizeQuantityOperation,
        Field(discriminator="name"),
    ]
    binding_ids: tuple[Sha256, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def transformation_authority(self) -> Self:
        if self.operation.name != "preserve_raw" and (not self.binding_ids or not self.evidence):
            raise ValueError("transformations require accepted bindings and source evidence")
        return self

    @property
    def step_id(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class CleaningPlan(ContractModel):
    version: Literal["cleaning-v1"] = "cleaning-v1"
    bindings_digest: Sha256
    profile_digests: tuple[Sha256, ...]
    steps: tuple[CleaningStep, ...] = Field(min_length=1, max_length=64)
    rollback: Literal["reject_candidate"] = "reject_candidate"


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


_DATE_CONTEXT_KEYS = {"sheet_year", "workbook_epoch", "number_format", "source_total"}
_WINDOWS_EPOCH = datetime(1899, 12, 30)
_MAC_EPOCH = datetime(1904, 1, 1)
_NUMERIC = re.compile(r"\d+(?:[.,]\d+)?", re.ASCII)
_GROUP_DOT = re.compile(r"\d{1,3}(?:\.\d{3})+", re.ASCII)
_GROUP_COMMA = re.compile(r"\d{1,3}(?:,\d{3})+", re.ASCII)
_GROUP_SPACE = re.compile(r"\d{1,3}(?: \d{3})+", re.ASCII)
_DMY = re.compile(r"(\d{1,2})(?:/|-| thg )(\d{1,2})(?:(?:/|-| )(\d{4}))?", re.IGNORECASE)
_DMY_TIME = re.compile(
    r"(\d{1,2})(?:/|-| thg )(\d{1,2})(?:(?:/|-| )(\d{4}))? "
    r"(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?",
    re.IGNORECASE,
)
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def _precision_supported(value: Decimal) -> bool:
    digits = value.as_tuple().digits
    exponent = value.as_tuple().exponent
    scale = max(-exponent, 0)
    integer_digits = max(len(digits) + exponent, 0)
    return max(integer_digits + scale, 1) <= 38


def _numeric_candidate(
    raw: TypedValue, number: Decimal, grammar: str, unit: str | None = None
) -> ParseCandidate:
    if _precision_supported(number):
        return ParseCandidate(number, grammar, unit)
    return ParseCandidate(raw, grammar, unit, ("precision_extension",), False)


def _scaled(number: Decimal, power: int) -> Decimal:
    parts = number.as_tuple()
    return Decimal((parts.sign, parts.digits, parts.exponent + power))


def _numeric_candidates(value: TypedValue, role: str) -> tuple[ParseCandidate, ...]:
    if type(value) is bool:
        return ()
    if isinstance(value, Decimal):
        return (_numeric_candidate(value, value, "native_decimal"),) if value.is_finite() else ()
    if type(value) in (int, float):
        if type(value) is int and value.bit_length() > 127:
            return (ParseCandidate(value, "native_number", None, ("precision_extension",), False),)
        if type(value) is float and not math.isfinite(value):
            return ()
        number = Decimal(str(value))
        return (_numeric_candidate(value, number, "native_number"),)
    if not isinstance(value, str):
        return ()
    token = value.strip()
    note_prefix = bool(role == "money" and re.match(r"^nợ\s+", token, re.IGNORECASE))
    if note_prefix:
        token = re.sub(r"^nợ\s+", "", token, count=1, flags=re.IGNORECASE)
    negative = token.startswith("(") and token.endswith(")")
    if negative:
        token = token[1:-1].strip()
    elif token.startswith(("-", "+")):
        negative = token[0] == "-"
        token = token[1:].strip()
    unit = None
    if role == "quantity":
        match = re.fullmatch(r"(.+?)\s+([^\W\d_]+)", token)
        if match:
            token, unit = match.group(1), match.group(2)
    currency = False
    if role == "money":
        match = re.fullmatch(r"(.+?)\s*(đồng|vnd|đ)", token, re.IGNORECASE)
        if match:
            token = match.group(1).strip()
            currency = True
    possibilities: list[tuple[Decimal, str]] = []
    compact_million = re.fullmatch(r"(\d+)tr(\d{1,3})", token, re.IGNORECASE)
    million = re.fullmatch(r"(\d+(?:,\d+)?)tr", token, re.IGNORECASE)
    thousand = re.fullmatch(r"(\d+(?:[.,]\d+)?)k", token, re.IGNORECASE)
    if role == "money" and compact_million:
        whole, tail = compact_million.groups()
        significant = whole.lstrip("0") or "0"
        if len(significant) > 32:
            return (
                ParseCandidate(value, "million_compact", None, ("precision_extension",), False),
            )
        possibilities.append(
            (Decimal(int(significant) * 1_000_000 + int(tail) * 1000), "million_compact")
        )
    elif role == "money" and million:
        possibilities.append((_scaled(Decimal(million.group(1).replace(",", ".")), 6), "million"))
    elif role == "money" and thousand:
        spelling = thousand.group(1)
        number = Decimal(spelling.replace(",", "."))
        suffix_kind = (
            "decimal_dot" if "." in spelling else "decimal_comma" if "," in spelling else "integer"
        )
        possibilities.append((_scaled(number, 3), "thousand_suffix_" + suffix_kind))
        for separator, pattern in ((".", _GROUP_DOT), (",", _GROUP_COMMA)):
            if pattern.fullmatch(spelling):
                possibilities.append(
                    (
                        _scaled(Decimal(spelling.replace(separator, "")), 3),
                        "thousand_suffix_dot_group"
                        if separator == "."
                        else "thousand_suffix_comma_group",
                    )
                )
    elif _GROUP_SPACE.fullmatch(token):
        possibilities.append((Decimal(token.replace(" ", "")), "space_group"))
    elif currency and _GROUP_DOT.fullmatch(token):
        possibilities.append((Decimal(token.replace(".", "")), "currency_dot_group"))
    elif token.count(".") > 1 and _GROUP_DOT.fullmatch(token):
        possibilities.append((Decimal(token.replace(".", "")), "dot_group"))
    elif token.count(",") > 1 and _GROUP_COMMA.fullmatch(token):
        possibilities.append((Decimal(token.replace(",", "")), "comma_group"))
    elif _NUMERIC.fullmatch(token):
        grammar = (
            "decimal_dot" if "." in token else "decimal_comma" if "," in token else "integer_text"
        )
        possibilities.append((Decimal(token.replace(",", ".")), grammar))
        if _GROUP_DOT.fullmatch(token):
            possibilities.append((Decimal(token.replace(".", "")), "dot_group"))
        if _GROUP_COMMA.fullmatch(token):
            possibilities.append((Decimal(token.replace(",", "")), "comma_group"))
    candidates = tuple(
        _numeric_candidate(value, number.copy_negate() if negative else number, grammar, unit)
        for number, grammar in possibilities
    )
    if note_prefix:
        return tuple(
            replace(
                candidate,
                context_requirements=candidate.context_requirements + ("debt_note_scope",),
            )
            for candidate in candidates
        )
    return candidates


def _serial_candidate(value: TypedValue, role: str, epoch: datetime) -> tuple[ParseCandidate, ...]:
    if epoch not in (_WINDOWS_EPOCH, _MAC_EPOCH):
        raise ValueError("unsupported workbook epoch context")
    try:
        serial = Decimal(str(value))
    except InvalidOperation:
        return ()
    if not serial.is_finite() or serial < 0 or (epoch == _WINDOWS_EPOCH and 60 <= serial < 61):
        return ()
    if not _precision_supported(serial):
        return (ParseCandidate(value, "excel_serial", None, ("datetime_precision",), False),)
    whole = int(serial)
    with localcontext() as decimal_context:
        decimal_context.prec = 50
        fraction = serial - whole
        micros = fraction * Decimal(86_400_000_000)
    if micros != micros.to_integral_value():
        return (ParseCandidate(value, "excel_serial", None, ("datetime_precision",), False),)
    if epoch == _WINDOWS_EPOCH and 0 < serial < 60:
        whole += 1
    try:
        timestamp = epoch + timedelta(days=whole, microseconds=int(micros))
    except OverflowError:
        return ()
    if fraction:
        return (ParseCandidate(timestamp, "excel_serial_datetime"),)
    return (ParseCandidate(timestamp.date(), "excel_serial_date"),)


def _date_candidates(value: TypedValue, role: str, context: dict) -> tuple[ParseCandidate, ...]:
    if isinstance(value, time):
        return (ParseCandidate(value, "native_time"),)
    if isinstance(value, datetime):
        return (ParseCandidate(value, "native_datetime"),)
    if type(value) is date:
        return (ParseCandidate(value, "native_date"),)
    if type(value) is bool:
        return ()
    if type(value) in (int, float) or isinstance(value, Decimal):
        if type(value) is int and value.bit_length() > 127:
            return (ParseCandidate(value, "excel_serial", None, ("datetime_precision",), False),)
        if type(value) is float and not math.isfinite(value):
            return ()
        epoch = context.get("workbook_epoch")
        return _serial_candidate(value, role, epoch) if epoch is not None else ()
    if not isinstance(value, str):
        return ()
    token = value.strip()
    if _ISO_DAY.fullmatch(token):
        try:
            return (ParseCandidate(date.fromisoformat(token), "iso_date"),)
        except ValueError:
            return ()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}.*", token):
        if re.search(r"[.,]\d{7,}", token):
            return (ParseCandidate(value, "iso_datetime", None, ("datetime_precision",), False),)
        try:
            return (ParseCandidate(datetime.fromisoformat(token), "iso_datetime"),)
        except ValueError:
            return ()
    timestamp_match = _DMY_TIME.fullmatch(token)
    if timestamp_match:
        day, month, year, hour, minute, second, fraction = timestamp_match.groups()
        resolved_year = int(year) if year else context.get("sheet_year")
        if type(resolved_year) is not int or not 1 <= resolved_year <= 9999:
            return ()
        try:
            parsed = datetime(
                resolved_year,
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second or 0),
                int((fraction or "").ljust(6, "0")) if fraction else 0,
            )
            return (ParseCandidate(parsed, "source_dmy_datetime"),)
        except ValueError:
            return ()
    match = _DMY.fullmatch(token)
    if match:
        day, month, year = match.groups()
        resolved_year = int(year) if year else context.get("sheet_year")
        if type(resolved_year) is not int or not 1 <= resolved_year <= 9999:
            return ()
        try:
            return (ParseCandidate(date(resolved_year, int(month), int(day)), "source_dmy"),)
        except ValueError:
            return ()
    if re.fullmatch(r"\d+(?:\.\d+)?", token, re.ASCII) and "workbook_epoch" in context:
        return _serial_candidate(value, role, context["workbook_epoch"])
    return ()


def parse_candidates(
    value: TypedValue, role: str, evidence: Sequence[Evidence], context: dict
) -> tuple[ParseCandidate, ...]:
    """Enumerate source-compatible interpretations without selecting a winner."""
    if role not in {"money", "quantity", "id", "date", "datetime"}:
        raise ValueError("unsupported parse role")
    if set(context) - _DATE_CONTEXT_KEYS:
        raise ValueError("context must contain explicit source fields only")
    if value is None:
        return (ParseCandidate(None, "raw_null"),)
    if isinstance(value, str) and not value.strip():
        return (ParseCandidate(value, "raw_blank"),)
    if role in {"money", "quantity"}:
        return _numeric_candidates(value, role)
    if role == "id":
        if not isinstance(value, str):
            return ()
        raw = ParseCandidate(value, "raw_id")
        trimmed = value.strip()
        if trimmed == value:
            return (raw,)
        return (raw, ParseCandidate(trimmed, "trim_id", None, ("collision_check",)))
    return _date_candidates(value, role, context)


FORMAT_GRAMMARS = {
    "plain": {
        "integer_text",
        "decimal_dot",
        "thousand_suffix_integer",
        "thousand_suffix_decimal_dot",
    },
    "dot_thousands": {
        "integer_text",
        "dot_group",
        "space_group",
        "currency_dot_group",
        "thousand_suffix_integer",
        "thousand_suffix_dot_group",
    },
    "comma_thousands": {
        "integer_text",
        "decimal_dot",
        "comma_group",
        "thousand_suffix_integer",
        "thousand_suffix_decimal_dot",
        "thousand_suffix_comma_group",
    },
    "vi": {
        "integer_text",
        "decimal_comma",
        "dot_group",
        "space_group",
        "currency_dot_group",
        "million",
        "million_compact",
        "thousand_suffix_integer",
        "thousand_suffix_decimal_comma",
        "thousand_suffix_dot_group",
    },
    "dmy": {"source_dmy", "source_dmy_datetime"},
    "iso": {"iso_date", "iso_datetime"},
    "excel_serial": {"excel_serial_date", "excel_serial_datetime"},
}
_NATIVE_GRAMMARS = {
    "native_number",
    "native_decimal",
    "native_date",
    "native_datetime",
    "native_time",
    "raw_null",
    "raw_blank",
}


def select_parse(candidates: Sequence[ParseCandidate], binding: ResolvedBinding) -> ParseDecision:
    """Selection requires evaluator-independent, accepted production source bindings."""
    alternatives = tuple(candidates)
    if not isinstance(binding, ResolvedBinding) or binding.status != "ACCEPTED":
        return ParseDecision("UNRESOLVED", None, None, alternatives, "binding is not accepted")
    selected = alternatives
    if binding.binding.kind == "format":
        allowed = FORMAT_GRAMMARS.get(binding.binding.value, set()) | _NATIVE_GRAMMARS
        selected = tuple(candidate for candidate in alternatives if candidate.grammar in allowed)
    elif binding.binding.kind != "role":
        return ParseDecision(
            "UNRESOLVED", None, None, alternatives, "no registered parse interpretation"
        )
    if not selected:
        return ParseDecision("UNRESOLVED", None, None, alternatives, "no source-admissible grammar")
    if any(not candidate.precision_supported for candidate in selected):
        return ParseDecision("UNSUPPORTED", None, None, alternatives, "unsupported precision")
    if len(selected) != 1 or selected[0].context_requirements:
        return ParseDecision(
            "UNRESOLVED", None, None, alternatives, "ambiguous or missing distinguishing context"
        )
    candidate = selected[0]
    return ParseDecision(
        "ACCEPTED",
        candidate.value,
        candidate.grammar,
        alternatives,
        "unique source-admissible interpretation",
    )


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
