"""Conservative, reversible Unicode candidates without modifying raw evidence."""

import json
from hashlib import sha256
from typing import Literal

import ftfy
from ftfy.badness import badness
from pydantic import Field

from datamate.contracts.base import ContractModel

# Only whole-string legacy-byte -> UTF-8 conversions are approved. ftfy's
# "sloppy" codec is a proposal label; replay always uses Python's strict cp1252.
_ENCODINGS = {"latin-1": "latin-1", "sloppy-windows-1252": "cp1252", "cp1252": "cp1252"}
_CODECS = ("latin-1", "cp1252")
_ALGORITHM_VERSION = "reversible-text-v1"


class TextIntegrityPolicy(ContractModel):
    enabled: bool = True
    max_chars: int = Field(default=8192, ge=1, le=65536)
    max_layers: int = Field(default=3, ge=1, le=3)
    auto_repair: bool = True

    @property
    def digest(self) -> str:
        return _digest(
            {
                "policy": self.model_dump(mode="json"),
                "ftfy": ftfy.__version__,
                "algorithm": _ALGORITHM_VERSION,
            }
        )


class TextDecision(ContractModel):
    before: str
    candidate: str | None
    status: Literal["UNCHANGED", "ACCEPTED", "UNRESOLVED", "LOSSY"]
    steps: tuple[tuple[str, str], ...] = ()
    reason: str
    protected: bool = False
    policy_digest: str

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))


def _digest(value: object) -> str:
    # ASCII escaping also hashes malformed Unicode deterministically for diagnostics.
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(serialized.encode("ascii")).hexdigest()


def inspect_text(
    text: str, policy: TextIntegrityPolicy | None = None, *, protected: bool = False
) -> TextDecision:
    """Propose strict reversible repairs; protected candidates require external proof.

    This intentionally refuses mixed clean/damaged fragments, unsupported codecs,
    and evidence that requires lossy, HTML, control-character or Unicode cleanup.
    Heuristic mojibake evidence cannot prove what an author intended.
    """
    policy = policy or TextIntegrityPolicy()
    base = TextDecision(
        before=text,
        candidate=None,
        status="UNCHANGED",
        reason="No strong mojibake evidence.",
        protected=protected,
        policy_digest=policy.digest,
    )
    if "\ufffd" in text:
        return base.model_copy(
            update={"status": "LOSSY", "reason": "Replacement character present."}
        )
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        return base.model_copy(
            update={"status": "UNRESOLVED", "reason": "Unpaired surrogate present."}
        )
    if len(text) > policy.max_chars:
        return base.model_copy(
            update={"status": "UNRESOLVED", "reason": "Text exceeds inspection bound."}
        )
    if not badness(text):
        return base
    if not policy.enabled:
        return base.model_copy(
            update={"status": "UNRESOLVED", "reason": "Text inspection is disabled."}
        )

    config = ftfy.TextFixerConfig(
        restore_byte_a0=False,
        replace_lossy_sequences=False,
        decode_inconsistent_utf8=False,
        fix_c1_controls=False,
        normalization=None,
        max_decode_length=policy.max_chars,
    )
    proposed = ftfy.fix_encoding_and_explain(text, config)
    steps = tuple(tuple(step) for step in (proposed.explanation or ()))
    candidate = proposed.text if proposed.text != text else None
    unresolved = base.model_copy(
        update={
            "candidate": candidate,
            "steps": steps,
            "status": "UNRESOLVED",
            "reason": "No uniquely reversible whole-string candidate within policy.",
        }
    )
    if candidate is None or len(steps) > policy.max_layers * 2:
        return unresolved
    if not _replays_exactly(text, candidate, steps):
        return unresolved
    if badness(candidate) or _clean_candidates(text, policy.max_layers) != {candidate}:
        return unresolved
    repeated = ftfy.fix_encoding_and_explain(candidate, config)
    if repeated.text != candidate or repeated.explanation:
        return unresolved
    if protected or not policy.auto_repair:
        return unresolved.model_copy(
            update={
                "reason": (
                    "Protected candidate requires complete external identity proof."
                    if protected
                    else "Automatic repair is disabled."
                )
            }
        )
    return unresolved.model_copy(
        update={"status": "ACCEPTED", "reason": "Unique strict reversible encoding repair."}
    )


def _replays_exactly(before: str, candidate: str, steps: tuple[tuple[str, str], ...]) -> bool:
    if not steps or len(steps) % 2:
        return False
    value = before
    codecs: list[str] = []
    try:
        for index in range(0, len(steps), 2):
            encode, decode = steps[index : index + 2]
            if encode[0] != "encode" or encode[1] not in _ENCODINGS:
                return False
            if decode != ("decode", "utf-8"):
                return False
            codec = _ENCODINGS[encode[1]]
            codecs.append(codec)
            value = value.encode(codec, errors="strict").decode("utf-8", errors="strict")
        if value != candidate:
            return False
        for codec in reversed(codecs):
            value = value.encode("utf-8", errors="strict").decode(codec, errors="strict")
    except UnicodeError:
        return False
    return value == before


def _clean_candidates(text: str, max_layers: int) -> set[str]:
    """Exhaust the fixed two-codec family, independent of ftfy's chosen plan."""
    frontier = {text}
    seen = {text}
    clean: set[str] = set()
    for _ in range(max_layers):
        next_frontier: set[str] = set()
        for value in sorted(frontier):
            for codec in _CODECS:
                try:
                    fixed = value.encode(codec, errors="strict").decode("utf-8", errors="strict")
                    reversed_value = fixed.encode("utf-8").decode(codec, errors="strict")
                except UnicodeError:
                    continue
                if reversed_value != value or fixed in seen:
                    continue
                seen.add(fixed)
                if "\ufffd" in fixed or badness(fixed) >= badness(value):
                    continue
                if badness(fixed):
                    next_frontier.add(fixed)
                else:
                    clean.add(fixed)
        frontier = next_frontier
    return clean
