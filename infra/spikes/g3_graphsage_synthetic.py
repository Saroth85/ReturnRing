"""Spike SPK-G3 (and the DGL half of SPK-G4): GraphSAGE on DGL, CPU against GPU (ROCm/HIP).

Self-contained: the graph, features and labels are synthetic and seeded, so nothing is
downloaded (CLAUDE.md rule 2). Model as in docs/spec.md section 8.1: one SAGEConv layer with
16 hidden units, max-pool aggregation ("pool"), dropout 0.2, Adam with learning rate 0.01,
then a linear head for a binary label. The numbers measure the stack, not model quality:
labels are random-ish by construction, so F1 here means nothing.

On ROCm builds of PyTorch the HIP device is exposed through the torch.cuda API, so the GPU is
torch.device("cuda") and torch.version.hip is set.

Runs in the trainer-gnn container (rocm/dgl), working directory /app (make spike-g3):
    python infra/spikes/g3_graphsage_synthetic.py --small     # smoke test, CPU and GPU
    python infra/spikes/g3_graphsage_synthetic.py             # 1.2M nodes, 12M edges (spec 11)
    python infra/spikes/g3_graphsage_synthetic.py --hetero    # customer/product + virtual nodes
    python infra/spikes/g3_graphsage_synthetic.py --device gpu --min-seconds 300   # SPK-G4
    python infra/spikes/g3_graphsage_synthetic.py --out artifacts/spikes/g3.json
    python infra/spikes/g3_graphsage_synthetic.py --device cpu \
        --gpu-result artifacts/spikes/g3_gpu.json   # CPU baseline, speedup vs the GPU file

The runbook times the GPU runs while the Gemma weights download and the CPU runs afterwards, alone
on the CPU; --gpu-result computes the speedup from the GPU file, --context records the conditions.

Exit codes: 0 pass, 1 training error or non-finite loss, 2 GPU requested but not visible.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from typing import Any

os.environ.setdefault("DGLBACKEND", "pytorch")

import dgl  # noqa: E402
import dgl.nn as dglnn  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402

HIDDEN = 16
DROPOUT = 0.2
LR = 0.01
N_CLASSES = 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--nodes", type=int, default=1_200_000, help="homogeneous graph: number of nodes"
    )
    p.add_argument(
        "--edges", type=int, default=12_000_000, help="homogeneous graph: number of edges"
    )
    p.add_argument("--feat-dim", type=int, default=32, help="input feature size")
    p.add_argument("--epochs", type=int, default=20, help="timed full-graph epochs per device")
    p.add_argument(
        "--warmup", type=int, default=3, help="untimed epochs per device (kernel load, caches)"
    )
    p.add_argument("--device", choices=["cpu", "gpu", "both"], default="both")
    p.add_argument("--hetero", action="store_true", help="heterogeneous graph with virtual nodes")
    p.add_argument("--small", action="store_true", help="tiny graph for a quick smoke test")
    p.add_argument(
        "--min-seconds",
        type=float,
        default=0.0,
        help="SPK-G4: keep training on the GPU until this many seconds have passed",
    )
    p.add_argument(
        "--threads", type=int, default=0, help="CPU threads for torch (0 = torch default)"
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    p.add_argument("--out", default="", help="also write the JSON result to this path")
    p.add_argument("--context", default="", help="recorded: what else ran at the same time")
    p.add_argument("--gpu-result", default="", help="CPU run: GPU result file for the speedup")
    args = p.parse_args()
    if args.small:
        args.nodes, args.edges, args.epochs, args.warmup = 20_000, 200_000, 5, 1
    if args.epochs < 1 or args.warmup < 0:
        p.error("--epochs must be >= 1 and --warmup >= 0")
    if args.edges > args.nodes * args.nodes // 20:
        p.error(
            "--edges must stay below 5% of nodes^2 (dgl.rand_graph samples without replacement)"
        )
    return args


# ---------------------------------------------------------------- synthetic data


def seed_everything(seed: int) -> None:
    dgl.seed(seed)
    torch.manual_seed(seed)


def synthetic_labels(x: torch.Tensor, gen: torch.Generator) -> torch.Tensor:
    """Binary label from a fixed random projection plus noise, so the loss can go down."""
    w = torch.randn(x.shape[1], generator=gen)
    score = x @ w + 0.5 * torch.randn(x.shape[0], generator=gen)
    return (score > 0).long()


def build_homogeneous(args: argparse.Namespace) -> dict[str, Any]:
    gen = torch.Generator().manual_seed(args.seed)
    g = dgl.rand_graph(args.nodes, args.edges)  # built on CPU, int64 ids
    x = torch.randn(args.nodes, args.feat_dim, generator=gen)
    y = synthetic_labels(x, gen)
    train_mask = torch.rand(args.nodes, generator=gen) < 0.8
    return {
        "graph": g,
        "x": x,
        "y": y,
        "train_mask": train_mask,
        "target": None,
        "desc": {
            "kind": "homogeneous dgl.rand_graph",
            "nodes": g.num_nodes(),
            "edges": g.num_edges(),
        },
    }


def build_heterogeneous(args: argparse.Namespace) -> dict[str, Any]:
    """Customers and products, plus virtual nodes for brand, product type and return reason
    (spec 8.1). Every relation has a reverse so messages reach the customer nodes we classify."""
    gen = torch.Generator().manual_seed(args.seed)
    n_cust = max(args.nodes // 2, 10)
    n_prod = max(args.nodes // 2, 10)
    n_brand, n_ptype, n_reason = 500, 60, 12
    n_buy = args.edges // 2

    def ri(high: int, n: int) -> torch.Tensor:
        return torch.randint(0, high, (n,), generator=gen)

    buy_c, buy_p = ri(n_cust, n_buy), ri(n_prod, n_buy)
    prod_ids = torch.arange(n_prod)
    brand_of, ptype_of = ri(n_brand, n_prod), ri(n_ptype, n_prod)
    n_ret = n_buy // 4
    ret_c, ret_r = ri(n_cust, n_ret), ri(n_reason, n_ret)
    data = {
        ("customer", "buys", "product"): (buy_c, buy_p),
        ("product", "bought_by", "customer"): (buy_p, buy_c),
        ("product", "of_brand", "brand"): (prod_ids, brand_of),
        ("brand", "brand_of", "product"): (brand_of, prod_ids),
        ("product", "of_type", "ptype"): (prod_ids, ptype_of),
        ("ptype", "type_of", "product"): (ptype_of, prod_ids),
        ("customer", "returns_for", "reason"): (ret_c, ret_r),
        ("reason", "reason_of", "customer"): (ret_r, ret_c),
    }
    counts = {
        "customer": n_cust,
        "product": n_prod,
        "brand": n_brand,
        "ptype": n_ptype,
        "reason": n_reason,
    }
    g = dgl.heterograph(data, num_nodes_dict=counts)
    x = {nt: torch.randn(n, args.feat_dim, generator=gen) for nt, n in counts.items()}
    y = synthetic_labels(x["customer"], gen)
    train_mask = torch.rand(n_cust, generator=gen) < 0.8
    return {
        "graph": g,
        "x": x,
        "y": y,
        "train_mask": train_mask,
        "target": "customer",
        "desc": {
            "kind": "dgl.heterograph, virtual brand/ptype/reason nodes",
            "nodes": g.num_nodes(),
            "edges": g.num_edges(),
            "node_counts": counts,
        },
    }


# ---------------------------------------------------------------- model


class Sage(nn.Module):
    """One SAGEConv layer (16, max-pool, dropout 0.2) and a linear head."""

    def __init__(self, in_feats: int) -> None:
        super().__init__()
        self.conv = dglnn.SAGEConv(in_feats, HIDDEN, "pool", feat_drop=DROPOUT)
        self.drop = nn.Dropout(DROPOUT)
        self.head = nn.Linear(HIDDEN, N_CLASSES)

    def forward(self, g: dgl.DGLGraph, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.drop(F.relu(self.conv(g, x))))


class HeteroSage(nn.Module):
    """Same layer per relation, summed by HeteroGraphConv; head on the target node type."""

    def __init__(self, in_feats: int, rels: list[str], target: str) -> None:
        super().__init__()
        self.target = target
        self.conv = dglnn.HeteroGraphConv(
            {r: dglnn.SAGEConv(in_feats, HIDDEN, "pool", feat_drop=DROPOUT) for r in rels},
            aggregate="sum",
        )
        self.drop = nn.Dropout(DROPOUT)
        self.head = nn.Linear(HIDDEN, N_CLASSES)

    def forward(self, g: dgl.DGLGraph, x: dict[str, torch.Tensor]) -> torch.Tensor:
        h = self.conv(g, x)[self.target]
        return self.head(self.drop(F.relu(h)))


# ---------------------------------------------------------------- training and timing


def sync(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def to_device(data: dict[str, Any], device: torch.device) -> dict[str, Any]:
    x = data["x"]
    return {
        "graph": data["graph"].to(device),
        "x": {k: v.to(device) for k, v in x.items()} if isinstance(x, dict) else x.to(device),
        "y": data["y"].to(device),
        "train_mask": data["train_mask"].to(device),
    }


def run_on(device: torch.device, data: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    torch.manual_seed(args.seed)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    t0 = time.perf_counter()
    d = to_device(data, device)
    sync(device)
    copy_s = time.perf_counter() - t0

    if data["target"] is None:
        model: nn.Module = Sage(args.feat_dim)
    else:
        model = HeteroSage(args.feat_dim, list(d["graph"].etypes), data["target"])
    model = model.to(device)
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    g, x, y, mask = d["graph"], d["x"], d["y"], d["train_mask"]

    def epoch() -> float:
        model.train()
        logits = model(g, x)
        loss = F.cross_entropy(logits[mask], y[mask])
        opt.zero_grad()
        loss.backward()
        opt.step()
        return loss.item()  # .item() also waits for the device

    first_loss = None
    for _ in range(args.warmup):
        loss = epoch()
        if first_loss is None:
            first_loss = loss
    times, losses = [], []
    start = time.perf_counter()
    i = 0
    while i < args.epochs or (
        device.type == "cuda" and time.perf_counter() - start < args.min_seconds
    ):
        sync(device)
        t = time.perf_counter()
        losses.append(epoch())
        sync(device)
        times.append(time.perf_counter() - t)
        i += 1
    wall = time.perf_counter() - start

    model.eval()
    with torch.no_grad():
        pred = model(g, x).argmax(dim=1)
        acc = (pred[mask] == y[mask]).float().mean().item()

    out: dict[str, Any] = {
        "device": str(device),
        "copy_to_device_s": round(copy_s, 4),
        "timed_epochs": len(times),
        "epoch_s_median": round(statistics.median(times), 5),
        "epoch_s_mean": round(statistics.fmean(times), 5),
        "epoch_s_min": round(min(times), 5),
        "timed_wall_s": round(wall, 3),
        "loss_first": round(first_loss if first_loss is not None else losses[0], 5),
        "loss_last": round(losses[-1], 5),
        "train_acc": round(acc, 4),
        "finite": all(math.isfinite(v) for v in losses),
    }
    if device.type == "cuda":
        out["gpu_peak_allocated_gb"] = round(torch.cuda.max_memory_allocated(device) / 1e9, 3)
        out["gpu_peak_reserved_gb"] = round(torch.cuda.max_memory_reserved(device) / 1e9, 3)
        # Device-wide, all processes (vLLM included under SPK-G4): a second source next to amd-smi.
        free, total = torch.cuda.mem_get_info(device)
        out["gpu_device_used_gb_at_end"] = round((total - free) / 1e9, 3)
    else:
        out["cpu_threads"] = torch.get_num_threads()
    return out


def environment() -> dict[str, Any]:
    gpu = torch.cuda.is_available()
    env: dict[str, Any] = {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_hip": getattr(torch.version, "hip", None),
        "dgl": dgl.__version__,
        "gpu_visible": gpu,
        "gpu_count": torch.cuda.device_count() if gpu else 0,
        "cpu_count": os.cpu_count(),
    }
    if gpu:
        props = torch.cuda.get_device_properties(0)
        env["gpu_name"] = torch.cuda.get_device_name(0)
        env["gpu_arch"] = getattr(props, "gcnArchName", None)
        env["gpu_total_gb"] = round(props.total_memory / 1e9, 1)
    return env


def main() -> int:
    args = parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
    env = environment()
    print(json.dumps({"environment": env}), flush=True)

    want_gpu = args.device in ("gpu", "both")
    if want_gpu and not env["gpu_visible"]:
        print(
            "FAIL: GPU requested but torch.cuda.is_available() is False. Check --device=/dev/kfd "
            "--device=/dev/dri, group video/render, and that torch.version.hip is set.",
            file=sys.stderr,
        )
        return 2

    seed_everything(args.seed)
    t = time.perf_counter()
    data = build_heterogeneous(args) if args.hetero else build_homogeneous(args)
    build_s = time.perf_counter() - t
    print(json.dumps({"graph": data["desc"], "build_s": round(build_s, 3)}), flush=True)

    devices = []
    if args.device in ("cpu", "both"):
        devices.append(torch.device("cpu"))
    if want_gpu:
        devices.append(torch.device("cuda", 0))

    results: dict[str, Any] = {}
    status = 0
    for dev in devices:
        try:
            r = run_on(dev, data, args)
        except Exception as exc:  # report and keep going: the CPU number is still useful
            r = {"device": str(dev), "error": f"{type(exc).__name__}: {exc}"}
            status = 1
        else:
            if not r["finite"]:
                status = 1
        results[dev.type] = r
        print(json.dumps({"result": r}), flush=True)

    summary: dict[str, Any] = {
        "spike": "SPK-G3",
        "context": args.context,
        "model": {
            "layer": "SAGEConv",
            "hidden": HIDDEN,
            "aggregator": "pool (max)",
            "dropout": DROPOUT,
            "optimizer": "Adam",
            "lr": LR,
        },
        "config": {
            k: v
            for k, v in vars(args).items()
            if k not in ("out", "context", "gpu_result", "device")
        },
        "environment": env,
        "graph": data["desc"],
        "results": results,
        "pass": status == 0,
    }
    if args.gpu_result and "cuda" not in results:
        with open(args.gpu_result, encoding="utf-8") as fh:
            other = json.load(fh)
        if other.get("config") != summary["config"]:
            print(f"FAIL: {args.gpu_result} has config {other.get('config')}", file=sys.stderr)
            return 1
        results["cuda"] = other["results"]["cuda"]
        summary["gpu_result_from"] = {"file": args.gpu_result, "context": other.get("context")}
    cpu, gpu = results.get("cpu", {}), results.get("cuda", {})
    if "epoch_s_median" in cpu and "epoch_s_median" in gpu and gpu["epoch_s_median"] > 0:
        summary["speedup_median_epoch"] = round(cpu["epoch_s_median"] / gpu["epoch_s_median"], 2)
    print(json.dumps(summary, indent=2))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
