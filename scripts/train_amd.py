"""Run one real offline training task on AMD, without a CPU/GPU speed comparison (ADR-18).

Each invocation gets a new output directory with its command, environment, exit status and log.
The training entry points refuse silent GPU-to-CPU fallback. No LLM, vLLM or B1-B4 job runs here.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASKS = ("risk", "propensity", "graphsage-asos", "graphsage-embed")


def training_command(task: str, data: Path, out: Path, seed: int) -> list[str]:
    common = ["--out", str(out), "--seed", str(seed)]
    if task == "risk":
        return [
            "-m",
            "ml.risk",
            "--data",
            str(data),
            "--device",
            "cuda",
            "--no-scores",
            "--no-results",
            *common,
        ]
    if task == "propensity":
        return [
            "-m",
            "ml.propensity",
            "--raw",
            str(data / "raw"),
            "--device",
            "cuda",
            "--no-doc",
            *common,
        ]
    if task == "graphsage-asos":
        return [
            "-m",
            "ml.gnn",
            "asos",
            "--raw",
            str(data / "raw"),
            "--device",
            "gpu",
            "--backend",
            "torch",
            "--no-xgboost",
            *common,
        ]
    if task == "graphsage-embed":
        return [
            "-m",
            "ml.gnn",
            "embed",
            "--store",
            str(data),
            "--models",
            str(out),
            "--device",
            "gpu",
            "--backend",
            "torch",
            *common,
        ]
    raise ValueError(f"unknown training task {task}")


def preflight(task: str, python: str) -> dict[str, Any]:
    if task.startswith("graphsage"):
        code = (
            "import json,torch; "
            "assert torch.version.hip and torch.cuda.is_available(), 'AMD ROCm GPU required'; "
            "print(json.dumps({'torch':str(torch.__version__),'hip':torch.version.hip,"
            "'gpu':torch.cuda.get_device_name(0),"
            "'memory_bytes':torch.cuda.get_device_properties(0).total_memory}))"
        )
    else:
        code = (
            "import json,xgboost; "
            "assert xgboost.build_info().get('USE_HIP'), 'AMD XGBoost USE_HIP required'; "
            "print(json.dumps({'xgboost':xgboost.__version__,'build':xgboost.build_info()}))"
        )
    result = subprocess.run([python, "-c", code], check=True, capture_output=True, text=True)
    return dict(json.loads(result.stdout))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", choices=TASKS)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/amd-training"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--nature", choices=["real", "synthetic"], required=True)
    args = parser.parse_args()
    data = args.data.resolve()
    if not data.is_dir():
        parser.error("--data must be an existing data root")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + args.task
    out = args.out.resolve() / run_id
    out.mkdir(parents=True, exist_ok=False)
    command = [args.python, *training_command(args.task, data, out, args.seed)]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    report: dict[str, Any] = {
        "task": args.task,
        "data_nature": args.nature,
        "data": str(data),
        "command": command,
        "git_commit": revision,
        "working_tree_dirty": bool(dirty),
        "host": platform.platform(),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "preflight",
        "cpu_gpu_comparison": False,
    }
    manifest = out / "session.json"

    def save() -> None:
        manifest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        report["environment"] = preflight(args.task, args.python)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        report.update(status="preflight_failed", error=type(exc).__name__)
        save()
        print(f"AMD preflight failed; no training: {manifest}", file=sys.stderr)
        return 2
    report["status"] = "running"
    save()
    started = time.perf_counter()
    with (out / "training.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    report.update(
        status="completed" if completed.returncode == 0 else "failed",
        exit_code=completed.returncode,
        elapsed_seconds=round(time.perf_counter() - started, 3),
        finished_at=datetime.now(UTC).isoformat(),
    )
    save()
    print(manifest)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
