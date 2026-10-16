"""Opt-in HTTP integration check on synthetic fixtures, not an accuracy benchmark.

Uses the real API regeneration route, engine decision and validator. Prompts contain generated
test-shop data only. The key is read with a hidden prompt or from RADEON_API_KEY, never saved.
Requires requirements/dev.txt. Run from the repository root: python -m scripts.check_tokenfactory.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.api.serving import Serving
from llm.client import Completion, TokenFactoryClient
from pipeline.generator.golden import ELENA, GIULIA, MARCO_ACCOUNTS
from pipeline.serving.export import export
from tests.api_harness import (
    SEED,
    _reset,
    build_store,
    golden_return,
    open_api,
    settings_for,
    sqlite_url,
    validate_body,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("artifacts/tokenfactory-check"))
    parser.add_argument(
        "--fixtures", type=Path, help="reuse a previous run's synthetic serving snapshot"
    )
    args = parser.parse_args()
    key = os.environ.get("RADEON_API_KEY") or getpass.getpass("AMD API key (hidden): ")
    run = args.out.resolve() / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True, exist_ok=False)
    serving_dir = run / "serving"
    if args.fixtures is None:
        data = build_store(run / "data")
        export(data, data / "artifacts", serving_dir, seed=SEED)
    else:
        serving_dir = args.fixtures.resolve() / "serving"
    serving = Serving.load(serving_dir)
    scored = serving.frame("scores/request_scores")
    marco = next(c for c in MARCO_ACCOUNTS if (scored["customer_id"] == c).any())
    database = run / "demo.sqlite"
    initial = settings_for(serving_dir, database)
    asyncio.run(_reset(sqlite_url(database), serving, initial))
    settings = settings_for(
        serving_dir,
        database,
        llm_mode="tokenfactory",
        radeon_api_key=key,
        llm_concurrency=1,
        vllm_timeout_s=90,
    )

    class TracedClient(TokenFactoryClient):
        """Keep synthetic replies for diagnosing validator rejections; never the API key."""

        def complete(
            self, case: Mapping[str, Any], *, retry_codes: Sequence[str] = ()
        ) -> Completion:
            completion = super().complete(case, retry_codes=retry_codes)
            n = len(list(run.glob("reply-*.json"))) + 1
            (run / f"reply-{n:02d}.json").write_text(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "retry_codes": list(retry_codes),
                        "content": completion.content,
                    },
                    indent=2,
                )
                + "\n",
                "utf-8",
            )
            return completion

    rows: list[dict[str, Any]] = []
    with contextmanager(open_api)(serving, settings, database, llm_client=TracedClient(key)) as api:
        ready = api.client.get("/readyz")
        validate_body("readyz_response", ready.json())
        (run / "readyz.json").write_text(json.dumps(ready.json(), indent=2) + "\n", "utf-8")
        for name, customer in (("giulia", GIULIA), ("marco", marco), ("elena", ELENA)):
            return_id = golden_return(serving, customer)
            before = api.client.get(f"/api/returns/{return_id}").json()
            decision = before["decision"]
            started = time.perf_counter()
            response = api.client.post(
                f"/api/returns/{return_id}/regenerate",
                json={
                    "decision_id": decision["decision_id"],
                    "languages": ["it", "en"],
                    "requested_by": "sara",
                },
            )
            row: dict[str, Any] = {
                "case": name,
                "http_status": response.status_code,
                "round_trip_seconds": round(time.perf_counter() - started, 3),
            }
            if response.status_code == 200:
                body = response.json()
                validate_body("regenerate_response", body)
                row["generation"] = body["draft"]["generation"]
                row["decision_unchanged"] = all(
                    body["decision"][field] == decision[field]
                    for field in ("decision_id", "path", "quadrant", "proposed_levers")
                )
                (run / f"{name}.json").write_text(json.dumps(body, indent=2) + "\n", "utf-8")
            rows.append(row)
            print(json.dumps(row), flush=True)
    report = {
        "nature": "synthetic",
        "purpose": "HTTP integration rehearsal, not model accuracy",
        "provider": "AMD Token Factory",
        "dedicated_gpu_claim": False,
        "cases": rows,
    }
    (run / "result.json").write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(f"Result: {run / 'result.json'}")
    return (
        0
        if all(
            r.get("decision_unchanged")
            and r.get("generation", {}).get("generation_path") in ("model", "regenerated")
            for r in rows
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
