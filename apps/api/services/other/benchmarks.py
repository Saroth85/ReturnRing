"""GET /api/benchmarks (US-902, spec 12 "Benchmark AMD"; ADR-15 section 4; rule 11, NFR-16).

The benchmark job (`python -m bench`, docs/benchmarks.md) writes one versioned run file,
bench/results/<run-id>.json, and the pointer bench/results/latest.json to the run the API
serves. The run file carries its projection on the contract ("api", BenchmarksResponse); every
result there is a Metric whose provenance gives origin, period and nature.

The API reads the pointer and the run it names, checks both formats, and serves each benchmark
as its model validates it:

- measured: real results with a measurement time, as written by the job, plus a note naming
  the run file;
- pending: a benchmark the run does not hold, cannot be read, or holds as "illustrative"
  (illustrative values belong to contract examples and mock servers only, never to the API).

With no run on file every benchmark is pending, with the reason. The directory is
BENCHMARKS_DIR (default bench/results), read only.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, get_args

from pydantic import ValidationError

from apps.api import schemas as s
from apps.api.services.provenance import short_origin

BENCHMARKS_ENV: Final = "BENCHMARKS_DIR"
DEFAULT_DIR: Final = Path("bench/results")
LATEST: Final = "latest.json"
LATEST_FORMAT: Final = "returnring.bench.latest/1"
RUN_FORMAT: Final = "returnring.bench/1"
_RUN_FILE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
BENCHMARK_IDS: Final[tuple[s.BenchmarkId, ...]] = get_args(s.BenchmarkId)
# Titles and datasets of spec 12 ("Benchmark AMD"), for a benchmark no run holds.
TITLES: Final[dict[str, str]] = {
    "B1": "XGBoost training on ASOS GraphReturns: CPU against MI300X",
    "B2": "GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1",
    "B3": "Gemma on vLLM: latency, throughput and cost per decision",
    "B4": "Nightly pipeline: duration per step and GPU utilisation",
}
DATASETS: Final[dict[str, str]] = {
    "B1": "ASOS GraphReturns train split, protocols of ADR-10",
    "B2": "ASOS GraphReturns, paper and decision-time protocols (ADR-10)",
    "B3": "llm_case inputs of the open returns of the synthetic store",
    "B4": "Navigli Fashion synthetic store, nightly pipeline",
}


def benchmarks_dir(env: Mapping[str, str] | None = None) -> Path:
    """BENCHMARKS_DIR, or bench/results relative to the working directory."""
    values = os.environ if env is None else env
    return Path(values.get(BENCHMARKS_ENV) or DEFAULT_DIR)


def pending(benchmark: s.BenchmarkId, *notes: str) -> s.Benchmark:
    return s.Benchmark(
        id=benchmark,
        title=TITLES[benchmark],
        status="pending",
        measured_at=None,
        dataset=DATASETS[benchmark],
        versions={},
        config={},
        results=[],
        notes=[short_origin(n) for n in notes],
    )


def _all_pending(reason: str) -> s.BenchmarksResponse:
    return s.BenchmarksResponse(items=[pending(b, reason) for b in BENCHMARK_IDS])


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _item(raw: Any, benchmark: s.BenchmarkId, run_note: str) -> s.Benchmark:
    try:
        item = s.Benchmark.model_validate(raw)
    except ValidationError as exc:
        errors = exc.error_count()
        return pending(benchmark, f"{run_note}: no valid {benchmark} ({errors} errors)")
    if item.id != benchmark:
        return pending(benchmark, f"{run_note}: no {benchmark}")
    if item.status == "illustrative":
        return pending(benchmark, f"{run_note}: illustrative values are never served by the API")
    if item.status == "measured":
        return item.model_copy(update={"notes": [*item.notes, short_origin(run_note)]})
    return item


def load(directory: Path) -> s.BenchmarksResponse:
    """The benchmarks of the run latest.json names (see the module docstring)."""
    where = directory / LATEST
    try:
        pointer = _read(where)
    except FileNotFoundError:
        return _all_pending(f"No benchmark run on file ({LATEST} not found): run make bench")
    except (OSError, ValueError):
        return _all_pending(f"{LATEST} cannot be read")
    name = pointer.get("file") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict)
        or pointer.get("schema") != LATEST_FORMAT
        or not isinstance(name, str)
        or _RUN_FILE.fullmatch(name) is None
    ):
        return _all_pending(f"{LATEST} is not a {LATEST_FORMAT} pointer")
    try:
        doc = _read(directory / name)
    except (OSError, ValueError):
        return _all_pending(f"The run file {name} named by {LATEST} cannot be read")
    api = doc.get("api") if isinstance(doc, dict) else None
    items = api.get("items") if isinstance(api, dict) else None
    if (
        not isinstance(items, list)
        or doc.get("schema") != RUN_FORMAT
        or doc.get("run_id") != pointer.get("run_id")
    ):
        return _all_pending(f"{name} is not the {RUN_FORMAT} run named by {LATEST}")
    run_note = f"Run {doc['run_id']}, file bench/results/{name}"
    by_id = {str(x.get("id")): x for x in items if isinstance(x, dict)}
    return s.BenchmarksResponse(
        items=[
            _item(by_id[b], b, run_note) if b in by_id else pending(b, f"{run_note}: no {b}")
            for b in BENCHMARK_IDS
        ]
    )
