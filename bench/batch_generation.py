"""The batch generation step of the nightly pipeline, as B4 times it (ADR-03):
python -m bench.batch_generation --data DIR --out FILE [--concurrency 32] [--limit N]

Every open return of DIR's scores/request_scores gets its draft: bench.cases builds the llm_case
(golden first), llm.generate.generate runs the model, the validator, one regeneration and the
template fallback, `concurrency` decisions in flight. LLM_MODE picks the model like the API
(llm.client.client_from_env): mock on a laptop (no model: the step then times the case building
and validation only, and says so), vllm on the droplet (the local vLLM, rule 2). Writes one JSON
line per decision and prints a summary. Exit 1 when the model was unavailable for any decision
in vllm mode (the drafts would be templates), 0 otherwise.

The API's own batch (apps/api/batch_generate, phase 4) also writes the demo cache; when it is
merged, B4 can time it instead by changing the step's command in bench/b4_pipeline.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bench.cases import BenchCase, build_cases
from llm.client import LlmClient, MockClient, client_from_env
from llm.generate import generate


def _client_factory() -> tuple[str, Any]:
    mode = os.environ.get("LLM_MODE", "mock")
    local = threading.local()

    def get() -> LlmClient:
        client = getattr(local, "client", None)
        if client is None:
            client = MockClient() if mode == "mock" else client_from_env()
            local.client = client
        return client

    return mode, get


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bench.batch_generation", description=__doc__)
    p.add_argument("--data", type=Path, required=True, help="root of store/, features/, scores/")
    p.add_argument("--out", type=Path, required=True, help="JSON lines, one per decision")
    p.add_argument("--concurrency", type=int, default=32)
    p.add_argument("--limit", type=int, help="only the first N open returns (golden first)")
    args = p.parse_args(argv)
    if args.concurrency < 1:
        p.error("--concurrency must be at least 1")
    mode, get = _client_factory()
    started = time.perf_counter()
    case_set = build_cases(args.data, args.limit)
    built = time.perf_counter() - started

    def one(case: BenchCase) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = generate(case.case, case.expected, get())
        return {
            "return_id": case.return_id,
            "decision": case.template_key,
            "path": result.path.value,
            "fallback_reason": result.fallback_reason,
            "model_calls": result.model_calls,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(one, case_set.cases))
    generation = time.perf_counter() - t0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    unavailable = sum(r["fallback_reason"] == "unavailable" for r in rows)
    summary = {
        "llm_mode": mode,
        "decisions": len(rows),
        "skipped_cases": len(case_set.skipped),
        "concurrency": args.concurrency,
        "cases_seconds": round(built, 3),
        "generation_seconds": round(generation, 3),
        "paths": dict(Counter(r["path"] for r in rows)),
        "unavailable": unavailable,
        "note": "mock model: no generation, case building and validation only"
        if mode == "mock"
        else "local vLLM",
    }
    print(json.dumps(summary, sort_keys=True))
    return 1 if mode != "mock" and unavailable else 0


if __name__ == "__main__":
    sys.exit(main())
