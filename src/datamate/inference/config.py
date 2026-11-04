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


class ModelProfile(ContractModel):
    name: NonblankString
    model_id: NonblankString
    base_url: NonblankString
    api_key_env: str = "LLM_API_KEY"
    revision: str | None = None
    runtime: Literal["nim", "vllm", "sglang", "llama.cpp", "compatible"] = "compatible"
    context_window: int = Field(default=32768, ge=2048, le=66000)
    input_byte_limit: int = Field(default=24000, ge=1000)
    max_tokens: int = Field(default=1024, ge=1, le=4096)
    temperature: float | None = None
    top_p: float | None = None
    extra_body: dict = Field(default_factory=dict)
    native_tools: bool = True
    quantization: str | None = None
    minimum_vram_gib: float | None = None
    tool_parser: str | None = None
    reasoning_parser: str | None = None
    qualification: str = "unverified"
    eligibility: str = "unverified"

    @model_validator(mode="after")
    def supported_settings(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or (url.username or url.password or url.query or url.fragment)
        ):
            raise ValueError("base_url must be HTTP(S) without credentials, query, or fragment")
        if self.runtime == "nim" and url.scheme != "https":
            raise ValueError("remote NIM credentials require HTTPS")
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


def gpu_preflight(profile: ModelProfile) -> dict:
    """No loading/downloading; report measured device capacity, never infer remote VRAM."""
    if profile.runtime in {"nim", "compatible"}:
        return {"gpu": None, "vram_gib": None, "status": "remote_or_unmanaged"}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("GPU telemetry unavailable; local qualification required") from error
    devices = []
    for line in result.stdout.strip().splitlines():
        name, memory = line.rsplit(",", 1)
        devices.append({"name": name.strip(), "vram_gib": float(memory) / 1024})
    if not devices or (
        profile.minimum_vram_gib and max(d["vram_gib"] for d in devices) < profile.minimum_vram_gib
    ):
        raise ValueError("insufficient VRAM for explicitly selected profile")
    return {"devices": devices, "status": "capacity_only_not_model_qualified"}


def profile_json(profile: ModelProfile) -> str:
    return json.dumps(profile.model_dump(mode="json"), sort_keys=True)
