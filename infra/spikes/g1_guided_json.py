"""SPK-G1 / SPK-G5: guided-JSON requests to the local vLLM, with tokens per second.

Standard library only. Runs in the spike-client container on the compose network, so vLLM needs
no published port (NFR-11):

    make spike-g1                       # SPK-G1, contracts/json/llm_output.schema.json
    make spike-g5 IMAGE=artifacts/spikes/garment.jpg   # SPK-G5, inspection checklist from a photo

The first request compiles the JSON grammar (xgrammar) and is reported as warm-up only. Tokens per
second here are completion tokens over the wall time of one non-streamed request, so they include
the time to first token; use `make spike-g1-bench` (vllm bench serve) for B3-grade numbers.
It also checks that vLLM refuses a request without the API key (NFR-11).
Exit codes: 0 pass, 1 fail (HTTP error, invalid JSON, truncated output, key not enforced).
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LLM_OUTPUT_SCHEMA = ROOT / "contracts/json/llm_output.schema.json"

CASE_SYSTEM = (
    "You explain return decisions that are already taken. Reply with one JSON object with exactly "
    "these fields: decision_echo (path, quadrant and lever_ids copied from the case), "
    "operator_explanation (at most 8 statements, each with a label OBSERVED, SCORE or HYPOTHESIS, "
    "the fact_ids it uses and an English text of at most 300 characters) and customer_message "
    "with sentence_ids: 2 to 5 phrasebook ids from the schema, one opening first, at most three "
    "extras, one closing last. You do not write the customer message: code writes it from the ids."
)
CASE_USER = (
    "Case: withdrawal path, quadrant Q4, lever instant_refund:false, risk 0.87, 7 linked accounts. "
    "Explain to the operator and write the customer message with the placeholder {terms}."
)

# Spike-only schema for SPK-G5 (US-501 items), not a contract: categorical confidence because the
# model's confidence is not calibrated, and "not_observable" is a valid outcome.
_ITEM: dict[str, Any] = {
    "type": "object",
    "required": ["outcome", "confidence"],
    "additionalProperties": False,
    "properties": {
        "outcome": {"enum": ["yes", "no", "not_observable"]},
        "confidence": {"enum": ["low", "medium", "high"]},
    },
}
CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tag_attached", "visible_damage", "photo_quality_ok", "notes"],
    "additionalProperties": False,
    "properties": {
        "tag_attached": _ITEM,
        "visible_damage": _ITEM,
        "photo_quality_ok": _ITEM,
        "notes": {"type": "string", "maxLength": 300},
    },
}
CHECKLIST_SYSTEM = (
    "You help a warehouse operator inspect a returned garment from one photo. Reply with one JSON "
    "object: tag_attached, visible_damage and photo_quality_ok, each with outcome (yes, no or "
    "not_observable) and confidence (low, medium or high), plus notes (English, at most 300 "
    "characters). Use not_observable when the photo does not show it. Do not guess the cause or "
    "the time of a damage."
)


def load_case_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(LLM_OUTPUT_SCHEMA.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    return schema


def build_body(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.image:
        mime = mimetypes.guess_type(args.image.name)[0] or "image/jpeg"
        b64 = base64.b64encode(args.image.read_bytes()).decode()
        system, schema, name = CHECKLIST_SYSTEM, CHECKLIST_SCHEMA, "inspection_checklist"
        user: list[dict[str, Any]] = [
            {"type": "text", "text": "Inspect this returned item."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    else:
        system, schema, name = CASE_SYSTEM, load_case_schema(), "llm_output"
        user = [{"type": "text", "text": CASE_USER}]
    body = {
        "model": os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": args.max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}},
    }
    return body, schema


def post(url: str, body: dict[str, Any], timeout: float) -> tuple[dict[str, Any], float]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('VLLM_API_KEY', '')}",
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local vLLM only
        data: dict[str, Any] = json.load(resp)
    return data, time.perf_counter() - start


def key_enforced(base: str, timeout: float) -> bool | None:
    """NFR-11: a request without the API key must get HTTP 401. None if vLLM is unreachable."""
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=timeout):  # noqa: S310 - local vLLM
            return False
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except (urllib.error.URLError, TimeoutError):
        return None


def check(text: str, finish: str, schema: dict[str, Any]) -> list[str]:
    problems = []
    if finish != "stop":
        problems.append(f"finish_reason={finish!r} (expected 'stop'; 'length' means truncated)")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return [*problems, f"not valid JSON: {exc}"]
    missing = [k for k in schema.get("required", []) if k not in obj]
    extra = [k for k in obj if k not in schema.get("properties", {})]
    if missing or extra:
        problems.append(f"missing keys {missing}, unexpected keys {extra}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", type=Path, help="SPK-G5: photo of a returned garment")
    parser.add_argument("--runs", type=int, default=3, help="requests; the first is warm-up")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", type=Path, help="write the result as JSON")
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be >= 2 (the first request is warm-up)")

    base = os.environ.get("VLLM_BASE_URL", "http://vllm:8001/v1").rstrip("/")
    body, schema = build_body(args)
    runs: list[dict[str, Any]] = []
    status = 0
    text = ""
    for i in range(args.runs):
        try:
            data, elapsed = post(f"{base}/chat/completions", body, args.timeout)
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:2000]}", file=sys.stderr)
            return 1
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"cannot reach {base}: {exc}", file=sys.stderr)
            return 1
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        tokens = int(data.get("usage", {}).get("completion_tokens", 0))
        problems = check(text, str(choice.get("finish_reason")), schema)
        status = status or (1 if problems else 0)
        run = {
            "run": i,
            "warmup": i == 0,
            "elapsed_s": round(elapsed, 3),
            "completion_tokens": tokens,
            "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
            "tokens_per_s": round(tokens / elapsed, 2) if elapsed else None,
            "finish_reason": choice.get("finish_reason"),
            "problems": problems,
        }
        runs.append(run)
        print(json.dumps(run), flush=True)
    print(text)

    timed = [r["tokens_per_s"] for r in runs if not r["warmup"] and r["tokens_per_s"]]
    enforced = key_enforced(base, args.timeout)
    if enforced is not True:
        print(f"FAIL: request without the API key was not refused with 401 ({enforced})")
        status = 1
    summary = {
        "spike": "SPK-G5" if args.image else "SPK-G1",
        "model": body["model"],
        "base_url": base,
        "max_tokens": args.max_tokens,
        "runs": runs,
        "tokens_per_s_median_after_warmup": round(statistics.median(timed), 2) if timed else None,
        "nfr11_key_enforced": enforced,
        "last_output": text,
        "pass": status == 0,
    }
    print(
        f"\n{summary['spike']}: pass={summary['pass']} median "
        f"{summary['tokens_per_s_median_after_warmup']} tokens/s (single request, after warm-up)"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
