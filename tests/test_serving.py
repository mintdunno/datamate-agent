"""Local serving plans preserve exact model identity without launching anything."""

import pytest

from datamate.inference.config import ModelProfile
from datamate.inference.serving import serving_command


def profile(**updates):
    base = ModelProfile(
        name="synthetic",
        model_id="owner/exact-model",
        base_url="http://127.0.0.1:8000/v1",
        runtime="vllm",
        revision="a" * 40,
        tool_parser="qwen3_xml",
        reasoning_parser="qwen3",
    )
    return base.model_copy(update=updates)


def test_serving_plan_pins_weights_tokenizer_and_parsers():
    assert serving_command(profile(), python_executable="/runtime/bin/python") == [
        "/runtime/bin/python",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        "owner/exact-model",
        "--served-model-name",
        "owner/exact-model",
        "--revision",
        "a" * 40,
        "--tokenizer-revision",
        "a" * 40,
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--max-model-len",
        "32768",
        "--dtype",
        "auto",
        "--tensor-parallel-size",
        "1",
        "--gpu-memory-utilization",
        "0.8",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_xml",
        "--reasoning-parser",
        "qwen3",
    ]


def test_local_snapshot_quantizer_and_paths_are_individual_arguments():
    command = serving_command(
        profile(
            model_path="/models/snapshot; inert",
            cache_dir="/cache dir",
            dtype="float16",
            tensor_parallel_size=2,
            gpu_memory_utilization=0.75,
            quantization="awq",
            chat_template="/templates/exact template.jinja",
        ),
        host="127.0.0.2",
        port=9000,
    )
    assert command[command.index("--model") + 1] == "/models/snapshot; inert"
    for flag, value in {
        "--served-model-name": "owner/exact-model",
        "--dtype": "float16",
        "--tensor-parallel-size": "2",
        "--gpu-memory-utilization": "0.75",
        "--quantization": "awq",
        "--download-dir": "/cache dir",
        "--chat-template": "/templates/exact template.jinja",
        "--port": "9000",
    }.items():
        assert command[command.index(flag) + 1] == value
    assert "--trust-remote-code" not in command


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"runtime": "nim"}, "unsupported"),
        ({"revision": None}, "revision"),
        ({"quantization": "bf16"}, "dtype"),
        ({"tool_parser": None}, "tool_parser"),
    ],
)
def test_invalid_serving_selection_fails_without_fallback(updates, message):
    with pytest.raises(ValueError, match=message):
        serving_command(profile(**updates))
