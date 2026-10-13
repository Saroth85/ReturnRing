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

Exit codes: 0 done, 1 some returns failed, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from apps.api import schemas as s
from apps.api.services.cases import IntakeError, propose
from apps.api.services.generation import (
    Generator,
    _Recording,
    cache_entry,
    cache_path,
    llm_case,
)
from apps.api.services.seeds import COUPON_RULE_V1
from apps.api.serving import Serving
from apps.api.settings import Settings
from contracts.files import atomic_write
from llm.client import LlmClient
from llm.generate import GenerationBlocked, generate
from policy.engine import PolicyError
from policy.simulator import PROPOSAL_EXAMPLE


def write_entry(target: Path, data: bytes) -> None:
    atomic_write(target, lambda out: out.write(data))


def batch(
    serving: Serving,
    client: LlmClient,
    model: str,
    *,
    limit: int | None = None,
    force: bool = False,
    log: Any = None,
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
    for n, return_id in enumerate(ids, start=1):
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
            target = cache_path(cache, item.digest)
            if target.exists() and not force:
                skipped += 1
                continue
            recorder = _Recording(client)
            started = time.perf_counter()
            result = generate(item.case, item.expected, recorder)
            latency = round((time.perf_counter() - started) * 1000)
        except (IntakeError, PolicyError, GenerationBlocked, ValueError) as exc:
            failed[return_id] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        paths[result.path.value] += 1
        if result.fallback_reason == "unavailable":
            failed[return_id] = "vLLM unavailable: not cached"
            continue
        tokens = sum(c.completion_tokens or 0 for c in recorder.completions) or None
        entry = cache_entry(item, result, model, at, latency, tokens)
        data = (json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        write_entry(target, data)
        if n % 25 == 0:
            say(f"{n} of {len(ids)} returns")
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

    serving = Serving.load(settings.serving_dir)
    generator = Generator(settings, llm_client)
    summary = batch(
        serving,
        generator.client(),
        settings.gemma_model,
        limit=args.limit,
        force=args.force,
        log=log,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
