"""CPU preparation watchdogs use real child processes, never model endpoints."""

import importlib
import time
from pathlib import Path

import pytest


def job_success(workspace):
    (Path(workspace) / "result.txt").write_text("bounded")
    return {"records": 3}


def job_wait(workspace):
    time.sleep(5)
    return {}


def job_spill(workspace):
    (Path(workspace) / "spill.bin").write_bytes(b"x" * 4096)
    time.sleep(5)
    return {}


def job_fail(workspace):
    raise ValueError("synthetic failure")


def job_tree(workspace):
    import multiprocessing as mp

    child = mp.get_context("spawn").Process(target=job_wait, args=(workspace,))
    child.start()
    (Path(workspace) / "child.pid").write_text(str(child.pid))
    child.join()
    return {}


def resources():
    return importlib.import_module("datamate.preparation.resources")


def test_success_reports_real_rss_disk_and_wall_time(tmp_path):
    module = resources()
    result, report = module.run_bounded_preparation(
        job_success, tmp_path, limits=module.PreparationLimits(timeout_seconds=10.0)
    )
    assert result == {"records": 3}
    assert report.peak_rss_bytes > 0
    assert report.peak_disk_bytes >= len("bounded")
    assert report.elapsed_seconds > 0
    assert report.measurements_complete


@pytest.mark.parametrize(
    ("job", "limits", "reason"),
    [
        (job_wait, {"timeout_seconds": 0.1}, "wall_time"),
        (job_wait, {"max_rss_bytes": 1}, "rss"),
        (job_spill, {"max_disk_bytes": 1024}, "disk"),
    ],
)
def test_resource_violation_terminates_owned_worker_and_keeps_diagnostics(
    tmp_path, job, limits, reason
):
    import psutil

    module = resources()
    with pytest.raises(module.PreparationResourceError) as failure:
        module.run_bounded_preparation(job, tmp_path, limits=module.PreparationLimits(**limits))
    assert failure.value.reason == reason
    assert not psutil.pid_exists(failure.value.worker_pid)
    assert failure.value.resources.elapsed_seconds > 0


def test_worker_error_is_explicit_and_is_not_a_successful_empty_build(tmp_path):
    module = resources()
    with pytest.raises(module.PreparationResourceError, match="worker_error:ValueError"):
        module.run_bounded_preparation(job_fail, tmp_path, limits=module.PreparationLimits())


def test_resource_limits_reject_nonpositive_and_unapproved_expansion():
    module = resources()
    for values in (
        {"batch_size": 0},
        {"max_rss_bytes": 9 * 1024**3},
        {"timeout_seconds": 2701.0},
        {"max_disk_bytes": 11 * 1024**3},
    ):
        with pytest.raises(ValueError):
            module.PreparationLimits(**values)


def test_timeout_cleans_owned_descendants(tmp_path):
    import psutil

    module = resources()
    with pytest.raises(module.PreparationResourceError) as failure:
        module.run_bounded_preparation(
            job_tree, tmp_path, limits=module.PreparationLimits(timeout_seconds=2.0)
        )
    assert failure.value.reason == "wall_time"
    child_pid = int((tmp_path / "child.pid").read_text())
    assert not psutil.pid_exists(child_pid)


def test_unavailable_disk_measurement_is_unknown_and_stops_worker(tmp_path, monkeypatch):
    module = resources()

    def denied(workspace):
        raise PermissionError("synthetic")

    monkeypatch.setattr(module, "_disk_bytes", denied)
    with pytest.raises(module.PreparationResourceError) as failure:
        module.run_bounded_preparation(job_wait, tmp_path, limits=module.PreparationLimits())
    assert failure.value.reason == "disk_telemetry_unavailable"
    assert failure.value.resources.peak_disk_bytes is None
    assert not failure.value.resources.measurements_complete
