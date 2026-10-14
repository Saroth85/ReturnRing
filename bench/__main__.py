"""CLI of the benchmarks (US-902). `make bench DEVICE=cpu|gpu` drives it; see docs/benchmarks.md.

    python -m bench run --device cpu|gpu [--parts B1,B2,B3,B4] [--run-id ID] [--no-merge] ...
    python -m bench first-request --run-id ID
    python -m bench merge --run-id ID [--force-latest]
    python -m bench report --run bench/results/<run-id>.json --out docs/results/<file>.md

`run` writes one part per benchmark to artifacts/bench/<run-id>/<B>.json, in the order B1, B2,
B4, B3 (B3's cost reads B4's pipeline time; on the droplet B1 and B2 run before vLLM starts),
then merges the parts into bench/results/<run-id>.json and moves the latest pointer. A part that
raises is written as "failed" with the error, and the next part still runs; the exit code is 1
when a part failed. DEVICE=gpu: B1 and B2 time the CPU and the GPU (cuda on ROCm) in the same
process, B3 is live against VLLM_BASE_URL, B4 samples amd-smi. DEVICE=cpu: CPU only, B3 a dry
run against the local fake server. `first-request` times one guided-JSON request against
VLLM_BASE_URL right after vLLM became healthy (`make bench-vllm`, before B4 uses vLLM) and writes
artifacts/bench/<run-id>/first-request.json, the cold start B3 reports.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from bench.record import (
    BENCHMARKS,
    Part,
    check_run_id,
    merge,
    new_run_id,
    read_json,
    run_device,
    utc_now,
    write_part,
    write_results,
)

ARTIFACTS = Path("artifacts/bench")
RESULTS = Path("bench/results")
WORK = Path("bench-work")
RUN_ORDER = ("B1", "B2", "B4", "B3")
TITLES = {
    "B1": "XGBoost training and scoring on ASOS GraphReturns",
    "B2": "GraphSAGE on ASOS GraphReturns",
    "B3": "Gemma on vLLM: latency, throughput and cost per decision",
    "B4": "Nightly pipeline: duration per step and GPU utilisation",
}


def _list(text: str, cast: Callable[[str], Any] = str) -> tuple[Any, ...]:
    return tuple(cast(x.strip()) for x in text.split(",") if x.strip())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m bench",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run benchmarks and write their parts")
    run.add_argument(
        "--device",
        choices=["cpu", "gpu"],
        default="gpu" if os.environ.get("DEVICE") == "gpu" else "cpu",
    )
    run.add_argument("--run-id", help="default: <UTC now>-<device>")
    run.add_argument("--parts", default=",".join(BENCHMARKS), help="default B1,B2,B3,B4")
    run.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    run.add_argument("--results", type=Path, default=RESULTS)
    run.add_argument("--no-merge", action="store_true", help="write the parts only")
    run.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    run.add_argument("--raw", type=Path, default=Path("data/raw"), help="raw/ of ASOS (B1, B2)")
    b1 = run.add_argument_group("B1")
    b1.add_argument("--b1-protocols", default="A,B")
    b1.add_argument("--b1-fractions", default="0.1,0.25,0.5,1")
    b1.add_argument("--b1-repeats", type=int, default=3)
    b1.add_argument("--b1-fixed-rounds", type=int, default=200, help="0: no fixed-rounds run")
    b1.add_argument("--b1-single-row-calls", type=int, default=200)
    b1.add_argument("--threads", type=int, help="CPU threads (default: every CPU)")
    b2 = run.add_argument_group("B2")
    b2.add_argument(
        "--b2-samples",
        default="0.25,0.5,1",
        help="customer shares of the GraphSAGE runs (1: every event, the US-202 run)",
    )
    b2.add_argument(
        "--gnn-out",
        type=Path,
        default=Path("artifacts"),
        help="artifacts folder of the full-data US-202 runs (eval/gnn/us-202-asos-*.json)",
    )
    b3 = run.add_argument_group("B3")
    b3.add_argument(
        "--b3", choices=["live", "dry-run", "skip"], help="default: live on gpu, dry-run on cpu"
    )
    b3.add_argument(
        "--data",
        type=Path,
        help="store with scores for the B3 cases (default: B4's store of this run, else data)",
    )
    b3.add_argument("--b3-batches", default="1,8,32")
    b3.add_argument("--b3-repeats", type=int, default=2)
    b3.add_argument(
        "--b3-concurrency", default="4,16", help="closed-loop levels (concurrency 1 is batch 1)"
    )
    b3.add_argument("--b3-requests", type=int, default=16, help="decisions per load, at least")
    b3.add_argument("--b3-warmup", type=int, default=2)
    b3.add_argument("--b3-timeout", type=float, default=180.0)
    b4 = run.add_argument_group("B4")
    b4.add_argument("--b4-scale", type=float, default=1.0)
    b4.add_argument("--b4-steps", help="default: every step")
    b4.add_argument(
        "--b4-sampler",
        choices=["none", "inprocess", "external"],
        help="default: external if --b4-samples exists, none on cpu, inprocess on gpu",
    )
    b4.add_argument("--b4-samples", type=Path, help="amd-smi samples file (JSON lines)")
    b4.add_argument("--amd-smi", default="amd-smi metric --json", help="sampler command")
    b4.add_argument("--b4-discard-data", action="store_true", help="delete the generated store")
    b4.add_argument(
        "--work",
        type=Path,
        default=WORK,
        help="B4's pipeline copy: <work>/<run-id> (never data/, never under artifacts/)",
    )
    f = sub.add_parser("first-request", help="time the first request after vLLM is healthy")
    f.add_argument("--run-id", required=True)
    f.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    f.add_argument("--timeout", type=float, default=600.0)
    m = sub.add_parser("merge", help="assemble bench/results/<run-id>.json")
    m.add_argument("--run-id", required=True)
    m.add_argument("--artifacts", type=Path, default=ARTIFACTS)
    m.add_argument("--results", type=Path, default=RESULTS)
    m.add_argument(
        "--force-latest",
        action="store_true",
        help="move latest.json to this run even if it is a CPU run or a GPU run is the latest",
    )
    r = sub.add_parser("report", help="Markdown report of a run file")
    r.add_argument("--run", type=Path, required=True, help="bench/results/<run-id>.json")
    r.add_argument("--out", type=Path, required=True)
    return p


def _log(message: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def _failed(benchmark: str, started: Any, exc: BaseException) -> Part:
    return Part(
        benchmark=benchmark,
        status="failed",
        title=TITLES[benchmark],
        dataset="see the error",
        started_at=started,
        finished_at=utc_now(),
        notes=[f"{type(exc).__name__}: {exc}"[:300]],
        details={"traceback": traceback.format_exc()[-4000:]},
    )


def _skipped(benchmark: str, reason: str) -> Part:
    now = utc_now()
    return Part(
        benchmark=benchmark,
        status="skipped",
        title=TITLES[benchmark],
        dataset="not run",
        started_at=now,
        finished_at=now,
        notes=[reason],
    )


def _part(benchmark: str, args: argparse.Namespace, run_dir: Path) -> Part:
    gpu = args.device == "gpu"
    if benchmark == "B1":
        from bench.b1_xgboost import B1Config, run_b1

        return run_b1(
            B1Config(
                raw_dir=args.raw,
                devices=("cpu", "cuda") if gpu else ("cpu",),
                protocols=_list(args.b1_protocols),
                fractions=_list(args.b1_fractions, float),
                repeats=args.b1_repeats,
                seed=args.seed,
                threads=args.threads,
                fixed_rounds=args.b1_fixed_rounds or None,
                single_row_calls=args.b1_single_row_calls,
            ),
            _log,
        )
    if benchmark == "B2":
        from bench.b2_gnn import B2Config, run_b2

        return run_b2(
            B2Config(
                raw_dir=args.raw,
                devices=("cpu", "cuda") if gpu else ("cpu",),
                samples=_list(args.b2_samples, float),
                seed=args.seed,
                out=args.gnn_out,
                run_dir=run_dir,
            ),
            _log,
        )
    if benchmark == "B3":
        mode = args.b3 or ("live" if gpu else "dry-run")
        if mode == "skip":
            return _skipped("B3", "B3 skipped on request (--b3 skip)")
        from bench.b3_llm import B3Config, run_b3

        b4_data = args.work / run_dir.name / "data"
        data = args.data or (b4_data if (b4_data / "scores").exists() else Path("data"))
        return run_b3(
            B3Config(
                mode="live" if mode == "live" else "dry_run",
                data_root=data,
                base_url=os.environ.get("VLLM_BASE_URL", ""),
                api_key=os.environ.get("VLLM_API_KEY", ""),
                model=os.environ.get("GEMMA_MODEL", ""),
                batch_sizes=_list(args.b3_batches, int),
                batch_repeats=args.b3_repeats,
                concurrency=_list(args.b3_concurrency, int),
                requests_per_level=args.b3_requests,
                warmup=args.b3_warmup,
                timeout_s=args.b3_timeout,
                seed=args.seed,
                vllm_start_file=run_dir / "vllm-start.txt",
                vllm_healthy_file=run_dir / "vllm-healthy.txt",
                vllm_conditions_file=run_dir / "vllm-start-conditions.txt",
                first_request_file=run_dir / "first-request.json",
                disk_used_file=run_dir / "disk-used-gb.txt",
                b4_part=run_dir / "B4.json",
            ),
            _log,
        )
    from bench.b4_pipeline import STEP_NAMES, B4Config, SamplerMode, run_b4

    samples = args.b4_samples
    sampler: SamplerMode = args.b4_sampler or (
        "external" if samples is not None else ("inprocess" if gpu else "none")
    )
    return run_b4(
        B4Config(
            run_dir=run_dir,
            device=args.device,
            seed=args.seed,
            scale=args.b4_scale,
            steps=_list(args.b4_steps) if args.b4_steps else STEP_NAMES,
            sampler=sampler,
            samples_file=samples,
            amd_smi=tuple(shlex.split(args.amd_smi)),
            keep_data=not args.b4_discard_data,
            work_dir=args.work / run_dir.name,
        ),
        _log,
    )


def _merge(run_id: str, artifacts: Path, results: Path, force_latest: bool) -> int:
    from bench import env

    doc = merge(run_id, artifacts / run_id, git=env.git_info())
    target, latest = write_results(results, doc, force_latest=force_latest)
    _log(
        f"wrote {target}"
        + (
            f" and {latest}"
            if latest
            else " (latest.json unchanged: a CPU run needs --force-latest, and a GPU run stays)"
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "merge":
        return _merge(check_run_id(args.run_id), args.artifacts, args.results, args.force_latest)
    if args.command == "first-request":
        from bench.b3_llm import first_request

        out = args.artifacts / check_run_id(args.run_id) / "first-request.json"
        ok = first_request(
            os.environ.get("VLLM_BASE_URL", ""),
            os.environ.get("VLLM_API_KEY", ""),
            os.environ.get("GEMMA_MODEL", ""),
            out,
            args.timeout,
        )
        _log(f"first request {'done' if ok else 'FAILED'} -> {out}")
        return 0 if ok else 1
    if args.command == "report":
        from bench.report import render

        doc = read_json(args.run)
        command = f"python -m bench report --run {args.run} --out {args.out}"
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(doc, command), encoding="utf-8")
        _log(f"wrote {args.out}")
        return 0
    run_id = check_run_id(args.run_id) if args.run_id else new_run_id(args.device)
    if run_device(run_id) != args.device:
        raise SystemExit(f"run id {run_id} is not a {args.device} run")
    wanted = set(_list(args.parts))
    unknown = wanted - set(BENCHMARKS)
    if unknown:
        raise SystemExit(f"unknown parts {sorted(unknown)}")
    run_dir = args.artifacts / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _log(f"run {run_id}: parts {', '.join(b for b in RUN_ORDER if b in wanted)} in {run_dir}")
    failed = False
    for benchmark in (b for b in RUN_ORDER if b in wanted):
        started = utc_now()
        try:
            part = _part(benchmark, args, run_dir)
        except Exception as exc:  # noqa: BLE001 - written as a failed part, the run goes on
            _log(f"{benchmark} failed: {type(exc).__name__}: {exc}")
            part = _failed(benchmark, started, exc)
        failed = failed or part.status == "failed"
        _log(
            f"{benchmark}: {part.status}, {len(part.measurements)} measurements -> "
            f"{write_part(run_dir, part)}"
        )
    if not args.no_merge:
        _merge(run_id, args.artifacts, args.results, False)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
