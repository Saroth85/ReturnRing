"""The cached demo package (scripts/demo_export.py; US-901, spec 11 step 6).

The package is built from a serving snapshot (checked against its own manifest), its llm_cache/
of MI300X drafts and the benchmark pointers with their run files, with a manifest of every hash;
`verify` refuses any change. The script runs on the droplet host and the demo server, where only
the standard library is installed: an import test keeps it that way.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.serving.manifest import verify as verify_serving
from pipeline.serving.manifest import write_manifest
from scripts import demo_export
from scripts.demo_export import FORMAT, MANIFEST, PackageError, build, main, verify

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)


def _cache_entry(digest: str, model: str = "google/gemma-4-31B-it", path: str = "model") -> dict:
    return {
        "format": "returnring.llm_cache.v1",
        "input_digest": digest,
        "case_id": f"RET-{digest[:6]}",
        "model": model,
        "generation_path": path,
        "fallback_reason": None,
        "model_calls": 1,
        "validator_codes": [],
        "output": {},
        "latency_ms": 4200,
        "completion_tokens": 310,
        "prompt_version": "prompts-abc",
        "created_at": "2026-10-01T00:00:00+00:00",
    }


def make_serving(root: Path, *, entries: int = 2) -> Path:
    """A serving snapshot written by pipeline.serving's own manifest writer, with a cache."""
    serving = root / "serving"
    (serving / "store").mkdir(parents=True)
    (serving / "store" / "returns.parquet").write_bytes(b"returns-table")
    (serving / "models" / "risk").mkdir(parents=True)
    (serving / "models" / "risk" / "manifest.json").write_text('{"training": 1}')  # nested name
    write_manifest(
        serving,
        {
            "as_of": "2026-10-01T00:00:00+00:00",
            "seed": 42,
            "contract_version": "2.0.0",
            "tables": {},
            "models": {},
        },
    )
    cache = serving / "llm_cache"
    cache.mkdir()
    for n in range(entries):
        digest = hashlib.sha256(str(n).encode()).hexdigest()
        (cache / f"{digest}.json").write_text(json.dumps(_cache_entry(digest)))
    assert verify_serving(serving) == []
    return serving


def make_bench(root: Path) -> Path:
    bench = root / "bench" / "results"
    bench.mkdir(parents=True)
    (bench / "20261016T100000Z-gpu.json").write_text('{"run": "gpu"}')
    (bench / "20261014T002158Z-cpu.json").write_text('{"run": "cpu"}')
    (bench / "older-run.json").write_text('{"run": "not pointed at"}')
    for name, run, device in (
        ("latest.json", "20261016T100000Z-gpu", "gpu"),
        ("latest-gpu.json", "20261016T100000Z-gpu", "gpu"),
        ("latest-cpu.json", "20261014T002158Z-cpu", "cpu"),
    ):
        pointer = {
            "schema": "returnring.bench.latest/1",
            "run_id": run,
            "file": f"{run}.json",
            "device": device,
            "created_at": "2026-10-16T10:00:00Z",
        }
        (bench / name).write_text(json.dumps(pointer))
    return bench


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    return make_serving(tmp_path), make_bench(tmp_path), tmp_path / "demo-cache"


def test_build_packs_the_snapshot_the_cache_and_the_pointed_runs(
    inputs: tuple[Path, Path, Path],
) -> None:
    serving, bench, out = inputs
    manifest = build(
        serving, bench, out, generated_on="AMD Instinct MI300X", now=NOW, commit="abc123"
    )
    assert verify(out) == []
    assert manifest["format"] == FORMAT and manifest["created_at"] == "2026-10-16T18:00:00Z"
    assert manifest["git_commit"] == "abc123"
    files = set(manifest["files"])
    assert "serving/manifest.json" in files and "serving/store/returns.parquet" in files
    assert "serving/models/risk/manifest.json" in files
    assert sum(f.startswith("serving/llm_cache/") for f in files) == 2
    runs = {f for f in files if f.startswith("bench/results/")}
    assert runs == {
        "bench/results/latest.json",
        "bench/results/latest-gpu.json",
        "bench/results/latest-cpu.json",
        "bench/results/20261016T100000Z-gpu.json",
        "bench/results/20261014T002158Z-cpu.json",
    }  # older-run.json is named by no pointer
    assert manifest["serving"]["as_of"] == "2026-10-01T00:00:00+00:00"
    assert manifest["serving"]["seed"] == 42
    cache = manifest["llm_cache"]
    assert cache["entries"] == 2 and cache["models"] == {"google/gemma-4-31B-it": 2}
    assert cache["generation_paths"] == {"model": 2}
    assert cache["generated_on"] == "AMD Instinct MI300X" and "vLLM" in cache["nature"]
    assert "synthetic" in manifest["serving"]["nature"]
    assert manifest["bench"]["pointers"]["latest.json"]["device"] == "gpu"
    written = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
    assert written == manifest


def test_verify_refuses_a_changed_missing_or_added_file(inputs: tuple[Path, Path, Path]) -> None:
    serving, bench, out = inputs
    build(serving, bench, out, now=NOW)
    (out / "serving" / "store" / "returns.parquet").write_bytes(b"tampered")
    assert verify(out) == ["changed: serving/store/returns.parquet"]
    build(serving, bench, out, now=NOW)
    (out / "bench" / "results" / "latest-cpu.json").unlink()
    assert verify(out) == ["missing: bench/results/latest-cpu.json"]
    build(serving, bench, out, now=NOW)
    (out / "serving" / "llm_cache" / "extra.json").write_text("{}")
    assert verify(out) == ["not in the manifest: serving/llm_cache/extra.json"]
    assert main(["verify", str(out)]) == 1
    build(serving, bench, out, now=NOW)
    assert main(["verify", str(out)]) == 0


def test_verify_of_something_that_is_not_a_package(tmp_path: Path) -> None:
    assert verify(tmp_path)[0].startswith(MANIFEST)
    (tmp_path / MANIFEST).write_text('{"format": "other"}')
    assert "not a demo package manifest" in verify(tmp_path)[0]


def test_build_refuses_a_snapshot_that_differs_from_its_manifest(
    inputs: tuple[Path, Path, Path],
) -> None:
    serving, bench, out = inputs
    (serving / "store" / "returns.parquet").write_bytes(b"changed after the export")
    with pytest.raises(PackageError, match="changed since the export: store/returns.parquet"):
        build(serving, bench, out, now=NOW)
    assert not out.exists()


def test_build_refuses_an_empty_cache_unless_it_is_a_rehearsal(tmp_path: Path) -> None:
    serving, bench = make_serving(tmp_path, entries=0), make_bench(tmp_path)
    with pytest.raises(PackageError, match="make batch-generate"):
        build(serving, bench, tmp_path / "out", now=NOW)
    manifest = build(serving, bench, tmp_path / "out", now=NOW, allow_empty_cache=True)
    assert manifest["llm_cache"]["entries"] == 0
    assert manifest["llm_cache"]["generated_on"] is None
    assert "template" in manifest["llm_cache"]["nature"]


def test_build_refuses_a_ground_truth_path(inputs: tuple[Path, Path, Path]) -> None:
    """Rule 12: the snapshot has no truth path (ADR-16); the package checks it again."""
    serving, bench, out = inputs
    truth = serving / "llm_cache" / "truth"
    truth.mkdir()
    (truth / "x.json").write_text("{}")
    with pytest.raises(PackageError, match="rule 12"):
        build(serving, bench, out, now=NOW)


def test_build_refuses_a_broken_cache_entry_or_pointer(inputs: tuple[Path, Path, Path]) -> None:
    serving, bench, out = inputs
    wrong = serving / "llm_cache" / ("0" * 64 + ".json")
    wrong.write_text(json.dumps(_cache_entry("1" * 64)))
    with pytest.raises(PackageError, match="input_digest"):
        build(serving, bench, out, now=NOW)
    wrong.unlink()
    (bench / "latest.json").write_text(
        json.dumps({"schema": "returnring.bench.latest/1", "file": "../outside.json"})
    )
    with pytest.raises(PackageError, match="names a run file"):
        build(serving, bench, out, now=NOW)


def test_build_replaces_only_a_previous_package(inputs: tuple[Path, Path, Path]) -> None:
    serving, bench, out = inputs
    out.mkdir()
    (out / "notes.txt").write_text("someone else's folder")
    with pytest.raises(PackageError, match="not replaced"):
        build(serving, bench, out, now=NOW)
    assert (out / "notes.txt").exists()
    (out / "notes.txt").unlink()
    build(serving, bench, out, now=NOW)
    build(serving, bench, out, now=NOW)  # a previous package is replaced
    assert verify(out) == []


def test_main_build_prints_a_summary(
    inputs: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    serving, bench, out = inputs
    args = ["build", "--serving", str(serving), "--bench", str(bench), "--out", str(out)]
    assert main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["llm_cache"]["entries"] == 2 and summary["as_of"].startswith("2026-10-01")
    (serving / "store" / "returns.parquet").write_bytes(b"x")
    assert main(args) == 1
    assert "does not match its manifest" in capsys.readouterr().err


def test_the_script_uses_the_standard_library_only() -> None:
    """make demo-export and make demo-verify run with the host python3 (no project packages)."""
    tree = ast.parse(Path(demo_export.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top == "__future__" or top in sys.stdlib_module_names, name
    assert (ROOT / "scripts" / "__init__.py").read_text(encoding="utf-8").strip() == ""
