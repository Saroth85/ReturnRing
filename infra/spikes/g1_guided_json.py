"""SPK-G1 / SPK-G5: one guided-JSON request to the local vLLM, with tokens per second.

Usage (on the droplet): VLLM_BASE_URL=http://localhost:8001/v1 VLLM_API_KEY=... \
    python infra/spikes/g1_guided_json.py [--image photo.jpg]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.request
from pathlib import Path

SCHEMA = json.loads(Path("contracts/json/llm_output.schema.json").read_text())
SCHEMA.pop("$schema", None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()

    content: list[dict[str, object]] = [
        {
            "type": "text",
            "text": "Case: withdrawal path, quadrant Q4, lever instant_refund:false, risk 0.87, "
            "7 linked accounts. Explain to the operator and write the customer message with "
            "the placeholder {terms}. Reply with JSON only.",
        }
    ]
    if args.image:
        b64 = base64.b64encode(args.image.read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    body = {
        "model": os.environ.get("GEMMA_MODEL", "google/gemma-4-31B-it"),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 600,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "llm_output", "schema": SCHEMA},
        },
    }
    req = urllib.request.Request(
        os.environ.get("VLLM_BASE_URL", "http://localhost:8001/v1") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('VLLM_API_KEY', '')}",
        },
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310 - local endpoint
        data = json.load(resp)
    elapsed = time.perf_counter() - start
    text = data["choices"][0]["message"]["content"]
    tokens = data.get("usage", {}).get("completion_tokens", 0)
    json.loads(text)  # raises if the output is not valid JSON
    print(text)
    print(
        f"\n{tokens} tokens in {elapsed:.1f} s = {tokens / elapsed:.1f} tokens/s (single request)"
    )


if __name__ == "__main__":
    main()
