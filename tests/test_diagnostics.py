from pydantic_ai import ModelRetry

from datamate.runtime.diagnostics import failure_code, safe_tool_feedback


def test_feedback_preserves_safe_messages_and_never_leaks_unknown():
    assert safe_tool_feedback(ValueError("invalid SQL")) == "invalid SQL"
    assert (
        safe_tool_feedback(
            ValueError("source quantity aggregate requires recorded-unit grouping/filter")
        )
        == "source quantity aggregate requires recorded-unit grouping/filter"
    )
    assert (
        safe_tool_feedback(ValueError("column outside qualified projection"))
        == "column outside qualified projection"
    )
    assert safe_tool_feedback(ValueError("invalid rule scope")) == "invalid rule scope"
    assert safe_tool_feedback(ValueError("unsupported SQL function: STR_SPLIT")) == (
        "unsupported SQL function: STR_SPLIT"
    )
    for message in ("C:/private/secret-key", "unsupported SQL function: SECRET/KEY", "x" * 500):
        feedback = safe_tool_feedback(ValueError(message))
        assert feedback == "invalid tool request"
        assert len(feedback) <= 160


def test_failure_codes_follow_causes_and_classify_known_retries():
    class BudgetExceeded(Exception):
        pass

    wrapped = RuntimeError("not safe")
    wrapped.__cause__ = BudgetExceeded("secret")
    assert failure_code(wrapped) == "BudgetExceeded"
    assert failure_code(
        ModelRetry("A successful non-truncated business data query is required.")
    ) == ("business_data_query_required")
    assert failure_code(
        ModelRetry("The exact scalar answer must be present in business query results.")
    ) == ("exact_scalar_answer_required")
    assert failure_code(
        ModelRetry("Read applicable split rulebook AND business README rules first.")
    ) == ("split_business_rules_required")
    assert failure_code(ModelRetry("private message")) == "ModelRetry"


def test_failure_code_status_and_bounded_cycle():
    error = RuntimeError("secret")
    error.status_code = 429
    assert failure_code(error) == "RuntimeError:429"
    error.__cause__ = error
    assert failure_code(error) == "RuntimeError:429"


def test_retry_exhaustion_classifies_only_known_shapes():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    assert (
        failure_code(UnexpectedModelBehavior("Exceeded maximum output retries (2)"))
        == "output_retry_exhausted"
    )
    assert (
        failure_code(
            UnexpectedModelBehavior(
                "Tool 'execute_sql' exceeded max retries count of 1. "
                "Consider raising the retry limit"
            )
        )
        == "tool_retry_exhausted"
    )
    assert failure_code(UnexpectedModelBehavior("private secret")) == "UnexpectedModelBehavior"
