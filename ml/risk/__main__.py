"""CLI of US-204: python -m ml.risk --data data --out artifacts --scores data
[--results docs/results/us-204-risk.md]

Offline training and evaluation job (reads data/truth, rule 12 as amended by ADR-13): trains the
ablation A0..A3 and the request model on the grouped, time-ordered split, evaluates them on the
held-out groups and on the out-of-distribution store (data/ood, data/truth/ood) when present,
saves the models under <out>/risk/models, writes every number to <out>/risk/metrics.json, runs the
batch scoring at the evaluation time into <scores>/scores/, measures FAIR-02 and FAIR-03 on it,
and rewrites the results file. SEED sets the default seed.

Exit codes: 0 done, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from ml.risk.data import utc
from ml.risk.model import TrainConfig
from ml.risk.report import render
from ml.risk.run import RunConfig, run
from ml.risk.split import SplitConfig

DEFAULT_RESULTS = Path("docs/results/us-204-risk.md")


def _instant(text: str) -> datetime:
    try:
        return utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a timezone-aware ISO instant: {text!r}") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ml.risk", description=__doc__)
    p.add_argument(
        "--data", type=Path, default=Path("data"), help="root of store/, features/, truth/"
    )
    p.add_argument("--out", type=Path, default=Path("artifacts"), help="artifacts root")
    p.add_argument("--scores", type=Path, help="root for scores/*.parquet (default: --data)")
    p.add_argument("--no-scores", action="store_true", help="do not write the batch scores")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS, help="Markdown results file")
    p.add_argument("--no-results", action="store_true", help="do not write the results file")
    p.add_argument("--train-end", type=_instant, help="train end (default: 3 months before)")
    p.add_argument("--eval-end", type=_instant, help="evaluation time (default: last snapshot)")
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started = time.perf_counter()  # for the log only, never written to a file

    def log(message: str) -> None:
        print(f"[us-204 {time.perf_counter() - started:6.1f} s] {message}", file=sys.stderr)

    config = RunConfig(
        train_end=args.train_end,
        eval_end=args.eval_end,
        split=SplitConfig(seed=args.seed),
        train=TrainConfig(seed=args.seed),
    )
    scores = None if args.no_scores else (args.scores or args.data)
    run(args.data, args.out, scores, config, log)
    # Rendered from the file on disk, so the results file is a function of metrics.json alone.
    doc = json.loads((args.out / "risk" / "metrics.json").read_text(encoding="utf-8"))
    if not args.no_results:
        args.results.parent.mkdir(parents=True, exist_ok=True)
        args.results.write_text(render(doc), encoding="utf-8")
        log(f"wrote {args.results}")
    checks = doc["golden"]["checks"]
    print(
        f"US-204: models in {args.out}/risk/models, metrics in {args.out}/risk/metrics.json; "
        f"golden cases as expected: {all(checks.values())}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
