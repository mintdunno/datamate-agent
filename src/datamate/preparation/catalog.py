"""Source-linked rule catalogs retain text as evidence, never executable instructions."""

import hashlib
import json
import re
import unicodedata
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from fractions import Fraction

from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from datamate.preparation.models import (
    BINDING_VALIDATOR_VERSION,
    Binding,
    ClauseDeclaration,
    EntityResolution,
    MeasurePolicy,
    QuantityReconciliation,
    ResolvedBinding,
    RuleClause,
    SemanticBindings,
    SourceDocument,
    SourceFile,
    TableProfile,
    UnitResolution,
)
from datamate.preparation.normalization import FORMAT_GRAMMARS


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


TOKENIZER_VERSION = "unicode-nfkc-casefold-word-v1"
RETRIEVAL_INDEX_VERSION = "bm25-okapi-rapidfuzz-partial-v1"
_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_EPSILON = 0.25
_FUZZY_CANDIDATE_THRESHOLD = 75.0


@dataclass(frozen=True)
class ClauseMatch:
    """Ranked evidence candidate, without any semantic acceptance decision."""

    clause: RuleClause
    score: float
    bm25_score: float
    fuzzy_score: float
    index_digest: str
    tokenizer_version: str


def _rule_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return _TOKEN_PATTERN.findall(normalized)


def _retrieval_index_digest(clauses: Sequence[RuleClause]) -> str:
    settings = {
        "tokenizer": TOKENIZER_VERSION,
        "index": RETRIEVAL_INDEX_VERSION,
        "bm25": {"k1": _BM25_K1, "b": _BM25_B, "epsilon": _BM25_EPSILON},
        "fuzzy_candidate_threshold": _FUZZY_CANDIDATE_THRESHOLD,
        "clauses": [
            (clause.clause_id, clause.source_document.sha256, clause.quote_sha256)
            for clause in clauses
        ],
    }
    return hashlib.sha256(
        json.dumps(settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def retrieve_clauses(query: str, clauses: Sequence[RuleClause], *, limit: int) -> list[ClauseMatch]:
    """Rank bounded lexical candidates; callers retain the complete clause ledger."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("rule result limit must be an integer from 1..100")
    query_tokens = _rule_tokens(query)
    if not query_tokens or not clauses:
        return []
    ordered = sorted(
        clauses,
        key=lambda clause: (
            clause.source_document.source,
            clause.line_start,
            clause.clause_id,
        ),
    )
    corpus = [_rule_tokens(clause.quote) for clause in ordered]
    if not any(corpus):
        return []
    index_digest = _retrieval_index_digest(ordered)
    index = BM25Okapi(corpus, k1=_BM25_K1, b=_BM25_B, epsilon=_BM25_EPSILON)
    scores = index.get_scores(query_tokens)
    query_text = " ".join(query_tokens)
    query_terms = set(query_tokens)
    ranked = []
    for clause, tokens, bm25_score in zip(ordered, corpus, scores, strict=True):
        fuzzy_score = float(fuzz.partial_ratio(query_text, " ".join(tokens))) if tokens else 0.0
        if not query_terms.intersection(tokens) and fuzzy_score < _FUZZY_CANDIDATE_THRESHOLD:
            continue
        lexical_score = max(0.0, float(bm25_score))
        match = ClauseMatch(
            clause=clause,
            score=lexical_score + fuzzy_score / 100,
            bm25_score=float(bm25_score),
            fuzzy_score=fuzzy_score,
            index_digest=index_digest,
            tokenizer_version=TOKENIZER_VERSION,
        )
        ranked.append(match)
    ranked.sort(
        key=lambda match: (
            -match.score,
            match.clause.source_document.source,
            match.clause.line_start,
            match.clause.clause_id,
        )
    )
    return ranked[:limit]


_ROLE_HEADERS = {
    "order_id": {"order_id", "mã hóa đơn", "mã hoá đơn", "ma hoa don", "số hóa đơn"},
    "product_id": {"product_id", "mã hàng", "ma hang", "mã sản phẩm", "mã sku"},
    "customer_id": {"customer_id", "mã khách", "mã khách hàng"},
    "ordered_at": {"ordered_at", "ngày đặt", "ngay dat"},
    "quantity": {"quantity", "số lượng", "so luong"},
    "order_total": {"order_total"},
    "line_net_amount": {"line_net_amount"},
    "payment_amount": {"payment_amount"},
    "payment_at": {"payment_at"},
    "inventory_quantity": {"inventory_quantity"},
    "unit_cost": {"unit_cost"},
    "unit": {"unit"},
    "quantity_base": {"quantity_base"},
    "unit_factor": {"unit_factor"},
    "base_unit": {"base_unit"},
    "product_name": {"product_name", "tên sản phẩm", "tên hàng"},
    "customer_name": {"customer_name"},
}
_ROLE_DECLARATIONS = {
    "order_total": r"số khách phải trả (?:cho )?cả đơn",
    "payment_amount": r"số tiền trả lần đó",
    "unit_cost": r"giá vốn một đơn vị bán",
    "inventory_quantity": r"tồn cuối ngày cuối có dữ liệu, theo đơn vị cơ bản",
}
_GRAIN_ROLES = {
    "order_header": {"order_id", "order_total"},
    "order_line": {"order_id", "product_id", "quantity", "line_net_amount"},
    "payment_event": {"order_id", "payment_at", "payment_amount"},
    "inventory_snapshot": {"product_id", "inventory_quantity"},
}


# These are exact source declarations, not fuzzy header guesses.
_README_ROLE_MEANINGS = {
    "order_id": r"mã (?:đơn|hóa đơn|hoá đơn)(?: hàng)?",
    "product_id": r"mã (?:hàng|sản phẩm)",
    "quantity": r"số lượng(?: sản phẩm)?",
    "ordered_at": r"ngày (?:bán|đặt hàng)",
    "unit": r"(?:đơn vị(?: tính)?|đơn vị lớn(?: \([^()\n]*\))?)",
    "base_unit": r"đơn vị cơ bản",
    "unit_factor": r"hệ số: số đơn vị cơ bản trong 1 đơn vị lớn",
    "product_name": r"tên(?: hàng| sản phẩm)(?: \(file chỉ có mặt hàng có đơn vị lớn\))?",
    "customer_name": r"tên khách(?: hàng)?(?: \(khách lẻ nếu không lưu\))?",
    "line_net_amount": r"thành tiền sau giảm(?: giá)?",
}


def _declared_role(header: str, role: str, profile: TableProfile, clause: RuleClause) -> bool:
    """Prove a complete field declaration in its native file section."""
    # The nearest file-bearing ancestor remains authoritative under subheadings.
    headings = []
    for line in clause.source_document.text.splitlines()[: clause.line_start - 1]:
        match = _HEADING.match(line)
        if match:
            level = len(match.group(1))
            headings = [(n, value) for n, value in headings if n < level]
            headings.append((level, _source_name(match.group(2))))
    filename = _source_name(profile.source.rsplit("/", 1)[-1])
    for _, section in reversed(headings):
        named_files = re.findall(r"[^\s`*]+\.(?:xlsx|xls|csv|sqlite|db)\b", section)
        if named_files:
            if filename not in named_files:
                return False
            break
    text = re.sub(r"^[-*+]\s+", "", _source_name(clause.quote))
    field = (
        r"(?:\*\*"
        + re.escape(header)
        + r"\*\*|`"
        + re.escape(header)
        + r"`|"
        + re.escape(header)
        + r")"
    )
    legacy = _ROLE_DECLARATIONS.get(role)
    # Semicolons separate positive field declarations. Arbitrary sentence prefixes
    # (including negations) cannot authorize a mapping by substring resemblance.
    domain = r"(?:\*\*(?:công nợ|tồn kho|giá vốn|nhập hàng)\*\*:\s*)?"
    if legacy and any(
        re.fullmatch(domain + field + r"\s+là\s+" + legacy + r"[.;]?", part.strip())
        for part in text.split(";")
    ):
        return True
    meaning = _README_ROLE_MEANINGS.get(role)
    return bool(meaning and re.fullmatch(field + r"\s*:\s*" + meaning + r"[.;]?", text))


def _source_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _binding_citations(binding: Binding, profile: TableProfile, clauses: dict[str, RuleClause]):
    citations = []
    header_witnesses = set()
    for evidence in binding.evidence:
        if evidence.locator.startswith("header:"):
            if (
                evidence.source != profile.source
                or evidence.sha256 != profile.source_sha256
                or evidence.locator[7:] not in {f"{profile.table_id}/{c}" for c in binding.columns}
            ):
                raise ValueError("header evidence differs from physical source")
            header_witnesses.add(evidence.locator.rsplit("/", 1)[1])
            continue
        if not evidence.locator.startswith("clause:"):
            raise ValueError("unsupported binding evidence locator")
        clause = clauses.get(evidence.locator[7:])
        if clause is None or (
            clause.source_document.source != evidence.source
            or clause.source_document.sha256 != evidence.sha256
            or (clause.line_start, clause.line_end) != (evidence.line_start, evidence.line_end)
            or clause.quote_sha256 != evidence.quote_sha256
        ):
            raise ValueError("clause evidence is stale or incomplete")
        if (
            evidence.source.startswith("data/")
            and evidence.source.split("/")[:2] != profile.source.split("/")[:2]
        ):
            raise ValueError("clause belongs to a different business")
        if clause.status == "CONFLICT":
            raise ValueError("cited source clause is conflicting")
        citations.append(clause)
    return tuple(citations), header_witnesses


def validate_bindings(
    clauses: Sequence[RuleClause], profiles: Sequence[TableProfile], proposals: Sequence[Binding]
) -> SemanticBindings:
    """Accept only registered source interpretations; resemblance is not authority."""
    from datamate.preparation.text_state import effective_header

    if len({p.table_id for p in profiles}) != len(profiles):
        raise ValueError("duplicate physical profiles")
    if (
        len({c.source_document.split_id for c in clauses}) != 1
        or len(
            {c.source_document.sha256 for c in clauses if c.source_document.source == "rulebook.md"}
        )
        != 1
    ):
        raise ValueError("bindings require one active split and rulebook")
    by_table = {profile.table_id: profile for profile in profiles}
    by_clause = {clause.clause_id: clause for clause in clauses}
    pending = {p.binding_id: Binding.model_validate_json(p.model_dump_json()) for p in proposals}
    if len(pending) != len(proposals):
        raise ValueError("duplicate binding proposals")
    entries = {}
    citations = {}
    for identifier, proposal in pending.items():
        profile = by_table.get(proposal.table_id)
        reason = None
        if profile is None or profile.digest != proposal.profile_digest:
            reason = "physical profile is missing or stale"
        elif not set(proposal.columns) <= {c.column_id for c in profile.columns}:
            reason = "unknown physical column"
        else:
            try:
                citations[identifier] = _binding_citations(proposal, profile, by_clause)
            except ValueError as exc:
                reason = str(exc)
        if reason:
            entries[identifier] = ResolvedBinding(proposal, "UNRESOLVED", reason)
    # Multiple target factual grains are allowed; contradictory roles on one cell are not.
    role_scopes = {}
    for identifier, proposal in pending.items():
        if identifier not in entries and proposal.kind in {"role", "format"}:
            for column in proposal.columns:
                role_scopes.setdefault((proposal.kind, proposal.table_id, column), []).append(
                    identifier
                )
    for identifiers in role_scopes.values():
        if len({pending[i].value for i in identifiers}) > 1:
            for identifier in identifiers:
                entries[identifier] = ResolvedBinding(
                    pending[identifier], "CONFLICT", "conflicting column roles"
                )
    while True:
        progressed = False
        for identifier, proposal in pending.items():
            if identifier in entries:
                continue
            if any(dependency not in entries for dependency in proposal.dependencies):
                continue
            if any(entries[d].status != "ACCEPTED" for d in proposal.dependencies):
                entries[identifier] = ResolvedBinding(
                    proposal, "UNRESOLVED", "unaccepted dependency"
                )
                progressed = True
                continue
            profile = by_table[proposal.table_id]
            cited, headers = citations[identifier]
            accepted = False
            reason = "no registered distinguishing source evidence"
            if proposal.kind == "structure" and proposal.value in {"note", "summary"}:
                for clause in cited:
                    prefix = proposal.value + "_marker:"
                    text = clause.quote.strip().removeprefix("- ")
                    if text.startswith(prefix):
                        try:
                            accepted = json.loads(text[len(prefix) :].strip()) == proposal.marker
                        except json.JSONDecodeError:
                            continue
                        if accepted:
                            break
                reason = "explicit structural marker declaration" if accepted else reason
            elif (
                proposal.kind == "format"
                and len(proposal.columns) == 1
                and proposal.value in FORMAT_GRAMMARS
            ):
                column = next(c for c in profile.columns if c.column_id == proposal.columns[0])
                header = _source_name(effective_header(profile, column))
                declarations = {
                    f"{field} {kind}: {proposal.value}"
                    for field in (
                        f"**{header}**",
                        f"`{header}`",
                        f"`{_source_name(profile.source_table)}.{header}`",
                    )
                    for kind in ("number_style", "date_format", "serial_format")
                }
                accepted = bool(header) and any(
                    _source_name(c.quote).strip("- ") in declarations for c in cited
                )
                reason = "explicit field-scoped grammar declaration" if accepted else reason
            elif proposal.kind == "role" and len(proposal.columns) == 1:
                column = next(c for c in profile.columns if c.column_id == proposal.columns[0])
                header = _source_name(effective_header(profile, column))
                accepted = proposal.columns[0] in headers and header in _ROLE_HEADERS.get(
                    proposal.value, set()
                )
                if not accepted and header:
                    accepted = any(
                        _declared_role(header, proposal.value, profile, c) for c in cited
                    )
                reason = "explicit column declaration" if accepted else reason
            elif proposal.kind in {"entity", "unit"}:
                proof_context = SemanticBindings(
                    (),
                    tuple(clauses),
                    tuple(p.digest for p in profiles),
                    tuple(by_table),
                    profile_sources=tuple(
                        (p.table_id, p.digest, p.source, p.source_sha256) for p in profiles
                    ),
                )
                roles = [
                    entries[d].binding
                    for d in proposal.dependencies
                    if entries[d].binding.kind == "role"
                    and entries[d].binding.table_id == proposal.table_id
                ]
                if proposal.kind == "entity":
                    result = accept_entity_links((proposal,), proof_context)
                    accepted = bool(result.accepted_links or result.aliases) and any(
                        b.value in {f"{proposal.entity_scope}_id", f"{proposal.entity_scope}_name"}
                        and set(proposal.columns) <= set(b.columns)
                        for b in roles
                    )
                else:
                    declaration = _unit_declaration(proposal)
                    accepted = (
                        declaration is not None
                        and _literal_proof(proposal, proof_context, declaration)
                        and any(b.value == "unit" and b.columns == proposal.columns for b in roles)
                        and any(b.value == "product_id" for b in roles)
                    )
                # Acceptance proves a source declaration, not a conflict-free graph.
                # Reviewed row operations independently check the entire scoped graph.
                reason = "current scoped source declaration" if accepted else reason
            elif proposal.kind == "grain":
                roles = {
                    entries[d].binding.value
                    for d in proposal.dependencies
                    if entries[d].binding.kind == "role"
                    and entries[d].binding.table_id == proposal.table_id
                    and set(entries[d].binding.columns) <= set(proposal.columns)
                }
                required = _GRAIN_ROLES.get(proposal.value)
                accepted = required is not None and required <= roles
                if proposal.value == "inventory_snapshot":
                    accepted = (
                        accepted
                        and proposal.snapshot_at is not None
                        and any(
                            _source_name(clause.quote).strip("- ")
                            == f"snapshot_at: {proposal.snapshot_at}"
                            for clause in cited
                        )
                    )
                reason = "target grain from accepted field roles" if accepted else reason
            elif proposal.kind == "capability" and proposal.value == "hour":
                selected = [c for c in profile.columns if c.column_id in proposal.columns]
                # Text dates remain processing uncertainty until independently parsed.
                date_only = all(
                    set(c.raw_types) <= {"date", "NoneType"} and c.raw_types.get("date", 0) > 0
                    for c in selected
                )
                accepted = proposal.available is False and date_only
                reason = "native source precision is day-only" if accepted else reason
            elif proposal.kind == "capability" and proposal.value == "event_history":
                accepted = proposal.available is False and any(
                    entries[d].binding.kind == "grain"
                    and entries[d].binding.value == "inventory_snapshot"
                    and entries[d].binding.table_id == proposal.table_id
                    for d in proposal.dependencies
                )
                reason = "fixed snapshot is not event history" if accepted else reason
            entries[identifier] = ResolvedBinding(
                proposal,
                "ACCEPTED" if accepted else "UNRESOLVED",
                reason,
                tuple(c.clause_id for c in cited),
                BINDING_VALIDATOR_VERSION if accepted else None,
            )
            progressed = True
        if not progressed:
            break
    for identifier in pending.keys() - entries.keys():
        entries[identifier] = ResolvedBinding(
            pending[identifier], "UNRESOLVED", "missing or cyclic dependency"
        )
    return SemanticBindings(
        tuple(entries[k] for k in sorted(entries)),
        tuple(clauses),
        tuple(sorted(p.digest for p in profiles)),
        tuple(sorted(by_table)),
        profile_sources=tuple(
            sorted((p.table_id, p.digest, p.source, p.source_sha256) for p in profiles)
        ),
    )


def render_measure(policy: MeasurePolicy, bindings: SemanticBindings) -> str:
    """Render a contract query only after a separate audit qualifies its projection."""
    policy = MeasurePolicy.model_validate(policy)
    if (policy.projection_id, policy.measure, policy.digest) not in bindings.qualified_measures:
        raise ValueError("measure projection and policy are not qualified")
    if not set(policy.clause_ids) <= {c.clause_id for c in bindings.clauses}:
        raise ValueError("unknown measure rule dependency")
    expressions = {
        "revenue": 'SUM("line_net_amount")',
        "debt": 'SUM("debt_amount")',
        "item_quantity": 'SUM("quantity_base")',
        "visit_count": 'COUNT(DISTINCT "visit_id")',
        "work_count": 'COUNT(DISTINCT "work_id")',
    }
    predicates = {
        "exclude_cancelled": 'NOT "cancelled"',
        "exclude_returned": 'NOT "returned"',
        "exclude_cod": "\"payment_method\" <> 'cod'",
        "exclude_marketplace": "\"payment_method\" <> 'marketplace'",
        "positive_amount": '"debt_amount" > 0',
        "exclude_fee": 'NOT "is_fee"',
    }
    where = " AND ".join(predicates[p] for p in policy.predicates)
    return (
        f'SELECT {expressions[policy.measure]} AS "value" FROM "clean"."{policy.projection_id}"'
        + (" WHERE " + where if where else "")
    )


def required_clauses(bindings: SemanticBindings, scope: str) -> set[str]:
    """Required source coverage is independent of top-k lexical candidates."""
    by_id = {entry.binding.binding_id: entry for entry in bindings.entries}
    if scope not in bindings.table_ids and scope not in by_id:
        raise ValueError("unknown binding/table scope")
    # Until reviewed applicability narrows a clause, unknown normative text stays
    # required. Retrieval scores cannot make source declarations disappear.
    required = {c.clause_id for c in bindings.clauses if c.status != "NOT_APPLICABLE"}
    pending = (
        [scope]
        if scope in by_id
        else [key for key, entry in by_id.items() if entry.binding.table_id == scope]
    )
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        entry = by_id.get(key)
        if entry is None:
            raise ValueError("missing binding dependency")
        required.update(entry.clause_ids)
        pending.extend(entry.binding.dependencies)
    return required


def _literal_proof(binding: Binding, bindings: SemanticBindings, declaration: str) -> bool:
    """Require a current, exact source span; a proposed acceptance flag is not proof."""
    if (
        binding.profile_digest not in bindings.profile_digests
        or binding.table_id not in bindings.table_ids
    ):
        return False
    from datamate.preparation.native_relations import has_native_proof

    if has_native_proof(binding, bindings):
        return True
    source_profiles = {
        table_id: (digest, source, source_hash)
        for table_id, digest, source, source_hash in bindings.profile_sources
    }
    profile = source_profiles.get(binding.table_id)
    if profile is None or profile[0] != binding.profile_digest:
        return False
    clauses = {clause.clause_id: clause for clause in bindings.clauses}
    for evidence in binding.evidence:
        if not evidence.locator.startswith("clause:"):
            continue
        clause = clauses.get(evidence.locator[7:])
        if clause is None or clause.status == "CONFLICT":
            continue
        if (
            evidence.source.startswith("data/")
            and evidence.source.split("/")[:2] != profile[1].split("/")[:2]
        ):
            continue
        if (
            evidence.source == clause.source_document.source
            and evidence.sha256 == clause.source_document.sha256
            and evidence.line_start == clause.line_start
            and evidence.line_end == clause.line_end
            and evidence.quote_sha256 == clause.quote_sha256
            and clause.quote.strip() == declaration
        ):
            return True
    return False


def _entity_node(value: str) -> tuple[str, str] | None:
    namespace, separator, native_code = value.partition(":")
    if not separator or not namespace or not native_code or native_code != native_code.strip():
        return None
    return namespace, native_code


def accept_entity_links(
    proposals: Sequence[Binding], bindings: SemanticBindings
) -> EntityResolution:
    """Union source-mapped keys while retaining native-code contradictions."""
    components: dict[tuple[str, str], set[tuple[str, str]]] = {}
    accepted: list[Binding] = []
    aliases: list[tuple[str, str, str]] = []
    conflicts: list[Binding] = []
    unresolved: list[Binding] = []
    for proposal in proposals:
        target = proposal.entity_target
        scope = proposal.entity_scope
        relation = proposal.entity_relation
        if proposal.kind != "entity" or not target or not scope or not relation:
            unresolved.append(proposal)
            continue
        if relation == "relation_key":
            unresolved.append(proposal)
            continue
        label = {"alias": "entity_alias", "explicit_map": "entity_map"}.get(relation, relation)
        declaration = f"{label}[{scope}]: {proposal.value} = {target}"
        if not _literal_proof(proposal, bindings, declaration):
            unresolved.append(proposal)
            continue
        if relation == "alias":
            if _entity_node(proposal.value) is None:
                unresolved.append(proposal)
            else:
                aliases.append((scope, proposal.value, target))
            continue
        left = _entity_node(proposal.value)
        right = _entity_node(target)
        if left is None or right is None:
            unresolved.append(proposal)
            continue
        if relation == "trusted_code" and left[1] != right[1]:
            conflicts.append(proposal)
            continue
        combined = {(scope, proposal.value), (scope, target)}
        for key in ((scope, proposal.value), (scope, target)):
            combined.update(components.get(key, ()))
        native_by_namespace: dict[str, set[str]] = {}
        for _, key in combined:
            node = _entity_node(key)
            assert node is not None
            native_by_namespace.setdefault(node[0], set()).add(node[1])
        if any(len(codes) > 1 for codes in native_by_namespace.values()):
            conflicts.append(proposal)
            continue
        for key in combined:
            components[key] = combined
        accepted.append(proposal)
    distinct = {
        (next(iter(component))[0], tuple(sorted(key for _, key in component)))
        for component in components.values()
    }
    aliases_by_name: dict[tuple[str, str], set[tuple[tuple[str, str], ...]]] = {}
    for scope, key, name in aliases:
        component = tuple(sorted(components.get((scope, key), {(scope, key)})))
        aliases_by_name.setdefault((scope, name), set()).add(component)
    ambiguous_aliases = tuple(
        sorted(scope_name for scope_name, keys in aliases_by_name.items() if len(keys) > 1)
    )
    return EntityResolution(
        tuple(accepted),
        tuple(sorted(distinct)),
        tuple(aliases),
        ambiguous_aliases,
        tuple(conflicts),
        tuple(unresolved),
    )


def _unit_declaration(proposal: Binding) -> str | None:
    if proposal.kind != "unit" or not proposal.unit_product:
        return None
    if proposal.unit_base is True and proposal.unit_target is None and proposal.unit_factor is None:
        return f"unit_base[product={proposal.unit_product}]: {proposal.value}"
    if proposal.unit_base is False and proposal.unit_target and proposal.unit_factor:
        return (
            f"unit_factor[product={proposal.unit_product}]: {proposal.value} = "
            f"{proposal.unit_factor} {proposal.unit_target}"
        )
    return None


def resolve_units(bindings: SemanticBindings, *, product_id: str, unit: str) -> UnitResolution:
    """Resolve Decimal source factors through exact rational cycle checks."""
    proposals = (
        entry.binding
        for entry in bindings.entries
        if entry.binding.kind == "unit"
        and entry.binding.unit_product == product_id
        and entry.status != "CONFLICT"
    )
    bases: dict[str, Binding] = {}
    edges: list[tuple[str, str, Decimal, Binding]] = []
    for proposal in proposals:
        if (
            proposal.unit_base is True
            and proposal.unit_target is None
            and proposal.unit_factor is None
        ):
            declaration = f"unit_base[product={product_id}]: {proposal.value}"
            if _literal_proof(proposal, bindings, declaration):
                bases[proposal.value] = proposal
        elif proposal.unit_base is False and proposal.unit_target and proposal.unit_factor:
            declaration = (
                f"unit_factor[product={product_id}]: {proposal.value} = "
                f"{proposal.unit_factor} {proposal.unit_target}"
            )
            if _literal_proof(proposal, bindings, declaration):
                edges.append((proposal.value, proposal.unit_target, proposal.unit_factor, proposal))
    if len(bases) > 1:
        return UnitResolution("CONFLICT", None, None)
    if not bases:
        return UnitResolution("UNRESOLVED", None, None)
    base = next(iter(bases))
    graph: dict[str, list[tuple[str, Fraction, Binding]]] = {}
    for source, target, factor, proposal in edges:
        ratio = Fraction(factor)
        graph.setdefault(source, []).append((target, ratio, proposal))
        graph.setdefault(target, []).append((source, 1 / ratio, proposal))
    factors: dict[str, Fraction] = {}
    parents: dict[str, tuple[str, Binding]] = {}
    reachable_from_base: set[str] = set()
    for start in (base, *sorted(graph)):
        if start in factors:
            continue
        factors[start] = Fraction(1)
        queue = deque((start,))
        while queue:
            current = queue.popleft()
            for neighbor, ratio, proposal in graph.get(current, ()):
                candidate = factors[current] / ratio
                if neighbor in factors:
                    if factors[neighbor] != candidate:
                        return UnitResolution("CONFLICT", None, base)
                else:
                    factors[neighbor] = candidate
                    parents[neighbor] = (current, proposal)
                    queue.append(neighbor)
        if start == base:
            reachable_from_base = set(factors)
    if unit not in reachable_from_base:
        return UnitResolution("UNRESOLVED", None, base)
    path: list[Binding] = []
    current = unit
    while current != base:
        current, proposal = parents[current]
        path.append(proposal)
    evidence = tuple(
        ref for proposal in (bases[base], *reversed(path)) for ref in proposal.evidence
    )
    ratio = factors[unit]
    denominator = ratio.denominator
    powers = []
    for prime in (2, 5):
        count = 0
        while denominator % prime == 0:
            count += 1
            denominator //= prime
        powers.append(count)
    if denominator != 1 or max(powers) > 38:
        return UnitResolution("UNRESOLVED", None, base, evidence)
    scale = max(powers)
    coefficient = ratio.numerator * 2 ** (scale - powers[0]) * 5 ** (scale - powers[1])
    digits = tuple(int(char) for char in str(abs(coefficient)))
    if max(len(digits), scale) > 38:
        return UnitResolution("UNRESOLVED", None, base, evidence)
    factor = Decimal((int(coefficient < 0), digits, -scale))
    return UnitResolution("ACCEPTED", factor, base, evidence)


def reconcile_quantity(
    raw_quantity: Decimal, base_quantity: Decimal, factor: Decimal | None
) -> QuantityReconciliation:
    """Compare an independently preconverted value without altering either source value."""
    values = (
        (raw_quantity, base_quantity) if factor is None else (raw_quantity, base_quantity, factor)
    )
    if any(not value.is_finite() for value in values):
        raise ValueError("non-finite quantity operand")
    if any(
        max(
            len(v.as_tuple().digits) + max(v.as_tuple().exponent, 0), -min(v.as_tuple().exponent, 0)
        )
        > 38
        for v in values
    ):
        raise ValueError("unsupported quantity precision")
    with localcontext() as context:
        context.prec = max(1, sum(len(v.as_tuple().digits) for v in values))
        expected = raw_quantity * factor if factor is not None else None
    return QuantityReconciliation(
        raw_quantity,
        base_quantity,
        expected,
        expected != base_quantity if expected is not None else None,
    )
