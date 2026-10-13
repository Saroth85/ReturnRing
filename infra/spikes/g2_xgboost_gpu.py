"""SPK-G2: amd_xgboost on the MI300X, then the same data and parameters on CPU (spec section 12).

Runs in the trainer-gbm container (make spike-g2). Steps:
1. Versions: xgboost, build_info USE_HIP / USE_RCCL, /opt/rocm/.info/version.
2. Pass criterion of the spec: the verify snippet of the AMD install docs
   (make_classification 1000 x 10, tree_method hist, device cuda, max_depth 3, 10 rounds).
   amd_xgboost falls back to CPU with only a warning when no GPU is visible, so the effective
   device is read back from the booster config and the run fails if it is "cpu".
3. Benchmark: 1.4M x 40 synthetic rows, 200 rounds, depth 4, eta 0.1; warm-up timed apart from
   training; held-out log loss on both devices, so the speedup compares equal quality.
4. Saves the GPU model as <out>_model.json plus <out>_check.json (a prediction sample), for
   g2_load_cpu.py (xgboost-cpu 3.2.0).

The runbook times the GPU half while the Gemma weights download and the CPU half afterwards, alone
on the CPU (make spike-g2, make spike-g2-cpu); --gpu-result lets the CPU run compute the speedup
from the GPU file, and --context records what else was running.

The device string is "cuda" on ROCm too ("hip" is rejected by XGBoost). Exit codes: 0 pass,
1 error, 2 GPU not used.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

PARAMS = {"tree_method": "hist", "max_depth": 4, "eta": 0.1, "objective": "binary:logistic"}
DEVICES = {"cpu": "cpu", "gpu": "cuda"}


def effective_device(bst: xgb.Booster) -> str:
    config = json.loads(bst.save_config())
    return str(config["learner"]["generic_param"]["device"])


def environment() -> dict[str, Any]:
    info = xgb.build_info()
    rocm = Path("/opt/rocm/.info/version")
    return {
        "xgboost": xgb.__version__,
        "use_hip": info.get("USE_HIP"),
        "use_rccl": info.get("USE_RCCL"),
        "rocm": rocm.read_text().strip() if rocm.exists() else None,
        "cpu_count": os.cpu_count(),
        "numpy": np.__version__,
    }


def official_verify() -> dict[str, Any]:
    """Verify snippet from rocm.docs.amd.com/projects/xgboost/en/docs-26.05/install/install.html."""
    from sklearn.datasets import make_classification

    x, y = make_classification(n_samples=1000, n_features=10, random_state=42)
    dtrain = xgb.DMatrix(x, label=y)
    params = {"tree_method": "hist", "device": "cuda", "max_depth": 3}
    bst = xgb.train(params, dtrain, num_boost_round=10)
    return {"device_requested": "cuda", "device_effective": effective_device(bst)}


def make_data(rows: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(rows, 40)).astype(np.float32)
    y = (x[:, 0] + 0.5 * x[:, 1] + rng.normal(scale=0.5, size=rows) > 0).astype(np.int32)
    return x, y


def logloss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def bench(
    device: str,
    dtrain: xgb.DMatrix,
    dtest: xgb.DMatrix,
    y_test: np.ndarray,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], xgb.Booster]:
    params = {**PARAMS, "device": DEVICES[device], "seed": args.seed}
    t = time.perf_counter()
    warm = xgb.train(params, dtrain, num_boost_round=2)
    warmup_s = time.perf_counter() - t
    eff = effective_device(warm)
    if device == "gpu" and eff == "cpu":
        raise SystemExit(f"GPU not used: requested cuda, effective device {eff!r}")

    times = []
    start = time.perf_counter()
    while True:
        t = time.perf_counter()
        bst = xgb.train(params, dtrain, num_boost_round=args.rounds)
        times.append(time.perf_counter() - t)
        if time.perf_counter() - start >= args.min_seconds:
            break
    loss = logloss(y_test, bst.predict(dtest))
    return {
        "device_requested": DEVICES[device],
        "device_effective": effective_device(bst),
        "warmup_2_rounds_s": round(warmup_s, 3),
        "train_s": round(min(times), 3),
        # Under SPK-G4 the minimum hides interference: compare medians there.
        "train_s_median": round(statistics.median(times), 3),
        "train_s_max": round(max(times), 3),
        "train_s_all": [round(t, 3) for t in times],
        "train_runs": len(times),
        "rounds": args.rounds,
        "test_logloss": round(loss, 5),
        "finite": math.isfinite(loss),
    }, bst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", choices=["gpu", "cpu", "both"], default="both")
    parser.add_argument("--rows", type=int, default=1_400_000)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "42")))
    parser.add_argument(
        "--min-seconds", type=float, default=0.0, help="SPK-G4: repeat training this long"
    )
    parser.add_argument("--out", type=Path, default=Path("artifacts/spikes/g2.json"))
    parser.add_argument("--skip-verify", action="store_true", help="for --device cpu only")
    parser.add_argument("--context", default="", help="recorded: what else ran at the same time")
    parser.add_argument(
        "--gpu-result", type=Path, help="CPU run: GPU result file to compute the speedup against"
    )
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "spike": "SPK-G2",
        "context": args.context,
        "config": {"rows": args.rows, "rounds": args.rounds, "seed": args.seed},
        "environment": environment(),
    }
    print(json.dumps(summary["environment"]), flush=True)
    if args.device != "cpu" and not args.skip_verify:
        verify = official_verify()
        summary["official_verify"] = verify
        print(json.dumps({"official_verify": verify}), flush=True)
        if verify["device_effective"] == "cpu":
            print("FAIL: GPU not used (no visible GPU: check /dev/kfd, /dev/dri, group video)")
            return 2

    x, y = make_data(args.rows, args.seed)
    split = int(len(x) * 6 / 7)
    t = time.perf_counter()
    dtrain = xgb.DMatrix(x[:split], label=y[:split])
    dtest = xgb.DMatrix(x[split:], label=y[split:])
    summary["data"] = {
        "rows_train": split,
        "rows_test": len(x) - split,
        "features": x.shape[1],
        "dmatrix_s": round(time.perf_counter() - t, 3),
    }
    order = ["gpu", "cpu"] if args.device == "both" else [args.device]
    results: dict[str, Any] = {}
    for device in order:
        result, bst = bench(device, dtrain, dtest, y[split:], args)
        results[device] = result
        print(json.dumps({device: result}), flush=True)
        if device == "gpu":
            args.out.parent.mkdir(parents=True, exist_ok=True)
            model = args.out.with_name(f"{args.out.stem}_model.json")
            bst.save_model(model)
            sample = x[split : split + 200]
            check = {
                "model": model.name,
                "x": sample.tolist(),
                "pred": bst.predict(xgb.DMatrix(sample)).tolist(),
            }
            args.out.with_name(f"{args.out.stem}_check.json").write_text(json.dumps(check))
    summary["results"] = results
    if "cpu" in results and "gpu" not in results and args.gpu_result:
        other = json.loads(args.gpu_result.read_text())
        if other.get("config") != summary["config"]:
            print(
                f"FAIL: {args.gpu_result} has config {other.get('config')}, not {summary['config']}"
            )
            return 1
        results["gpu"] = other["results"]["gpu"]
        summary["gpu_result_from"] = {"file": str(args.gpu_result), "context": other["context"]}
    if "gpu" in results and "cpu" in results:
        summary["speedup_train"] = round(results["cpu"]["train_s"] / results["gpu"]["train_s"], 2)
    summary["pass"] = all(r["finite"] for r in results.values()) and (
        "gpu" not in results or results["gpu"]["device_effective"] != "cpu"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
