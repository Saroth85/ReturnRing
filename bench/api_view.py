"""The run document as GET /api/benchmarks serves it: apps/api/schemas.py BenchmarksResponse.

Standard library only. The API reads bench/results/latest.json, opens the run file it names and
serves its "api" member, validated by BenchmarksResponse (tests/test_bench.py checks every
projection against the pydantic model). The projection follows the contract as it is:

- measured: the part's real measurements, with a measurement time;
- pending: dry runs, skipped and failed parts, with the reason in the notes (a dry run's
  simulated numbers are never served: "illustrative" is for contract examples only, ADR-15);
- the simulated cost of B3 cannot sit in a measured benchmark (all results must be real): it
  goes to the notes as text with its scenario, nature and figures, until the contract change of
  docs/benchmarks.md ("Contract") allows a simulated cost result;
- versions longer than the API's 64 characters (image references with a digest) are shortened
  to the tag and the first 12 hex digits; the full references stay in the run file;
- data size (rows) and cold or warm are in the metric name and the label (no field yet).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bench.record import BENCHMARKS, short

MAX_VERSION = 64
TITLES = {
    "B1": "XGBoost training on ASOS GraphReturns: CPU against MI300X",
    "B2": "GraphSAGE on ASOS GraphReturns: CPU against MI300X, F1",
    "B3": "Gemma on vLLM: latency, throughput and cost per decision",
    "B4": "Nightly pipeline: duration per step and GPU utilisation",
}
DATASETS = {
    "B1": "ASOS GraphReturns train split (OSF c793h), protocols A and B (ADR-10)",
    "B2": "ASOS GraphReturns, paper and decision-time protocols (ADR-10)",
    "B3": "llm_case inputs built by llm.case from the synthetic store, golden cases first",
    "B4": "Navigli Fashion synthetic store, nightly pipeline",
}


def short_version(value: str) -> str:
    """At most 64 characters: name:tag@sha256:<12 hex> for an image reference."""
    if len(value) <= MAX_VERSION:
        return value
    if "@sha256:" in value:
        name, digest = value.split("@sha256:", 1)
        candidate = f"{name}@sha256:{digest[:12]}"
        if len(candidate) <= MAX_VERSION:
            return candidate
        return f"sha256:{digest[:12]}"
    return value[: MAX_VERSION - 3] + "..."


def _result(m: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "metric": m["metric"],
        "label": m["label"],
        "device": m["device"],
        "phase": m["phase"],
        "value": {"value": m["value"], "unit": m["unit"], "provenance": dict(m["provenance"])},
        "concurrency": m.get("concurrency"),
        "batch_size": m.get("batch_size"),
        "protocol": m.get("protocol"),
    }


def _cost_notes(part: Mapping[str, Any]) -> list[str]:
    notes = []
    for m in part.get("measurements", []):
        if m["phase"] != "cost":
            continue
        p = m["provenance"]
        notes.append(
            short(
                f"{m['label']}: {m['value']:.2f} EUR per 1,000 decisions. Nature "
                f"{p['nature']}: {p['origin']}"
            )
        )
    return notes


def _item(benchmark: str, part: Mapping[str, Any] | None, run_id: str) -> dict[str, Any]:
    pending: dict[str, Any] = {
        "id": benchmark,
        "title": TITLES[benchmark],
        "status": "pending",
        "measured_at": None,
        "dataset": DATASETS[benchmark],
        "versions": {},
        "config": {},
        "results": [],
        "notes": [],
    }
    if part is None:
        pending["notes"] = [f"Not run in run {run_id}"]
        return pending
    notes = [short(n) for n in part.get("notes", []) if n.strip()]
    status = part["status"]
    if status != "measured":
        reason = {
            "dry_run": f"Dry run only in run {run_id} (simulated numbers, not served)",
            "skipped": f"Skipped in run {run_id}",
            "failed": f"Run {run_id} failed: see bench/results/{run_id}.json",
        }[status]
        pending["notes"] = [reason, *notes]
        return pending
    real = [m for m in part["measurements"] if m["provenance"]["nature"] == "real"]
    return {
        "id": benchmark,
        "title": short(part["title"]),
        "status": "measured",
        "measured_at": part["finished_at"],
        "dataset": short(part["dataset"]),
        "versions": {
            k: short_version(str(v)) for k, v in sorted(part.get("versions", {}).items()) if v
        },
        "config": dict(part.get("config", {})),
        "results": [_result(m) for m in real],
        "notes": [*notes, *_cost_notes(part)],
    }


def benchmarks_response(doc: Mapping[str, Any]) -> dict[str, Any]:
    """{"items": [B1..B4]} for BenchmarksResponse; a benchmark absent from the run is pending."""
    parts = doc.get("benchmarks", {})
    run_id = str(doc["run_id"])
    return {"items": [_item(b, parts.get(b), run_id) for b in BENCHMARKS]}
