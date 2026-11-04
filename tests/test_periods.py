import pytest

from datamate.runtime.semantics import period_bounds


@pytest.mark.parametrize(
    "day,period,start,end",
    [
        ("2026-01-01", "last_week", "2025-12-22", "2025-12-28"),
        ("2026-01-01", "this_week", "2025-12-29", "2026-01-01"),
        ("2024-03-31", "previous_month_comparison", "2024-02-01", "2024-02-29"),
        ("2025-03-31", "previous_month_comparison", "2025-02-01", "2025-02-28"),
        ("2026-01-04", "previous_month_comparison", "2025-12-01", "2025-12-04"),
        ("2026-06-18", "this_month", "2026-06-01", "2026-06-18"),
        ("2026-06-18", "year_to_date", "2026-01-01", "2026-06-18"),
        ("2026-01-01", "yesterday", "2025-12-31", "2025-12-31"),
    ],
)
def test_confirmed_inclusive_calendar_periods(day, period, start, end):
    result = period_bounds(day, period)
    assert (result["start"], result["end"], result["inclusive"]) == (start, end, True)


def test_last_n_days_includes_today_and_rejects_invalid_count():
    assert period_bounds("2026-01-01", "last_n_days", 2)["start"] == "2025-12-31"
    with pytest.raises(ValueError):
        period_bounds("2026-01-01", "last_n_days", 0)
