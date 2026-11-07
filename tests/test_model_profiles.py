"""Profile contracts and a bootstrap that cannot launch jobs by default."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from datamate.inference.config import gpu_preflight, load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config" / "model_profiles"


def test_profiles_load_without_credentials_or_model_weights(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    paths = sorted(PROFILE_DIR.glob("*.json"))
    assert len(paths) == 5
    profiles = [load_profile(path) for path in paths]
    assert {profile.runtime for profile in profiles} == {"nim", "vllm", "compatible"}
    for profile in profiles:
        assert profile.native_tools
        assert profile.qualification == "unverified"
        if profile.runtime != "compatible":
            assert profile.context_window == 32768
            assert profile.input_byte_limit == 24000
            assert profile.max_tokens == 1024
        if profile.runtime == "vllm":
            assert profile.revision and len(profile.revision) == 40
            assert profile.minimum_vram_gib > 8
            assert profile.tool_parser and profile.reasoning_parser


def test_nim_preflight_does_not_inspect_local_gpu_or_read_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Remote preflight must not access local GPU or credentials")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    profile = load_profile(PROFILE_DIR / "nim_nemotron_3_5_bolt_e4b.json")
    assert gpu_preflight(profile)["status"] == "remote_or_unmanaged"


def test_bootstrap_default_only_plans_shared_cli_and_creates_no_outputs(tmp_path):
    output = tmp_path / "new-output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bootstrap_gpu.py"),
            "--repo-root",
            str(ROOT),
            "--profile",
            "config/model_profiles/qwen3_14b_vllm.json",
            "--suite",
            "synthetic.jsonl",
            "--split-root",
            "split",
            "--state-root",
            "state",
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["action"] == "plan"
    assert report["commands"]["setup"] == ["uv", "sync", "--frozen"]
    assert report["commands"]["preflight"][2:7] == [
        "--frozen",
        "python",
        "-m",
        "datamate",
        "preflight",
    ]
    assert report["commands"]["run"][6] == "run"
    assert not output.exists()


def test_bootstrap_rejects_incomplete_run_before_executing(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bootstrap_gpu.py"),
            "--repo-root",
            str(tmp_path),
            "--action",
            "run",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--suite" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_google_studio_gemma_profile_is_explicit_and_portable():
    profile = load_profile(PROFILE_DIR / "google_gemma_4_26b_a4b_it.json")
    assert profile.model_id == "gemma-4-26b-a4b-it"
    assert profile.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert profile.runtime == "compatible"
    assert profile.streaming and profile.supports_streaming
    assert not profile.force_output_tool
    assert not profile.strict_tool_schema
    assert profile.extra_body == {} and profile.temperature is None
    assert profile.input_byte_limit == 56000 and profile.context_window == 65536
