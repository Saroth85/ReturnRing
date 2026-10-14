"""The cached demo package (US-901, spec 11 step 6): make demo-export, make demo-verify.

    python3 -m scripts.demo_export build --serving serving --bench bench/results --out demo-cache
    python3 -m scripts.demo_export verify demo-cache

`build` packs what the public demo (DEMO_MODE=cached, GPU off) serves, after
`python -m apps.api.batch_generate` ran on the MI300X:

- serving/: the serving snapshot of ADR-16 (store, features, scores, models; synthetic data, no
  ground truth), checked first against its own manifest.json, with llm_cache/: the drafts Gemma
  wrote on the MI300X through vLLM, one file per llm_case;
- bench/results/: the benchmark pointers (latest*.json) and the run files they name, as
  GET /api/benchmarks reads them;
- MANIFEST.json, written last: the sha256 and size of every file, the snapshot instant and seed,
  what the cache holds (entries by model, generation path and fallback reason) and the nature of
  each part (rule 11).

`verify` recomputes every hash of a package, and of its serving manifest; `make demo-up` and
`make demo-local` run it before starting. It uses the standard library only, so it runs on the
droplet host and on the demo server, where the project's Python environment is not installed.
No container image is part of the package (ADR-12).

Exit codes: 0 done, 1 the package (or the snapshot) is not what its manifest says, or the cache
is empty without --allow-empty-cache, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

FORMAT: Final = "returnring.demo_export.v1"
MANIFEST: Final = "MANIFEST.json"
SERVING_FORMAT: Final = "returnring.serving.v1"  # pipeline/serving/manifest.py
SERVING_MANIFEST: Final = "manifest.json"
LLM_CACHE: Final = "llm_cache"
CACHE_FORMAT: Final = "returnring.llm_cache.v1"  # apps/api/services/generation.py
BENCH_LATEST: Final = "returnring.bench.latest/1"  # bench/record.py
CHUNK: Final = 1 << 20
NATURE: Final[Mapping[str, str]] = {
    "serving": "synthetic: the Navigli Fashion store generated from the ASOS calibration "
    "(docs/synthetic-store.md), with the scores and models computed on it",
    "llm_cache": "generated: drafts written through vLLM by the models listed, by python -m "
    "apps.api.batch_generate on the hardware in generated_on, checked again by llm/validator.py "
    "when served; amounts, terms and deadlines come from templates (rule 10)",
    "bench": "measured where a run says measured, pending otherwise (docs/benchmarks.md)",
}


class PackageError(Exception):
    """The input or the package is not what it should be."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def files_under(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError(f"{path}: {type(exc).__name__}: {exc}") from exc


def check_no_truth(root: Path) -> None:
    """Rule 12: the package never holds a path with a truth part (the snapshot has none)."""
    for path in files_under(root):
        if "truth" in path.relative_to(root).parts:
            raise PackageError(f"{path}: a ground-truth path in the package (rule 12)")


def check_serving(serving: Path) -> dict[str, Any]:
    """The snapshot's own manifest: every file present and unchanged, none added outside
    llm_cache/ (the rule of pipeline.serving.manifest.verify). Returns the manifest."""
    manifest = _read_json(serving / SERVING_MANIFEST)
    if not isinstance(manifest, dict) or manifest.get("format") != SERVING_FORMAT:
        raise PackageError(f"{serving / SERVING_MANIFEST} is not a serving manifest")
    listed = manifest.get("files")
    if not isinstance(listed, dict) or not listed:
        raise PackageError(f"{serving / SERVING_MANIFEST} lists no file")
    found = {
        rel
        for rel in (p.relative_to(serving).as_posix() for p in files_under(serving))
        if rel.split("/")[0] != LLM_CACHE and rel != SERVING_MANIFEST
    }
    problems = [f"not in the serving manifest: {rel}" for rel in sorted(found - set(listed))]
    for rel, entry in sorted(listed.items()):
        path = serving / rel
        if rel not in found:
            problems.append(f"missing from the snapshot: {rel}")
        elif sha256_of(path) != entry.get("sha256"):
            problems.append(f"changed since the export: {rel}")
    if problems:
        raise PackageError(
            "the serving snapshot does not match its manifest: " + "; ".join(problems)
        )
    return manifest


def cache_summary(cache: Path) -> dict[str, Any]:
    """What llm_cache/ holds: entries by model, generation path and fallback reason."""
    models: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    fallbacks: Counter[str] = Counter()
    created: list[str] = []
    entries = sorted(cache.glob("*.json")) if cache.is_dir() else []
    for path in entries:
        entry = _read_json(path)
        if not isinstance(entry, dict) or entry.get("format") != CACHE_FORMAT:
            raise PackageError(f"{path}: not an llm_cache entry ({CACHE_FORMAT})")
        if path.stem != entry.get("input_digest"):
            raise PackageError(f"{path}: the file name is not the entry's input_digest")
        models[str(entry.get("model"))] += 1
        paths[str(entry.get("generation_path"))] += 1
        fallbacks[str(entry.get("fallback_reason") or "none")] += 1
        created.append(str(entry.get("created_at")))
    return {
        "entries": len(entries),
        "models": dict(sorted(models.items())),
        "generation_paths": dict(sorted(paths.items())),
        "fallback_reasons": dict(sorted(fallbacks.items())),
        "snapshot_instants": sorted(set(created)),
    }


def bench_files(bench: Path) -> tuple[list[Path], dict[str, Any]]:
    """The pointers latest*.json and the run files they name (GET /api/benchmarks)."""
    chosen: list[Path] = []
    pointers: dict[str, Any] = {}
    for pointer in sorted(bench.glob("latest*.json")):
        data = _read_json(pointer)
        if not isinstance(data, dict) or data.get("schema") != BENCH_LATEST:
            raise PackageError(f"{pointer}: not a benchmark pointer ({BENCH_LATEST})")
        run = bench / str(data.get("file"))
        if not run.is_file() or run.parent != bench:
            raise PackageError(f"{pointer}: names a run file that is not in {bench}: {run.name}")
        chosen += [pointer, run]
        pointers[pointer.name] = {
            "run_id": data.get("run_id"),
            "device": data.get("device"),
            "file": run.name,
        }
    return sorted(set(chosen)), pointers


def git_commit(repo: Path) -> str | None:
    value = os.environ.get("BENCH_GIT_COMMIT")
    if value:
        return value
    try:
        done = subprocess.run(  # noqa: S603 - a fixed command, no shell
            ["git", "-C", str(repo), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return None
    return done.stdout.strip() or None


def _safe_to_replace(out: Path) -> None:
    """Only an empty folder or a previous package may be replaced."""
    if not out.exists():
        return
    if not out.is_dir():
        raise PackageError(f"{out} exists and is not a folder")
    if any(out.iterdir()) and not (out / MANIFEST).is_file():
        raise PackageError(f"{out} is not empty and holds no {MANIFEST}: not replaced")
    shutil.rmtree(out)


def build(
    serving: Path,
    bench: Path,
    out: Path,
    *,
    allow_empty_cache: bool = False,
    generated_on: str | None = None,
    now: datetime | None = None,
    commit: str | None = None,
) -> dict[str, Any]:
    """Pack the demo; returns the manifest written to out/MANIFEST.json."""
    snapshot = check_serving(serving)
    check_no_truth(serving)
    cache = cache_summary(serving / LLM_CACHE)
    if cache["entries"] == 0 and not allow_empty_cache:
        raise PackageError(
            f"{serving / LLM_CACHE} holds no draft: run `make batch-generate` on the MI300X first "
            "(ALLOW_EMPTY_CACHE=1 packs a CPU rehearsal whose drafts are templates)"
        )
    runs, pointers = bench_files(bench)
    _safe_to_replace(out)
    shutil.copytree(serving, out / "serving")
    (out / "bench" / "results").mkdir(parents=True)
    for path in runs:
        shutil.copy2(path, out / "bench" / "results" / path.name)
    check_no_truth(out)
    files = {
        p.relative_to(out).as_posix(): {"sha256": sha256_of(p), "bytes": p.stat().st_size}
        for p in files_under(out)
    }
    stamp = (now or datetime.now(UTC)).replace(microsecond=0)
    manifest = {
        "format": FORMAT,
        "created_at": stamp.isoformat().replace("+00:00", "Z"),
        "git_commit": commit,
        "serving": {
            "as_of": snapshot.get("as_of"),
            "seed": snapshot.get("seed"),
            "contract_version": snapshot.get("contract_version"),
            "versions": snapshot.get("versions", {}),
            "manifest_sha256": files["serving/" + SERVING_MANIFEST]["sha256"],
            "nature": NATURE["serving"],
        },
        "llm_cache": {
            **cache,
            "generated_on": generated_on if cache["entries"] else None,
            "nature": NATURE["llm_cache"]
            if cache["entries"]
            else "empty: a CPU rehearsal, every draft of the demo is the fixed template",
        },
        "bench": {"pointers": pointers, "nature": NATURE["bench"]},
        "files": files,
    }
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (out / MANIFEST).write_text(text, encoding="utf-8")
    return manifest


def verify(package: Path) -> list[str]:
    """Why the package under `package` is not the one its MANIFEST.json describes (empty: it is)."""
    try:
        manifest = _read_json(package / MANIFEST)
    except PackageError as exc:
        return [f"{MANIFEST}: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        return [f"{package / MANIFEST} is not a demo package manifest ({FORMAT})"]
    listed = manifest.get("files")
    if not isinstance(listed, dict):
        return [f"{package / MANIFEST} lists no file"]
    found = {p.relative_to(package).as_posix() for p in files_under(package)} - {MANIFEST}
    problems = [f"not in the manifest: {rel}" for rel in sorted(found - set(listed))]
    for rel, entry in sorted(listed.items()):
        path = package / rel
        if rel not in found:
            problems.append(f"missing: {rel}")
        elif sha256_of(path) != entry.get("sha256"):
            problems.append(f"changed: {rel}")
    if not problems:
        try:
            check_serving(package / "serving")
            check_no_truth(package)
        except PackageError as exc:
            problems.append(str(exc))
    return problems


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m scripts.demo_export", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="pack serving/ (with llm_cache/) and bench/results/")
    b.add_argument("--serving", type=Path, default=Path("serving"))
    b.add_argument("--bench", type=Path, default=Path("bench/results"))
    b.add_argument("--out", type=Path, default=Path("demo-cache"))
    b.add_argument("--allow-empty-cache", action="store_true", help="a CPU rehearsal")
    b.add_argument("--generated-on", help="the hardware the batch ran on (the MI300X droplet)")
    v = sub.add_parser("verify", help="check every hash of a package")
    v.add_argument("package", type=Path, nargs="?", default=Path("demo-cache"))
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build":
        try:
            manifest = build(
                args.serving,
                args.bench,
                args.out,
                allow_empty_cache=args.allow_empty_cache,
                generated_on=args.generated_on,
                commit=git_commit(Path.cwd()),
            )
        except PackageError as exc:
            print(f"demo-export: {exc}", file=sys.stderr)
            return 1
        summary = {
            "out": str(args.out),
            "files": len(manifest["files"]),
            "as_of": manifest["serving"]["as_of"],
            "llm_cache": {
                k: manifest["llm_cache"][k] for k in ("entries", "models", "generated_on")
            },
            "bench": manifest["bench"]["pointers"],
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    problems = verify(args.package)
    for problem in problems:
        print(f"demo-verify: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"demo-verify: {args.package} matches its {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
