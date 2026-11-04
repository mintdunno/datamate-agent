"""Safe, bounded diagnostic categories for solver and tool failures."""

import re

_SAFE_MESSAGES = frozenset(
    {
        "SQL exceeds length limit",
        "invalid SQL",
        "only one SELECT query is permitted",
        "external or qualified table access denied",
        "unsupported table scope",
        "table outside current business",
        "non-query SQL denied",
        "query column metadata exceeds result budget",
        "unknown table",
        "SQL parameter budget exceeded",
        "invalid column or limit",
        "invalid search column, limit, or query length",
        "invalid rule page",
    }
)
_RETRY_REASONS = {
    "A successful non-truncated business data query is required.": "business_data_query_required",
    "The exact scalar answer must be present in business query results.": (
        "exact_scalar_answer_required"
    ),
    "Read applicable split rulebook AND business README rules first.": (
        "split_business_rules_required"
    ),
    "Use the exact question ID.": "question_id_mismatch",
}


def safe_tool_feedback(error: Exception) -> str:
    if isinstance(error, ValueError):
        message = str(error)
        if message in _SAFE_MESSAGES:
            return message
        if re.fullmatch(r"unsupported SQL function: [A-Z0-9_]{1,64}", message, re.ASCII):
            return message
    if isinstance(error, TimeoutError):
        return "tool request timed out"
    return "invalid tool request"


def failure_code(error: Exception) -> str:
    current = error
    seen: set[int] = set()
    retry_reason = None
    status_code = None
    for _ in range(8):
        if id(current) in seen:
            break
        seen.add(id(current))
        name = type(current).__name__
        if name in {"BudgetExceeded", "ContextBudgetExceeded", "UsageLimitExceeded"}:
            return name
        if name == "ModelRetry" and str(current) in _RETRY_REASONS:
            retry_reason = _RETRY_REASONS[str(current)]
        if name == "UnexpectedModelBehavior":
            message = str(current)
            if re.fullmatch(r"Exceeded maximum output retries \(\d+\)", message):
                retry_reason = retry_reason or "output_retry_exhausted"
            elif re.fullmatch(
                r"Tool '[A-Za-z0-9_]{1,80}' exceeded max retries count of \d+\. .*", message
            ):
                retry_reason = retry_reason or "tool_retry_exhausted"
        status = getattr(current, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            status_code = status
        if current.__cause__ is None:
            break
        current = current.__cause__
    if retry_reason:
        return retry_reason
    name = type(error).__name__
    return f"{name}:{status_code}" if status_code is not None else name
