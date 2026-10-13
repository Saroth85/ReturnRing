"""Command line: python -m pipeline.generator --calibration <json> --out data --seed 42
[--scale 1.0] [--scenario main|ood]

Writes the Navigli Fashion store (synthetic, calibrated on ASOS aggregates) and prints a JSON
summary on stdout; the elapsed time goes to stderr. Exit codes: 0 done, 1 invalid calibration (or a
valid one no store can be built from, such as one-size types only) or an output folder that cannot
be written, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from pipeline.generator.calibration import CalibrationError, load_calibration
from pipeline.generator.config import SCENARIO_NAMES
from pipeline.generator.core import generate


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return value


def _seed(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return value


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m pipeline.generator",
        description="Generate the Navigli Fashion synthetic store (US-102..US-105).",
    )
    p.add_argument("--calibration", required=True, type=Path, help="calibration JSON (ADR-13)")
    p.add_argument("--out", type=Path, default=Path("data"), help="output root (default: data)")
    p.add_argument("--seed", type=_seed, default=42, help="random seed (default: 42)")
    p.add_argument("--scale", type=_positive, default=1.0, help="1.0 = 20,000 customers")
    p.add_argument("--scenario", choices=SCENARIO_NAMES, default="main")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        cal = load_calibration(args.calibration)
    except CalibrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    started = time.perf_counter()  # elapsed time for the log only, never written to a file
    try:
        summary = generate(cal, args.out, args.seed, args.scale, args.scenario)
    except CalibrationError as exc:  # valid file, but no store can be built from it (G3)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:  # a folder in the way, no permission on data/ (review P5), full disk
        print(f"error: cannot write the store under {args.out}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"generated in {time.perf_counter() - started:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
