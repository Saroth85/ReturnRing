"""CLI of US-202 and A4: python -m ml.gnn {asos,embed,report} (see ml/gnn/__init__.py).

    python -m ml.gnn asos --raw data/raw --out artifacts [--device cpu|gpu] [--sample 0.25]
    python -m ml.gnn embed --store data --out data --models artifacts [--fit-as-of T] [--as-of T]
    python -m ml.gnn embed --store data/ood --out data/ood \
        --encoder artifacts/gnn/models/customer_encoder
    python -m ml.gnn report --artifacts artifacts --doc docs/results/us-202-graphsage.md

asos and embed need PyTorch (DGL is used when importable); report needs neither. DEVICE=cpu|gpu
sets the default device, SEED the default seed. The cold start (torch and DGL import, device
context, first kernels) is timed from the start of the command, apart from the job.

Exit codes: 0 done, 2 usage or GPU requested but not visible.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

DEFAULT_DOC = Path("docs/results/us-202-graphsage.md")


def _log(prefix: str) -> Any:
    started = time.perf_counter()

    def log(message: str) -> None:
        print(f"[{prefix} {time.perf_counter() - started:7.1f} s] {message}", file=sys.stderr)

    return log


def _instant(text: str) -> datetime:
    from pipeline.graph.build import parse_instant

    return parse_instant(text)


def _device_default() -> str:
    return "gpu" if os.environ.get("DEVICE", "cpu").lower() in ("gpu", "cuda") else "cpu"


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--device", choices=["cpu", "gpu"], default=_device_default())
    p.add_argument("--backend", choices=["auto", "dgl", "torch"], default="auto")
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    p.add_argument("--threads", type=int, default=None, help="torch CPU threads (default: all)")
    p.add_argument("--aggregator", choices=["mean", "pool"], default="mean")
    p.add_argument(
        "--no-deterministic",
        action="store_true",
        help="do not ask torch for deterministic algorithms (faster on some GPU kernels)",
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ml.gnn", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("asos", help="US-202: GraphSAGE on ASOS against XGBoost and the paper")
    a.add_argument("--raw", type=Path, required=True, help="the raw/ folder of a data root")
    a.add_argument("--out", type=Path, required=True, help="artifacts folder")
    a.add_argument("--protocols", default="B,A", help="comma list of ADR-10 protocols, B first")
    a.add_argument("--sample", type=float, default=1.0, help="share of customers kept (0, 1]")
    a.add_argument("--max-epochs", type=int, default=None)
    a.add_argument("--batch-size", type=int, default=None)
    a.add_argument("--no-xgboost", action="store_true", help="skip the XGBoost comparison")
    a.add_argument("--no-mlp", action="store_true", help="skip the MLP control")
    _common(a)

    e = sub.add_parser("embed", help="A4: customer embeddings by link prediction")
    e.add_argument("--store", type=Path, required=True, help="root holding store/ and features/")
    e.add_argument(
        "--out", type=Path, help="root for features/customer_embeddings (default --store)"
    )
    e.add_argument("--models", type=Path, default=Path("artifacts"), help="artifacts folder")
    e.add_argument("--encoder", type=Path, help="apply this saved encoder (no training)")
    e.add_argument(
        "--fit-as-of", type=_instant, help="encoder training time (default: risk train end)"
    )
    e.add_argument("--as-of", type=_instant, action="append", help="embedding time (repeatable)")
    e.add_argument("--max-epochs", type=int, default=None)
    _common(e)

    r = sub.add_parser("report", help="docs/results/us-202-graphsage.md from the metrics files")
    r.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    r.add_argument("--doc", type=Path, default=DEFAULT_DOC)
    return p


def _cold_start(device_name: str, backend_name: str) -> tuple[Any, Any, dict[str, float]]:
    """Import torch (and DGL), create the device context and run the first kernels, timed."""
    t0 = time.perf_counter()
    from ml.gnn import runtime

    t1 = time.perf_counter()
    backend = runtime.resolve_backend(backend_name)
    t2 = time.perf_counter()
    device = runtime.resolve_device(device_name)
    warm = runtime.warm_up(device)
    return (
        backend,
        device,
        {
            "import_torch_seconds": t1 - t0,
            "import_backend_seconds": t2 - t1,
            "device_init_and_first_kernels_seconds": warm,
        },
    )


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(args_list)
    if args.command == "report":
        from ml.gnn.report import write_report

        path = write_report(args.artifacts, args.doc)
        print(f"US-202: wrote {path}")
        return 0
    try:
        backend, device, cold = _cold_start(args.device, args.backend)
    except RuntimeError as exc:  # GpuUnavailable, or --backend dgl without DGL
        print(f"error: {exc}", file=sys.stderr)
        return 2
    argv_full = ["python", "-m", "ml.gnn", *args_list]
    if args.command == "asos":
        from ml.gnn.asos import AsosConfig, metrics_path, run
        from ml.gnn.train import TrainConfig

        train = TrainConfig(seed=args.seed)
        if args.max_epochs is not None:
            train = TrainConfig(**{**train.as_dict(), "max_epochs": args.max_epochs})
        if args.batch_size is not None:
            train = TrainConfig(**{**train.as_dict(), "batch_size": args.batch_size})
        protocols = tuple(p.strip() for p in args.protocols.split(",") if p.strip())
        if not protocols or any(p not in ("A", "B") for p in protocols):
            print("error: --protocols takes A and/or B", file=sys.stderr)
            return 2
        config = AsosConfig(
            seed=args.seed,
            device=args.device,
            backend=backend,
            protocols=cast(tuple[Literal["A", "B"], ...], protocols),
            sample=args.sample,
            aggregator=args.aggregator,
            deterministic=not args.no_deterministic,
            threads=args.threads,
            xgboost=not args.no_xgboost,
            mlp=not args.no_mlp,
            train=train,
        )
        doc = run(args.raw, args.out, config, backend, device, cold, _log("us-202"), argv_full)
        b = doc["protocols"].get("B", {}).get("models", {}).get("graphsage")
        line = "" if b is None else f"; protocol B F1 {b['test']['decision_time']['f1']:.4f}"
        print(f"US-202 ASOS: metrics in {metrics_path(args.out, device)}{line}")
        return 0
    from ml.gnn.embeddings import EmbedConfig, run_embed

    config_e = EmbedConfig(
        seed=args.seed,
        aggregator=args.aggregator,
        deterministic=not args.no_deterministic,
        threads=args.threads,
    )
    if args.max_epochs is not None:
        config_e = EmbedConfig(**{**config_e.as_dict(), "max_epochs": args.max_epochs})
    doc = run_embed(
        store=args.store,
        out=args.out or args.store,
        artifacts=args.models,
        encoder=args.encoder,
        fit_as_of=args.fit_as_of,
        as_ofs=args.as_of,
        config=config_e,
        backend=backend,
        device=device,
        cold_start=cold,
        log=_log("a4-embed"),
        argv=argv_full,
    )
    print(f"A4 embeddings: {doc['output']['file']} ({doc['output']['rows']} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
