"""Where the precomputed outputs of the cached demo come from (US-901; rules 11 and 13).

"Precomputed on AMD MI300X" is a claim about hardware, so /readyz shows it only when both hold:

- the demo package's MANIFEST.json (scripts/demo_export.py), next to the serving snapshot as in
  demo-cache/ (SERVING_DIR/../MANIFEST.json; infra/compose.demo.yml mounts it there), says the
  drafts are not a rehearsal, were made on hardware that names the MI300X, and names their
  model (gemma_model: GEMMA_MODEL of the droplet, which the export checked every draft against
  on a batch that ran with DEVICE=gpu);
- every entry of SERVING_DIR/llm_cache/ was written by that model.

The drafts are compared with the package's model, not with GEMMA_MODEL of this API: in cached
mode the API calls no model, and a demo server keeps the .env.example default while the package
may hold the plan-B model's drafts (second review of 14 Oct 2026). Otherwise (a CPU rehearsal
with a fake vLLM server, another model, no manifest) the label says it is a rehearsal, and the
demo_cache check says why: a CPU package is never labelled MI300X. No server path reaches the
public reason (H26).

The answer is kept per cache folder and manifest until either changes (their modification
times), so /readyz does not read every entry on each call.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from apps.api import schemas as s

MANIFEST_NAME: Final = "MANIFEST.json"
PACKAGE_FORMAT: Final = "returnring.demo_export.v1"  # scripts/demo_export.py FORMAT
GPU_NAME: Final = "MI300X"  # scripts/demo_export.py GPU_NAME


@dataclass(frozen=True)
class CacheOrigin:
    """What the cache holds and the label it earns: entries 0 means no precomputed output."""

    entries: int
    label: s.CachedLabel | None
    detail: str | None  # why the label is the rehearsal one


_MEMO: dict[tuple[Path, Path], tuple[tuple[object, ...], CacheOrigin]] = {}
_LOCK = threading.Lock()


def manifest_path(serving_dir: Path) -> Path:
    """The package manifest next to the snapshot, as in demo-cache/."""
    return serving_dir.parent / MANIFEST_NAME


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _package_model(manifest: Any) -> tuple[str | None, str | None]:
    """The model of the drafts of an MI300X package, and None; or None and why the package
    manifest does not say MI300X."""
    if not isinstance(manifest, dict) or manifest.get("format") != PACKAGE_FORMAT:
        return None, "no MANIFEST.json of a demo package next to the serving snapshot"
    cache = manifest.get("llm_cache")
    cache = cache if isinstance(cache, dict) else {}
    if cache.get("rehearsal") is not False:
        return None, "the package MANIFEST.json declares a rehearsal"
    generated_on = cache.get("generated_on")
    if not isinstance(generated_on, str) or GPU_NAME not in generated_on:
        return None, f"the package MANIFEST.json does not name the {GPU_NAME} of its drafts"
    model = cache.get("gemma_model")
    if not isinstance(model, str) or not model:
        return None, "the package MANIFEST.json names no model for its drafts"
    return model, None


def _origin(cache_dir: Path, manifest: Path) -> CacheOrigin:
    entries = sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
    if not entries:
        return CacheOrigin(0, None, None)
    model, problem = _package_model(_read_json(manifest))
    if model is not None:
        models = set()
        for path in entries:
            entry = _read_json(path)
            models.add(str(entry.get("model")) if isinstance(entry, dict) else "unreadable")
        others = sorted(models - {model})
        if others:
            problem = f"drafts of {', '.join(others)} in the cache, not of the package's {model}"
    if problem is None:
        return CacheOrigin(len(entries), s.CACHED_LABEL, None)
    return CacheOrigin(len(entries), s.REHEARSAL_LABEL, f"{problem}: labelled a rehearsal")


def cache_origin(serving_dir: Path) -> CacheOrigin:
    """The origin of SERVING_DIR/llm_cache/ (see the module doc)."""
    cache_dir, manifest = serving_dir / "llm_cache", manifest_path(serving_dir)
    key = (cache_dir, manifest)
    stamp = (_stamp(cache_dir), _stamp(manifest))
    with _LOCK:
        found = _MEMO.get(key)
    if found is not None and found[0] == stamp:
        return found[1]
    origin = _origin(cache_dir, manifest)
    with _LOCK:
        _MEMO[key] = (stamp, origin)
    return origin
