"""B3: Gemma on the local vLLM: latency p50 and p95, tokens per second, cost per decision.

What is timed is one decision's generation as the product runs it: llm.generate.generate on a
real llm_case (bench.cases: the synthetic store's open returns, golden first), through
llm.client.VllmClient against VLLM_BASE_URL only (rule 2), guided JSON, the validator, and one
regeneration when the validator asks for it. A decision whose model call failed (HTTP error,
timeout) is counted as a failure and kept out of the latencies: the template fallback is fast and
would flatter them.

Loads (spec 12, "Tempi distinti" and "Carichi crescenti"):
- batches of 1, 8 and 32 decisions sent at once (the nightly pre-generation of ADR-03), each
  repeated: latency p50 and p95 inside the batch, batch wall time, output tokens per second;
- closed-loop concurrency levels 1, 8 and 32 (C requests in flight until N are done): latency
  p50 and p95, decisions per second, aggregate and per-request output tokens per second;
- cold start: vLLM start to healthy (from the start and healthy files the Makefile writes, 15 s
  polling) and the first request after it, apart from the warm-up.

Output tokens are vLLM's usage.completion_tokens; tokens per second of one request are its output
tokens over its whole latency (prefill included), aggregate ones the output tokens of a load over
its wall time. Modes: live (the MI300X) and dry_run (bench.fake_vllm on 127.0.0.1: simulated
numbers that check the harness only). The cost per decision (bench.cost) comes with the part.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import Counter
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
from bench.record import Measurement, Part, read_json, short, utc_now
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


@dataclass(frozen=True)
class B3Config:
    mode: Mode
    data_root: Path
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    batch_sizes: tuple[int, ...] = (1, 8, 32)
    batch_repeats: int = 2
    concurrency: tuple[int, ...] = (1, 8, 32)
    requests_per_level: int = 16  # a level runs max(this, 2 x concurrency) decisions
    warmup: int = 2
    timeout_s: float = 180.0
    max_tokens: int = 1200
    vllm_start_file: Path | None = None
    vllm_healthy_file: Path | None = None
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


class CaseCycle:
    """Cases in order, round robin: consecutive jobs get distinct cases."""

    def __init__(self, cases: Sequence[BenchCase]) -> None:
        self.cases = list(cases)
        self.position = 0

    def take(self, n: int) -> list[BenchCase]:
        out = [self.cases[(self.position + i) % len(self.cases)] for i in range(n)]
        self.position = (self.position + n) % len(self.cases)
        return out


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
        "latency_ms": summary([r.latency_s * 1000 for r in ok], 1),
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
    """Seconds of the B4 pipeline without its generation step (the nightly cost adds the day's
    generation at the B3 rate instead)."""
    if path is None or not path.exists():
        return None
    part = read_json(path)
    steps = (part.get("details") or {}).get("steps") or []
    if part.get("status") != "measured" or not steps:
        return None
    return sum(
        (Decimal(str(s["seconds"])) for s in steps if s.get("name") != "batch_generation"),
        Decimal(0),
    )


def run_b3(config: B3Config, log: Log = lambda _m: None) -> Part:
    started = utc_now()
    case_set = build_cases(config.data_root)
    cycle = CaseCycle(case_set.cases)
    live = config.mode == "live"
    fake = None if live else FakeVllm(latency=config.fake_latency).start()
    try:
        base_url = config.base_url if live else fake.base_url  # type: ignore[union-attr]
        api_key = config.api_key if live else fake.api_key  # type: ignore[union-attr]
        model = config.model if live else fake.model  # type: ignore[union-attr]
        info = server_info(base_url, api_key)
        clients = Clients(VllmConfig(base_url, api_key, model, config.timeout_s, config.max_tokens))
        details: dict[str, Any] = {"cases": case_set.summary(), "server": info}
        warm = [run_decision(c, clients) for c in cycle.take(config.warmup)]
        details["warmup"] = load_summary(warm, [r.latency_s for r in warm])
        batches: dict[int, dict[str, Any]] = {}
        for size in config.batch_sizes:
            records: list[DecisionRecord] = []
            walls: list[float] = []
            for _ in range(config.batch_repeats):
                got, wall = run_load(cycle.take(size), size, clients)
                records += got
                walls.append(wall)
            batches[size] = load_summary(records, walls)
            log(
                f"B3 batch {size}: {batches[size]['latency_ms']} ms, "
                f"{batches[size]['output_tokens_per_second']} tok/s"
            )
        levels: dict[int, dict[str, Any]] = {}
        for level in config.concurrency:
            n = max(config.requests_per_level, 2 * level)
            got, wall = run_load(cycle.take(n), level, clients)
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
        status = "measured" if any(m.nature == "real" for m in measurements) else "failed"
    if status == "failed":
        notes.append("No decision succeeded against the live server: no latency to report.")
    if failures:
        notes.append(
            f"{failures} decisions failed (model call error or timeout): kept out of "
            "the latencies, counted in details."
        )
    if live and cold_start_s is None:
        notes.append("vLLM was already running: no cold start measured in this run.")
    return Part(
        benchmark="B3",
        status=status,
        title="Gemma on vLLM: latency, throughput and cost per decision"
        + ("" if live else " (dry run, fake server)"),
        dataset=short(
            f"{len(case_set.cases)} llm_case inputs built by llm.case from the open returns of "
            f"the {case_set.provenance.nature} Navigli Fashion store as of "
            f"{case_set.as_of.date()}, golden cases first"
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
            "requests_per_level": config.requests_per_level,
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
                label="vLLM cold start: container start to /health answering (15 s polling)",
                value=round(cold_start_s, 1),
                unit="seconds",
                phase="cold_start",
                warm=False,
                repeats=1,
                **common,
            )
        )
    first = next((r for r in warm if r.ok), None)
    if first is not None:
        out.append(
            Measurement(
                metric="first_request_ms",
                label="First decision of the run (warm-up, not in the percentiles), one request",
                value=round(first.latency_s * 1000, 1),
                unit="milliseconds",
                phase="cold_start",
                concurrency=1,
                batch_size=1,
                warm=False,
                repeats=1,
                **common,
            )
        )
    for size, s in batches.items():
        if not s["latency_ms"].get("n"):
            continue
        tag = f"batch {size} sent at once, {config.batch_repeats} repeats"
        for q in ("p50", "p95"):
            out.append(
                Measurement(
                    metric=f"latency_{q}_ms_batch_{size}",
                    label=short(
                        f"Latency {q} of one decision, {tag} ({s['latency_ms']['n']} decisions)"
                    ),
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
                    f"Wall time of a batch of {size} decisions (median of {config.batch_repeats})"
                ),
                value=float(s["wall_seconds"]["p50"]),
                unit="seconds",
                phase="generation",
                concurrency=size,
                batch_size=size,
                warm=True,
                repeats=config.batch_repeats,
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
                    repeats=config.batch_repeats,
                    **common,
                )
            )
    for level, s in levels.items():
        if not s["latency_ms"].get("n"):
            continue
        tag = f"concurrency {level}, {s['decisions']} decisions in a closed loop"
        for q in ("p50", "p95"):
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
                    f"B3 cost, simulated from {costs.VERSION}: {result['usd_per_day']} USD per day "
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
