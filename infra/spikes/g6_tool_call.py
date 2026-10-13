"""SPK-G6: one round trip with a fake read-only tool through vLLM tool calling.

Standard library only; runs in the spike-client container (make spike-g6). Needs vLLM started with
--enable-auto-tool-choice --tool-call-parser=gemma4 (infra/docker-compose.yml).
  1. The model is asked a question it can answer only with the tool; it must call get_case_orders.
  2. The fake tool result goes back as a "tool" message; the answer must cite both order IDs.
--mode auto uses tool_choice "auto" (needs the gemma4 parser). --mode named forces the function
through structured outputs and works without the parser: the plan B of spec section 13 (the code
picks the tool, Gemma writes the answer). Exit codes: 0 pass, 1 fail.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

TOOL = {
    "type": "function",
    "function": {
        "name": "get_case_orders",
        "description": "Read-only. Orders linked to a return case.",
        "parameters": {
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        },
    },
}
QUESTION = "Which orders are linked to return case R-1042? Use the tool, then answer in one line."
FAKE_RESULT = {"case_id": "R-1042", "orders": ["O-1001", "O-1002"]}


def chat(base: str, body: dict[str, Any]) -> tuple[dict[str, Any], float]:
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('VLLM_API_KEY', '')}",
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310 - local vLLM only
        data: dict[str, Any] = json.load(resp)
    return data, time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["auto", "named"], default="auto")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    base = os.environ.get("VLLM_BASE_URL", "http://vllm:8001/v1").rstrip("/")
    model = os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it")
    choice: Any = "auto"
    if args.mode == "named":
        choice = {"type": "function", "function": {"name": "get_case_orders"}}
    messages: list[dict[str, Any]] = [{"role": "user", "content": QUESTION}]
    result: dict[str, Any] = {"spike": "SPK-G6", "mode": args.mode, "model": model}
    try:
        first, t1 = chat(
            base,
            {
                "model": model,
                "messages": messages,
                "tools": [TOOL],
                "tool_choice": choice,
                "temperature": 0.0,
                "max_tokens": 256,
            },
        )
        msg = first["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        result["step1"] = {
            "elapsed_s": round(t1, 3),
            "tool_calls": calls,
            "content": msg.get("content"),
        }
        call = calls[0] if calls else None
        if not call or call["function"]["name"] != "get_case_orders":
            result["pass"] = False
            result["reason"] = "no call to get_case_orders in step 1"
        else:
            arguments = json.loads(call["function"]["arguments"] or "{}")
            result["step1"]["arguments"] = arguments
            messages += [
                {"role": "assistant", "content": msg.get("content") or "", "tool_calls": [call]},
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", "call_0"),
                    "content": json.dumps(FAKE_RESULT),
                },
            ]
            second, t2 = chat(
                base,
                {
                    "model": model,
                    "messages": messages,
                    "tools": [TOOL],
                    "tool_choice": "none",
                    "temperature": 0.0,
                    "max_tokens": 256,
                },
            )
            answer = second["choices"][0]["message"].get("content") or ""
            cited = all(order in answer for order in FAKE_RESULT["orders"])
            result["step2"] = {"elapsed_s": round(t2, 3), "answer": answer, "cites_orders": cited}
            result["pass"] = arguments.get("case_id") == "R-1042" and cited
    except urllib.error.HTTPError as exc:
        result["pass"] = False
        result["reason"] = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:2000]}"
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        result["pass"] = False
        result["reason"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    if not result["pass"]:
        print(f"SPK-G6 FAIL ({args.mode}). Plan B: --mode named.", file=sys.stderr)
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
