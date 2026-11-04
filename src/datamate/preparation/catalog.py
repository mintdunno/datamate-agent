"""Source-linked rule catalogs retain text as evidence, never executable instructions."""

import hashlib
import re

from datamate.preparation.models import SourceFile


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
