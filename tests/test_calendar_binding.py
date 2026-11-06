"""Declarations require exact active source proof and never supply missing calendar rows."""

from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256

import duckdb
import pytest

from datamate.preparation.calendar import (
    CalendarResolutionError,
    build_calendar_candidate,
    calendar_interval,
)
from datamate.preparation.calendar_binding import (
    bind_calendar_rule,
    compile_calendar_declarations,
    qualify_calendar_days,
    question_month_year,
    source_date_year,
)
from datamate.preparation.catalog import compile_clauses
from datamate.preparation.models import SourceFile, SourceRow, SourceTable
from datamate.preparation.storage import create_storage, stage_table

# Reviewed verbatim calendar/year rows from active train rulebook sections 2 and 8.
TET = (
    "| Tết năm Y, dịp Tết, mùa Tết | từ ngày dương của **23 tháng Chạp** "
    "(năm âm Y − 1) đến **hết** ngày dương của **mùng 10 tháng Giêng** (năm âm Y). "
    "**So với trước Tết** = cùng số ngày ngay trước 23 tháng Chạp. "
    "Tra lịch âm trong `calendar.csv` cùng thư mục. |"
)
QUESTION_YEAR = (
    "| Tháng M, quý Q không ghi năm | của **năm nay** nếu không sau hôm nay; "
    "sau hôm nay thì của **năm trước**. Quý theo dương lịch, mỗi quý 3 tháng tính từ tháng 1 |"
)
SOURCE_YEAR = "| Ngày không ghi năm | năm của sheet chứa dòng đó |"
COLUMNS = dict(
    zip(
        ("date", "weekday", "lunar_day", "lunar_month", "lunar_year", "lunar_leap"),
        ("c0001", "c0002", "c0003", "c0004", "c0005", "c0006"),
        strict=True,
    )
)


def _declarations(tmp_path, text=None, *, split_id="train", expected_hash=None):
    text = text if text is not None else "\n".join((TET, QUESTION_YEAR, SOURCE_YEAR)) + "\n"
    path = tmp_path / "rulebook.md"
    path.write_bytes(text.encode("utf-8"))
    digest = sha256(text.encode()).hexdigest()
    clauses = compile_clauses((SourceFile(path, "rulebook.md", digest),), split_id=split_id)
    return compile_calendar_declarations(
        clauses, split_id=split_id, rulebook_sha256=expected_hash or digest
    )


def _candidate(year, start, *, missing=None, leap=False):
    lunar_days = [(day, 12, year - 1) for day in range(23, 30)]
    lunar_days += [(day, 1, year) for day in range(1, 11)]
    rows = []
    for i, (day, month, lunar_year) in enumerate(lunar_days):
        if i == missing:
            continue
        solar = start + timedelta(days=i)
        rows.append(
            SourceRow(
                i + 2,
                (
                    solar.isoformat(),
                    solar.isoweekday(),
                    day,
                    month,
                    lunar_year,
                    "true" if leap and i == 7 else "false",
                ),
            )
        )
    with duckdb.connect() as db:
        create_storage(db)
        table = stage_table(
            db,
            SourceFile(None, "calendar.csv", "a" * 64),
            SourceTable("calendar", tuple(COLUMNS), 1, rows),
        )
        return build_calendar_candidate(db, table.table_id, COLUMNS, "a" * 64)


@pytest.mark.parametrize(
    "year,start,end",
    [
        (2025, date(2025, 1, 22), date(2025, 2, 7)),
        (2026, date(2026, 2, 10), date(2026, 2, 26)),
    ],
)
def test_reviewed_clause_binds_inclusive_requested_lunar_year(tmp_path, year, start, end):
    declarations = _declarations(tmp_path)
    candidate = _candidate(year, start)
    rule = bind_calendar_rule(candidate, declarations)
    assert declarations.tet_interval.status == "ACCEPTED"
    assert rule.source_clause_id == declarations.tet_interval.proof.clause_id
    assert calendar_interval(candidate, rule, year) == (start, end)
    with pytest.raises(CalendarResolutionError, match="missing"):
        calendar_interval(candidate, rule, year + 1)


def test_calendar_days_are_typed_source_bound_facts(tmp_path):
    candidate = _candidate(2025, date(2025, 1, 22))
    qualified = qualify_calendar_days(
        candidate,
        source_sha256=candidate.source_sha256,
        calendar_sha256=candidate.calendar_sha256,
    )
    assert qualified.status == "QUALIFIED"
    assert len(qualified.rows) == 17
    assert qualified.rows[0].solar_date == date(2025, 1, 22)
    assert qualified.rows[0].lunar_day == 23
    assert qualified.rows[0].lunar_leap is False
    assert qualified.rows[0].record_id
    assert qualified.source_sha256 == "a" * 64
    assert qualified.calendar_sha256 == candidate.calendar_sha256
    assert qualified.table_id == candidate.table_id
    assert qualified.date_only is True
    assert qualified.timezone is None


@pytest.mark.parametrize("change", ["missing", "leap", "source_hash", "calendar_hash"])
def test_unqualified_calendar_never_exposes_partial_rows(tmp_path, change):
    candidate = _candidate(
        2025,
        date(2025, 1, 22),
        missing=8 if change == "missing" else None,
        leap=change == "leap",
    )
    qualification = qualify_calendar_days(
        candidate,
        source_sha256="b" * 64 if change == "source_hash" else candidate.source_sha256,
        calendar_sha256="b" * 64 if change == "calendar_hash" else candidate.calendar_sha256,
    )
    assert qualification.status == "UNRESOLVED"
    assert qualification.rows == ()
    assert qualification.findings
    if change in {"missing", "leap"}:
        with pytest.raises(CalendarResolutionError):
            bind_calendar_rule(candidate, _declarations(tmp_path))


@pytest.mark.parametrize(
    "text",
    [
        "| Tết năm Y | mùng 1 đến mùng 7 tháng Giêng |\n",
        TET.replace("23 tháng Chạp", "24 tháng Chạp") + "\n",
        TET + "\n" + TET + "\n",
        TET + "\n| Tết theo lịch khác | 1 đến 7 tháng Giêng |\n",
        "# No supported calendar declaration\n",
    ],
)
def test_unknown_changed_or_ambiguous_rule_has_no_train_default(tmp_path, text):
    declarations = _declarations(tmp_path, text, split_id="private")
    assert declarations.tet_interval.status == "UNRESOLVED"
    assert declarations.tet_interval.proof is None
    with pytest.raises(CalendarResolutionError):
        bind_calendar_rule(_candidate(2025, date(2025, 1, 22)), declarations)


def test_stale_rulebook_and_wrong_active_split_block_declarations(tmp_path):
    stale = _declarations(tmp_path, expected_hash="0" * 64)
    assert stale.tet_interval.status == "UNRESOLVED"
    assert stale.question_year.status == "UNRESOLVED"
    assert stale.source_sheet_year.status == "UNRESOLVED"
    path = tmp_path / "rulebook.md"
    digest = sha256(path.read_bytes()).hexdigest()
    clauses = compile_clauses((SourceFile(path, "rulebook.md", digest),), split_id="train")
    wrong = compile_calendar_declarations(clauses, split_id="private", rulebook_sha256=digest)
    assert wrong.tet_interval.status == "UNRESOLVED"


def test_question_month_and_source_sheet_year_are_distinct(tmp_path):
    declarations = _declarations(tmp_path)
    assert question_month_year(declarations, month=2, as_of_date=date(2026, 6, 18)) == 2026
    assert question_month_year(declarations, month=7, as_of_date=date(2026, 6, 18)) == 2025
    assert question_month_year(declarations, month=6, as_of_date=date(2026, 6, 18)) == 2026
    assert source_date_year(declarations, sheet_year=2025) == 2025
    with pytest.raises(CalendarResolutionError):
        source_date_year(declarations, sheet_year=None)
    unsupported = _declarations(tmp_path, TET + "\n")
    with pytest.raises(CalendarResolutionError):
        question_month_year(unsupported, month=2, as_of_date=date(2026, 6, 18))
    with pytest.raises(CalendarResolutionError):
        source_date_year(unsupported, sheet_year=2025)


@pytest.mark.parametrize("value", [True, 0, 13, "2"])
def test_invalid_question_month_is_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        question_month_year(_declarations(tmp_path), month=value, as_of_date=date(2026, 6, 18))


def test_leap_boundary_is_not_silently_selected(tmp_path):
    candidate = _candidate(2025, date(2025, 1, 22))
    # Even a manually supplied candidate must not turn a leap month into ordinary Tet.
    candidate = replace(
        candidate, days=tuple(replace(day, lunar_leap=True) for day in candidate.days)
    )
    rule = bind_calendar_rule(candidate, _declarations(tmp_path))
    with pytest.raises(CalendarResolutionError, match="missing"):
        calendar_interval(candidate, rule, 2025)


def test_omitting_conflicting_clause_does_not_make_document_acceptable(tmp_path):
    text = TET + "\n| Tết theo lịch khác | 1 đến 7 tháng Giêng |\n"
    path = tmp_path / "rulebook.md"
    path.write_bytes(text.encode())
    digest = sha256(path.read_bytes()).hexdigest()
    clauses = compile_clauses((SourceFile(path, "rulebook.md", digest),), split_id="train")
    result = compile_calendar_declarations(clauses[:1], split_id="train", rulebook_sha256=digest)
    assert result.tet_interval.status == "UNRESOLVED"


def test_forged_acceptance_of_changed_clause_cannot_bind_calendar(tmp_path):
    declarations = _declarations(tmp_path, TET.replace("23 tháng Chạp", "24 tháng Chạp") + "\n")
    path = tmp_path / "rulebook.md"
    digest = sha256(path.read_bytes()).hexdigest()
    clause = compile_clauses((SourceFile(path, "rulebook.md", digest),), split_id="train")[0]
    forged = replace(
        declarations,
        tet_interval=replace(declarations.tet_interval, status="ACCEPTED", proof=clause),
    )
    with pytest.raises(CalendarResolutionError):
        bind_calendar_rule(_candidate(2025, date(2025, 1, 22)), forged)


def test_year_declaration_cannot_be_reused_as_tet_interval_proof(tmp_path):
    declarations = _declarations(tmp_path)
    declarations = replace(declarations, tet_interval=declarations.question_year)
    with pytest.raises(CalendarResolutionError):
        bind_calendar_rule(_candidate(2025, date(2025, 1, 22)), declarations)
