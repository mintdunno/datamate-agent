"""Narrow, source-proven calendar declarations and factual qualification receipts."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from datamate.preparation.calendar import (
    CalendarCandidate,
    CalendarDay,
    CalendarIntervalRule,
    CalendarResolutionError,
)
from datamate.preparation.models import RuleClause

# Exact reviewed clauses, not a default profile for train or any other split.
# Their complete text (including the comparison definition) must be present in
# the supplied active source. New wording requires a separately reviewed adapter.
_TET = (
    "| Tết năm Y, dịp Tết, mùa Tết | từ ngày dương của **23 tháng Chạp** "
    "(năm âm Y − 1) đến **hết** ngày dương của **mùng 10 tháng Giêng** (năm âm Y). "
    "**So với trước Tết** = cùng số ngày ngay trước 23 tháng Chạp. "
    "Tra lịch âm trong `calendar.csv` cùng thư mục. |"
)
_QUESTION_YEAR = (
    "| Tháng M, quý Q không ghi năm | của **năm nay** nếu không sau hôm nay; "
    "sau hôm nay thì của **năm trước**. Quý theo dương lịch, mỗi quý 3 tháng tính từ tháng 1 |"
)
_SOURCE_YEAR = "| Ngày không ghi năm | năm của sheet chứa dòng đó |"


@dataclass(frozen=True)
class CalendarDeclaration:
    kind: Literal["tet_interval", "question_year", "source_sheet_year"]
    status: Literal["ACCEPTED", "UNRESOLVED"]
    proof: RuleClause | None
    reason: str


@dataclass(frozen=True)
class TemporalDeclarations:
    split_id: str
    rulebook_sha256: str
    tet_interval: CalendarDeclaration
    question_year: CalendarDeclaration
    source_sheet_year: CalendarDeclaration


@dataclass(frozen=True)
class CalendarQualification:
    status: Literal["QUALIFIED", "UNRESOLVED"]
    table_id: str
    source_sha256: str
    calendar_sha256: str
    rows: tuple[CalendarDay, ...]
    findings: tuple[str, ...]
    date_only: Literal[True] = True
    timezone: None = None


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _relevant(quote: str, kind: str) -> bool:
    text = quote.casefold()
    if kind == "tet_interval":
        return "tết" in text
    if kind == "question_year":
        return "không ghi năm" in text and ("tháng" in text or "quý" in text)
    return "không ghi năm" in text and "ngày" in text


def compile_calendar_declarations(
    clauses: Sequence[RuleClause], *, split_id: str, rulebook_sha256: str
) -> TemporalDeclarations:
    """Accept only exact reviewed clauses from one explicitly identified active book."""
    rulebook = [clause for clause in clauses if clause.source_document.source == "rulebook.md"]
    covered = {
        line for clause in rulebook for line in range(clause.line_start, clause.line_end + 1)
    }
    required = (
        {
            i
            for i, line in enumerate(rulebook[0].source_document.text.splitlines(), start=1)
            if line.strip()
        }
        if rulebook
        else set()
    )
    active = (
        bool(split_id.strip())
        and _sha256(rulebook_sha256)
        and bool(rulebook)
        and required <= covered
        and all(clause.source_document.split_id == split_id for clause in clauses)
        and all(clause.source_document.sha256 == rulebook_sha256 for clause in rulebook)
    )

    def declaration(kind, reviewed):
        matches = [clause for clause in rulebook if _relevant(clause.quote, kind)]
        if not active:
            reason = "active_split_or_rulebook_fingerprint_mismatch"
        elif len(matches) != 1:
            reason = "missing_or_ambiguous_declaration"
        elif (
            matches[0].quote.rstrip("\r\n") != reviewed
            or matches[0].authority != "general"
            or matches[0].status in {"CONFLICT", "NOT_APPLICABLE"}
            or matches[0].conflicts_with
        ):
            reason = "unsupported_or_conflicting_declaration"
        else:
            return CalendarDeclaration(kind, "ACCEPTED", matches[0], "exact_active_source_clause")
        return CalendarDeclaration(kind, "UNRESOLVED", None, reason)

    return TemporalDeclarations(
        split_id,
        rulebook_sha256,
        declaration("tet_interval", _TET),
        declaration("question_year", _QUESTION_YEAR),
        declaration("source_sheet_year", _SOURCE_YEAR),
    )


def _proof(
    declarations: TemporalDeclarations, declaration: CalendarDeclaration, kind: str
) -> RuleClause:
    reviewed = {
        "tet_interval": _TET,
        "question_year": _QUESTION_YEAR,
        "source_sheet_year": _SOURCE_YEAR,
    }
    proof = declaration.proof
    if (
        declaration.kind != kind
        or declaration.status != "ACCEPTED"
        or proof is None
        or proof.quote.rstrip("\r\n") != reviewed.get(declaration.kind)
        or proof.authority != "general"
        or proof.status in {"CONFLICT", "NOT_APPLICABLE"}
        or proof.conflicts_with
        or proof.source_document.split_id != declarations.split_id
        or proof.source_document.source != "rulebook.md"
        or proof.source_document.sha256 != declarations.rulebook_sha256
    ):
        raise CalendarResolutionError("active temporal declaration is unresolved")
    return proof


def qualify_calendar_days(
    candidate: CalendarCandidate, *, source_sha256: str, calendar_sha256: str
) -> CalendarQualification:
    """Return all validated factual rows, or no rows if their identity is stale."""
    findings = set(candidate.findings)
    if not _sha256(source_sha256) or candidate.source_sha256 != source_sha256:
        findings.add("source_fingerprint_mismatch")
    if not _sha256(calendar_sha256) or candidate.calendar_sha256 != calendar_sha256:
        findings.add("calendar_fingerprint_mismatch")
    if not candidate.days:
        findings.add("empty_calendar")
    return CalendarQualification(
        "UNRESOLVED" if findings else "QUALIFIED",
        candidate.table_id,
        candidate.source_sha256,
        candidate.calendar_sha256,
        () if findings else candidate.days,
        tuple(sorted(findings)),
    )


def bind_calendar_rule(
    candidate: CalendarCandidate, declarations: TemporalDeclarations
) -> CalendarIntervalRule:
    """Bind the reviewed inclusive Tet rule to this validated calendar, without DDL."""
    proof = _proof(declarations, declarations.tet_interval, "tet_interval")
    qualification = qualify_calendar_days(
        candidate,
        source_sha256=candidate.source_sha256,
        calendar_sha256=candidate.calendar_sha256,
    )
    if qualification.status != "QUALIFIED":
        raise CalendarResolutionError("calendar candidate is unresolved")
    return CalendarIntervalRule(
        proof.clause_id,
        candidate.calendar_sha256,
        candidate.source_sha256,
        23,
        12,
        -1,
        False,
        10,
        1,
        0,
        False,
    )


def question_month_year(declarations: TemporalDeclarations, *, month: int, as_of_date: date) -> int:
    """Resolve a question's unspecified year from its as-of date, not source sheets."""
    _proof(declarations, declarations.question_year, "question_year")
    if type(month) is not int or not 1 <= month <= 12 or type(as_of_date) is not date:
        raise ValueError("question month and as-of date are invalid")
    year = as_of_date.year if month <= as_of_date.month else as_of_date.year - 1
    if year < 1:
        raise CalendarResolutionError("question month falls outside supported years")
    return year


def source_date_year(declarations: TemporalDeclarations, *, sheet_year: int | None) -> int:
    """Require an independently established sheet year for incomplete source dates."""
    _proof(declarations, declarations.source_sheet_year, "source_sheet_year")
    if type(sheet_year) is not int or not 1 <= sheet_year <= 9999:
        raise CalendarResolutionError("source sheet year is missing or ambiguous")
    return sheet_year
