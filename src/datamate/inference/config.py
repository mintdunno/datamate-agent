"""Configuration-driven model settings; credentials never enter profiles or prompts."""

import json
import os
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, model_validator

from datamate.contracts.base import ContractModel, NonblankString


class ResourceUnavailable(ValueError):
    """Safe resource preflight diagnostic containing no request/source payloads."""


class ModelProfile(ContractModel):
    name: NonblankString
    model_id: NonblankString
    base_url: NonblankString
    api_key_env: str = "LLM_API_KEY"
    revision: str | None = None
    runtime: Literal["nim", "vllm", "sglang", "llama.cpp", "compatible"] = "compatible"
    context_window: int = Field(default=32768, ge=2048, le=1048576)
    input_byte_limit: int = Field(default=24000, ge=1000)
    max_tokens: int = Field(default=1024, ge=1, le=4096)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    question_token_budget: int = Field(default=24000, ge=1024)
    tool_call_limit: int = Field(default=16, ge=1, le=64)
    sql_row_limit: int = Field(default=40, ge=1, le=500)
    sql_byte_limit: int = Field(default=12000, ge=256, le=64000)
    sql_timeout: float = Field(default=15, gt=0, le=60)
    temperature: float | None = None
    top_p: float | None = None
    extra_body: dict = Field(default_factory=dict)
    native_tools: bool = True
    streaming: bool = False
    supports_streaming: bool = False
    force_output_tool: bool = False
    strict_tool_schema: bool = False
    connect_timeout_seconds: float = Field(default=15, gt=0, le=3600)
    read_timeout_seconds: float = Field(default=300, gt=0, le=3600)
    write_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    pool_timeout_seconds: float = Field(default=10, gt=0, le=3600)
    request_deadline_seconds: float = Field(default=360, gt=0, le=7200)
    quantization: str | None = None
    minimum_vram_gib: float | None = Field(default=None, gt=0)
    dtype: Literal["auto", "bfloat16", "float16", "float32"] = "auto"
    model_path: str | None = None
    cache_dir: str | None = None
    chat_template: str | None = None
    tensor_parallel_size: int = Field(default=1, ge=1, le=8)
    gpu_memory_utilization: float = Field(default=0.8, gt=0, le=0.95)
    supported_sampling: list[str] = Field(default_factory=lambda: ["temperature", "top_p"])
    supported_extra_body: list[str] = Field(default_factory=lambda: ["chat_template_kwargs"])
    tool_parser: str | None = None
    reasoning_parser: str | None = None
    qualification: str = "unverified"
    eligibility: str = "unverified"

    @model_validator(mode="after")
    def supported_settings(self):
        if self.streaming and not self.supports_streaming:
            raise ValueError("streaming requested but not declared supported by selected backend")
        if (self.force_output_tool or self.strict_tool_schema) and not self.native_tools:
            raise ValueError("structured output controls require declared native tool support")
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or (url.username or url.password or url.query or url.fragment)
        ):
            raise ValueError("base_url must be HTTP(S) without credentials, query, or fragment")
        if self.runtime == "nim" and url.scheme != "https":
            raise ValueError("remote NIM credentials require HTTPS")
        if self.runtime == "nim" and self.context_window > 65536:
            raise ValueError("NIM context exceeds documented conservative 64K endpoint limit")
        reserved = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "stream",
            "n",
            "max_tokens",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "response_format",
        }
        if reserved & self.extra_body.keys():
            raise ValueError("extra_body cannot override harness request controls")
        if self.extra_body.keys() - set(self.supported_extra_body):
            raise ValueError("extra body feature not declared supported by selected profile")
        for name in ("temperature", "top_p"):
            if getattr(self, name) is not None and name not in self.supported_sampling:
                raise ValueError("sampling parameter not supported by selected profile")
        if self.context_window <= self.max_tokens + 1024:
            raise ValueError("no input context remains after output and template reserves")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.top_p is not None and not 0 < self.top_p <= 1:
            raise ValueError("top_p must be in (0, 1]")
        return self


def load_profile(path: Path) -> ModelProfile:
    return ModelProfile.model_validate_json(path.read_text(encoding="utf-8"))


def credentials(profile: ModelProfile, env_file: Path | None = None) -> str:
    values = dotenv_values(env_file) if env_file and env_file.is_file() else {}
    key = os.environ.get(profile.api_key_env) or values.get(profile.api_key_env)
    if not key and profile.runtime == "nim":
        raise ValueError("missing NIM credential")
    return key or "local-no-auth"


def is_local_backend(profile: ModelProfile) -> bool:
    return profile.runtime in {"vllm", "sglang", "llama.cpp"} and urlsplit(
        profile.base_url
    ).hostname in {"localhost", "127.0.0.1", "::1"}


def gpu_preflight(profile: ModelProfile, *, force_local: bool = False) -> dict:
    """No loading/downloading; report measured device capacity, never infer remote VRAM."""
    if not force_local and not is_local_backend(profile):
        return {"gpu": None, "vram_gib": None, "status": "remote_or_unmanaged"}
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ResourceUnavailable(
            "GPU telemetry unavailable; local qualification required"
        ) from error
    devices = []
    for line in result.stdout.strip().splitlines():
        name, memory, free = line.rsplit(",", 2)
        devices.append(
            {
                "name": name.strip(),
                "vram_gib": float(memory) / 1024,
                "free_vram_gib": float(free) / 1024,
            }
        )
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible is not None:
        try:
            indices = [int(index) for index in visible.split(",") if index.strip()]
            if len(set(indices)) != len(indices) or any(index < 0 for index in indices):
                raise ValueError("invalid visible GPU indices")
            devices = [devices[index] for index in indices]
        except (ValueError, IndexError) as error:
            raise ResourceUnavailable(
                "CUDA_VISIBLE_DEVICES requires valid telemetry indices"
            ) from error
    count = profile.tensor_parallel_size
    capacities = [d["vram_gib"] for d in devices]
    required = (profile.minimum_vram_gib or 0) / count
    usable = [
        min(d["free_vram_gib"], d["vram_gib"] * profile.gpu_memory_utilization)
        for d in devices[:count]
    ]
    if len(capacities) < count or min(usable, default=0) < required:
        available = capacities[:count]
        raise ResourceUnavailable(
            f"insufficient GPU capacity: need {count} device(s), estimated "
            f"{required:.1f} GiB each; capacity {available}, free/utilization budgets {usable}"
        )
    return {
        "devices": devices,
        "tensor_parallel_size": count,
        "status": "capacity_only_not_model_qualified",
    }


def profile_json(profile: ModelProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), sort_keys=True)
