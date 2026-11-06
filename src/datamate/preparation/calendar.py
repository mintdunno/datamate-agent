"""Source-bound, typed calendar candidates and explicit lunar intervals."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from duckdb import DuckDBPyConnection

from datamate.preparation.normalization import encoded
from datamate.preparation.storage import iter_raw_batches

REQUIRED_COLUMNS = frozenset(
    {"date", "weekday", "lunar_day", "lunar_month", "lunar_year", "lunar_leap"}
)


class CalendarResolutionError(ValueError):
    """A requested interval cannot be proven from the active candidate."""


@dataclass(frozen=True)
class CalendarDay:
    solar_date: date
    weekday: int
    lunar_day: int
    lunar_month: int
    lunar_year: int
    lunar_leap: bool
    holiday: str | None
    record_id: str
    source_row: int


@dataclass(frozen=True)
class CalendarCandidate:
    table_id: str
    source_sha256: str
    calendar_sha256: str
    days: tuple[CalendarDay, ...]
    findings: tuple[str, ...]
    date_only: Literal[True] = True
    timezone: None = None


@dataclass(frozen=True)
class CalendarIntervalRule:
    """An accepted clause must supply both lunar boundaries and source identity."""

    source_clause_id: str
    calendar_sha256: str
    source_sha256: str
    start_lunar_day: int
    start_lunar_month: int
    start_year_offset: int
    start_lunar_leap: bool
    end_lunar_day: int
    end_lunar_month: int
    end_year_offset: int
    end_lunar_leap: bool

    def __post_init__(self) -> None:
        if not self.source_clause_id.strip():
            raise ValueError("calendar interval requires a source clause ID")
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in (self.calendar_sha256, self.source_sha256)
        ):
            raise ValueError("calendar interval requires SHA256 fingerprints")
        for day, month in (
            (self.start_lunar_day, self.start_lunar_month),
            (self.end_lunar_day, self.end_lunar_month),
        ):
            if type(day) is not int or not 1 <= day <= 30:
                raise ValueError("lunar day must be an integer from 1 to 30")
            if type(month) is not int or not 1 <= month <= 12:
                raise ValueError("lunar month must be an integer from 1 to 12")
        if type(self.start_lunar_leap) is not bool or type(self.end_lunar_leap) is not bool:
            raise ValueError("lunar leap conditions must be explicit booleans")
        if type(self.start_year_offset) is not int or type(self.end_year_offset) is not int:
            raise ValueError("lunar year offsets must be integers")


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is int:
        result = value
    elif type(value) is str and value.isascii() and value.isdecimal():
        result = int(value)
    else:
        raise ValueError("invalid calendar integer")
    if not minimum <= result <= maximum:
        raise ValueError("calendar integer out of range")
    return result


def _date(value: object) -> date:
    if type(value) is date:
        return value
    if type(value) is not str:
        raise ValueError("invalid solar date")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("solar date must use ISO format")
    return result


def _leap(value: object) -> bool:
    if type(value) is bool:
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("invalid lunar leap flag")


def _day(
    values: tuple, columns: dict[str, str], indexes: dict[str, int], record_id: str, source_row: int
) -> CalendarDay:
    def field(name: str) -> object:
        return values[indexes[columns[name]]]

    solar_date = _date(field("date"))
    weekday = _integer(field("weekday"), 1, 7)
    if weekday != solar_date.isoweekday():
        raise ValueError("solar weekday mismatch")
    lunar_day = _integer(field("lunar_day"), 1, 30)
    lunar_month = _integer(field("lunar_month"), 1, 12)
    lunar_year = _integer(field("lunar_year"), 1, 9999)
    leap = _leap(field("lunar_leap"))
    holiday = field("holiday") if "holiday" in columns else None
    if holiday is not None and type(holiday) is not str:
        raise ValueError("invalid holiday label")
    return CalendarDay(
        solar_date,
        weekday,
        lunar_day,
        lunar_month,
        lunar_year,
        leap,
        holiday or None,
        record_id,
        source_row,
    )


def _lunar_successor(previous: CalendarDay, current: CalendarDay) -> bool:
    if current.lunar_day == previous.lunar_day + 1 and (
        current.lunar_month,
        current.lunar_year,
        current.lunar_leap,
    ) == (previous.lunar_month, previous.lunar_year, previous.lunar_leap):
        return True
    if previous.lunar_day not in (29, 30) or current.lunar_day != 1:
        return False
    if previous.lunar_leap:
        return (current.lunar_year, current.lunar_month, current.lunar_leap) == (
            previous.lunar_year,
            previous.lunar_month + 1,
            False,
        )
    if (current.lunar_year, current.lunar_month, current.lunar_leap) == (
        previous.lunar_year,
        previous.lunar_month,
        True,
    ):
        return True
    if previous.lunar_month == 12:
        return (current.lunar_year, current.lunar_month, current.lunar_leap) == (
            previous.lunar_year + 1,
            1,
            False,
        )
    return (current.lunar_year, current.lunar_month, current.lunar_leap) == (
        previous.lunar_year,
        previous.lunar_month + 1,
        False,
    )


def build_calendar_candidate(
    db: DuckDBPyConnection,
    table_id: str,
    columns: dict[str, str],
    source_sha256: str,
) -> CalendarCandidate:
    """Validate all staged calendar rows without publishing a clean relation."""
    if not REQUIRED_COLUMNS <= columns.keys() or set(columns) - REQUIRED_COLUMNS - {"holiday"}:
        raise ValueError("calendar column map is incomplete or unsupported")
    source = db.execute(
        "SELECT source_sha256,columns_json,row_count FROM meta.source_tables WHERE table_id=?",
        [table_id],
    ).fetchone()
    if (
        source is None
        or source[0] != source_sha256
        or len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256)
    ):
        raise ValueError("calendar source fingerprint mismatch")
    column_ids = json.loads(source[1])
    if len(set(columns.values())) != len(columns) or not set(columns.values()) <= set(column_ids):
        raise ValueError("calendar column map has unknown or reused columns")
    indexes = {column: i for i, column in enumerate(column_ids)}
    digest = hashlib.sha256()
    digest.update(source_sha256.encode("ascii"))
    digest.update(table_id.encode("utf-8"))
    digest.update(json.dumps(columns, sort_keys=True).encode("utf-8"))
    from datamate.preparation.text_state import effective_values

    days = []
    findings = set()
    count = 0
    for batch in iter_raw_batches(db, table_id, 1000):
        for record in batch:
            if record.kind != "data":
                continue
            count += 1
            digest.update(
                json.dumps(
                    [record.record_id, [encoded(value) for value in record.source.values]],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            )
            try:
                days.append(
                    _day(
                        effective_values(db, record.record_id, record.source.values),
                        columns,
                        indexes,
                        record.record_id,
                        record.source.number,
                    )
                )
            except (ValueError, IndexError, TypeError):
                findings.add("invalid_row")
    if count != source[2] or count == 0:
        findings.add("row_count_mismatch")
    solar_keys = [day.solar_date for day in days]
    lunar_keys = [(day.lunar_year, day.lunar_month, day.lunar_day, day.lunar_leap) for day in days]
    if len(set(solar_keys)) != len(solar_keys):
        findings.add("duplicate_solar_date")
    if len(set(lunar_keys)) != len(lunar_keys):
        findings.add("duplicate_lunar_date")
    for previous, current in zip(days, days[1:], strict=False):
        if current.solar_date != previous.solar_date + timedelta(days=1):
            findings.add("solar_gap")
        if not _lunar_successor(previous, current):
            findings.add("lunar_discontinuity")
    return CalendarCandidate(
        table_id, source_sha256, digest.hexdigest(), tuple(days), tuple(sorted(findings))
    )


def calendar_interval(
    candidate: CalendarCandidate, rule: CalendarIntervalRule, year: int
) -> tuple[date, date]:
    """Resolve an inclusive interval only from a complete accepted candidate."""
    if type(year) is not int or not 1 <= year <= 9999:
        raise ValueError("requested year is invalid")
    if (
        candidate.calendar_sha256 != rule.calendar_sha256
        or candidate.source_sha256 != rule.source_sha256
    ):
        raise CalendarResolutionError("calendar fingerprint mismatch")
    if candidate.findings:
        raise CalendarResolutionError("calendar candidate is unresolved")

    def endpoint(day: int, month: int, offset: int, leap: bool) -> date:
        lunar_year = year + offset
        matches = [
            entry.solar_date
            for entry in candidate.days
            if (entry.lunar_day, entry.lunar_month, entry.lunar_year, entry.lunar_leap)
            == (day, month, lunar_year, leap)
        ]
        if len(matches) != 1:
            raise CalendarResolutionError("calendar endpoint missing or ambiguous")
        return matches[0]

    start = endpoint(
        rule.start_lunar_day,
        rule.start_lunar_month,
        rule.start_year_offset,
        rule.start_lunar_leap,
    )
    end = endpoint(
        rule.end_lunar_day,
        rule.end_lunar_month,
        rule.end_year_offset,
        rule.end_lunar_leap,
    )
    if start > end:
        raise CalendarResolutionError("calendar interval is reversed")
    days_in_range = [day for day in candidate.days if start <= day.solar_date <= end]
    if len(days_in_range) != (end - start).days + 1:
        raise CalendarResolutionError("calendar interval has missing dates")
    return start, end
