"""GET /api/telemetry and its sampler (US-905, NFR-12, OBS-01; spec 9: a telemetry process
samples amd-smi every 5 s and writes the metrics to the database; rule 11).

- The sampler, `python -m apps.api.services.other.telemetry`, reads `amd-smi metric --json`
  (run here, or the file the telemetry container rewrites every 5 s) and appends one row per GPU
  to the table telemetry_samples, every INTERVAL_S seconds; it keeps RETENTION of samples.
- GET /api/telemetry serves the latest sample of the first GPU and its recent history, the vLLM
  generations of the last LLM_WINDOW_S seconds (llm_generations: latency p50 and p95, output
  tokens per second) and the API latency when it is recorded (not yet: null).
- On a CPU (no sampler running, no table, or no sample in the last STALE_S seconds) the
  response is empty and well formed: no GPU numbers, and a note that says why. In cached mode
  the GPU is off: no GPU or LLM numbers, and the note of the contract.

telemetry_samples is a table of contracts/sql.py (ADR-16 section 9): the demo reset creates it
with the others. The sampler creates it when it starts before the reset, and a sample it cannot
write (the reset is recreating the tables) is reported and skipped, not fatal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
from sqlalchemy import delete, insert, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.provenance import provenance
from apps.api.settings import Settings
from contracts.sql import llm_generations, telemetry_samples

INTERVAL_S: Final = 5  # spec 9 and TelemetryResponse.interval_s
STALE_S: Final = 60  # a GPU sample older than this is not "live"
HISTORY: Final = 60  # samples of the history: five minutes at 5 s
LLM_WINDOW_S: Final = 300
RETENTION: Final = timedelta(hours=24)
DEFAULT_DEVICE: Final = "AMD Instinct MI300X"
AMD_SMI: Final = ("amd-smi", "metric", "--json")
# Where the precomputed results were made is the claim of the /readyz label alone, which checks
# the demo package (apps/api/services/demo_package.py); a CPU rehearsal is cached mode too.
CACHED_NOTE: Final = "GPU off: cached mode serves precomputed results"
# The telemetry container rewrites its amd-smi file every INTERVAL_S seconds, into a volume
# that survives a reboot: an older file is a reading of the past, not a sample (rule 11).
SOURCE_MAX_AGE_S: Final = 3 * INTERVAL_S
_MIB: Final[dict[str, float]] = {"b": 1 / 2**20, "kb": 1 / 1024, "kib": 1 / 1024, "mb": 1.0}
_MIB.update({"mib": 1.0, "gb": 1024.0, "gib": 1024.0})


# ---- Sampler ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuReading:
    gpu_index: int
    utilization_percent: float | None
    memory_used_gib: float | None
    memory_total_gib: float | None
    power_watts: float | None


def _number(raw: Any) -> tuple[float | None, str | None]:
    """A number and its unit from 12, "12 %", {"value": 12, "unit": "%"} or "N/A"."""
    if isinstance(raw, Mapping):
        value, unit = _number(raw.get("value"))
        return value, str(raw["unit"]) if raw.get("unit") is not None else unit
    if isinstance(raw, bool):
        return None, None
    if isinstance(raw, int | float):
        return float(raw), None
    if isinstance(raw, str):
        parts = raw.split()
        try:
            return float(parts[0]), parts[1] if len(parts) > 1 else None
        except (ValueError, IndexError):
            return None, None
    return None, None


def _first(section: Any, *keys: str) -> tuple[float | None, str | None]:
    if isinstance(section, Mapping):
        for key in keys:
            value, unit = _number(section.get(key))
            if value is not None:
                return value, unit
    return None, None


def _gib(value: float | None, unit: str | None) -> float | None:
    factor = _MIB.get((unit or "MB").strip().lower())
    return None if value is None or factor is None else round(value * factor / 1024, 3)


def parse_amd_smi(text: str) -> list[GpuReading]:
    """The GPUs of `amd-smi metric --json`: a list of GPU objects or {"gpu_data": [...]}, values
    as numbers, {"value", "unit"} or "N/A", memory in MB (read as MiB) or GB. Lines before the
    JSON (warnings) are skipped. ValueError when there is no JSON of GPUs."""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise ValueError("no JSON in the amd-smi output")
    data = json.loads(text[min(starts) :])
    if isinstance(data, Mapping):
        data = data.get("gpu_data", [data] if "gpu" in data else None)
    if not isinstance(data, list):
        raise ValueError("amd-smi JSON holds no GPU list")
    readings = []
    for position, gpu in enumerate(g for g in data if isinstance(g, Mapping)):
        index, _ = _number(gpu.get("gpu", position))
        busy, _ = _first(gpu.get("usage"), "gfx_activity", "gfx_usage", "gfx_busy_percent")
        memory = gpu.get("mem_usage", gpu.get("vram"))
        power, _ = _first(gpu.get("power"), "socket_power", "average_socket_power")
        readings.append(
            GpuReading(
                gpu_index=int(index) if index is not None else position,
                utilization_percent=busy if busy is not None and 0 <= busy <= 100 else None,
                memory_used_gib=_gib(*_first(memory, "used_vram", "vram_used")),
                memory_total_gib=_gib(*_first(memory, "total_vram", "vram_total")),
                power_watts=power if power is not None and power >= 0 else None,
            )
        )
    return readings


async def ensure_table(conn: AsyncConnection) -> None:
    """Create telemetry_samples when the sampler starts before the demo reset made it."""
    await conn.run_sync(lambda sync: telemetry_samples.create(sync, checkfirst=True))


async def store_sample(
    conn: AsyncConnection,
    at: datetime,
    readings: Sequence[GpuReading],
    *,
    device: str = DEFAULT_DEVICE,
    error: str | None = None,
    source: str = "amd-smi",
) -> int:
    """Append one row per GPU (or one row with the error), drop rows older than RETENTION."""
    rows = [
        {
            "sampled_at": at,
            "device": device,
            "gpu_index": r.gpu_index,
            "utilization_percent": r.utilization_percent,
            "memory_used_gib": r.memory_used_gib,
            "memory_total_gib": r.memory_total_gib,
            "power_watts": r.power_watts,
            "source": source,
            "error": None,
        }
        for r in readings
    ] or [
        {
            "sampled_at": at,
            "device": device,
            "gpu_index": 0,
            "utilization_percent": None,
            "memory_used_gib": None,
            "memory_total_gib": None,
            "power_watts": None,
            "source": source,
            "error": (error or "no GPU in the amd-smi output")[:500],
        }
    ]
    await conn.execute(insert(telemetry_samples), rows)
    await conn.execute(
        delete(telemetry_samples).where(telemetry_samples.c.sampled_at < at - RETENTION)
    )
    return len(rows)


def read_source(
    command: Sequence[str], source_file: Path | None
) -> tuple[list[GpuReading], str | None]:
    """One reading of amd-smi: the file the telemetry container writes, when it was rewritten
    in the last SOURCE_MAX_AGE_S seconds, or the command."""
    try:
        if source_file is not None:
            age = time.time() - source_file.stat().st_mtime
            if age > SOURCE_MAX_AGE_S:
                return [], f"{source_file.name} not updated for {age:.0f} s: no live reading"
            text = source_file.read_text(encoding="utf-8")
        else:
            done = subprocess.run(  # noqa: S603 - a fixed command, no shell
                list(command), capture_output=True, text=True, timeout=10, check=False
            )
            if done.returncode != 0:
                return [], f"amd-smi exit {done.returncode}: {done.stderr.strip()[:200]}"
            text = done.stdout
        return parse_amd_smi(text), None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"[:300]


async def sample_loop(
    url: str,
    *,
    command: Sequence[str] = AMD_SMI,
    source_file: Path | None = None,
    device: str = DEFAULT_DEVICE,
    interval_s: float = INTERVAL_S,
    count: int | None = None,
) -> int:
    """Sample every interval_s seconds (count times, or until stopped); returns rows written."""
    from apps.api.db import make_engine

    engine = make_engine(url)
    written = 0
    try:
        async with engine.begin() as conn:
            await ensure_table(conn)
        done = 0
        while count is None or done < count:
            readings, error = await asyncio.to_thread(read_source, command, source_file)
            try:
                async with engine.begin() as conn:
                    written += await store_sample(
                        conn, datetime.now(UTC), readings, device=device, error=error
                    )
            except SQLAlchemyError as exc:  # the reset recreating the tables: skip one sample
                print(f"telemetry: sample not stored: {type(exc).__name__}", file=sys.stderr)
            done += 1
            if count is None or done < count:
                await asyncio.sleep(interval_s)
    finally:
        await engine.dispose()
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="amd-smi sampler of GET /api/telemetry (US-905)")
    parser.add_argument(
        "--source-file", type=Path, help="amd-smi JSON rewritten by another process"
    )
    parser.add_argument("--command", default=" ".join(AMD_SMI), help="the amd-smi command")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--interval", type=float, default=INTERVAL_S)
    parser.add_argument("--count", type=int, help="stop after this many samples")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    written = asyncio.run(
        sample_loop(
            settings.database_url,
            command=shlex.split(args.command),
            source_file=args.source_file,
            device=args.device,
            interval_s=args.interval,
            count=args.count,
        )
    )
    print(f"telemetry: {written} rows written")
    return 0


# ---- GET /api/telemetry ----------------------------------------------------------------------


def _metric(value: float | int, unit: s.Unit, prov: s.Provenance) -> s.Metric:
    return s.Metric(value=value, unit=unit, provenance=prov)


def _gpu_sample(row: Mapping[str, Any]) -> s.GpuSample | None:
    used, total, busy = row["memory_used_gib"], row["memory_total_gib"], row["utilization_percent"]
    if row["error"] is not None or used is None or total is None or busy is None or used > total:
        return None
    at: datetime = row["sampled_at"]
    day = at.date()
    prov = provenance(
        f"amd-smi metric on {row['device']}, GPU {row['gpu_index']}, sampled every "
        f"{INTERVAL_S} s by the telemetry sampler",
        day,
        day,
        "real",
    )
    power = row["power_watts"]
    return s.GpuSample(
        sampled_at=at,
        utilization=_metric(round(float(busy), 2), "percent", prov),
        memory_used=_metric(round(float(used), 3), "gib", prov),
        memory_total=_metric(round(float(total), 3), "gib", prov),
        power=None if power is None else _metric(round(float(power), 1), "watts", prov),
    )


async def _samples(conn: AsyncConnection) -> list[Mapping[str, Any]]:
    """The latest HISTORY rows of the first GPU, oldest first; none without the table."""
    exists = await conn.run_sync(lambda sync: inspect(sync).has_table(telemetry_samples.name))
    if not exists:
        return []
    query = (
        select(telemetry_samples)
        .where(telemetry_samples.c.gpu_index == 0)
        .order_by(telemetry_samples.c.sampled_at.desc(), telemetry_samples.c.sample_id.desc())
        .limit(HISTORY)
    )
    rows = (await conn.execute(query)).mappings().all()
    return [dict(r) for r in reversed(rows)]


async def llm_stats(conn: AsyncConnection, at: datetime) -> s.LlmStats | None:
    """vLLM generations of the last LLM_WINDOW_S seconds (live, with a measured latency and
    completion tokens): requests, latency p50 and p95, output tokens per second."""
    since = at - timedelta(seconds=LLM_WINDOW_S)
    rows = (
        await conn.execute(
            select(llm_generations.c.latency_ms, llm_generations.c.completion_tokens).where(
                (llm_generations.c.llm_mode.in_(("vllm", "tokenfactory")))
                & (llm_generations.c.served_from == "live")
                & (llm_generations.c.model_calls > 0)
                & llm_generations.c.latency_ms.is_not(None)
                & (llm_generations.c.created_at > since)
                & (llm_generations.c.created_at <= at)
            )
        )
    ).all()
    timed = [(int(latency), tokens) for latency, tokens in rows if latency is not None]
    counted = [(latency, int(tokens)) for latency, tokens in timed if tokens and latency > 0]
    if not timed or not counted:
        return None
    day = at.date()
    prov = provenance(
        f"llm_generations of the last {LLM_WINDOW_S} s: model calls of the API, end-to-end latency "
        "and completion tokens",
        (at - timedelta(seconds=LLM_WINDOW_S)).date(),
        day,
        "real",
    )
    latencies = np.array([latency for latency, _ in timed], dtype=float)
    rate = sum(t for _, t in counted) / (sum(latency for latency, _ in counted) / 1000)
    return s.LlmStats(
        window_s=LLM_WINDOW_S,
        requests=_metric(len(timed), "count", prov),
        latency_p50=_metric(round(float(np.percentile(latencies, 50)), 1), "milliseconds", prov),
        latency_p95=_metric(round(float(np.percentile(latencies, 95)), 1), "milliseconds", prov),
        tokens_per_second=_metric(round(rate, 2), "tokens_per_second", prov),
    )


async def component_check(
    conn: AsyncConnection, settings: Settings, at: datetime
) -> s.ComponentCheck:
    """The telemetry check of /readyz (US-901): ok with a GPU sample of the last STALE_S
    seconds; disabled in cached mode and on a CPU without samples; unavailable on a GPU whose
    sampler is silent. /readyz (apps/api/routes/health.py) can call it in place of its fixed
    "disabled" check."""
    if settings.cached:
        return s.ComponentCheck(component="telemetry", status="disabled", detail=CACHED_NOTE)
    rows = await _samples(conn)
    fresh = [
        g
        for g in (_gpu_sample(r) for r in rows)
        if g is not None and at - g.sampled_at <= timedelta(seconds=STALE_S)
    ]
    if fresh:
        return s.ComponentCheck(component="telemetry", status="ok", detail=None)
    detail = f"no amd-smi sample in the last {STALE_S} s (DEVICE={settings.device})"
    status: Literal["unavailable", "disabled"] = (
        "unavailable" if settings.device == "gpu" else "disabled"
    )
    return s.ComponentCheck(component="telemetry", status=status, detail=detail)


async def telemetry(
    conn: AsyncConnection, settings: Settings, at: datetime, wall: datetime | None = None
) -> s.TelemetryResponse:
    """GET /api/telemetry (see the module docstring). `at` is the API's clock, which dates the
    model calls of llm_generations; `wall` the wall clock of the amd-smi samples (ADR-15
    section 15: the demo clock runs from the snapshot instant, the GPU in real time)."""
    sampled = wall or at
    if settings.cached:
        return s.TelemetryResponse(
            mode="cached",
            sampled_at=sampled,
            device=None,
            gpu=None,
            gpu_history=[],
            llm=None,
            api_latency_p95=None,
            note=CACHED_NOTE,
        )
    rows = await _samples(conn)
    history = [g for g in (_gpu_sample(r) for r in rows) if g is not None]
    latest = history[-1] if history else None
    live = latest is not None and sampled - latest.sampled_at <= timedelta(seconds=STALE_S)
    llm = await llm_stats(conn, at) if settings.llm_mode != "mock" else None
    if live and latest is not None:
        device = next(
            str(r["device"]) for r in reversed(rows) if r["sampled_at"] == latest.sampled_at
        )
        note = None
    else:
        device = None
        if rows:
            last = f"{rows[-1]['sampled_at']:%Y-%m-%d %H:%M:%S}"
            note = f"No valid GPU sample in the last {STALE_S} s (last sample: {last} UTC)"
        else:
            note = (
                f"No GPU sample in the last {STALE_S} s: the amd-smi sampler is not running "
                f"(DEVICE={settings.device})"
            )
    return s.TelemetryResponse(
        mode="live",
        sampled_at=latest.sampled_at if live and latest is not None else sampled,
        device=device,
        gpu=latest if live else None,
        gpu_history=history,
        llm=llm,
        api_latency_p95=None,
        note=note,
    )


if __name__ == "__main__":  # pragma: no cover - the sampler process
    raise SystemExit(main())
