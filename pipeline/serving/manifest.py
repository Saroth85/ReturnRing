"""The manifest of a serving snapshot: what it holds, from where, and the sha256 of each file.

Written last by the export, read first by the API: verify() recomputes every hash, so a file
changed or added after the export (outside llm_cache/, which the MI300X batch job fills later and
whose entries the API validates one by one) makes the snapshot unusable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from contracts.files import atomic_write

MANIFEST: Final = "manifest.json"
FORMAT: Final = "returnring.serving.v1"
LLM_CACHE: Final = "llm_cache"  # filled by python -m apps.api.batch_generate, not hashed here
CHUNK: Final = 1 << 20
# The keys of the manifest's "versions": the model_versions every decision records (the export
# writes exactly these; the API contract examples use the same keys, ADR-15 section 16).
MODEL_VERSION_KEYS: Final = (
    "risk",
    "risk_customer",
    "checkout",
    "reference",
    "value",
    "evidence",
    "anomalies",
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_files(root: Path) -> Iterator[Path]:
    """Every file of a snapshot the manifest covers: all but the manifest and llm_cache/."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] != LLM_CACHE and rel.as_posix() != MANIFEST:
            yield path


@dataclass(frozen=True)
class Manifest:
    """A serving snapshot as the API sees it."""

    as_of: datetime
    seed: int
    tables: Mapping[str, Mapping[str, Any]]
    models: Mapping[str, Mapping[str, Any]]
    files: Mapping[str, Mapping[str, Any]]
    raw: Mapping[str, Any]

    @classmethod
    def read(cls, root: Path) -> Manifest:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError(f"{root / MANIFEST} is not a serving manifest ({FORMAT})")
        return cls(
            as_of=datetime.fromisoformat(str(data["as_of"])),
            seed=int(data["seed"]),
            tables=data["tables"],
            models=data["models"],
            files=data["files"],
            raw=data,
        )

    def model_versions(self) -> dict[str, str]:
        """The version of each model and versioned method the snapshot was built with."""
        return {str(k): str(v) for k, v in self.raw.get("versions", {}).items()}


def write_manifest(root: Path, document: Mapping[str, Any]) -> Path:
    """Write the manifest with the sha256 and size of every file of the snapshot."""
    files = {
        p.relative_to(root).as_posix(): {"sha256": sha256_of(p), "bytes": p.stat().st_size}
        for p in snapshot_files(root)
    }
    body = {"format": FORMAT, **document, "files": files}
    text = json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return atomic_write(root / MANIFEST, lambda out: out.write(text.encode("utf-8")))


def verify(root: Path) -> list[str]:
    """Why the snapshot under `root` is not the one its manifest describes (empty: it is)."""
    try:
        manifest = Manifest.read(root)
    except (OSError, ValueError, KeyError) as exc:
        return [f"manifest: {type(exc).__name__}: {exc}"]
    problems: list[str] = []
    found = {p.relative_to(root).as_posix() for p in snapshot_files(root)}
    for rel in sorted(found - set(manifest.files)):
        problems.append(f"not in the manifest: {rel}")
    for rel, entry in sorted(manifest.files.items()):
        path = root / rel
        if rel not in found:
            problems.append(f"missing: {rel}")
        elif sha256_of(path) != entry.get("sha256"):
            problems.append(f"changed since the export: {rel}")
    return problems
