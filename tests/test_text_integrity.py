"""Conservative Unicode repair keeps raw evidence and refuses ambiguous cleanup."""

import pytest
from pydantic import ValidationError

from datamate.preparation.text_integrity import (
    TextDecision,
    TextIntegrityPolicy,
    inspect_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "literal ASCII &amp; \\u1ebf ?",
        "Tiếng Việt: Ân, Ãn, € £ © – —",
        "a\u0306\u0301",
        "<b>Text</b>\r\n\t１２ ﬁ ‘quoted’",
    ],
)
def test_preserves_clean_unicode_without_cleanup(text: str) -> None:
    decision = inspect_text(text)
    assert decision.before == text
    assert decision.candidate is None
    assert decision.status == "UNCHANGED"
    assert decision.steps == ()


@pytest.mark.parametrize(
    ("before", "expected", "layers"),
    [
        ("FranÃ§ois", "François", 1),
        ("Tiáº¿ng Viá»‡t", "Tiếng Việt", 1),
        ("FranÃƒÂ§ois", "François", 2),
        ("a Ã¢â‚¬â€œ b", "a – b", 2),
    ],
)
def test_accepts_only_consistent_reversible_candidates(
    before: str, expected: str, layers: int
) -> None:
    decision = inspect_text(before)
    assert decision.before == before
    assert decision.candidate == expected
    assert decision.status == "ACCEPTED"
    assert len(decision.steps) == layers * 2
    assert inspect_text(expected).status == "UNCHANGED"


def test_mixed_clean_and_mojibake_does_not_rewrite_fragments() -> None:
    text = "Tiếng Việt, FranÃ§ois"
    decision = inspect_text(text)
    assert decision.status == "UNRESOLVED"
    assert decision.before == text


def test_protected_value_still_gets_candidate_without_acceptance() -> None:
    decision = inspect_text("FranÃ§ois", protected=True)
    assert decision.candidate == "François"
    assert decision.status == "UNRESOLVED"
    assert decision.protected is True


def test_disabled_auto_repair_still_generates_candidate() -> None:
    decision = inspect_text("FranÃ§ois", TextIntegrityPolicy(auto_repair=False))
    assert decision.candidate == "François"
    assert decision.status == "UNRESOLVED"


def test_replacement_character_is_lossy_and_question_mark_is_not() -> None:
    decision = inspect_text("Ti\ufffdng Việt?")
    assert decision.status == "LOSSY"
    assert decision.candidate is None
    assert inspect_text("Question?").status == "UNCHANGED"


@pytest.mark.parametrize("text", ["\ud800", "\udfff"])
def test_unpaired_surrogate_is_unresolved_not_silently_replaced(text: str) -> None:
    decision = inspect_text(text)
    assert decision.status == "UNRESOLVED"
    assert decision.candidate is None
    assert len(decision.digest) == 64


def test_character_limit_prevents_candidate_generation() -> None:
    decision = inspect_text("FranÃ§ois", TextIntegrityPolicy(max_chars=4))
    assert decision.status == "UNRESOLVED"
    assert decision.candidate is None


def test_layer_limit_refuses_multilayer_candidate() -> None:
    decision = inspect_text("FranÃƒÂ§ois", TextIntegrityPolicy(max_layers=1))
    assert decision.status == "UNRESOLVED"


def test_disabled_policy_does_not_silently_approve_suspicious_text() -> None:
    decision = inspect_text("FranÃ§ois", TextIntegrityPolicy(enabled=False))
    assert decision.status == "UNRESOLVED"
    assert decision.candidate is None


def test_policy_bounds_are_validated() -> None:
    for values in ({"max_chars": 0}, {"max_layers": 0}, {"max_layers": 4}):
        with pytest.raises(ValidationError):
            TextIntegrityPolicy(**values)


def test_decision_round_trip_and_digest_bind_policy_and_protection() -> None:
    decision = inspect_text("FranÃ§ois")
    restored = TextDecision.model_validate_json(decision.model_dump_json())
    assert restored == decision
    assert restored.digest == decision.digest
    assert inspect_text("FranÃ§ois").digest == decision.digest
    assert inspect_text("FranÃ§ois", protected=True).digest != decision.digest
    assert inspect_text("FranÃ§ois", TextIntegrityPolicy(max_chars=100)).digest != decision.digest


def test_unknown_candidate_steps_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from ftfy import ExplainedText

    monkeypatch.setattr(
        "datamate.preparation.text_integrity.ftfy.fix_encoding_and_explain",
        lambda *args, **kwargs: ExplainedText("François", [("apply", "unescape_html")]),
    )
    decision = inspect_text("FranÃ§ois")
    assert decision.status == "UNRESOLVED"
    assert decision.candidate == "François"


def test_candidate_must_match_replayed_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    from ftfy import ExplainedText

    monkeypatch.setattr(
        "datamate.preparation.text_integrity.ftfy.fix_encoding_and_explain",
        lambda *args, **kwargs: ExplainedText(
            "Invented", [("encode", "latin-1"), ("decode", "utf-8")]
        ),
    )
    assert inspect_text("FranÃ§ois").status == "UNRESOLVED"


def test_lossy_byte_restore_and_partial_decoding_are_disabled() -> None:
    for before in ("FranÃ ois", "Tiếng Việt FranÃ§ois", "broken â�"):
        assert inspect_text(before).status != "ACCEPTED"
