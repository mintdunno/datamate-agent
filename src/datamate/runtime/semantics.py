"""Inclusive calendar periods confirmed by train rulebook section 2; never wall clock."""

import calendar
from datetime import date, timedelta
from typing import Literal

Period = Literal[
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "previous_month_comparison",
    "year_to_date",
    "last_n_days",
]
TRAIN_RULEBOOK_SHA256 = "740267bccba29df70f745bc39968bbec5b3e62dada2c858c45314f3ec723d113"


def period_bounds(as_of_date: str, period: Period, days: int = 1) -> dict:
    day = date.fromisoformat(as_of_date)
    end = day
    match period:
        case "today":
            start = day
        case "yesterday":
            start = end = day - timedelta(days=1)
        case "this_week":
            start = day - timedelta(days=day.weekday())
        case "last_week":
            end = day - timedelta(days=day.weekday() + 1)
            start = end - timedelta(days=6)
        case "this_month":
            start = day.replace(day=1)
        case "previous_month_comparison":
            last = day.replace(day=1) - timedelta(days=1)
            start = last.replace(day=1)
            end = last.replace(day=min(day.day, calendar.monthrange(last.year, last.month)[1]))
        case "year_to_date":
            start = day.replace(month=1, day=1)
        case "last_n_days":
            if type(days) is not int or not 1 <= days <= 3660:
                raise ValueError("days must be an integer in 1..3660")
            start = day - timedelta(days=days - 1)
        case _:
            raise ValueError("unsupported period; use explicit question dates")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "inclusive": True,
        "source": "active train rulebook section 2",
    }
