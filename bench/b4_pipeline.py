"""B4: the nightly pipeline, step by step, with the GPU utilisation of each step (US-902).

The steps are the commands of `make pipeline`, `make score` and the batch generation (ADR-03),
run as subprocesses in this order on a fresh copy in the work folder (bench-work/<run-id>/ from the
CLI; data/ is never touched): generate (main and OOD stores), graph, features, anomalies, risk
(offline training and evaluation: it reads the generated truth, rule 12 allows offline jobs),
scoring (the saved models at the last snapshot) and batch_generation (bench.batch_generation:
LLM_MODE=mock on a laptop, the local vLLM on the droplet). Each step is timed wall clock; the run
stops at the first command that fails, as make does, and keeps what it measured (rule 13). The
work folder is outside artifacts/ (which running services mount) and the generated ground truth
is deleted when the job ends; the logs go to the run folder.

Each step says where it executes (review G3): every step but the generation runs on the CPU, also
on the droplet (pipeline/ and ml.risk, ml.scoring take no device: XGBoost trains with the CPU
`hist` method), so its time is a CPU number ("droplet CPU (<model>)"); only batch_generation runs
on the MI300X, through vLLM, and only with LLM_MODE=vllm (with the mock model it is CPU work too).
The pipeline total says how many of its seconds ran on the GPU.

The nightly seconds (review G6) are what a production night runs before the drafts: the main
store's commands of generate (the stand-in for the ingest), graph, features, anomalies and
scoring. The OOD store (evaluation only), the risk step (offline training and evaluation against
the generated truth, which a production night does not have) and the generation (the cost adds it
at the B3 batch-32 rate) are timed but left out; bench.cost reads this sum.

GPU utilisation comes from `amd-smi metric --json` sampled every 5 s (bench.amdsmi): by a host
sampler writing the samples file (sampler "external", the droplet default, see the Makefile), or
by a thread of this process ("inprocess"), or not at all ("none", CPU runs). Each step gets the
samples inside its window: mean and max gfx activity, max VRAM used (vLLM's reservation included
when it is up), mean power: MI300X readings, whatever the step ran on. A step shorter than the
interval may get no sample: it then has no GPU number rather than a made-up one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from bench import amdsmi, env
from bench.record import Measurement, Part, short, utc_now

ROOT = Path(__file__).resolve().parents[1]
Log = Callable[[str], None]
SamplerMode = Literal["none", "inprocess", "external"]
CALIBRATION = Path("pipeline/calibration/asos_calibration.json")


Executes = Literal["cpu", "gpu", "vllm"]


@dataclass(frozen=True)
class Step:
    name: str
    title: str
    commands: tuple[tuple[str, ...], ...]  # argv templates: {python} {data} {artifacts} ...
    # Where the work runs on a GPU run: "cpu", "gpu" (the step itself uses the GPU), or "vllm"
    # (the GPU through the local vLLM, when LLM_MODE=vllm; the mock model runs on the CPU).
    executes_on: Executes = "cpu"
    # Per command: part of a production night (None: every command). bench.cost reads the sum.
    nightly: tuple[bool, ...] | None = None

    def is_nightly(self, index: int) -> bool:
        return self.nightly is None or self.nightly[index]


PY = "{python}"
STEPS: tuple[Step, ...] = (
    Step(
        "generate",
        "synthetic store, main and OOD scenarios (stand-in for the nightly ingest)",
        tuple(
            (
                PY,
                "-m",
                "pipeline.generator",
                "--calibration",
                "{calibration}",
                "--out",
                "{data}",
                "--seed",
                "{seed}",
                "--scale",
                "{scale}",
                "--scenario",
                scenario,
            )
            for scenario in ("main", "ood")
        ),
        nightly=(True, False),
    ),
    Step(
        "graph",
        "account graph and clusters, main and OOD",
        (
            (PY, "-m", "pipeline.graph", "--store", "{data}"),
            (PY, "-m", "pipeline.graph", "--store", "{data}/ood"),
        ),
        nightly=(True, False),
    ),
    Step(
        "features",
        "customer features, value and evidence snapshots, main and OOD",
        (
            (PY, "-m", "pipeline.features", "--store", "{data}", "--out", "{data}"),
            (
                PY,
                "-m",
                "pipeline.features",
                "--store",
                "{data}/ood",
                "--out",
                "{data}/ood",
                "--source",
                "{data}",
            ),
        ),
        nightly=(True, False),
    ),
    Step(
        "anomalies",
        "product anomalies, main and OOD",
        (
            (PY, "-m", "pipeline.anomalies", "--store", "{data}", "--out", "{data}"),
            (PY, "-m", "pipeline.anomalies", "--store", "{data}/ood", "--out", "{data}/ood"),
        ),
        nightly=(True, False),
    ),
    Step(
        "risk",
        "risk models: training, evaluation and batch scores (offline job)",
        (
            (
                PY,
                "-m",
                "ml.risk",
                "--data",
                "{data}",
                "--out",
                "{artifacts}",
                "--scores",
                "{data}",
                "--results",
                "{artifacts}/us-204-risk.md",
            ),
        ),
        nightly=(False,),  # offline training and evaluation: not a production night's work
    ),
    Step(
        "scoring",
        "batch scoring with the saved models at the last snapshot",
        (
            (
                PY,
                "-m",
                "ml.scoring",
                "--data",
                "{data}",
                "--models",
                "{artifacts}/risk/models",
                "--out",
                "{data}",
                "--fairness",
                "{artifacts}/eval/scores/fairness.json",
            ),
        ),
        nightly=(True,),
    ),
    Step(
        "batch_generation",
        "drafts of every open return through llm.generate (ADR-03)",
        (
            (
                PY,
                "-m",
                "bench.batch_generation",
                "--data",
                "{data}",
                "--out",
                "{artifacts}/generation.jsonl",
                "--concurrency",
                "{concurrency}",
            ),
        ),
        executes_on="vllm",
    ),
)
STEP_NAMES = tuple(s.name for s in STEPS)


@dataclass(frozen=True)
class B4Config:
    run_dir: Path
    device: Literal["cpu", "gpu"] = "cpu"
    seed: int = 42
    scale: float = 1.0
    concurrency: int = 32
    steps: tuple[str, ...] = STEP_NAMES
    table: tuple[Step, ...] = STEPS  # the step definitions (tests pass their own)
    sampler: SamplerMode = "none"
    samples_file: Path | None = None  # default <run_dir>/amdsmi.jsonl
    amd_smi: tuple[str, ...] = amdsmi.COMMAND
    interval_s: float = amdsmi.INTERVAL_S
    keep_data: bool = True
    work_dir: Path | None = None  # default <run_dir>/pipeline (the CLI: bench-work/<run-id>)
    python: str = sys.executable
    llm_mode: str | None = None  # default: mock on cpu, vllm on gpu
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [s.name for s in self.table]
        unknown = [s for s in self.steps if s not in names]
        if unknown or not self.steps:
            raise ValueError(f"unknown steps {unknown}; steps are {names}")
        for step in self.table:
            if step.nightly is not None and len(step.nightly) != len(step.commands):
                raise ValueError(f"step {step.name}: one nightly flag per command")
        if self.sampler not in ("none", "inprocess", "external"):
            raise ValueError(f"sampler {self.sampler!r}")

    @property
    def pipeline_dir(self) -> Path:
        return self.work_dir or self.run_dir / "pipeline"

    @property
    def samples(self) -> Path:
        return self.samples_file or self.run_dir / "amdsmi.jsonl"


def _values(config: B4Config) -> dict[str, str]:
    return {
        "python": config.python,
        "data": str(config.pipeline_dir / "data"),
        "artifacts": str(config.pipeline_dir / "artifacts"),
        "calibration": str(CALIBRATION),
        "seed": str(config.seed),
        "scale": f"{config.scale:g}",
        "concurrency": str(config.concurrency),
    }


def executes_on(step: Step, config: B4Config) -> str:
    """The API device a step's time belongs to: "mi300x" only for work that ran on the GPU."""
    if config.device != "gpu" or step.executes_on == "cpu":
        return "cpu"
    if step.executes_on == "vllm" and _environment(config)["LLM_MODE"] != "vllm":
        return "cpu"
    return "mi300x"


def nightly_summary(table: Sequence[Step], steps: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The seconds of a production night before the drafts (bench.cost reads them), and what
    was timed but left out."""
    by_name = {s.name: s for s in table}
    seconds = 0.0
    included: list[str] = []
    excluded: list[str] = []
    for record in steps:
        step = by_name[record["name"]]
        n = len(step.commands)
        for i, command in enumerate(record["commands"]):
            label = record["name"] if n == 1 else f"{record['name']} (command {i + 1} of {n})"
            if step.is_nightly(i) and step.executes_on != "vllm":
                seconds += command["seconds"]
                included.append(label)
            else:
                excluded.append(label)
    complete = len(steps) > 0 and all(r["ok"] for r in steps)
    return {
        "seconds": round(seconds, 3),
        "complete": complete,
        "included": included,
        "excluded": excluded,
        "what": "a production night before the drafts: the main store's generate (stand-in for "
        "the ingest), graph, features, anomalies and scoring; the OOD store, the offline risk "
        "evaluation and the generation (the cost adds it at the B3 batch-32 rate) left out",
    }


def _environment(config: B4Config) -> dict[str, str]:
    llm_mode = config.llm_mode or ("vllm" if config.device == "gpu" else "mock")
    return {
        **os.environ,
        "DEVICE": config.device,
        "SEED": str(config.seed),
        "LLM_MODE": llm_mode,
        **config.env,
    }


def run_steps(config: B4Config, log: Log = lambda _m: None) -> list[dict[str, Any]]:
    """Run the steps in order; one record per step (commands, seconds, exit codes)."""
    values = _values(config)
    environment = _environment(config)
    logs = config.run_dir / "b4-logs"
    logs.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for step in (s for s in config.table if s.name in config.steps):
        record: dict[str, Any] = {
            "name": step.name,
            "title": step.title,
            "executes_on": executes_on(step, config),
            "commands": [],
        }
        record["start_unix"] = time.time()
        t0 = time.perf_counter()
        failed = False
        for i, template in enumerate(step.commands):
            argv = [part.format(**values) for part in template]
            log_path = logs / f"{step.name}-{i + 1}.log"
            c0 = time.perf_counter()
            with log_path.open("w", encoding="utf-8") as handle:
                code = subprocess.run(
                    argv,
                    cwd=ROOT,
                    env=environment,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    check=False,
                ).returncode
            record["commands"].append(
                {
                    # The interpreter path is the machine's, not the command's.
                    "argv": ["python", *argv[1:]] if argv[0] == config.python else argv,
                    "seconds": round(time.perf_counter() - c0, 3),
                    "exit_code": code,
                    "log": str(log_path),
                    "nightly": step.is_nightly(i) and step.executes_on != "vllm",
                }
            )
            if code != 0:
                failed = True
                break
        record["seconds"] = round(time.perf_counter() - t0, 3)
        record["end_unix"] = time.time()
        record["ok"] = not failed
        records.append(record)
        log(f"B4 {step.name}: {record['seconds']:.1f} s" + (" FAILED" if failed else ""))
        if failed:
            break
    return records


def run_b4(config: B4Config, log: Log = lambda _m: None) -> Part:
    started = utc_now()
    gpu = config.device == "gpu"
    hardware = env.hardware(gpu=gpu)
    if config.pipeline_dir.exists():
        shutil.rmtree(config.pipeline_dir)  # a fresh copy each run, never data/
    sampler = None
    if config.sampler == "inprocess":
        sampler = amdsmi.Sampler(config.samples, config.amd_smi, config.interval_s).start()
    try:
        steps = run_steps(config, log)
    finally:
        if sampler is not None:
            sampler.stop()
    finished = utc_now()
    samples = amdsmi.read_samples(config.samples) if config.sampler != "none" else []
    for step in steps:
        step["gpu"] = (
            amdsmi.window_summary(samples, step["start_unix"], step["end_unix"])
            if config.sampler != "none"
            else None
        )
    whole = (
        amdsmi.window_summary(samples, steps[0]["start_unix"], steps[-1]["end_unix"])
        if steps and config.sampler != "none"
        else None
    )
    ok = bool(steps) and all(s["ok"] for s in steps) and len(steps) == len(config.steps)
    nightly = nightly_summary(config.table, steps)
    measurements = _measurements(
        config, steps, whole, nightly, hardware, started.date(), finished.date()
    )
    timeline = (
        [s.to_dict() for s in samples if steps[0]["start_unix"] <= s.t <= steps[-1]["end_unix"]]
        if steps
        else []
    )
    # Rule 12: the generated ground truth served the offline risk step only.
    shutil.rmtree(config.pipeline_dir / "data" / "truth", ignore_errors=True)
    if not config.keep_data:
        for sub in ("data", "artifacts"):
            shutil.rmtree(config.pipeline_dir / sub, ignore_errors=True)
    failed_step = next((s for s in steps if not s["ok"]), None)
    notes = [
        "Steps run one after the other in one container or machine, as `make pipeline`, "
        "`make score` and the batch generation would; the store is generated (a stand-in for "
        "the nightly ingest).",
    ]
    if failed_step is not None:
        notes.append(
            f"Step {failed_step['name']} failed (exit "
            f"{failed_step['commands'][-1]['exit_code']}): later steps did not run."
        )
    if _environment(config)["LLM_MODE"] == "mock":
        notes.append(
            "batch_generation ran with the mock model (LLM_MODE=mock): it times case "
            "building and validation, not a language model."
        )
    if config.sampler == "none":
        notes.append("No GPU sampling in this run (CPU only).")
    if gpu:
        on_gpu = [s["name"] for s in steps if s["executes_on"] == "mi300x"]
        notes.append(
            "Every step but "
            + (", ".join(on_gpu) if on_gpu else "none")
            + " ran on the droplet CPU and is reported as a CPU time; the GPU numbers of a CPU "
            "step are amd-smi readings of the MI300X during it (vLLM's reservation included)."
        )
    return Part(
        benchmark="B4",
        status="measured" if ok else "failed",
        title="Nightly pipeline: duration per step and GPU utilisation"
        + ("" if gpu else " (CPU, no GPU)"),
        dataset=short(
            f"Navigli Fashion synthetic store generated from the committed ASOS calibration, "
            f"seed {config.seed}, scale {config.scale:g} (main and OOD scenarios)"
        ),
        started_at=started,
        finished_at=finished,
        versions=env.software(("numpy", "polars", "pyarrow", "sklearn", "networkx", "xgboost")),
        config={
            "device": config.device,
            "seed": config.seed,
            "scale": config.scale,
            "steps": ",".join(s["name"] for s in steps),
            "llm_mode": _environment(config)["LLM_MODE"],
            "generation_concurrency": config.concurrency,
            "gpu_sampler": config.sampler,
            "sample_interval_s": config.interval_s,
            "cpu": short(str(hardware["cpu"]), 64),
            "logical_cpus": hardware["logical_cpus"] or 0,
        },
        measurements=measurements,
        notes=notes,
        details={
            "steps": [{k: v for k, v in s.items()} for s in steps],
            "nightly": nightly,
            "gpu_whole_pipeline": whole,
            "gpu_timeline": timeline[:5000],
        },
        hardware=hardware,
    )


def _measurements(
    config: B4Config,
    steps: Sequence[dict[str, Any]],
    whole: dict[str, Any] | None,
    nightly: dict[str, Any],
    hardware: dict[str, Any],
    start: date,
    end: date,
) -> list[Measurement]:
    gpu = config.device == "gpu"
    cpu = f"{hardware['cpu']}, {hardware['logical_cpus']} CPUs"
    where = {
        "cpu": f"the droplet CPU ({cpu})" if gpu else cpu,
        "mi300x": "the MI300X droplet (the GPU through vLLM)",
    }
    sampling = (
        f"GPU from amd-smi every {config.interval_s:g} s"
        if config.sampler != "none"
        else "no GPU sampling"
    )

    def common(device: str) -> dict[str, Any]:
        return {
            "benchmark": "B4",
            "device": device,
            "origin": short(
                f"B4, measured by bench on {where[device]}: wall time of nightly pipeline steps "
                f"(seed {config.seed}, scale {config.scale:g}); {sampling}"
            ),
            "period_start": start,
            "period_end": end,
            "nature": "real",
            "data_nature": "synthetic",
            "phase": "pipeline",
        }

    gpu_readings = {
        **common("mi300x"),
        "origin": short(
            f"B4, amd-smi readings of the MI300X every {config.interval_s:g} s during nightly "
            f"pipeline steps (seed {config.seed}, scale {config.scale:g})"
        ),
    }
    out: list[Measurement] = []
    for step in steps:
        if not step["ok"]:
            continue
        device = step["executes_on"]
        out.append(
            Measurement(
                metric=f"step_seconds_{step['name']}",
                label=short(
                    f"Nightly pipeline step {step['name']}: {step['title']} "
                    f"(ran on {'the MI300X' if device == 'mi300x' else 'the CPU'})"
                ),
                value=step["seconds"],
                unit="seconds",
                repeats=1,
                warm=False,
                **common(device),
                detail={"commands": [c["seconds"] for c in step["commands"]]},
            )
        )
        out += _gpu_measurements(step["gpu"], step["name"], f"step {step['name']}", gpu_readings)
    if steps and all(s["ok"] for s in steps):
        on_gpu = [s for s in steps if s["executes_on"] == "mi300x"]
        gpu_seconds = round(sum(s["seconds"] for s in on_gpu), 3)
        total = round(sum(s["seconds"] for s in steps), 3)
        split = (
            f"; {gpu_seconds:,.1f} s of it on the MI300X ({', '.join(s['name'] for s in on_gpu)})"
            f", the rest on the CPU"
            if on_gpu
            else "; every step on the CPU"
        )
        out.append(
            Measurement(
                metric="pipeline_seconds_total",
                label=short(
                    f"Nightly pipeline, {len(steps)} steps one after the other: "
                    + ", ".join(s["name"] for s in steps)
                    + split
                ),
                value=total,
                unit="seconds",
                repeats=1,
                warm=False,
                detail={"gpu_seconds": gpu_seconds, "cpu_seconds": round(total - gpu_seconds, 3)},
                **common("cpu" if len(on_gpu) < len(steps) else "mi300x"),
            )
        )
        if on_gpu:
            out.append(
                Measurement(
                    metric="pipeline_seconds_gpu_steps",
                    label=short(
                        "Nightly pipeline steps that ran on the MI300X: "
                        + ", ".join(s["name"] for s in on_gpu)
                    ),
                    value=gpu_seconds,
                    unit="seconds",
                    repeats=1,
                    warm=False,
                    **common("mi300x"),
                )
            )
        if nightly["included"]:
            out.append(
                Measurement(
                    metric="pipeline_seconds_nightly",
                    label=short(
                        "Nightly operating work before the drafts (the cost's input): "
                        + ", ".join(nightly["included"])
                        + "; without the OOD store, the risk evaluation and the generation"
                    ),
                    value=nightly["seconds"],
                    unit="seconds",
                    repeats=1,
                    warm=False,
                    detail={"excluded": nightly["excluded"]},
                    **common("cpu"),
                )
            )
        out += _gpu_measurements(whole, "total", "the whole pipeline", gpu_readings)
    return out


def _gpu_measurements(
    gpu: dict[str, Any] | None, key: str, what: str, common: dict[str, Any]
) -> list[Measurement]:
    if not gpu or not gpu.get("n"):
        return []
    n = gpu["n"]
    out = []
    for field_name, metric, unit, text in (
        ("gfx_activity_mean_percent", "gpu_activity_mean_percent", "percent", "mean GPU activity"),
        ("gfx_activity_max_percent", "gpu_activity_max_percent", "percent", "max GPU activity"),
        ("vram_used_max_gib", "vram_used_max_gib", "gib", "max VRAM used, all processes"),
        ("power_mean_w", "power_mean_watts", "watts", "mean socket power"),
    ):
        value = gpu.get(field_name)
        if value is None:
            continue
        out.append(
            Measurement(
                metric=f"{metric}_{key}",
                label=short(f"{text[0].upper()}{text[1:]} during {what} ({n} amd-smi samples)"),
                value=round(float(value), 2),
                unit=unit,
                repeats=n,
                **common,
            )
        )
    return out
