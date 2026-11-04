from types import SimpleNamespace

from datamate.inference.telemetry import GPUMonitor


def test_gpu_sampling_reports_device_scope_and_observed_peak(monkeypatch):
    outputs = iter(["0, 200, 10", "0, 400, 30"])
    monkeypatch.setattr("subprocess.run", lambda *a, **k: SimpleNamespace(stdout=next(outputs)))
    monitor = GPUMonitor(enabled=True)
    monitor.observe()
    monitor.observe()
    report = monitor.snapshot()
    assert report["devices"]["0"]["sampled_peak_used_mib"] == 400
    assert report["devices"]["0"]["mean_gpu_utilization_percent"] == 20
    assert "other processes" in report["scope"]


def test_remote_monitor_does_not_query_gpu(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("GPU must not be queried for remote inference")

    monkeypatch.setattr("subprocess.run", forbidden)
    with GPUMonitor(enabled=False) as monitor:
        monitor.observe()
    assert monitor.snapshot()["devices"] == {}
