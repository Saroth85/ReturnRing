"""B3: Gemma on the local vLLM: latency p50 and p95, tokens per second, cost per decision.

What is timed is one decision's generation as the product runs it: llm.generate.generate on a
real llm_case (bench.cases: the synthetic store's open returns, golden first), through
llm.client.VllmClient against VLLM_BASE_URL only (rule 2), guided JSON, the validator, and one
regeneration when the validator asks for it. A decision whose model call failed (HTTP error,
timeout) is counted as a failure and kept out of the latencies: the template fallback is fast and
would flatter them.

Loads (spec 12, "Tempi distinti" and "Carichi crescenti"), all on one case list (review G4):
N decisions drawn once, seeded and stratified by decision type (Q1-Q4, insufficient evidence,
guarantee: one case of each, the rest in the store's proportions), golden cases included and
counted, N the largest batch
times its repeats (at least 2 x the largest concurrency), rounded up to a multiple of every batch
size. Every load runs exactly these N decisions, so a batch of 1 and a batch of 32 time the same
work and each has N latencies; a p95 is reported only from 20 values or more. Before the loads,
one untimed pass over the list fills vLLM's prefix cache, so every load sees the same cache (a
later load would otherwise reuse the prefixes an earlier one computed):
- batches of 1, 8 and 32 decisions sent at once (the nightly pre-generation of ADR-03): latency
  p50 and p95 inside the batches, batch wall time, output tokens per second;
- closed-loop concurrency levels (C requests in flight until the N are done; 4 and 16 by
  default, concurrency 1 being the batch of 1): latency p50 and p95, decisions per second,
  aggregate and per-request output tokens per second;
- cold start: vLLM start to healthy (the start and healthy files the Makefile writes, 15 s
  polling; the page cache state in the conditions file) and the first request after it, timed
  by `python -m bench first-request` in `make bench-vllm`, before B4 sends anything (reviews G7,
  G15). The first decision of B3 itself comes after B4's batch generation: it is reported as a
  warm generation, never as a cold start.

Output tokens are vLLM's usage.completion_tokens; tokens per second of one request are its output
tokens over its whole latency (prefill included), aggregate ones the output tokens of a load over
its wall time. Modes: live (the MI300X) and dry_run (bench.fake_vllm on 127.0.0.1: simulated
numbers that check the harness only). The cost per decision (bench.cost) comes with the part.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from bench import cost as costs
from bench import env
from bench.cases import BenchCase, CaseSet, build_cases
from bench.fake_vllm import FakeLatency, FakeVllm
from bench.record import Measurement, Part, iso, read_json, short, utc_now, write_json
from bench.stats import summary
from llm.client import (
    TEMPERATURE,
    Completion,
    LlmUnavailable,
    VllmClient,
    VllmConfig,
    check_local_url,
)
from llm.generate import generate

Mode = Literal["live", "dry_run"]
Log = Callable[[str], None]
MIN_P95 = 20  # fewer latencies than this: no p95 (it would be an interpolation near the maximum)
ROOT = Path(__file__).resolve().parents[1]
FIRST_REQUEST_CASE = Path("contracts/examples/llm_case.json")


@dataclass(frozen=True)
class B3Config:
    mode: Mode
    data_root: Path
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    batch_sizes: tuple[int, ...] = (1, 8, 32)
    batch_repeats: int = 2  # batches of the largest size; every load runs as many decisions
    concurrency: tuple[int, ...] = (4, 16)
    requests_per_level: int = 16  # decisions per load, at least
    warmup: int = 2
    timeout_s: float = 180.0
    max_tokens: int = 1200
    seed: int = 42
    vllm_start_file: Path | None = None
    vllm_healthy_file: Path | None = None
    vllm_conditions_file: Path | None = None  # page cache state at the start (make bench-vllm)
    first_request_file: Path | None = None  # the first request after healthy (bench-vllm)
    disk_used_file: Path | None = None  # GB used on the droplet disk: the snapshot size
    b4_part: Path | None = None  # the B4 part of the same run, for the nightly cost
    fake_latency: FakeLatency = field(default_factory=FakeLatency)  # dry run only

    def __post_init__(self) -> None:
        if self.mode not in ("live", "dry_run"):
            raise ValueError(f"mode {self.mode!r}")
        if self.mode == "live" and not (self.base_url and self.api_key and self.model):
            raise ValueError("live B3 needs VLLM_BASE_URL, VLLM_API_KEY and GEMMA_MODEL")
        sizes = (*self.batch_sizes, *self.concurrency)
        if not sizes or min(sizes) < 1 or self.batch_repeats < 1 or self.warmup < 0:
            raise ValueError("batch sizes, concurrency and repeats must be at least 1")

    def decisions_per_load(self) -> int:
        """N: the largest batch times its repeats, at least twice the largest concurrency and
        requests_per_level, rounded up to a multiple of every batch size (whole batches)."""
        n = max(
            self.requests_per_level,
            max(self.batch_sizes, default=1) * self.batch_repeats,
            2 * max(self.concurrency, default=0),
        )
        step = math.lcm(*self.batch_sizes) if self.batch_sizes else 1
        return -(-n // step) * step


class UsageClient(VllmClient):
    """VllmClient that keeps the usage block of its last response (prompt tokens included).
    One instance per thread: the attribute is not shared."""

    last_usage: Mapping[str, Any] | None = None

    def _post(self, body: Mapping[str, Any]) -> tuple[dict[str, Any], float]:
        self.last_usage = None
        data, elapsed = super()._post(body)
        usage = data.get("usage")
        self.last_usage = usage if isinstance(usage, Mapping) else None
        return data, elapsed


@dataclass(frozen=True)
class CallRecord:
    elapsed_s: float
    ok: bool
    completion_tokens: int | None = None
    prompt_tokens: int | None = None
    error: str | None = None


@dataclass
class DecisionRecord:
    return_id: str
    template_key: str
    start: float
    end: float
    path: str
    fallback_reason: str | None
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.calls) and all(c.ok for c in self.calls)

    @property
    def latency_s(self) -> float:
        return self.end - self.start

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens or 0 for c in self.calls)


class RecordingClient:
    """The LlmClient generate() sees: times every call and keeps its usage."""

    def __init__(self, inner: UsageClient) -> None:
        self.inner = inner
        self.calls: list[CallRecord] = []

    def complete(self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()) -> Completion:
        t0 = time.perf_counter()
        try:
            completion = self.inner.complete(case, retry_codes=retry_codes)
        except LlmUnavailable as exc:
            self.calls.append(CallRecord(time.perf_counter() - t0, False, error=str(exc)))
            raise
        usage = self.inner.last_usage or {}
        prompt = usage.get("prompt_tokens")
        self.calls.append(
            CallRecord(
                elapsed_s=time.perf_counter() - t0,
                ok=True,
                completion_tokens=completion.completion_tokens,
                prompt_tokens=prompt if isinstance(prompt, int) else None,
            )
        )
        return completion


class Clients:
    """One UsageClient per worker thread."""

    def __init__(self, config: VllmConfig) -> None:
        self.config = config
        self._local = threading.local()

    def get(self) -> UsageClient:
        client = getattr(self._local, "client", None)
        if client is None:
            client = UsageClient(self.config)
            self._local.client = client
        return client


def run_decision(case: BenchCase, clients: Clients) -> DecisionRecord:
    recorder = RecordingClient(clients.get())
    start = time.perf_counter()
    result = generate(case.case, case.expected, recorder)
    end = time.perf_counter()
    return DecisionRecord(
        return_id=case.return_id,
        template_key=case.template_key,
        start=start,
        end=end,
        path=result.path.value,
        fallback_reason=result.fallback_reason,
        calls=recorder.calls,
    )


def fixed_case_list(cases: Sequence[BenchCase], n: int, seed: int) -> list[BenchCase]:
    """n cases drawn once for every load: each decision type (template key) gets one case when
    the list has room for every type, and the rest in the store's proportions (largest
    remainder, ties by key); the cases of a type are taken in a seeded order and reused in turn
    when the store has fewer, and the whole list is then shuffled with the seed."""
    if not cases or n < 1:
        raise ValueError("B3 needs at least one case and one decision per load")
    by_type: dict[str, list[BenchCase]] = defaultdict(list)
    for case in cases:
        by_type[case.template_key].append(case)
    base = 1 if n >= len(by_type) else 0
    rest = n - base * len(by_type)
    shares = {k: rest * len(v) / len(cases) for k, v in by_type.items()}
    quota = {k: base + math.floor(v) for k, v in shares.items()}
    order = sorted(shares, key=lambda k: (-(shares[k] - math.floor(shares[k])), k))
    for key in order[: n - sum(quota.values())]:
        quota[key] += 1
    rng = random.Random(seed)
    chosen: list[BenchCase] = []
    for key in sorted(by_type):
        pool = sorted(by_type[key], key=lambda c: c.return_id)
        rng.shuffle(pool)
        chosen += [pool[i % len(pool)] for i in range(quota[key])]
    rng.shuffle(chosen)
    return chosen


def run_load(
    cases: Sequence[BenchCase], workers: int, clients: Clients
) -> tuple[list[DecisionRecord], float]:
    """Run every case with `workers` in flight; wall time from the first start to the last end.
    With len(cases) == workers this is a batch sent at once (a barrier releases them together)."""
    barrier = threading.Barrier(workers) if len(cases) == workers else None

    def job(case: BenchCase) -> DecisionRecord:
        if barrier is not None:
            barrier.wait()
        return run_decision(case, clients)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(job, cases))
    wall = max(r.end for r in records) - min(r.start for r in records)
    return records, wall


def load_summary(records: Sequence[DecisionRecord], walls: Sequence[float]) -> dict[str, Any]:
    ok = [r for r in records if r.ok]
    latency = summary([r.latency_s * 1000 for r in ok], 1)
    if 0 < latency["n"] < MIN_P95:
        latency["p95"] = None  # too few values for a 95th percentile
    calls = [c for r in ok for c in r.calls]
    per_request = [
        c.completion_tokens / c.elapsed_s for c in calls if c.completion_tokens and c.elapsed_s > 0
    ]
    tokens = sum(r.completion_tokens for r in ok)
    wall_total = sum(walls)
    prompt = [c.prompt_tokens for c in calls if c.prompt_tokens is not None]
    return {
        "decisions": len(records),
        "failed": len(records) - len(ok),
        "paths": dict(Counter(r.path for r in records)),
        "fallbacks": dict(Counter(r.fallback_reason for r in records if r.fallback_reason)),
        "model_calls": sum(len(r.calls) for r in records),
        "case_mix": dict(sorted(Counter(r.template_key for r in records).items())),
        "latency_ms": latency,
        "wall_seconds": summary(list(walls), 3),
        "output_tokens": tokens,
        "output_tokens_per_decision": summary([float(r.completion_tokens) for r in ok], 1),
        "prompt_tokens_per_call": summary([float(p) for p in prompt], 1),
        "output_tokens_per_second": round(tokens / wall_total, 2) if wall_total > 0 else None,
        "per_request_tokens_per_second": summary(per_request, 2),
        "decisions_per_second": round(len(ok) / wall_total, 4) if wall_total > 0 else None,
    }


def _read_instant(path: Path | None) -> datetime | None:
    if path is None or not path.exists():
        return None
    try:
        return datetime.fromisoformat(
            path.read_text(encoding="utf-8").strip().replace("Z", "+00:00")
        )
    except ValueError:
        return None


def _get_json(url: str, api_key: str, timeout_s: float = 10.0) -> Any:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with opener.open(request, timeout=timeout_s) as response:
            return json.loads(response.read(1_000_000))
    except (OSError, ValueError):
        return None


def server_info(base_url: str, api_key: str) -> dict[str, Any]:
    """vLLM version (/version) and served model (/v1/models) of the local server (rule 2: the
    URL is checked like VllmClient's)."""
    endpoint = check_local_url(base_url)  # .../v1/chat/completions
    v1 = endpoint.rsplit("/chat/completions", 1)[0]
    root = v1.rsplit("/v1", 1)[0]
    version = _get_json(f"{root}/version", api_key)
    models = _get_json(f"{v1}/models", api_key)
    served = (models or {}).get("data") if isinstance(models, Mapping) else None
    first = served[0] if isinstance(served, list) and served else {}
    return {
        "vllm_version": version.get("version") if isinstance(version, Mapping) else None,
        "served_model": first.get("id") if isinstance(first, Mapping) else None,
        "max_model_len": first.get("max_model_len") if isinstance(first, Mapping) else None,
    }


def _b4_pipeline_seconds(path: Path | None) -> Decimal | None:
    """B4's nightly seconds: the main store's operating steps, without the OOD store, the offline
    risk evaluation and the generation (the nightly cost adds the day's generation at the B3
    rate instead; review G6). None without a measured B4 part that has them."""
    if path is None or not path.exists():
        return None
    part = read_json(path)
    nightly = (part.get("details") or {}).get("nightly") or {}
    if part.get("status") != "measured" or nightly.get("seconds") is None:
        return None
    return Decimal(str(nightly["seconds"]))


def _read_text(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _disk_used_gb(path: Path | None) -> Decimal | None:
    text = _read_text(path)
    try:
        value = Decimal(text.rstrip("Gg")) if text else None
    except ArithmeticError:
        return None
    return value if value is not None and value > 0 else None


def first_request(base_url: str, api_key: str, model: str, out: Path, timeout_s: float) -> bool:
    """Time one guided-JSON completion of the contract example case (the first request after
    vLLM became healthy, before anything else uses it) and write it to `out`."""
    case = json.loads((ROOT / FIRST_REQUEST_CASE).read_text(encoding="utf-8"))
    client = UsageClient(VllmConfig(base_url, api_key, model, timeout_s))
    doc: dict[str, Any] = {"case": FIRST_REQUEST_CASE.as_posix(), "at": iso(utc_now())}
    t0 = time.perf_counter()
    try:
        completion = client.complete(case)
    except LlmUnavailable as exc:
        doc.update(ok=False, error=str(exc)[:300])
    else:
        usage = client.last_usage or {}
        doc.update(
            ok=True,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            completion_tokens=completion.completion_tokens,
            prompt_tokens=usage.get("prompt_tokens"),
        )
    write_json(out, doc)
    return bool(doc["ok"])


def run_b3(config: B3Config, log: Log = lambda _m: None) -> Part:
    started = utc_now()
    case_set = build_cases(config.data_root)
    n = config.decisions_per_load()
    measured = fixed_case_list(case_set.cases, n, config.seed)
    live = config.mode == "live"
    fake = None if live else FakeVllm(latency=config.fake_latency).start()
    try:
        base_url = config.base_url if live else fake.base_url  # type: ignore[union-attr]
        api_key = config.api_key if live else fake.api_key  # type: ignore[union-attr]
        model = config.model if live else fake.model  # type: ignore[union-attr]
        info = server_info(base_url, api_key)
        clients = Clients(VllmConfig(base_url, api_key, model, config.timeout_s, config.max_tokens))
        details: dict[str, Any] = {
            "cases": case_set.summary(),
            "server": info,
            "case_list": {
                "decisions_per_load": n,
                "seed": config.seed,
                "return_ids": [c.return_id for c in measured],
                "by_decision": dict(sorted(Counter(c.template_key for c in measured).items())),
                "golden": sum(c.golden for c in measured),
                "rule": "drawn once, stratified by decision type in the store's proportions; "
                "every batch size and concurrency level runs these decisions (review G4)",
            },
        }
        # Warm-up: the first cases of the store (golden first), one at a time, untimed.
        warm = [run_decision(c, clients) for c in case_set.cases[: config.warmup]]
        details["warmup"] = load_summary(warm, [r.latency_s for r in warm])
        # One untimed pass over the list: every load then sees the same prefix cache.
        unique = list({c.return_id: c for c in measured}.values())
        width = min(len(unique), max((*config.batch_sizes, *config.concurrency)))
        passed, pass_wall = run_load(unique, width, clients)
        details["prefix_cache_pass"] = {
            "decisions": len(passed),
            "concurrency": width,
            "wall_seconds": round(pass_wall, 3),
            "why": "every case of the list sent once before the loads, so each load finds the "
            "same prefixes cached (latencies of known cases: the prefill is mostly cached)",
        }
        batches: dict[int, dict[str, Any]] = {}
        for size in config.batch_sizes:
            records: list[DecisionRecord] = []
            walls: list[float] = []
            for begin in range(0, n, size):
                got, wall = run_load(measured[begin : begin + size], size, clients)
                records += got
                walls.append(wall)
            batches[size] = load_summary(records, walls)
            log(
                f"B3 batch {size}: {batches[size]['latency_ms']} ms, "
                f"{batches[size]['output_tokens_per_second']} tok/s"
            )
        levels: dict[int, dict[str, Any]] = {}
        for level in config.concurrency:
            got, wall = run_load(measured, level, clients)
            levels[level] = load_summary(got, [wall])
            log(
                f"B3 concurrency {level}: {levels[level]['latency_ms']} ms, "
                f"{levels[level]['output_tokens_per_second']} tok/s"
            )
    finally:
        if fake is not None:
            fake.stop()
    details["batches"] = {str(k): v for k, v in batches.items()}
    details["concurrency"] = {str(k): v for k, v in levels.items()}
    if fake is not None:
        details["fake_server"] = {
            "requests": fake.requests,
            "max_in_flight": fake.max_in_flight,
            "latency_model": f"{fake.latency.ttft_s} s + tokens / {fake.latency.tokens_per_s}",
        }
    started_at = _read_instant(config.vllm_start_file)
    healthy_at = _read_instant(config.vllm_healthy_file)
    cold_start_s = (
        (healthy_at - started_at).total_seconds()
        if live and started_at and healthy_at and healthy_at >= started_at
        else None
    )
    conditions = _read_text(config.vllm_conditions_file) or "page cache state not recorded"
    details["vllm_start_conditions"] = conditions
    first = (
        read_json(config.first_request_file)
        if live and config.first_request_file is not None and config.first_request_file.exists()
        else None
    )
    details["first_request"] = first
    snapshot_gb = _disk_used_gb(config.disk_used_file) if live else None
    # Cost: the declared scenario needs the live batch-32 rate and the B4 pipeline.
    volume = costs.decisions_per_day(config.data_root, case_set.as_of)
    batch32 = batches.get(32)
    rate = None  # seconds per decision at batch 32: the wall time of a batch over its size
    if live and batch32 and batch32["failed"] == 0 and batch32["wall_seconds"].get("n"):
        rate = Decimal(str(batch32["wall_seconds"]["p50"])) / Decimal(32)
    inputs = costs.Inputs(
        decisions_per_day=volume,
        seconds_per_decision=rate,
        pipeline_seconds=_b4_pipeline_seconds(config.b4_part) if live else None,
        vllm_start_seconds=Decimal(str(cold_start_s)) if cold_start_s is not None else None,
        snapshot_gb=snapshot_gb,
        vllm_start_origin=f"measured on a running droplet ({conditions}): a lower bound for a "
        "start from a snapshot",
    )
    details["cost"] = {
        "assumptions": costs.ASSUMPTIONS.as_dict(),
        "declared_scenario": costs.DECLARED,
        "inputs": {
            "decisions_per_day": str(volume),
            "decisions_per_day_origin": "return requests per day in the "
            f"{costs.VOLUME_WINDOW_DAYS} days before {case_set.as_of.date()} in the store B3 "
            f"read ({case_set.provenance.nature})",
            "seconds_per_decision": str(rate) if rate is not None else None,
            "pipeline_seconds": str(inputs.pipeline_seconds) if inputs.pipeline_seconds else None,
            "vllm_start_seconds": str(inputs.vllm_start_seconds)
            if inputs.vllm_start_seconds is not None
            else None,
            "snapshot_gb": str(snapshot_gb) if snapshot_gb is not None else None,
        },
        "scenarios": costs.all_costs(inputs),
    }
    finished = utc_now()
    measurements = _measurements(
        config,
        details,
        batches,
        levels,
        cold_start_s,
        warm,
        started.date(),
        finished.date(),
        case_set,
    )
    versions = env.software()
    for key in ("vllm_version", "served_model"):
        if info.get(key):
            versions[key] = str(info[key])
    failures = sum(v["failed"] for v in (*batches.values(), *levels.values()))
    notes = []
    if not live:
        notes.append(
            "Dry run against bench.fake_vllm on 127.0.0.1: simulated numbers that check "
            "the harness; no Gemma, no MI300X."
        )
    status = "dry_run"
    if live:
        # Measured when a decision was generated; a start or a first request alone is not B3.
        generated = any(m.nature == "real" and m.phase == "generation" for m in measurements)
        status = "measured" if generated else "failed"
    if status == "failed":
        notes.append("No decision succeeded against the live server: no latency to report.")
    if failures:
        notes.append(
            f"{failures} decisions failed (model call error or timeout): kept out of "
            "the latencies, counted in details."
        )
    if live and cold_start_s is None:
        notes.append("vLLM was already running: no cold start measured in this run.")
    if live and first is None:
        b4_ran = config.b4_part is not None and config.b4_part.exists()
        notes.append(
            "No first request timed right after vLLM became healthy (make bench-vllm): the "
            "first decision of B3 is reported as a warm generation"
            + (", after B4's batch generation." if b4_ran else ".")
        )
    notes.append(
        f"Every load ran the same {n} decisions (seeded, stratified by decision type), after one "
        "untimed pass that filled vLLM's prefix cache; p95 only from 20 latencies or more."
    )
    return Part(
        benchmark="B3",
        status=status,
        title="Gemma on vLLM: latency, throughput and cost per decision"
        + ("" if live else " (dry run, fake server)"),
        dataset=short(
            f"{len(case_set.cases)} llm_case inputs built by llm.case from the open returns of "
            f"the {case_set.provenance.nature} Navigli Fashion store as of "
            f"{case_set.as_of.date()}; every load on the same {n} decisions, stratified by "
            "decision type"
        ),
        started_at=started,
        finished_at=finished,
        versions=versions,
        config={
            "mode": config.mode,
            "model": short(model, 64),
            "temperature": TEMPERATURE,
            "max_tokens": config.max_tokens,
            "guided_json": "response_format json_schema strict (llm_output)",
            "batch_sizes": ",".join(map(str, config.batch_sizes)),
            "batch_repeats": config.batch_repeats,
            "concurrency": ",".join(map(str, config.concurrency)),
            "decisions_per_load": n,
            "case_seed": config.seed,
            "warmup": config.warmup,
            "timeout_s": config.timeout_s,
            "cost_assumptions": costs.VERSION,
        },
        measurements=measurements,
        notes=notes,
        details=details,
        hardware=env.hardware(gpu=False),
    )


def _measurements(
    config: B3Config,
    details: Mapping[str, Any],
    batches: Mapping[int, Mapping[str, Any]],
    levels: Mapping[int, Mapping[str, Any]],
    cold_start_s: float | None,
    warm: Sequence[DecisionRecord],
    start: date,
    end: date,
    case_set: CaseSet,
) -> list[Measurement]:
    live = config.mode == "live"
    nature = "real" if live else "simulated"
    device = "mi300x" if live else "cpu"
    server = details["server"]
    where = (
        f"Gemma {config.model} on vLLM {server.get('vllm_version') or '(version unknown)'}, "
        "MI300X droplet"
        if live
        else "Illustrative dry run against the local fake OpenAI-compatible server, not Gemma"
    )
    origin = short(
        f"B3, measured by bench through llm.client.VllmClient: {where}; inputs "
        f"from the {case_set.provenance.nature} store"
    )
    common: dict[str, Any] = {
        "benchmark": "B3",
        "device": device,
        "origin": origin,
        "period_start": start,
        "period_end": end,
        "nature": nature,
        "data_nature": case_set.provenance.nature,
    }
    out: list[Measurement] = []
    if cold_start_s is not None:
        out.append(
            Measurement(
                metric="vllm_start_to_healthy_seconds",
                label=short(
                    "vLLM start: container start to /health answering (15 s polling) on a "
                    f"running droplet, {details['vllm_start_conditions']}; compile cache kept: "
                    "not a start from a snapshot"
                ),
                value=round(cold_start_s, 1),
                unit="seconds",
                phase="cold_start",
                warm=False,
                repeats=1,
                **common,
            )
        )
    first_request = details.get("first_request")
    if first_request and first_request.get("ok"):
        out.append(
            Measurement(
                metric="first_request_ms",
                label=short(
                    "First request after vLLM became healthy, before any other (one guided-JSON "
                    "completion of the contract example case, make bench-vllm): grammar "
                    "compile and first kernels included"
                ),
                value=float(first_request["latency_ms"]),
                unit="milliseconds",
                phase="cold_start",
                concurrency=1,
                batch_size=1,
                warm=False,
                repeats=1,
                **common,
            )
        )
    first = next((r for r in warm if r.ok), None)
    if first is not None:
        after = (
            "vLLM had already served the first request and B4's batch generation"
            if live and config.b4_part is not None and config.b4_part.exists()
            else "the server may have served earlier requests"
        )
        out.append(
            Measurement(
                metric="first_b3_request_ms",
                label=short(
                    f"First decision of B3 (warm-up, not in the percentiles); {after}: not a "
                    "cold start"
                ),
                value=round(first.latency_s * 1000, 1),
                unit="milliseconds",
                phase="generation",
                concurrency=1,
                batch_size=1,
                warm=True,
                repeats=1,
                **common,
            )
        )
    for size, s in batches.items():
        if not s["latency_ms"].get("n"):
            continue
        tag = f"batch {size} sent at once, {s['latency_ms']['n']} decisions in batches"
        for q in ("p50", "p95"):
            if s["latency_ms"].get(q) is None:
                continue
            out.append(
                Measurement(
                    metric=f"latency_{q}_ms_batch_{size}",
                    label=short(f"Latency {q} of one decision, {tag}"),
                    value=float(s["latency_ms"][q]),
                    unit="milliseconds",
                    phase="generation",
                    concurrency=size,
                    batch_size=size,
                    warm=True,
                    repeats=s["latency_ms"]["n"],
                    detail={"latency_ms": s["latency_ms"]},
                    **common,
                )
            )
        out.append(
            Measurement(
                metric=f"batch_wall_seconds_{size}",
                label=short(
                    f"Wall time of a batch of {size} decisions (median of "
                    f"{s['wall_seconds']['n']} batches)"
                ),
                value=float(s["wall_seconds"]["p50"]),
                unit="seconds",
                phase="generation",
                concurrency=size,
                batch_size=size,
                warm=True,
                repeats=s["wall_seconds"]["n"],
                **common,
            )
        )
        if s["output_tokens_per_second"] is not None:
            out.append(
                Measurement(
                    metric=f"output_tokens_per_second_batch_{size}",
                    label=short(f"Output tokens per second, aggregate, {tag}"),
                    value=float(s["output_tokens_per_second"]),
                    unit="tokens_per_second",
                    phase="generation",
                    concurrency=size,
                    batch_size=size,
                    warm=True,
                    repeats=s["wall_seconds"]["n"],
                    **common,
                )
            )
    for level, s in levels.items():
        if not s["latency_ms"].get("n"):
            continue
        tag = f"concurrency {level}, {s['decisions']} decisions in a closed loop"
        for q in ("p50", "p95"):
            if s["latency_ms"].get(q) is None:
                continue
            out.append(
                Measurement(
                    metric=f"latency_{q}_ms_concurrency_{level}",
                    label=short(f"Latency {q} of one decision, {tag}"),
                    value=float(s["latency_ms"][q]),
                    unit="milliseconds",
                    phase="generation",
                    concurrency=level,
                    warm=True,
                    repeats=s["latency_ms"]["n"],
                    detail={"latency_ms": s["latency_ms"]},
                    **common,
                )
            )
        if s["output_tokens_per_second"] is not None:
            out.append(
                Measurement(
                    metric=f"output_tokens_per_second_concurrency_{level}",
                    label=short(f"Output tokens per second, aggregate, {tag}"),
                    value=float(s["output_tokens_per_second"]),
                    unit="tokens_per_second",
                    phase="generation",
                    concurrency=level,
                    warm=True,
                    repeats=1,
                    **common,
                )
            )
        single = s["per_request_tokens_per_second"]
        if single.get("n"):
            out.append(
                Measurement(
                    metric=f"request_tokens_per_second_concurrency_{level}",
                    label=short(
                        f"Output tokens per second of one request (median, prefill included), {tag}"
                    ),
                    value=float(single["p50"]),
                    unit="tokens_per_second",
                    phase="generation",
                    concurrency=level,
                    warm=True,
                    repeats=single["n"],
                    **common,
                )
            )
    for scenario, result in details["cost"]["scenarios"].items():
        if "unavailable" in result:
            continue
        out.append(
            Measurement(
                benchmark="B3",
                metric=f"cost_eur_per_1000_decisions_{scenario.replace('-', '_')}",
                label=short(
                    f"Cost per 1,000 decisions, scenario {scenario} ({costs.VERSION}): "
                    f"{result['eur_per_decision']} EUR per decision, target "
                    f"{costs.TARGET_EUR_PER_DECISION}; break-even "
                    f"{result['break_even_decisions_per_day']} decisions per day"
                ),
                value=float(result["eur_per_1000_decisions"]),
                unit="eur",
                device="mi300x",  # the droplet the scenario prices, whatever ran this part
                phase="cost",
                origin=short(
                    f"B3 cost, simulated from {costs.VERSION} (assumptions in the details): "
                    f"{result['usd_per_day']} USD per day "
                    f"at 1.99 USD/h, {costs.ASSUMPTIONS.eur_per_usd} EUR/USD (assumed), "
                    f"{details['cost']['inputs']['decisions_per_day']} decisions per day "
                    f"({case_set.provenance.nature} store)"
                ),
                period_start=start,
                period_end=end,
                nature="simulated",
                data_nature=case_set.provenance.nature,
                detail=result,
            )
        )
    return out
