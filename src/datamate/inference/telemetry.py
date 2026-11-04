"""Bounded, device-wide GPU sampling; never pretend it measures a remote provider."""

import subprocess
import threading


class GPUMonitor:
    def __init__(self, *, enabled: bool, interval: float = 2):
        if interval < 0.1:
            raise ValueError("GPU sampling interval must be at least 0.1 seconds")
        self.enabled, self.interval = enabled, interval
        self.devices = {}
        self.failures = 0
        self._stop = threading.Event()
        self._thread = None

    def observe(self):
        if not self.enabled:
            return
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            for line in result.stdout.strip().splitlines():
                index, memory, utilization = line.split(",")
                memory, utilization = float(memory), float(utilization)
                device = self.devices.setdefault(
                    index.strip(), {"samples": 0, "peak_used_mib": 0, "utilization_sum": 0}
                )
                device["samples"] += 1
                device["peak_used_mib"] = max(device["peak_used_mib"], memory)
                device["utilization_sum"] += utilization
        except (OSError, subprocess.SubprocessError, ValueError):
            self.failures += 1

    def _poll(self):
        while not self._stop.wait(self.interval):
            self.observe()

    def __enter__(self):
        if self.enabled:
            self.observe()
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=4)

    def snapshot(self):
        return {
            "status": "sampled_device_wide" if self.enabled else "not_measured_remote_or_unmanaged",
            "scope": "all host devices, includes other processes; not model-allocated VRAM",
            "interval_seconds": self.interval,
            "sample_failures": self.failures,
            "devices": {
                index: {
                    "samples": d["samples"],
                    "sampled_peak_used_mib": d["peak_used_mib"],
                    "mean_gpu_utilization_percent": d["utilization_sum"] / d["samples"],
                }
                for index, d in self.devices.items()
            },
        }
