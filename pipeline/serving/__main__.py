"""Command line: python -m pipeline.serving --data data --artifacts artifacts --out serving
[--as-of ISO] [--seed N]

Writes the serving snapshot the API reads (ADR-16) and prints a JSON summary. Default instant:
the batch scoring instant of <data>/scores/scores.parquet. It never reads data/truth (rule 12).

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

from pipeline.features.snapshot import utc
from pipeline.serving.export import export, summary


def _instant(text: str) -> datetime:
    try:
        return utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a timezone-aware ISO instant: {text!r}") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m pipeline.serving", description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"), help="root of store/, features/...")
    p.add_argument("--artifacts", type=Path, default=Path("artifacts"), help="artifacts root")
    p.add_argument("--out", type=Path, default=Path("serving"), help="the serving snapshot")
    p.add_argument("--as-of", type=_instant, help="snapshot instant (default: the scoring one)")
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started = time.perf_counter()  # for the log only, never written to a file

    def log(message: str) -> None:
        print(f"[serving {time.perf_counter() - started:6.1f} s] {message}", file=sys.stderr)

    result = export(args.data, args.artifacts, args.out, as_of=args.as_of, seed=args.seed, log=log)
    print(json.dumps(summary(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
