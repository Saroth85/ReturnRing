"""The demo reset (US-903, ADR-03): python -m apps.api.reset [--limit N] [--no-drafts]

In one command, deterministic for a serving snapshot and SEED:

1. drop and recreate every table of contracts/sql.py (CHECKs and append-only triggers);
2. load the stored assumption version assumptions-v1 and the general coupon rule coupon-rule-v1;
3. run the intake (flow F1) of every open return of the serving snapshot, golden cases first:
   the decision (version 1) and its draft, in LLM_MODE (mock or vLLM) or, with
   DEMO_MODE=cached, from the MI300X cache (SERVING_DIR/llm_cache), the template otherwise.

Every timestamp is the snapshot instant (a fixed clock), so two resets of the same snapshot
write the same rows. Reads DATABASE_URL and SERVING_DIR; never data/ (rule 12).

Exit codes: 0 done, 1 some returns failed (listed), 2 usage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.db import create_schema, drop_schema, make_engine
from apps.api.services.cases import IntakeError, intake
from apps.api.services.generation import Generator
from apps.api.services.seeds import load_seeds
from apps.api.serving import Serving
from apps.api.settings import Settings
from llm.client import LlmClient
from llm.generate import GenerationBlocked
from policy.engine import PolicyError


async def reset(
    engine: AsyncEngine,
    serving: Serving,
    generator: Generator | None,
    *,
    limit: int | None = None,
    log: Any = None,
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
    drafts: Counter[str] = Counter()
    failed: dict[str, str] = {}
    for n, return_id in enumerate(ids, start=1):
        record = serving.store_return(return_id)
        if record is None:
            failed[return_id] = "not in the store"
            continue
        try:
            async with engine.begin() as conn:
                view = await intake(conn, serving, generator, record, at=at, source="store")
        except (IntakeError, PolicyError, GenerationBlocked, ValueError) as exc:
            failed[return_id] = f"{type(exc).__name__}: {exc}"[:300]
            continue
        paths[view.path.value] += 1
        quadrants[view.quadrant.value if view.quadrant else "none"] += 1
        if generator is not None and view.generation_id is not None:
            drafts["ready"] += 1
        if n % 50 == 0:
            say(f"{n} of {len(ids)} returns")
    return {
        "as_of": at.isoformat(),
        "returns": len(ids),
        "decided": sum(paths.values()),
        "paths": dict(sorted(paths.items())),
        "quadrants": dict(sorted(quadrants.items())),
        "drafts": dict(drafts),
        "failed": failed,
        "golden_first": ids[:5],
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m apps.api.reset", description=__doc__)
    p.add_argument("--limit", type=int, help="only the first N open returns (golden first)")
    p.add_argument("--no-drafts", action="store_true", help="leave the drafts pending")
    return p


def main(argv: Sequence[str] | None = None, *, llm_client: LlmClient | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    if settings.database_url is None:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    started = time.perf_counter()

    def log(message: str) -> None:
        print(f"[reset {time.perf_counter() - started:6.1f} s] {message}", file=sys.stderr)

    serving = Serving.load(settings.serving_dir)
    log(f"serving snapshot {settings.serving_dir} at {serving.as_of.isoformat()}")
    generator = None if args.no_drafts else Generator(settings, llm_client)

    async def run() -> dict[str, Any]:
        engine = make_engine(settings.database_url or "")
        try:
            return await reset(engine, serving, generator, limit=args.limit, log=log)
        finally:
            await engine.dispose()

    summary = asyncio.run(run())
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
