"""Supervise trusted CPU preparation workers with fixed pilot resource ceilings."""

import json
import multiprocessing as mp
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil
from pydantic import Field

from datamate.contracts.base import ContractModel


class PreparationLimits(ContractModel):
    batch_size: int = Field(default=1000, ge=1, le=1000)
    duckdb_memory_bytes: int = Field(default=2 * 1024**3, ge=1, le=2 * 1024**3)
    max_rss_bytes: int = Field(default=8 * 1024**3, ge=1, le=8 * 1024**3)
    max_disk_bytes: int = Field(default=10 * 1024**3, ge=1, le=10 * 1024**3)
    timeout_seconds: float = Field(default=2700.0, gt=0, le=2700)


@dataclass(frozen=True)
class PreparationResources:
    elapsed_seconds: float
    peak_rss_bytes: int | None
    peak_disk_bytes: int | None
    measurements_complete: bool


class PreparationResourceError(RuntimeError):
    def __init__(self, reason: str, worker_pid: int, resources: PreparationResources):
        super().__init__(reason)
        self.reason = reason
        self.worker_pid = worker_pid
        self.resources = resources


def _disk_bytes(workspace: Path) -> int:
    def fail(error):
        raise error

    size = 0
    for directory, names, files in os.walk(workspace, followlinks=False, onerror=fail):
        for name in (*names, *files):
            path = Path(directory) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError("linked preparation output is forbidden")
            if name in files:
                try:
                    size += path.stat().st_size
                except FileNotFoundError:
                    # Spill files can be removed between enumeration and stat.
                    pass
    return size


def _worker(target: Callable[[Path], dict], workspace: Path, sender) -> None:
    try:
        value = target(workspace)
        if type(value) is not dict:
            raise ValueError("worker result must be a bounded JSON object")
        payload = json.dumps({"status": "ok", "value": value}, allow_nan=False).encode()
        if len(payload) > 65536:
            raise ValueError("worker result exceeds 64KiB")
    except Exception as exc:
        # Do not leak source payloads or credentials from arbitrary exception messages.
        payload = json.dumps({"status": "error", "type": type(exc).__name__}).encode()
    try:
        sender.send_bytes(payload)
    finally:
        sender.close()


def _terminate_owned(process: mp.Process, monitored: psutil.Process) -> None:
    try:
        children = monitored.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def run_bounded_preparation(
    target: Callable[[Path], dict], workspace: Path, *, limits: PreparationLimits
) -> tuple[dict, PreparationResources]:
    """Run application-owned code, never a model-supplied callable or generated code.

    Limits supervise the entire owned process tree at 50ms intervals. This is a
    resource watchdog, not a generated-code security sandbox or an OS RSS hard cap.
    The workspace must contain all output/spill files for disk accounting.
    """
    limits = PreparationLimits.model_validate(limits)
    if workspace.is_symlink() or workspace.is_junction():
        raise ValueError("linked workspace is forbidden")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace must be an existing directory")
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(target, workspace, sender))
    started = time.monotonic()
    process.start()
    worker_pid = process.pid
    monitored = psutil.Process(worker_pid)
    sender.close()
    peak_rss = peak_disk = None
    complete = True
    message = None
    reason = None
    try:
        while True:
            if time.monotonic() - started > limits.timeout_seconds:
                reason = "wall_time"
                break
            try:
                members = [monitored, *monitored.children(recursive=True)]
                rss = sum(member.memory_info().rss for member in members)
                peak_rss = max(peak_rss or 0, rss)
            except psutil.NoSuchProcess:
                if process.is_alive():
                    complete = False
                    reason = "telemetry_unavailable"
                    break
            except psutil.Error:
                complete = False
                reason = "telemetry_unavailable"
                break
            try:
                disk = _disk_bytes(workspace)
            except (OSError, ValueError):
                complete = False
                reason = "disk_telemetry_unavailable"
                break
            peak_disk = max(peak_disk or 0, disk)
            if peak_rss is not None and peak_rss > limits.max_rss_bytes:
                reason = "rss"
                break
            if disk > limits.max_disk_bytes:
                reason = "disk"
                break
            if message is None and receiver.poll():
                try:
                    message = json.loads(receiver.recv_bytes(maxlength=65536))
                except EOFError:
                    pass
            if not process.is_alive():
                process.join()
                if process.exitcode != 0 or message is None:
                    reason = "worker_exit"
                elif message["status"] != "ok":
                    reason = "worker_error:" + message["type"]
                break
            time.sleep(0.05)
    finally:
        _terminate_owned(process, monitored)
        receiver.close()
        process.close()
    report = PreparationResources(
        time.monotonic() - started,
        peak_rss,
        peak_disk,
        complete and peak_rss is not None and peak_disk is not None,
    )
    if reason is not None:
        raise PreparationResourceError(reason, worker_pid, report)
    return message["value"], report
