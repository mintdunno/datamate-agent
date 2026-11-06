"""Active-calendar candidates are source-bound and never infer absent days."""

from datetime import date, timedelta

import duckdb
import pytest

from datamate.preparation.calendar import (
    CalendarIntervalRule,
    CalendarResolutionError,
    build_calendar_candidate,
    calendar_interval,
)
from datamate.preparation.models import SourceFile, SourceRow, SourceTable
from datamate.preparation.storage import create_storage, stage_table

COLUMNS = {
    "date": "c0001",
    "weekday": "c0002",
    "lunar_day": "c0003",
    "lunar_month": "c0004",
    "lunar_year": "c0005",
    "lunar_leap": "c0006",
    "holiday": "c0007",
}


def _candidate(db, rows, *, digest="a" * 64):
    create_storage(db)
    staged = stage_table(
        db,
        SourceFile(None, "calendar.csv", digest),
        SourceTable(
            "calendar",
            tuple(COLUMNS),
            1,
            [SourceRow(i + 2, row) for i, row in enumerate(rows)],
        ),
    )
    return build_calendar_candidate(db, staged.table_id, COLUMNS, digest)


def _rows(start, lunar_day, lunar_month, lunar_year, count):
    return [
        (
            (start + timedelta(days=i)).isoformat(),
            (start + timedelta(days=i)).isoweekday(),
            lunar_day + i,
            lunar_month,
            lunar_year,
            "false",
            "" if i else "holiday",
        )
        for i in range(count)
    ]


def _rule(candidate, start_day, start_month, start_year_offset, end_day, end_month):
    return CalendarIntervalRule(
        source_clause_id="train-rulebook:tet",
        calendar_sha256=candidate.calendar_sha256,
        source_sha256=candidate.source_sha256,
        start_lunar_day=start_day,
        start_lunar_month=start_month,
        start_year_offset=start_year_offset,
        start_lunar_leap=False,
        end_lunar_day=end_day,
        end_lunar_month=end_month,
        end_year_offset=0,
        end_lunar_leap=False,
    )


@pytest.mark.parametrize("year,start", [(2025, date(2025, 1, 29)), (2026, date(2026, 2, 17))])
def test_explicit_one_to_seven_fixture_bounds_are_year_specific(year, start):
    with duckdb.connect() as db:
        candidate = _candidate(db, _rows(start, 1, 1, year, 7))
    assert candidate.findings == ()
    assert candidate.days[0].solar_date == start
    assert candidate.days[0].lunar_day == 1
    assert candidate.days[0].lunar_leap is False
    assert candidate.date_only is True
    assert calendar_interval(candidate, _rule(candidate, 1, 1, 0, 7, 1), year) == (
        start,
        start + timedelta(days=6),
    )
    with pytest.raises(CalendarResolutionError, match="missing"):
        calendar_interval(candidate, _rule(candidate, 23, 12, -1, 10, 1), year)


def test_candidate_rejects_missing_day_and_duplicate_keys():
    start = date(2025, 1, 29)
    rows = _rows(start, 1, 1, 2025, 3)
    rows[2] = (date(2025, 2, 1).isoformat(), 6, 3, 1, 2025, "false", "")
    with duckdb.connect() as db:
        candidate = _candidate(db, rows)
    assert "solar_gap" in candidate.findings
    with pytest.raises(CalendarResolutionError, match="unresolved"):
        calendar_interval(candidate, _rule(candidate, 1, 1, 0, 3, 1), 2025)

    with duckdb.connect() as db:
        duplicate = _candidate(db, _rows(start, 1, 1, 2025, 2) * 2)
    assert "duplicate_solar_date" in duplicate.findings
    assert "duplicate_lunar_date" in duplicate.findings


def test_invalid_types_ranges_and_weekday_are_unresolved():
    rows = _rows(date(2025, 1, 29), 1, 1, 2025, 4)
    rows[1] = ("2025-02-30", 4, 2, 1, 2025, "false", "")
    rows[2] = ("2025-01-31", True, 31, 13, 2025, "maybe", "")
    with duckdb.connect() as db:
        candidate = _candidate(db, rows)
    assert "invalid_row" in candidate.findings
    assert len(candidate.days) == 2


def test_calendar_and_source_fingerprints_must_match():
    with duckdb.connect() as db:
        candidate = _candidate(db, _rows(date(2025, 1, 29), 1, 1, 2025, 7))
        table_id = candidate.table_id
        with pytest.raises(ValueError, match="source fingerprint"):
            build_calendar_candidate(db, table_id, COLUMNS, "b" * 64)
    rule = _rule(candidate, 1, 1, 0, 7, 1)
    wrong = CalendarIntervalRule(**{**vars(rule), "calendar_sha256": "0" * 64})
    with pytest.raises(CalendarResolutionError, match="fingerprint"):
        calendar_interval(candidate, wrong, 2025)


def test_ambiguous_leap_key_and_disconnected_span_are_unresolved():
    rows = _rows(date(2025, 1, 29), 1, 1, 2025, 7)
    rows[1] = (*rows[1][:5], "true", rows[1][6])
    with duckdb.connect() as db:
        candidate = _candidate(db, rows)
    assert "lunar_discontinuity" in candidate.findings


def test_tet_requires_previous_lunar_year_and_complete_span():
    start = date(2025, 1, 22)
    previous = _rows(start, 23, 12, 2024, 8)
    current = _rows(date(2025, 1, 30), 2, 1, 2025, 9)
    with duckdb.connect() as db:
        candidate = _candidate(db, previous + current)
    assert "lunar_discontinuity" in candidate.findings
    with pytest.raises(CalendarResolutionError, match="unresolved"):
        calendar_interval(candidate, _rule(candidate, 23, 12, -1, 10, 1), 2025)


def test_complete_tet_span_uses_previous_lunar_year():
    start = date(2025, 1, 22)
    previous = _rows(start, 23, 12, 2024, 7)
    current = _rows(date(2025, 1, 29), 1, 1, 2025, 10)
    with duckdb.connect() as db:
        candidate = _candidate(db, previous + current)
    assert candidate.findings == ()
    assert calendar_interval(candidate, _rule(candidate, 23, 12, -1, 10, 1), 2025) == (
        date(2025, 1, 22),
        date(2025, 2, 7),
    )
    with pytest.raises(CalendarResolutionError, match="missing"):
        calendar_interval(candidate, _rule(candidate, 23, 12, -1, 10, 1), 2026)


def test_declared_leap_month_keeps_lunar_keys_distinct():
    start = date(2025, 1, 29)
    rows = _rows(start, 29, 6, 2025, 1)
    rows.append(("2025-01-30", 4, 1, 6, 2025, "true", ""))
    rows.append(("2025-01-31", 5, 2, 6, 2025, "true", ""))
    with duckdb.connect() as db:
        candidate = _candidate(db, rows)
    assert candidate.findings == ()
    rule = CalendarIntervalRule(
        "rulebook:leap",
        candidate.calendar_sha256,
        candidate.source_sha256,
        29,
        6,
        0,
        False,
        2,
        6,
        0,
        True,
    )
    assert calendar_interval(candidate, rule, 2025) == (start, date(2025, 1, 31))
