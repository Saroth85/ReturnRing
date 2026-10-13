"""ASOS GraphReturns ingest: download -> verify -> convert -> profile (US-101).

    python -m pipeline.ingest [--dest data/raw/asos] [--source-dir DIR] [--skip-download]
                              [--data-root data]
                              [--calibration-out pipeline/calibration/asos_calibration.json]
                              [--report-out data/raw/asos_profile.txt]

The steps are:

1. download: the six pinned pickles into --dest, from OSF or from a local cache (--source-dir).
   --skip-download uses the files already in --dest.
2. verify: every file against its pinned sha256. The run stops here on any mismatch.
3. convert: <data-root>/raw/asos_{events,customers,variants}.parquet, contract-checked, with
   provenance.
4. profile: the plain-text report and the calibration JSON. The JSON holds aggregates only and
   is the one file meant to be committed.

Raw data stays under data/, which git ignores. Each step prints its wall time; the run ends with
the peak resident memory of the process.
"""

from __future__ import annotations

import argparse
import pickle
import resource
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pipeline.ingest import asos_convert, asos_download, asos_profile


@contextmanager
def _step(name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    print(f"[{name}] done in {time.perf_counter() - start:.1f} s", flush=True)


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.ingest",
        description="ASOS GraphReturns ingest (US-101): download, verify, convert, profile.",
    )
    parser.add_argument("--dest", type=Path, default=Path("data/raw/asos"), help="pickles")
    parser.add_argument("--source-dir", type=Path, help="local cache to link or copy from")
    parser.add_argument("--skip-download", action="store_true", help="use the files in --dest")
    parser.add_argument("--data-root", type=Path, default=Path("data"), help="contract root")
    parser.add_argument(
        "--calibration-out",
        type=Path,
        default=Path("pipeline/calibration/asos_calibration.json"),
    )
    parser.add_argument("--report-out", type=Path, help="default <data-root>/raw/asos_profile.txt")
    args = parser.parse_args(argv)
    report_out = args.report_out or args.data_root / "raw" / "asos_profile.txt"
    print(asos_download.ATTRIBUTION)

    if args.skip_download:
        print("[download] skipped: using the files in", args.dest)
    else:
        print(
            f"[download] into {args.dest}" + (f" from {args.source_dir}" if args.source_dir else "")
        )
        try:
            with _step("download"):
                actions = asos_download.download(args.dest, source_dir=args.source_dir)
        except (asos_download.IntegrityError, OSError, ValueError) as exc:
            return _fail(f"[download] failed: {exc}")
        for name, action in actions.items():
            print(f"  {name}: {action}")

    try:
        with _step("verify"):
            problems = asos_download.verify(args.dest)
    except ValueError as exc:  # a path in dest that resolves outside it
        return _fail(f"[verify] failed: {exc}")
    if problems:
        return _fail(
            "[verify] files that do not match the pinned sha256 (ADR-10):\n"
            + "\n".join(f"  {name}: {reason}" for name, reason in problems.items())
        )

    try:
        with _step("convert"):
            rows = asos_convert.convert(args.dest, args.data_root)
        for contract, counts in rows.items():
            print(f"  {contract}: " + ", ".join(f"{s} {n:,} rows" for s, n in counts.items()))
        with _step("profile"):
            doc, _ = asos_profile.profile(args.data_root, args.calibration_out, report_out)
    except (asos_download.IntegrityError, pickle.UnpicklingError, ValueError, OSError) as exc:
        # ValueError covers ConversionError, ContractError and ProfileError; OSError a path
        # that cannot be written (a directory in the way, a full disk).
        return _fail(f"[convert/profile] failed: {exc}")
    print(f"  report: {report_out}")
    print(f"  calibration: {args.calibration_out} (splits {doc['splits']})")
    print(f"peak resident memory: {_peak_rss_mb():,.0f} MiB")
    return 0
