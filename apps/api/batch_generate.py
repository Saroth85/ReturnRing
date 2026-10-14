"""The MI300X batch of drafts for cached mode (ADR-03, US-901):
python -m apps.api.batch_generate [--limit N] [--force]

Runs on the GPU droplet with LLM_MODE=vllm (the local vLLM, rule 2). For every open return of the
serving snapshot, golden cases first, it builds the llm_case the API builds at the demo reset
(the same intake, the same snapshot instant, assumptions-v1 and coupon-rule-v1), sends it to
vLLM through llm.generate (validator, one regeneration, template), and writes the result to
SERVING_DIR/llm_cache/<sha256 of the llm_case>.json. The public demo (DEMO_MODE=cached, no GPU)
serves these outputs, validated again and labelled "Precomputed on AMD MI300X". An output that
fell back because vLLM was unavailable is not cached: the demo then uses its own template,
labelled as such.

The cases are sent with at most LLM_CONCURRENCY model calls in flight (vLLM batches concurrent
requests, ADR-03; H32): 623 returns take minutes, not one call after the other for hours.

Exit codes: 0 done, 1 some returns failed, 2 usage, a vLLM configuration the client refuses or
a serving snapshot that cannot be read (the reason is printed).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.api import schemas as s
from apps.api.services.cases import IntakeError, propose
from apps.api.services.generation import (
    Generator,
    LlmCase,
    _Recording,
    cache_entry,
    cache_path,
    llm_case,
)
from apps.api.services.seeds import COUPON_RULE_V1
from apps.api.serving import Serving, ServingError
from apps.api.settings import DEFAULT_LLM_CONCURRENCY, Settings
from contracts.files import atomic_write
from contracts.io import ContractError
from llm.client import LlmClient, VllmClient
from llm.generate import Generation, GenerationBlocked, generate
from policy.engine import PolicyError
from policy.simulator import PROPOSAL_EXAMPLE


def write_entry(target: Path, data: bytes) -> None:
    atomic_write(target, lambda out: out.write(data))


@dataclass(frozen=True)
class _Job:
    return_id: str
    item: LlmCase
    target: Path


@dataclass(frozen=True)
class _Done:
    job: _Job
    result: Generation
    latency_ms: int
    tokens: int | None


def _run(client: LlmClient, job: _Job) -> _Done:
    recorder = _Recording(client)
    started = time.perf_counter()
    result = generate(job.item.case, job.item.expected, recorder)
    latency = round((time.perf_counter() - started) * 1000)
    tokens = sum(c.completion_tokens or 0 for c in recorder.completions) or None
    return _Done(job, result, latency, tokens)


def batch(
    serving: Serving,
    client: LlmClient,
    model: str,
    *,
    limit: int | None = None,
    force: bool = False,
    log: Any = None,
    concurrency: int = DEFAULT_LLM_CONCURRENCY,
) -> dict[str, Any]:
    """Write the cache entries of the open returns; returns a summary."""
    say = log or (lambda _m: None)
    cache = serving.root / "llm_cache"
    cache.mkdir(parents=True, exist_ok=True)
    at = serving.as_of
    assumptions = s.Assumptions.from_policy(PROPOSAL_EXAMPLE)
    ids = serving.open_return_ids()
    if limit is not None:
        ids = ids[:limit]
    paths: Counter[str] = Counter()
    skipped = 0
    failed: dict[str, str] = {}
    jobs: list[_Job] = []
    for return_id in ids:
        record = serving.store_return(return_id)
        if record is None:
            failed[return_id] = "not in the store"
            continue
        try:
            proposal = propose(
                serving,
                record,
                created_at=at,
                assumptions=assumptions,
                coupon_rule_version=COUPON_RULE_V1,
            )
            item = llm_case(proposal.draft_inputs())
        except (IntakeError, PolicyError, ValueError) as exc:
            failed[return_id] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        target = cache_path(cache, item.digest)
        if target.exists() and not force:
            skipped += 1
            continue
        jobs.append(_Job(return_id, item, target))
    say(f"{len(jobs)} cases to generate, {min(concurrency, max(1, len(jobs)))} at once")
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(jobs) or 1))) as pool:
        futures = [pool.submit(_run, client, job) for job in jobs]
        for n, future in enumerate(futures, start=1):  # in order: the summary is deterministic
            try:
                done = future.result()
            except (GenerationBlocked, ValueError) as exc:
                failed[jobs[n - 1].return_id] = f"{type(exc).__name__}: {exc}"[:300]
                continue
            result = done.result
            paths[result.path.value] += 1
            if result.fallback_reason == "unavailable":
                failed[done.job.return_id] = "vLLM unavailable: not cached"
                continue
            entry = cache_entry(done.job.item, result, model, at, done.latency_ms, done.tokens)
            data = (json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
            write_entry(done.job.target, data)
            if n % 25 == 0:
                say(f"{n} of {len(jobs)} cases")
    return {
        "as_of": at.isoformat(),
        "returns": len(ids),
        "paths": dict(sorted(paths.items())),
        "skipped_cached": skipped,
        "failed": failed,
        "cache": str(cache),
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m apps.api.batch_generate", description=__doc__)
    p.add_argument("--limit", type=int, help="only the first N open returns (golden first)")
    p.add_argument("--force", action="store_true", help="generate again what is cached")
    return p


def main(argv: Sequence[str] | None = None, *, llm_client: LlmClient | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    if llm_client is None and settings.llm_mode != "vllm":
        print(
            "batch_generate runs with LLM_MODE=vllm (the cache holds vLLM outputs)", file=sys.stderr
        )
        return 2
    started = time.perf_counter()

    def log(message: str) -> None:
        print(f"[batch {time.perf_counter() - started:6.1f} s] {message}", file=sys.stderr)

    try:
        serving = Serving.load(settings.serving_dir)
    except (ServingError, ContractError, OSError, ValueError, KeyError) as exc:
        print(f"the serving snapshot {settings.serving_dir} cannot be read: {exc}", file=sys.stderr)
        return 2
    generator = Generator(settings, llm_client)
    client = generator.client()
    if llm_client is None and not isinstance(client, VllmClient):
        print(f"vLLM is not configured: {generator.client_error}", file=sys.stderr)
        return 2
    summary = batch(
        serving,
        client,
        settings.gemma_model,
        limit=args.limit,
        force=args.force,
        log=log,
        concurrency=settings.llm_concurrency,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
