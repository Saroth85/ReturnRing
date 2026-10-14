"""GPU utilisation from `amd-smi metric --json`, sampled every 5 s (B4, US-905).

Standard library only and Python 3.10 compatible: on the droplet the sampler runs on the host
(`python3 -m bench.amdsmi --out FILE`), where amd-smi matches the host driver (the SPK-G4 lesson),
while the benchmark jobs run in containers and read the samples file afterwards. One JSON line per
sample: {"t": unix seconds, "sampled_at", "gpus": [...], "error"}. A failed call is written as a
sample with an error, never skipped silently, and the sampler keeps going.

Parsing accepts the layouts amd-smi has used: a list of per-GPU objects, or {"gpu_data": [...]};
values as numbers, {"value": v, "unit": u} or "N/A"; memory in MB or GB, which amd-smi reports in
binary units (196,592 MB on a 192 GB MI300X), so MB is read as MiB. gfx_activity is the busy
share amd-smi reports at the instant of the call: a 5 s sample can miss short bursts.
"""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

INTERVAL_S = 5.0
COMMAND: tuple[str, ...] = ("amd-smi", "metric", "--json")
TIMEOUT_S = 10.0
_MIB_PER = {"b": 1 / 2**20, "kb": 1 / 1024, "kib": 1 / 1024, "mb": 1.0, "mib": 1.0}
_MIB_PER.update({"gb": 1024.0, "gib": 1024.0})


@dataclass(frozen=True)
class GpuReading:
    gpu: int
    gfx_activity_percent: float | None
    vram_used_gib: float | None
    vram_total_gib: float | None
    power_w: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gpu": self.gpu,
            "gfx_activity_percent": self.gfx_activity_percent,
            "vram_used_gib": self.vram_used_gib,
            "vram_total_gib": self.vram_total_gib,
            "power_w": self.power_w,
        }


def _value(raw: Any) -> tuple[float | None, str | None]:
    """A number and its unit from 12, 12.5, "12 %", {"value": 12, "unit": "%"} or "N/A"."""
    if isinstance(raw, Mapping):
        number, unit = _value(raw.get("value"))
        unit_field = raw.get("unit")
        return number, (str(unit_field) if unit_field is not None else unit)
    if isinstance(raw, bool):
        return None, None
    if isinstance(raw, int | float):
        return float(raw), None
    if isinstance(raw, str):
        parts = raw.strip().split()
        try:
            return float(parts[0]), (parts[1] if len(parts) > 1 else None)
        except (ValueError, IndexError):
            return None, None
    return None, None


def _first(section: Any, *keys: str) -> tuple[float | None, str | None]:
    if not isinstance(section, Mapping):
        return None, None
    for key in keys:
        if key in section:
            number, unit = _value(section[key])
            if number is not None:
                return number, unit
    return None, None


def _gib(number: float | None, unit: str | None) -> float | None:
    if number is None:
        return None
    factor = _MIB_PER.get((unit or "MB").strip().lower())
    if factor is None:
        return None
    return round(number * factor / 1024, 3)


def _gpu_objects(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        if isinstance(data.get("gpu_data"), list):
            data = data["gpu_data"]
        elif "gpu" in data:
            data = [data]
        else:
            raise ValueError("amd-smi JSON has neither gpu_data nor gpu")
    if not isinstance(data, list):
        raise ValueError("amd-smi JSON is not a list of GPUs")
    return [g for g in data if isinstance(g, Mapping)]


def parse_metric_json(text: str) -> list[GpuReading]:
    """The readings of `amd-smi metric --json`; warning lines before the JSON are ignored."""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("no JSON in the amd-smi output")
    data = json.loads(text[min(starts) :])
    readings = []
    for position, gpu in enumerate(_gpu_objects(data)):
        index, _ = _value(gpu.get("gpu", position))
        activity, _ = _first(gpu.get("usage"), "gfx_activity", "gfx_usage", "gfx_busy_percent")
        memory = gpu.get("mem_usage", gpu.get("vram"))
        used = _gib(*_first(memory, "used_vram", "vram_used"))
        total = _gib(*_first(memory, "total_vram", "vram_total"))
        power, _ = _first(
            gpu.get("power"), "socket_power", "average_socket_power", "current_socket_power"
        )
        if activity is not None and not 0 <= activity <= 100:
            activity = None
        readings.append(
            GpuReading(
                gpu=int(index) if index is not None else position,
                gfx_activity_percent=activity,
                vram_used_gib=used,
                vram_total_gib=total,
                power_w=power,
            )
        )
    return readings


@dataclass(frozen=True)
class Sample:
    t: float
    gpus: tuple[GpuReading, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        moment = datetime.fromtimestamp(self.t, timezone.utc)  # noqa: UP017 (Python 3.10 host)
        return {
            "t": round(self.t, 3),
            "sampled_at": moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "gpus": [g.to_dict() for g in self.gpus],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Sample:
        gpus = tuple(
            GpuReading(
                gpu=int(g["gpu"]),
                gfx_activity_percent=g.get("gfx_activity_percent"),
                vram_used_gib=g.get("vram_used_gib"),
                vram_total_gib=g.get("vram_total_gib"),
                power_w=g.get("power_w"),
            )
            for g in data.get("gpus", [])
        )
        return cls(float(data["t"]), gpus, data.get("error"))


def sample_once(command: Sequence[str] = COMMAND, timeout_s: float = TIMEOUT_S) -> Sample:
    t = time.time()
    try:
        done = subprocess.run(
            list(command), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Sample(t, (), f"{type(exc).__name__}: {exc}"[:300])
    if done.returncode != 0:
        return Sample(t, (), f"exit {done.returncode}: {done.stderr.strip()[:280]}")
    try:
        return Sample(t, tuple(parse_metric_json(done.stdout)))
    except ValueError as exc:  # json.JSONDecodeError included
        return Sample(t, (), f"unparsable output: {exc}"[:300])


class Sampler:
    """Appends one sample to `out` every `interval_s` seconds in a background thread."""

    def __init__(
        self,
        out: Path,
        command: Sequence[str] = COMMAND,
        interval_s: float = INTERVAL_S,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.out = out
        self.command = tuple(command)
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        self.out.parent.mkdir(parents=True, exist_ok=True)
        with self.out.open("a", encoding="utf-8") as handle:
            while True:
                started = time.monotonic()
                sample = sample_once(self.command)
                handle.write(json.dumps(sample.to_dict(), sort_keys=True) + "\n")
                handle.flush()
                if self._stop.wait(max(0.0, self.interval_s - (time.monotonic() - started))):
                    return

    def start(self) -> Sampler:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="amd-smi", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=TIMEOUT_S + self.interval_s)

    def __enter__(self) -> Sampler:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


def read_samples(path: Path) -> list[Sample]:
    """The samples of a file; a line cut by a kill at the end is ignored."""
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            samples.append(Sample.from_dict(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            continue
    return samples


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def window_summary(samples: Sequence[Sample], start: float, end: float) -> dict[str, Any]:
    """Utilisation between two unix times: every GPU of a sample together (mean activity, summed
    memory and power), then mean and max over the samples. n counts the samples with readings,
    errors counts the failed calls; a window shorter than the interval may have none."""
    inside = [s for s in samples if start <= s.t <= end]
    ok = [s for s in inside if s.gpus]
    activity = [
        sum(g.gfx_activity_percent for g in s.gpus if g.gfx_activity_percent is not None)
        / len([g for g in s.gpus if g.gfx_activity_percent is not None])
        for s in ok
        if any(g.gfx_activity_percent is not None for g in s.gpus)
    ]
    used = [
        sum(g.vram_used_gib for g in s.gpus if g.vram_used_gib is not None)
        for s in ok
        if any(g.vram_used_gib is not None for g in s.gpus)
    ]
    total = [
        sum(g.vram_total_gib for g in s.gpus if g.vram_total_gib is not None)
        for s in ok
        if any(g.vram_total_gib is not None for g in s.gpus)
    ]
    power = [
        sum(g.power_w for g in s.gpus if g.power_w is not None)
        for s in ok
        if any(g.power_w is not None for g in s.gpus)
    ]
    return {
        "n": len(ok),
        "errors": len(inside) - len(ok),
        "gfx_activity_mean_percent": _mean(activity),
        "gfx_activity_max_percent": round(max(activity), 2) if activity else None,
        "vram_used_max_gib": round(max(used), 3) if used else None,
        "vram_total_gib": round(max(total), 3) if total else None,
        "power_mean_w": _mean(power),
        "power_max_w": round(max(power), 2) if power else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m bench.amdsmi", description=__doc__)
    p.add_argument("--out", type=Path, required=True, help="JSON lines file (appended)")
    p.add_argument("--interval", type=float, default=INTERVAL_S, help="seconds (default 5)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--command", default=" ".join(COMMAND), help="default: amd-smi metric --json")
    args = p.parse_args(argv)
    command = shlex.split(args.command)
    stop = threading.Event()

    def _handler(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    first = sample_once(command)
    if first.error:
        print(f"amd-smi sampler: first sample failed ({first.error}); going on", file=sys.stderr)
    with Sampler(args.out, command, args.interval):
        stop.wait(args.duration)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
