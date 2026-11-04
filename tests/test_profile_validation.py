from types import SimpleNamespace

import pytest

from datamate.inference.config import ModelProfile, ResourceUnavailable, gpu_preflight


def profile(**updates):
    return ModelProfile(
        name="test", model_id="model", base_url="http://localhost:8000/v1", **updates
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"temperature": 1.0, "supported_sampling": []},
        {"extra_body": {"unqualified_feature": True}},
        {"base_url": "http://user:password@localhost/v1"},
        {"minimum_vram_gib": -1.0},
    ],
)
def test_profile_rejects_unsupported_features_and_secret_urls(updates):
    data = {"name": "test", "model_id": "model", "base_url": "http://localhost/v1", **updates}
    with pytest.raises(ValueError):
        ModelProfile.model_validate(data)


def test_resource_preflight_is_explicit_and_uses_selected_devices(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout="small,8192,7000\nbig,32768,32000\n"),
    )
    selected = profile(runtime="vllm", minimum_vram_gib=28.0, gpu_memory_utilization=0.9)
    with pytest.raises(ResourceUnavailable, match="28.0"):
        gpu_preflight(selected)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert gpu_preflight(selected)["devices"][0]["vram_gib"] == 32
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,1")
    with pytest.raises(ResourceUnavailable):
        gpu_preflight(selected)


def test_native_context_configuration_is_not_globally_nim_limited():
    assert profile(context_window=262144).context_window == 262144
    with pytest.raises(ValueError):
        ModelProfile(
            name="nim",
            model_id="nim",
            base_url="https://example.test/v1",
            runtime="nim",
            context_window=66000,
        )
