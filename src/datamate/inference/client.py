"""One OpenAI-compatible model factory; no backend or generation-mode fallback."""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from datamate.inference.budget import BudgetTransport, RequestBudget
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
        }.items()
        if value is not None
    }


class GuardedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, profile: ModelProfile):
        self.inner, self.profile = inner, profile

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
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
