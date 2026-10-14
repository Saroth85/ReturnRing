"""The demo reset (US-903, ADR-03): python -m apps.api.reset [--limit N] [--no-drafts]

In one command, deterministic for a serving snapshot and SEED:

1. drop and recreate every table of contracts/sql.py (CHECKs and append-only triggers);
2. load the stored assumption version assumptions-v1 and the general coupon rule coupon-rule-v1;
3. run the intake (flow F1) of every open return of the serving snapshot, golden cases first:
   the decision (version 1), one transaction per return;
4. make their drafts outside any transaction, with at most LLM_CONCURRENCY model calls in
   flight (vLLM batches them, ADR-03; H32), in LLM_MODE (mock or vLLM) or, with
   DEMO_MODE=cached, from the MI300X cache (SERVING_DIR/llm_cache), the template otherwise,
   and store them in the same order, so the ids stay deterministic;
5. record the golden cases with their live ids (GET /api/demo/cases) and start the demo clock:
   the API's 'now' is the snapshot instant plus the time elapsed since then (DEMO_CLOCK,
   ADR-15 section 15).

Every timestamp is the snapshot instant (a fixed clock), so two resets of the same snapshot
write the same rows (the wall time the demo clock started aside). Reads DATABASE_URL and
SERVING_DIR; never data/ (rule 12).

Exit codes: 0 done; 1 some returns failed (listed), or vLLM was unavailable for some drafts in
LLM_MODE=vllm (they use the template, labelled); 2 usage, or a serving snapshot that cannot be
read (the reason and the remedy are printed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.clock import system_now
from apps.api.db import create_schema, drop_schema, make_engine
from apps.api.services.cases import IntakeError, Proposal, admit
from apps.api.services.demo import record_reset
from apps.api.services.generation import Generator
from apps.api.services.generation import store as store_generation
from apps.api.services.seeds import load_seeds
from apps.api.serving import Serving, ServingError
from apps.api.settings import Settings
from contracts.io import ContractError
from llm.client import LlmClient
from llm.generate import GenerationBlocked
from policy.engine import PolicyError

REMEDY = (
    "build the serving snapshot first: make pipeline (generate, graph, features, anomalies, "
    "risk), then python -m pipeline.serving --data data --artifacts artifacts --out serving"
)


async def reset(
    engine: AsyncEngine,
    serving: Serving,
    generator: Generator | None,
    *,
    limit: int | None = None,
    log: Any = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """Drop, recreate, seed and pre-compute; returns a summary."""
    say = log or (lambda _m: None)
    at = serving.as_of
    await drop_schema(engine)
    await create_schema(engine)
    async with engine.begin() as conn:
        await load_seeds(conn, at)
    ids = serving.open_return_ids()
    if limit is not None:
        ids = ids[:limit]
    paths: Counter[str] = Counter()
    quadrants: Counter[str] = Counter()
    failed: dict[str, str] = {}
    admitted: list[Proposal] = []
    for n, return_id in enumerate(ids, start=1):
        record = serving.store_return(return_id)
        if record is None:
            failed[return_id] = "not in the store"
            continue
        try:
            async with engine.begin() as conn:
                proposal = await admit(conn, serving, record, at=at, source="store")
        except (IntakeError, PolicyError, ValueError) as exc:
            failed[return_id] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        admitted.append(proposal)
        view = proposal.view
        paths[view.path.value] += 1
        quadrants[view.quadrant.value if view.quadrant else "none"] += 1
        if n % 100 == 0:
            say(f"{n} of {len(ids)} returns decided")
    drafts: Counter[str] = Counter()
    generation: Counter[str] = Counter()
    if generator is not None and admitted:
        say(f"{len(admitted)} drafts, {generator.settings.llm_concurrency} model calls at once")
        made = await asyncio.to_thread(
            generator.generate_each,
            [p.draft_inputs() for p in admitted],
            created_at=at,
            on_done=lambda n: say(f"{n} of {len(admitted)} drafts") if n % 100 == 0 else None,
        )
        for proposal, outcome in zip(admitted, made, strict=True):
            if isinstance(outcome, Exception):  # the case stays draft_status pending
                failed[proposal.view.return_id] = f"draft {type(outcome).__name__}: {outcome}"[:300]
                continue
            try:
                async with engine.begin() as conn:
                    await store_generation(conn, outcome, proposal.view)
            except (GenerationBlocked, ValueError) as exc:
                failed[proposal.view.return_id] = f"draft {type(exc).__name__}: {exc}"[:300]
                continue
            info = outcome.info
            drafts["ready"] += 1
            reason = f"/{info.fallback_reason}" if info.fallback_reason else ""
            generation[f"{info.generation_path}{reason}"] += 1
    async with engine.begin() as conn:
        await record_reset(conn, serving, started_at or system_now())
    return {
        "as_of": at.isoformat(),
        "returns": len(ids),
        "decided": sum(paths.values()),
        "paths": dict(sorted(paths.items())),
        "quadrants": dict(sorted(quadrants.items())),
        "drafts": dict(drafts),
        "generation": dict(sorted(generation.items())),
        "failed": failed,
        "golden_first": ids[:5],
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m apps.api.reset", description=__doc__)
    p.add_argument("--limit", type=int, help="only the first N open returns (golden first)")
    p.add_argument("--no-drafts", action="store_true", help="leave the drafts pending")
    return p


def unavailable_drafts(summary: dict[str, Any]) -> int:
    return int(summary.get("generation", {}).get("template/unavailable", 0))


def main(argv: Sequence[str] | None = None, *, llm_client: LlmClient | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    started = time.perf_counter()

    def log(message: str) -> None:
        print(f"[reset {time.perf_counter() - started:6.1f} s] {message}", file=sys.stderr)

    try:
        serving = Serving.load(settings.serving_dir)
    except (ServingError, ContractError, OSError, ValueError, KeyError) as exc:
        print(f"the serving snapshot {settings.serving_dir} cannot be read: {exc}", file=sys.stderr)
        print(f"remedy: {REMEDY}", file=sys.stderr)
        return 2
    log(f"serving snapshot {settings.serving_dir} at {serving.as_of.isoformat()}")
    generator = None if args.no_drafts else Generator(settings, llm_client)
    if generator is not None and settings.llm_mode == "vllm" and not settings.cached:
        generator.client()
        if generator.client_error is not None:
            log(f"WARNING: vLLM is not configured, every draft uses the template: "
                f"{generator.client_error}")  # fmt: skip

    async def run() -> dict[str, Any]:
        engine = make_engine(settings.database_url or "")
        try:
            return await reset(engine, serving, generator, limit=args.limit, log=log)
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    print(json.dumps(summary, indent=2, sort_keys=True))
    missing = unavailable_drafts(summary)
    fell_back = missing if settings.llm_mode == "vllm" and not settings.cached else 0
    if fell_back:
        log(f"WARNING: vLLM was unavailable for {fell_back} drafts: they use the template")
    elif missing and settings.cached:
        log(f"WARNING: the MI300X cache has no output for {missing} drafts: they use the template")
    return 1 if summary["failed"] or fell_back else 0


if __name__ == "__main__":
    sys.exit(main())
