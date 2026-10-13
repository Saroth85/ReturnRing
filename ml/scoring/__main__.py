"""CLI of the batch scoring (US-204): python -m ml.scoring --data data --out data
[--models artifacts/risk/models] [--as-of ISO [--backtest]] [--truth data --fairness PATH]

Writes <out>/scores/scores.parquet (every customer) and <out>/scores/request_scores.parquet
(every return request open at the decision time) with contracts.io, model versions, thresholds
and provenance, and prints a JSON summary. Default decision time: the last snapshot of
features/customer_features. A decision time before the models' training end is a usage error
(rule 7): the scores would be in-sample; --backtest allows it for an offline check and says so
in the origin.

The scoring never reads the ground truth (rule 12). --truth names the root of truth/labels for an
offline evaluation run only: it then also measures FAIR-02 and FAIR-03 on the held-out customers
of the training run (the split of ml.risk, from the model manifest) and writes them to --fairness,
under artifacts/eval/ by default (offline outputs, never mounted by a service). A service never
passes it.

Exit codes: 0 done, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ml.risk.data import instants, read_features, utc
from ml.scoring.batch import (
    Models,
    check_decision_time,
    read_inputs,
    score_loaded,
    summary_json,
    write,
)
from pipeline.features.snapshot import load_snapshot

DEFAULT_MODELS = Path("artifacts/risk/models")
DEFAULT_FAIRNESS = Path("artifacts/eval/scores/fairness.json")


def _instant(text: str) -> datetime:
    try:
        return utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a timezone-aware ISO instant: {text!r}") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ml.scoring", description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"), help="root of store/ and features/")
    p.add_argument("--out", type=Path, help="root for scores/*.parquet (default: --data)")
    p.add_argument("--models", type=Path, default=DEFAULT_MODELS, help="ml.risk model folder")
    p.add_argument("--as-of", type=_instant, help="decision time, ISO 8601 with a time zone")
    p.add_argument(
        "--backtest",
        action="store_true",
        help="offline check only: allow a decision time before the models' training end; the "
        "origin then says in-sample backtest (rule 7)",
    )
    p.add_argument("--truth", type=Path, help="offline evaluation only: root of truth/labels")
    p.add_argument("--fairness", type=Path, default=DEFAULT_FAIRNESS, help="FAIR-02/03 JSON")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    features, _ = read_features(args.data, "features/customer_features")
    as_of = args.as_of or instants(features)[-1]
    models = Models.load(args.models)
    try:
        check_decision_time(models, as_of, args.backtest)
    except ValueError as exc:
        p.error(str(exc))
    inputs = read_inputs(args.data, as_of)
    snap = load_snapshot(args.data, as_of)
    result = score_loaded(models, inputs, snap, as_of, backtest=args.backtest)
    result.summary["outputs"] = [str(p) for p in write(result, args.out or args.data)]
    if args.truth is not None:
        # Offline evaluation path (rule 12): imported only here.
        from ml.risk.fairness import evaluate_scores, held_out
        from ml.risk.labels import read_labels

        manifest = json.loads((args.models / "manifest.json").read_text(encoding="utf-8"))
        labels = read_labels(args.truth)
        population = held_out(inputs, labels, manifest)
        report, checks = evaluate_scores(result, inputs, snap, models.customer, labels, population)
        payload: dict[str, Any] = {
            "as_of": result.as_of.isoformat(),
            "nature": result.provenance.nature,
            "origin": result.provenance.origin,
            "fair02": report.as_dict(),
            "fair03": [c.as_dict() for c in checks],
        }
        args.fairness.parent.mkdir(parents=True, exist_ok=True)
        args.fairness.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        result.summary["fairness"] = {
            "file": str(args.fairness),
            "fair02_failures": payload["fair02"]["failures"],
            "fair03_passed": all(c.passed for c in checks),
        }
    print(summary_json(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
