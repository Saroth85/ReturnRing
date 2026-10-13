"""Command line: python -m pipeline.anomalies --store data --out data [--as-of ISO ...]

Writes features/product_anomalies under <out>/features/ for every decision time and prints a JSON
summary on stdout. Without --as-of, the decision times are the Mondays 00:00 UTC of the store
period and the instant after its last day. It reads <store>/store only, never data/truth (rule 12).

Exit codes: 0 done, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pipeline.anomalies.build import build, store_decision_times
from pipeline.features.snapshot import utc


def _instant(text: str) -> datetime:
    try:
        return utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError as exc:
        message = f"not a timezone-aware ISO instant: {text!r} ({exc})"
        raise argparse.ArgumentTypeError(message) from None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.anomalies",
        description="Product anomalies per variant, reason and sales week (US-208, spec 8.9).",
    )
    p.add_argument("--store", type=Path, required=True, help="root of <store>/store/*.parquet")
    p.add_argument("--out", type=Path, required=True, help="root of <out>/features/*.parquet")
    p.add_argument("--as-of", type=_instant, nargs="+", help="decision times (ISO, with offset)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_ofs = args.as_of or store_decision_times(args.store)
    started = time.perf_counter()  # elapsed time for the log only, never written to a file
    summary = build(args.store, args.out, as_ofs)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"product anomalies built in {time.perf_counter() - started:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
