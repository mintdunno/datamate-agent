"""Deterministic subset of train matching, with no invented epsilon or alias coverage."""

import unicodedata
from collections import Counter
from decimal import Decimal

from pydantic import JsonValue

from datamate.evaluation.references import ReferenceAnswer

Match = tuple[bool | None, str]


def normalized_name(value: str) -> str:
    """Train section 9 ignores case, whitespace, punctuation, but preserves accents."""
    return "".join(
        char
        for char in value.casefold()
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def _key(value: JsonValue, *, normalize_strings: bool = True) -> tuple:
    """Typed keys avoid Python's True == 1 equality and preserve row multiplicity."""
    if type(value) in (int, float):
        return ("number", Decimal(str(value)))
    if isinstance(value, str):
        return ("string", normalized_name(value) if normalize_strings else value)
    if isinstance(value, list):
        return ("list", tuple(_key(item, normalize_strings=False) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                sorted((key, _key(item, normalize_strings=False)) for key, item in value.items())
            ),
        )
    return (type(value).__name__, value)


def _cell(actual: JsonValue, expected: JsonValue) -> Match:
    if _key(actual) == _key(expected):
        return True, "value_match"
    if type(actual) in (int, float) and type(expected) in (int, float):
        if type(actual) is int and type(expected) is int:
            return False, "value_mismatch"
        return None, "floating_tolerance_unspecified"
    if isinstance(actual, str) and isinstance(expected, str):
        return None, "source_alias_coverage_unspecified"
    if type(actual) is type(expected) and isinstance(actual, (list, dict)):
        return None, "nested_value_matching_unspecified"
    return False, "value_mismatch"


def _combine(matches: list[Match]) -> Match:
    for match in matches:
        if match[0] is False:
            return match
    for match in matches:
        if match[0] is None:
            return match
    return True, "value_match"


def _list(actual: list, expected: list, aliases: list) -> Match:
    if len(actual) != len(expected):
        return False, "value_mismatch"
    actual_keys = [_key(value) for value in actual]
    expected_keys = [_key(value) for value in expected]
    if actual_keys == expected_keys:
        return True, "value_match"
    if aliases:
        return None, "list_alias_mapping_unspecified"
    if Counter(actual_keys) == Counter(expected_keys):
        return False, "value_mismatch"
    return _combine([_cell(a, e) for a, e in zip(actual, expected, strict=True)])


def _row_key(row: list[JsonValue]) -> tuple:
    """Normalize scalar cells, while nested JSON keeps exact structural semantics."""
    return tuple(_key(cell) for cell in row)


def _table(actual: dict, expected: dict, aliases: list) -> Match:
    columns, expected_columns = actual["columns"], expected["columns"]
    rows, expected_rows = actual["rows"], expected["rows"]
    if len(columns) != len(expected_columns) or len(rows) != len(expected_rows):
        return False, "value_mismatch"
    if not columns or len(set(columns)) != len(columns):
        return None, "ambiguous_table_columns"
    if len(set(expected_columns)) != len(expected_columns):
        return None, "ambiguous_table_columns"
    if set(columns) == set(expected_columns):
        positions = [columns.index(name) for name in expected_columns]
    elif set(columns).isdisjoint(expected_columns):
        positions = list(range(len(columns)))
    else:
        return None, "ambiguous_table_columns"
    aligned = [[row[position] for position in positions] for row in rows]
    # The rulebook specifies one object per row; repeated identities lack clear semantics.
    identities = [_key(row[0]) for row in aligned]
    expected_identities = [_key(row[0]) for row in expected_rows]
    if len(set(identities)) != len(identities) or len(set(expected_identities)) != len(
        expected_identities
    ):
        return None, "ambiguous_table_entities"
    if Counter(_row_key(row) for row in aligned) == Counter(_row_key(row) for row in expected_rows):
        return True, "value_match"
    if aliases:
        return None, "table_alias_mapping_unspecified"
    by_identity = dict(zip(identities, aligned, strict=True))
    if set(by_identity) != set(expected_identities):
        return None, "source_alias_coverage_unspecified"
    return _combine(
        [
            _cell(actual_cell, expected_cell)
            for identity, expected_row in zip(expected_identities, expected_rows, strict=True)
            for actual_cell, expected_cell in zip(by_identity[identity], expected_row, strict=True)
        ]
    )


def match_train_value(reference: ReferenceAnswer, actual: JsonValue) -> Match:
    """Only use under an explicit train rulebook binding; never round predictions."""
    expected = reference.answer
    if reference.type in ("boolean", "date"):
        return actual == expected, "value_match" if actual == expected else "value_mismatch"
    if reference.type == "string":
        candidates = [expected, *(alias for alias in reference.alias if isinstance(alias, str))]
        if any(normalized_name(actual) == normalized_name(candidate) for candidate in candidates):
            return True, "value_match"
        return None, "source_alias_coverage_unspecified"
    if reference.type == "list":
        return _list(actual, expected, reference.alias)
    if reference.type == "table":
        return _table(actual, expected, reference.alias)
    return _cell(actual, expected)
