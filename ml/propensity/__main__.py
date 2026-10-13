"""CLI of US-201: python -m ml.propensity --raw data/raw --out artifacts.

Offline training and evaluation job on the real ASOS tables (raw/asos_*.parquet, written by
python -m pipeline.ingest). It never reads data/truth/ (rule 12). DEVICE=cpu|gpu picks the default
device (gpu means "cuda", also on ROCm); SEED picks the default seed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ml.propensity.run import RunConfig, run
from ml.propensity.train import MAX_ROUNDS, Device

DEFAULT_DOC = Path("docs/results/us-201-propensity.md")


def _default_device() -> Device:
    return "cuda" if os.environ.get("DEVICE", "cpu").lower() in ("gpu", "cuda") else "cpu"


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="python -m ml.propensity", description=__doc__)
    parser.add_argument("--raw", type=Path, required=True, help="the raw/ folder of a data root")
    parser.add_argument("--out", type=Path, required=True, help="artifacts folder")
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="Markdown report path")
    parser.add_argument("--no-doc", action="store_true", help="do not write the report")
    parser.add_argument("--device", choices=["cpu", "cuda"], default=_default_device())
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    parser.add_argument("--threads", type=int, default=None, help="default: every CPU")
    args = parser.parse_args(args_list)
    config = RunConfig(
        seed=args.seed, device=args.device, max_rounds=args.max_rounds, threads=args.threads
    )

    def log(message: str) -> None:
        print(f"[us-201] {message}", file=sys.stderr, flush=True)

    doc = run(
        args.raw,
        args.out,
        None if args.no_doc else args.doc,
        config,
        log=log,
        argv=["python", "-m", "ml.propensity", *args_list],
    )
    verdict = "GO" if doc["go_no_go"]["go"] else "NO-GO"
    print(f"US-201 protocol B: {verdict} (ADR-10); metrics in {args.out}/metrics/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
