"""The offline evaluation report of the serving snapshot: reports/evaluation.json (US-603, US-605,
FAIR-02, NFR-04; rules 11 and 12).

A false positive needs a label, so the API can never compute one (rule 12). The offline job of
ml.risk measures FAIR-02 on the synthetic labels of the held-out test customers and writes it to
artifacts/eval/risk/metrics.json (ml.risk.run.metrics_path). The export copies the FAIR-02
aggregates of that file, and nothing else of it (counts and rates by dimension and segment, no
customer), into the snapshot, where the manifest hashes it like every served file; the fairness
report and the KPIs of the API read it. metrics.json itself is never served.

Format returnring.evaluation.v1: report_id, scenario, as_of (the evaluation instant),
model_version (the customer model evaluated), the provenance of the evaluation (its data, period
and nature) and fair02, the FairnessReport of ml.risk.fairness as metrics.json holds it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from contracts.files import atomic_write

REPORT_FILE: Final = "reports/evaluation.json"
REPORT_FORMAT: Final = "returnring.evaluation.v1"


def evaluation_document(metrics: Mapping[str, Any], model_version: str) -> dict[str, Any]:
    """The report of the offline evaluation `metrics` (artifacts/eval/risk/metrics.json) of the
    customer model `model_version`: its FAIR-02 aggregates, instant and provenance."""
    data = metrics["data"]["main"]
    start, end = str(data["period"]).split("..")
    eval_end = str(metrics["eval_end"])
    return {
        "format": REPORT_FORMAT,
        "report_id": f"eval-{eval_end[:10]}",
        "scenario": "main",
        "as_of": eval_end,
        "model_version": model_version,
        "provenance": {
            "origin": (
                f"offline evaluation (ml.risk, FAIR-02) of the batch scores at {eval_end} on "
                f"the synthetic labels of the held-out test customers; computed from: "
                f"{data['origin']}"
            ),
            "period_start": start,
            "period_end": end,
            "nature": metrics["nature"],
        },
        "fair02": metrics["fair02"],
    }


def write_report(metrics_path: Path, out: Path, model_version: str) -> Path:
    """Write reports/evaluation.json under the snapshot `out` from the metrics at
    `metrics_path`; the export calls it before the manifest, which then covers it."""
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if not isinstance(metrics, dict) or "fair02" not in metrics:
        raise ValueError(f"{metrics_path}: no FAIR-02 report in the offline evaluation")
    document = evaluation_document(metrics, model_version)
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    target = out / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write(target, lambda handle: handle.write(text.encode("utf-8")))
