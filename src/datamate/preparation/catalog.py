"""Source-linked rule catalogs retain text as evidence, never executable instructions."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace

from datamate.preparation.models import (
    ClauseDeclaration,
    RuleClause,
    SourceDocument,
    SourceFile,
)


def rule_entries(documents: tuple[SourceFile, ...]) -> list[dict]:
    entries = []
    for document in documents:
        if document.path.suffix.lower() != ".md":
            continue
        lines = document.path.read_text(encoding="utf-8-sig").splitlines()
        starts = [i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]
        if not starts or starts[0] != 0:
            starts.insert(0, 0)
        for start, end in zip(starts, [*starts[1:], len(lines)], strict=True):
            text = "\n".join(lines[start:end])
            if not text.strip():
                continue
            identity = f"{document.relative_path}:{document.sha256}:{start}:{end}"
            entries.append(
                {
                    "rule_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "source": document.relative_path,
                    "source_sha256": document.sha256,
                    "line_start": start + 1,
                    "line_end": end,
                    "title": lines[start].lstrip("# ").strip() or document.path.name,
                    "scope": "split" if document.relative_path == "rulebook.md" else "business",
                    "text": text,
                    "untrusted": True,
                }
            )
    return entries


def search_rules(entries: list[dict], query: str, *, limit: int = 10) -> list[dict]:
    """Bounded lexical lookup; callers must fetch applicable general/industry rules too."""
    if not 1 <= limit <= 100:
        raise ValueError("rule result limit must be 1..100")
    tokens = set(re.findall(r"\w+", query.casefold()))
    ranked = []
    for entry in entries:
        score = len(tokens & set(re.findall(r"\w+", entry["text"].casefold())))
        if score:
            ranked.append((-score, entry["source"], entry["line_start"], entry))
    return [item[-1] for item in sorted(ranked, key=lambda item: item[:3])[:limit]]


_HEADING = re.compile(r"^\ufeff?(#{1,6})\s+(.+)$")
_BULLET = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_INDUSTRY_HEADING = re.compile(r"^(?:industry|ngành)\s*:\s*(.+)$", re.IGNORECASE)
_WEEK_START = re.compile(r"^(?:[-*+]\s+)?week_start\s*:\s*([a-z]+)$", re.IGNORECASE)


def _markdown_blocks(text: str) -> list[tuple[int, int, str, str | None, str | None]]:
    """Keep every nonblank Markdown block, including headings and unfamiliar syntax."""
    lines = text.splitlines(keepends=True)
    blocks = []
    start = None
    section = None
    industry = None
    industry_level = None
    block_section = None
    block_industry = None
    for position, line in enumerate(lines):
        heading = _HEADING.match(line)
        boundary = (
            not line.strip() or heading is not None or _BULLET.match(line) or _TABLE_ROW.match(line)
        )
        if boundary and start is not None:
            blocks.append(
                (start + 1, position, "".join(lines[start:position]), block_section, block_industry)
            )
            start = None
        if not line.strip():
            continue
        if heading:
            level = len(heading.group(1))
            section = heading.group(2).strip()
            industry_match = _INDUSTRY_HEADING.match(section)
            if industry_match:
                industry = industry_match.group(1).strip().casefold()
                industry_level = level
            elif industry_level is not None and level <= industry_level:
                industry = None
                industry_level = None
            blocks.append((position + 1, position + 1, line, section, industry))
            continue
        if start is None:
            start = position
            block_section = section
            block_industry = industry
    if start is not None:
        blocks.append(
            (start + 1, len(lines), "".join(lines[start:]), block_section, block_industry)
        )
    return blocks


def _declaration(quote: str) -> ClauseDeclaration | None:
    match = _WEEK_START.fullmatch(quote.strip())
    if match and match.group(1).casefold() in {
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    }:
        return ClauseDeclaration("week_start", match.group(1).casefold())
    return None


def compile_clauses(
    documents: tuple[SourceFile, ...], *, split_id: str | None = None
) -> tuple[RuleClause, ...]:
    """Compile exact active-split source clauses; unknown semantics remain visible."""
    rulebooks = [document for document in documents if document.relative_path == "rulebook.md"]
    if len(rulebooks) != 1:
        raise ValueError("exactly one active split rulebook is required")
    split_root = rulebooks[0].path.parent.resolve(strict=True)
    split_id = split_root.name if split_id is None else split_id
    if not split_id.strip() or any(char in split_id for char in "/\\:\x00"):
        raise ValueError("invalid active split identity")
    clauses = []
    for source in documents:
        if source.path.suffix.lower() != ".md":
            continue
        path = source.path.resolve(strict=True)
        if (
            not path.is_relative_to(split_root)
            or path.relative_to(split_root).as_posix() != source.relative_path
        ):
            raise ValueError("source document belongs to another split")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source.sha256:
            raise ValueError("source document hash changed")
        text = raw.decode("utf-8")
        document = SourceDocument(split_id, source.relative_path, source.sha256, text)
        for first, last, quote, section, industry in _markdown_blocks(text):
            authority = (
                "readme"
                if source.relative_path != "rulebook.md"
                else ("industry" if industry is not None else "general")
            )
            # Headings and table labels can contain substantive rules. Only a
            # pure separator is demonstrably syntax without normative content.
            markup_only = bool(_TABLE_SEPARATOR.fullmatch(quote.strip()))
            declaration = None if markup_only else _declaration(quote)
            status = (
                "NOT_APPLICABLE"
                if markup_only
                else ("MAPPED" if declaration is not None else "UNMAPPED")
            )
            quote_sha = hashlib.sha256(quote.encode("utf-8")).hexdigest()
            identity = "\0".join(
                (
                    document.split_id,
                    document.source,
                    document.sha256,
                    str(first),
                    str(last),
                    quote_sha,
                )
            )
            clauses.append(
                RuleClause(
                    clause_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    source_document=document,
                    line_start=first,
                    line_end=last,
                    quote=quote,
                    quote_sha256=quote_sha,
                    section=section,
                    authority=authority,
                    industry_scope=industry if authority == "industry" else None,
                    status=status,
                    declaration=declaration,
                    dependencies=(declaration.kind,) if declaration else (),
                )
            )
    groups: dict[tuple[str, str | None, str], list[int]] = {}
    for index, clause in enumerate(clauses):
        if clause.declaration:
            key = (clause.authority, clause.industry_scope, clause.declaration.kind)
            groups.setdefault(key, []).append(index)
    for members in groups.values():
        for index in members:
            conflicts = tuple(
                clauses[other].clause_id
                for other in members
                if clauses[other].declaration != clauses[index].declaration
            )
            if conflicts:
                clauses[index] = replace(
                    clauses[index], status="CONFLICT", conflicts_with=conflicts
                )
    for index, clause in enumerate(clauses):
        if clause.authority != "readme" or clause.declaration is None:
            continue
        higher_conflicts = tuple(
            other.clause_id
            for other in clauses
            if other.authority == "general"
            and other.declaration is not None
            and other.declaration.kind == clause.declaration.kind
            and other.declaration != clause.declaration
        )
        if higher_conflicts:
            clauses[index] = replace(
                clause, conflicts_with=clause.conflicts_with + higher_conflicts
            )
    return tuple(clauses)


def resolve_declaration(
    clauses: Sequence[RuleClause],
    kind: str,
    *,
    split_id: str,
    industry: str | None = None,
    question_override: ClauseDeclaration | None = None,
    override_permitted: bool = False,
) -> RuleClause | ClauseDeclaration | None:
    """Select a supported declaration by authority, blocking same-rank conflicts."""
    rulebook_hashes = {
        clause.source_document.sha256
        for clause in clauses
        if clause.source_document.source == "rulebook.md"
    }
    if (
        not split_id
        or any(clause.source_document.split_id != split_id for clause in clauses)
        or len(rulebook_hashes) != 1
    ):
        raise ValueError("clauses must belong to one active split and rulebook")
    if question_override is not None:
        if not override_permitted:
            raise ValueError("question override is not permitted by the active rulebook")
        if question_override.kind != kind:
            raise ValueError("question override kind differs from requested declaration")
        return question_override
    industry = industry.casefold() if industry else None
    for authority in ("industry", "general", "readme"):
        candidates = [
            clause
            for clause in clauses
            if clause.authority == authority
            and clause.declaration is not None
            and clause.declaration.kind == kind
            and (authority != "industry" or clause.industry_scope == industry)
        ]
        if candidates:
            if any(clause.status == "CONFLICT" for clause in candidates):
                return None
            return candidates[0]
    return None
