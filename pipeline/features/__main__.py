"""Command line: python -m pipeline.features --store data --out data [--as-of ISO ...]
[--source data] [--clusters data]

Writes features/customer_features, features/customer_value and features/evidence_quality under
<out>/features/ for every decision time, and prints a JSON summary on stdout. Without --as-of, the
decision times are the month ends of the store period (00:00 UTC of the first day of each next
month). It reads <store>/store only, never data/truth (rule 12).

--source: train the reference model on that store instead, at each decision time (use the main
store for the out-of-distribution store, which is never trained on: US-105).
--clusters: read features/clusters under that root, rows computed at each decision time, to
report the linked-account observations (n_observations_linked) of the evidence quality. They are
never counted towards sufficient evidence (review F14), and the file must have been built from
this store (review F22). The pipeline does not pass it.

Exit codes: 0 done, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contracts.io import read_table
from pipeline.features.build import build
from pipeline.features.snapshot import month_ends, utc


def _instant(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return utc(moment)
    except ValueError as exc:
        message = f"not a timezone-aware ISO instant: {text!r} ({exc})"
        raise argparse.ArgumentTypeError(message) from None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.features",
        description="Decision-time customer features, value and evidence (US-205, 206, 209).",
    )
    p.add_argument("--store", type=Path, required=True, help="root of <store>/store/*.parquet")
    p.add_argument("--out", type=Path, required=True, help="root of <out>/features/*.parquet")
    p.add_argument("--as-of", type=_instant, nargs="+", help="decision times (ISO, with offset)")
    p.add_argument("--source", type=Path, help="train the reference model on this store")
    p.add_argument("--clusters", type=Path, help="root of features/clusters (account graph)")
    return p


def store_month_ends(store: Path) -> list[datetime]:
    """Month ends of the period in the provenance of <store>/store/orders.parquet."""
    _, prov = read_table("store/orders", store)
    midnight = datetime.min.time()
    first = datetime.combine(prov.period_start, midnight, tzinfo=UTC)
    last = datetime.combine(prov.period_end + timedelta(days=1), midnight, tzinfo=UTC)
    return month_ends(first, last)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_ofs = args.as_of or store_month_ends(args.store)
    started = time.perf_counter()  # elapsed time for the log only, never written to a file
    summary = build(
        args.store, args.out, as_ofs, source_root=args.source, clusters_root=args.clusters
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"features built in {time.perf_counter() - started:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
