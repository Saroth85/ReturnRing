"""Measurements, benchmark parts and the run document (US-902). Standard library only.

A part is what one benchmark wrote in one run (artifacts/bench/<run-id>/<B>.json); the merge puts
the parts of a run into bench/results/<run-id>.json and moves the latest pointer. Every
measurement carries origin, period and nature (rule 11): a time measured on real hardware is
"real", even when the data it ran on is synthetic (`data_nature` says that); a number computed
under declared assumptions, or measured against the fake server of a dry run, is "simulated".
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from bench import SCHEMA, STORY

BENCHMARKS = ("B1", "B2", "B3", "B4")
# The literals of apps/api/schemas.py (BenchmarkDevice, BenchmarkPhase, Unit, Nature).
API_DEVICES = ("cpu", "mi300x")
PHASES = ("cold_start", "training", "scoring", "generation", "pipeline", "cost")
UNITS = (
    "eur",
    "usd",
    "count",
    "ratio",
    "percent",
    "seconds",
    "milliseconds",
    "tokens_per_second",
    "gib",
    "watts",
)
NATURES = ("real", "synthetic", "simulated")
PROTOCOLS = ("paper", "decision_time")  # ADR-10 A and B, as the API names them
# measured: real numbers; dry_run: harness check against fakes (simulated numbers); skipped: not
# run, with the reason in the notes; failed: the run broke, with the error in the notes.
PART_STATUSES = ("measured", "dry_run", "skipped", "failed")
METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_TEXT = 300  # ShortText of the API
DEVICE_RANK = {"cpu": 0, "gpu": 1}


class BenchError(ValueError):
    """A measurement, part or run document that breaks the rules of this module."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """ISO 8601 in UTC with a Z, to the second."""
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short(text: str, limit: int = MAX_TEXT) -> str:
    """At most `limit` characters (the API's ShortText), cut with an ellipsis."""
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


@dataclass(frozen=True)
class Measurement:
    """One number: what was measured, on what, and where it comes from (rule 11)."""

    benchmark: str
    metric: str
    label: str
    value: float | int
    unit: str
    device: str
    phase: str
    origin: str
    period_start: date
    period_end: date
    nature: str
    data_nature: str | None = None  # nature of the data the measurement ran on
    protocol: str | None = None
    concurrency: int | None = None
    batch_size: int | None = None
    rows: int | None = None  # data size of the measurement (spec 12, "Carichi crescenti")
    warm: bool | None = None  # False: cold start included; True: after a warm-up
    repeats: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        errors = []
        if self.benchmark not in BENCHMARKS:
            errors.append(f"benchmark {self.benchmark!r}")
        if not METRIC_RE.match(self.metric):
            errors.append(f"metric {self.metric!r} (snake case, at most 64 characters)")
        if not 1 <= len(self.label) <= MAX_TEXT or not 1 <= len(self.origin) <= MAX_TEXT:
            errors.append("label and origin need 1 to 300 characters")
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            errors.append(f"value {self.value!r} is not a number")
        elif not math.isfinite(self.value):
            errors.append(f"value {self.value!r} is not finite")
        elif self.unit == "count" and (not isinstance(self.value, int) or self.value < 0):
            errors.append("a count is a non-negative integer")
        elif self.unit == "percent" and not 0 <= self.value <= 100:
            errors.append("a percent is in [0, 100]")
        elif self.unit == "ratio" and not 0 <= self.value <= 1:
            errors.append("a ratio is in [0, 1]")
        elif self.unit in ("eur", "usd") and abs(round(self.value, 2) - self.value) > 1e-9:
            errors.append("an amount has at most 2 decimals")
        elif self.unit not in ("eur", "usd") and self.value < 0:
            errors.append(f"{self.unit} cannot be negative")
        if self.unit not in UNITS:
            errors.append(f"unit {self.unit!r}")
        if self.device not in API_DEVICES:
            errors.append(f"device {self.device!r}")
        if self.phase not in PHASES:
            errors.append(f"phase {self.phase!r}")
        if self.nature not in NATURES or self.data_nature not in (*NATURES, None):
            errors.append(f"nature {self.nature!r} / data nature {self.data_nature!r}")
        if self.protocol not in (*PROTOCOLS, None):
            errors.append(f"protocol {self.protocol!r}")
        if self.period_start > self.period_end:
            errors.append("period_start is after period_end")
        for name in ("concurrency", "batch_size", "rows", "repeats"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or value < 1):
                errors.append(f"{name} must be at least 1")
        if errors:
            raise BenchError(f"{self.benchmark} {self.metric}: " + "; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "metric": self.metric,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "device": self.device,
            "phase": self.phase,
            "provenance": {
                "origin": self.origin,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "nature": self.nature,
            },
            "data_nature": self.data_nature,
            "protocol": self.protocol,
            "concurrency": self.concurrency,
            "batch_size": self.batch_size,
            "rows": self.rows,
            "warm": self.warm,
            "repeats": self.repeats,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Measurement:
        try:
            p = data["provenance"]
            return cls(
                benchmark=data["benchmark"],
                metric=data["metric"],
                label=data["label"],
                value=data["value"],
                unit=data["unit"],
                device=data["device"],
                phase=data["phase"],
                origin=p["origin"],
                period_start=date.fromisoformat(p["period_start"]),
                period_end=date.fromisoformat(p["period_end"]),
                nature=p["nature"],
                data_nature=data.get("data_nature"),
                protocol=data.get("protocol"),
                concurrency=data.get("concurrency"),
                batch_size=data.get("batch_size"),
                rows=data.get("rows"),
                warm=data.get("warm"),
                repeats=data.get("repeats"),
                detail=data.get("detail") or {},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchError(f"invalid measurement: {exc!r}") from None


@dataclass(frozen=True)
class Period:
    """The days a part measured on (UTC): the period of its measurements."""

    start: datetime
    end: datetime

    @property
    def days(self) -> tuple[date, date]:
        return self.start.astimezone(UTC).date(), self.end.astimezone(UTC).date()


@dataclass
class Part:
    """What one benchmark wrote in one run."""

    benchmark: str
    status: str
    title: str
    dataset: str
    started_at: datetime
    finished_at: datetime
    versions: dict[str, str] = field(default_factory=dict)
    config: dict[str, str | int | float | bool] = field(default_factory=dict)
    measurements: list[Measurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    hardware: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.benchmark not in BENCHMARKS or self.status not in PART_STATUSES:
            raise BenchError(f"part {self.benchmark!r} with status {self.status!r}")
        for m in self.measurements:
            if m.benchmark != self.benchmark:
                raise BenchError(f"{m.metric} belongs to {m.benchmark}, not {self.benchmark}")
        if self.status == "measured" and not any(m.nature == "real" for m in self.measurements):
            raise BenchError(f"{self.benchmark}: a measured part has real measurements")
        if self.status == "skipped" and self.measurements:
            raise BenchError(f"{self.benchmark}: a skipped part has no measurements")
        if self.status == "dry_run" and any(m.nature == "real" for m in self.measurements):
            raise BenchError(f"{self.benchmark}: a dry run measures nothing real")
        # A failed part keeps what it measured before the failure (rule 13), and the API
        # projection shows it as pending with the error in the notes.

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "benchmark": self.benchmark,
            "status": self.status,
            "title": self.title,
            "dataset": self.dataset,
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "versions": dict(self.versions),
            "config": dict(self.config),
            "measurements": [m.to_dict() for m in self.measurements],
            "notes": list(self.notes),
            "details": self.details,
            "hardware": self.hardware,
        }


def _check_part_dict(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a part read from disk (every measurement again) and return it as a dict."""
    if data.get("schema") != SCHEMA:
        raise BenchError(f"part schema {data.get('schema')!r}, expected {SCHEMA!r}")
    measurements = [Measurement.from_dict(m) for m in data.get("measurements", [])]
    try:
        Part(
            benchmark=data["benchmark"],
            status=data["status"],
            title=data["title"],
            dataset=data["dataset"],
            started_at=datetime.fromisoformat(data["started_at"]),
            finished_at=datetime.fromisoformat(data["finished_at"]),
            measurements=measurements,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchError(f"invalid part: {exc!r}") from None
    return dict(data)


def write_json(path: Path, doc: Mapping[str, Any]) -> Path:
    """Write through a temporary file in the same folder, then rename (no half-written file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return path


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BenchError(f"{path}: not a JSON object")
    return data


RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-(cpu|gpu)(-[a-z0-9-]{1,32})?$")


def check_run_id(run_id: str) -> str:
    """<UTC yyyymmddThhmmssZ>-<cpu|gpu>[-tag]: sortable, and safe as a file name."""
    if not RUN_ID_RE.match(run_id):
        raise BenchError(f"run id {run_id!r} is not <yyyymmddThhmmssZ>-<cpu|gpu>[-tag]")
    return run_id


def new_run_id(device: str, moment: datetime | None = None) -> str:
    return check_run_id(f"{(moment or utc_now()).astimezone(UTC):%Y%m%dT%H%M%SZ}-{device}")


def run_device(run_id: str) -> str:
    return check_run_id(run_id).split("-")[1]


def part_path(run_dir: Path, benchmark: str) -> Path:
    return run_dir / f"{benchmark}.json"


def write_part(run_dir: Path, part: Part) -> Path:
    return write_json(part_path(run_dir, part.benchmark), part.to_dict())


def read_parts(run_dir: Path) -> dict[str, dict[str, Any]]:
    """The parts present in a run folder, validated, by benchmark id."""
    parts: dict[str, dict[str, Any]] = {}
    for benchmark in BENCHMARKS:
        path = part_path(run_dir, benchmark)
        if path.exists():
            parts[benchmark] = _check_part_dict(read_json(path))
    return parts


RULES = {
    "comparable_quality": "Same data, version and configuration on CPU and GPU; no speedup from "
    "different workloads. B1 runs the same matrices, xgboost build, parameters and seed on both "
    "devices and computes speedups from the fixed-rounds runs only (identical work), early "
    "stopping shown apart with its rounds; B2 divides median epochs of runs with identical "
    "settings; B3 runs every load on the same case list; B4 labels each step with the device "
    "it ran on.",
    "separate_times": "Cold start, training, scoring and generation measured apart; latency p50 "
    "and p95 always with concurrency and throughput.",
    "full_cost": "The cost per decision includes idle time and the snapshot in the declared "
    "operating scenario (bench.cost, versioned, every assumption listed); development "
    "experiments (OOD store, risk evaluation) stay apart.",
    "growing_loads": "Several data sizes (B1, B2), batch sizes and concurrency levels (B3), to "
    "see where the GPU adds value instead of assuming it.",
}


def merge(
    run_id: str,
    run_dir: Path,
    *,
    git: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """The run document of a run folder: every part, every measurement, the API projection."""
    from bench.api_view import benchmarks_response  # stdlib only

    device = run_device(run_id)
    parts = read_parts(run_dir)
    if not parts:
        raise BenchError(f"no part in {run_dir}: run `python -m bench run` first")
    measurements = [m for part in parts.values() for m in part["measurements"]]
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "story": STORY,
        "run_id": run_id,
        "device": device,
        "created_at": iso(created_at or utc_now()),
        "git": dict(git or {}),
        "rules": dict(RULES),
        "hardware": {b: p.get("hardware", {}) for b, p in parts.items()},
        "software": {b: p.get("versions", {}) for b, p in parts.items()},
        "benchmarks": parts,
        "measurements": measurements,
    }
    doc["api"] = benchmarks_response(doc)
    return doc


def write_results(
    results_dir: Path, doc: Mapping[str, Any], *, force_latest: bool = False
) -> tuple[Path, Path | None]:
    """bench/results/<run-id>.json, latest-<device>.json, and latest.json for a GPU run, unless
    a run of a higher device rank is already the latest. A CPU run measures the harness on a
    development machine and moves latest.json (the run GET /api/benchmarks serves) only with
    force_latest (review G8). Returns the run file and the latest pointer moved."""
    run_id = check_run_id(str(doc["run_id"]))
    device = run_device(run_id)
    target = write_json(results_dir / f"{run_id}.json", doc)
    pointer = {
        "schema": "returnring.bench.latest/1",
        "run_id": run_id,
        "file": target.name,
        "device": device,
        "created_at": doc["created_at"],
    }
    write_json(results_dir / f"latest-{device}.json", pointer)
    latest = results_dir / "latest.json"
    if device == "cpu" and not force_latest:
        return target, None
    if latest.exists() and not force_latest:
        current = read_json(latest)
        if DEVICE_RANK.get(str(current.get("device")), 0) > DEVICE_RANK[device]:
            return target, None
    return target, write_json(latest, pointer)


def resolve_latest(results_dir: Path, name: str = "latest.json") -> Path:
    """The run file a pointer names (the file the API reads for GET /api/benchmarks)."""
    pointer = read_json(results_dir / name)
    target = results_dir / check_run_id(str(pointer["run_id"]))
    path = target.with_suffix(".json")
    if pointer.get("file") != path.name or not path.exists():
        raise BenchError(f"{name} names {pointer.get('file')!r}, which is not in {results_dir}")
    return path


def flatten(groups: Iterable[Sequence[Measurement]]) -> list[Measurement]:
    return [m for group in groups for m in group]
