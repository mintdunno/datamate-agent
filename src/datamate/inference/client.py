"""One OpenAI-compatible model factory; no backend or generation-mode fallback."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic import ValidationError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from datamate.inference.budget import (
    BudgetExceeded,
    BudgetTransport,
    RequestBudget,
    RequestDeadlineExceeded,
)
from datamate.inference.config import ModelProfile, credentials


class ContextBudgetExceeded(ValueError):
    """Refuse a request before it reaches the provider or consumes request budget."""


def model_settings(profile: ModelProfile) -> dict:
    return {
        key: value
        for key, value in {
            "max_tokens": profile.max_tokens,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "extra_body": profile.extra_body,
            "max_retries": 0,
            "openai_continuous_usage_stats": (
                True if getattr(profile, "stream_usage_mode", "final") == "cumulative" else None
            ),
        }.items()
        if value is not None
    }


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, profile: ModelProfile):
        self.inner, self.profile = inner, profile

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        if getattr(self.profile, "stream_usage_mode", "final") == "cumulative":
            payload = json.loads(body)
            options = payload.get("stream_options")
            if isinstance(options, dict) and "continuous_usage_stats" in options:
                # The SDK flag also requests a vendor extension. This backend already
                # emits cumulative counters; use its supported wire protocol unchanged.
                options.pop("continuous_usage_stats")
                body = json.dumps(
                    payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode()
                headers = request.headers.copy()
                headers["content-length"] = str(len(body))
                request = httpx.Request(
                    request.method,
                    request.url,
                    headers=headers,
                    content=body,
                    extensions=request.extensions,
                )
        # UTF-8 bytes are a conservative tokenizer-independent proxy, including tool schemas.
        # Retain 1K tokens for template/control overhead; never silently truncate evidence.
        limit = min(
            self.profile.input_byte_limit,
            self.profile.context_window - self.profile.max_tokens - 1024,
        )
        if len(body) > limit:
            raise ContextBudgetExceeded("request exceeds conservative context byte budget")
        response = await self.inner.handle_async_request(request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


@asynccontextmanager
async def inference_model(
    profile: ModelProfile,
    budget: RequestBudget,
    *,
    env_file: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float | None = None,
    max_retries: int = 2,
):
    if not profile.native_tools:
        raise ValueError("selected profile has no qualified native tool-call support")
    if profile.runtime == "nim" and (budget.rpm > 40 or budget.daily_limit > 10000):
        raise ValueError("configured budget exceeds NIM provider limits")
    inner = BudgetTransport(
        transport or httpx.AsyncHTTPTransport(retries=0),
        budget,
        max_retries=max_retries,
        deadline_seconds=profile.request_deadline_seconds,
    )
    timeouts = (
        httpx.Timeout(timeout)
        if timeout is not None
        else httpx.Timeout(
            connect=profile.connect_timeout_seconds,
            read=profile.read_timeout_seconds,
            write=profile.write_timeout_seconds,
            pool=profile.pool_timeout_seconds,
        )
    )
    async with httpx.AsyncClient(
        transport=GuardedTransport(inner, profile),
        timeout=timeouts,
        follow_redirects=False,
    ) as http_client:
        client = AsyncOpenAI(
            api_key=credentials(profile, env_file),
            base_url=profile.base_url,
            http_client=http_client,
            max_retries=0,
            timeout=timeouts,
        )
        provider = OpenAIProvider(openai_client=client)
        capabilities = OpenAIModelProfile(
            supports_tools=True,
            supports_json_schema_output=False,
            supports_json_object_output=False,
            openai_supports_strict_tool_definition=profile.strict_tool_schema,
            supports_forced_tool_choice=profile.force_output_tool,
            openai_chat_supports_max_completion_tokens=profile.max_tokens_field
            == "max_completion_tokens",
            openai_chat_send_back_thinking_parts=False,
        )
        yield OpenAIChatModel(
            profile.model_id,
            provider=provider,
            profile=capabilities,
            settings=model_settings(profile),
        )


def inference_failure(error: BaseException, request_trace: dict | None = None) -> dict:
    """Classify safe structured causes; never serialize exception payloads or text."""
    trace = request_trace or {}
    causes = []
    pending = [error]
    seen = set()
    while pending and len(causes) < 12:
        item = pending.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        causes.append(item)
        pending.extend(c for c in (item.__cause__, item.__context__) if c is not None)
    diagnostic = {
        "code": "INFERENCE_ERROR",
        "phase": "unknown",
        "error_type": type(error).__name__,
        "cause_types": list(dict.fromkeys(type(c).__name__ for c in causes)),
        "provider_queue_or_reasoning": "unknown",
    }

    def result(code, phase):
        diagnostic.update(code=code, phase=phase)
        return diagnostic

    if any(isinstance(c, BudgetExceeded) for c in causes):
        return result("BUDGET_EXCEEDED", "budget")
    if any(isinstance(c, ContextBudgetExceeded) for c in causes):
        return result("CONTEXT_BUDGET_EXCEEDED", "context")
    if any(type(c).__name__ == "UsageLimitExceeded" for c in causes):
        if trace.get("has_content") and not trace.get("has_tool_calls"):
            diagnostic["output_diagnostic"] = "OUTPUT_TOOL_NOT_CALLED"
        return result("USAGE_LIMIT_EXCEEDED", "agent")
    if any(isinstance(c, RequestDeadlineExceeded) for c in causes):
        return result("DEADLINE_EXCEEDED", "transport")
    for kind, code in (
        (httpx.ConnectTimeout, "TIMEOUT_CONNECT"),
        (httpx.ReadTimeout, "TIMEOUT_READ"),
        (httpx.WriteTimeout, "TIMEOUT_WRITE"),
        (httpx.PoolTimeout, "TIMEOUT_POOL"),
    ):
        if any(isinstance(c, kind) for c in causes):
            return result(code, "transport")
    if any(
        type(c).__name__ in {"ModelTimeoutError", "APITimeoutError", "TimeoutError"} for c in causes
    ):
        return result("TIMEOUT_UNCLASSIFIED", "transport")
    status = next(
        (c.status_code for c in causes if type(getattr(c, "status_code", None)) is int),
        trace.get("http_status"),
    )
    if type(status) is int and status >= 400:
        diagnostic["http_status"] = status
        return result("PROVIDER_RATE_LIMIT" if status == 429 else "PROVIDER_HTTP_ERROR", "provider")
    if trace.get("stream_requested") and not trace.get("stream_received"):
        return result("STREAM_PROTOCOL_MISMATCH", "provider")
    if trace.get("provider_stream_error"):
        return result("PROVIDER_STREAM_ERROR", "provider")
    if trace.get("finish_reason") == "length" or any(
        type(c).__name__ == "IncompleteToolCall" for c in causes
    ):
        return result("OUTPUT_TRUNCATED", "output")
    if trace.get("finish_reason") == "content_filter":
        return result("OUTPUT_FILTERED", "output")
    fields = {
        "mappings",
        "column_id",
        "role",
        "clause_ids",
        "steps",
        "operation",
        "name",
        "binding_ids",
        "evidence",
        "source",
        "sha256",
        "locator",
        "line_start",
        "line_end",
        "quote_sha256",
        "bindings_digest",
        "profile_digests",
    }
    for cause in causes:
        if isinstance(cause, ValidationError):
            errors = []
            for item in cause.errors(include_input=False, include_context=False)[:8]:
                location = ".".join(
                    str(p) if type(p) is int or p in fields else "<extra_field>"
                    for p in item["loc"]
                )
                errors.append({"field": location, "type": item["type"]})
            diagnostic["validation_errors"] = errors
            code = (
                "OUTPUT_INVALID_JSON"
                if any(e["type"] == "json_invalid" for e in errors)
                else "OUTPUT_SCHEMA_INVALID"
            )
            return result(code, "output")
    if trace.get("has_content") and not trace.get("has_tool_calls"):
        return result("OUTPUT_TOOL_NOT_CALLED", "output")
    if trace.get("failure_code"):
        return result(trace["failure_code"], "transport")
    if any(isinstance(c, (httpx.NetworkError, httpx.RemoteProtocolError)) for c in causes):
        return result("NETWORK_ERROR", "transport")
    if any(type(c).__name__ in {"UnexpectedModelBehavior", "PlanFormatError"} for c in causes):
        return result("OUTPUT_VALIDATION_FAILED", "output")
    if any(isinstance(c, ValueError) for c in causes):
        return result("CONFIGURATION_OR_CONTEXT_ERROR", "configuration")
    return diagnostic
