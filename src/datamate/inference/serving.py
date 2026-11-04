"""Build explicit vLLM argv without loading models or invoking processes."""

import re

from datamate.inference.config import ModelProfile


def serving_command(
    profile: ModelProfile,
    *,
    python_executable: str = "python",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> list[str]:
    """Return a pinned server plan; callers own launch and download permissions."""
    if profile.runtime != "vllm":
        raise ValueError(f"unsupported serving runtime: {profile.runtime}")
    if not profile.revision or not re.fullmatch(r"[0-9a-f]{40}", profile.revision):
        raise ValueError("vLLM serving requires a pinned 40-character revision")
    if profile.quantization in {"bf16", "bfloat16", "fp16", "float16", "fp32", "float32"}:
        raise ValueError("precision belongs in dtype; quantization must name an actual quantizer")
    if profile.native_tools and not profile.tool_parser:
        raise ValueError("native tool serving requires an explicit tool_parser")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    model_path = getattr(profile, "model_path", None)
    command = [
        python_executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        str(model_path) if model_path is not None else profile.model_id,
        "--served-model-name",
        profile.model_id,
        "--revision",
        profile.revision,
        "--tokenizer-revision",
        profile.revision,
        "--host",
        host,
        "--port",
        str(port),
        "--max-model-len",
        str(profile.context_window),
        "--dtype",
        str(getattr(profile, "dtype", "auto")),
        "--tensor-parallel-size",
        str(getattr(profile, "tensor_parallel_size", 1)),
        "--gpu-memory-utilization",
        str(getattr(profile, "gpu_memory_utilization", 0.8)),
    ]
    if profile.quantization:
        command.extend(["--quantization", profile.quantization])
    if profile.native_tools:
        command.extend(["--enable-auto-tool-choice", "--tool-call-parser", profile.tool_parser])
    if profile.reasoning_parser:
        command.extend(["--reasoning-parser", profile.reasoning_parser])
    for field, flag in (("cache_dir", "--download-dir"), ("chat_template", "--chat-template")):
        value = getattr(profile, field, None)
        if value is not None:
            command.extend([flag, str(value)])
    return command
