"""US-203 end to end on a tiny generated store: the build CLI, the golden cases, determinism,
provenance and rule 12. Every number here is synthetic (a generated test store).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import polars as pl
import pytest

from contracts.io import read_table
from contracts.parquet import CONTRACT_VERSION
from pipeline.generator import golden
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate
from pipeline.graph.build import ORIGIN, main

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
OUTPUTS = ("features/links", "features/clusters")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("graph-store")
    generate(load_calibration(EXAMPLE), out, seed=7, scale=0.02, scenario="main")
    return out


def run(
    store: Path, out: Path, *extra: str, capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    assert main(["--store", str(store), "--out", str(out), *extra]) == 0
    summary: dict[str, object] = json.loads(capsys.readouterr().out)
    return summary


def clusters_of(out: Path) -> pl.DataFrame:
    table, _ = read_table("features/clusters", out)
    frame = pl.from_arrow(table)
    assert isinstance(frame, pl.DataFrame)
    return frame


def test_the_cli_writes_both_contract_files(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = run(store, tmp_path, capsys=capsys)
    assert summary["as_of"] == "2026-10-01T00:00:00+00:00"  # the day after the store period
    assert summary["nature"] == "synthetic"
    for name in OUTPUTS:
        table, provenance = read_table(name, tmp_path)
        assert table.num_rows > 0
        assert provenance.nature == "synthetic"
        assert provenance.origin.startswith(ORIGIN)
        assert "Navigli Fashion generator" in provenance.origin  # the store it read
        assert "as of 2026-10-01T00:00:00+00:00" in provenance.origin
        assert str(provenance.period_end) == "2026-09-30"
        assert table.schema.metadata[b"returnring.contract_version"] == CONTRACT_VERSION.encode()
    _, store_provenance = read_table("store/customers", store)
    assert provenance.period_start == store_provenance.period_start


def test_golden_groups_are_clusters_of_exactly_their_accounts(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Marco's ring, the Rossi family and the Rossi family with a shared card (US-104): each is
    one cluster, with no other account. A cluster is not a judgement (spec 8.3)."""
    run(store, tmp_path, capsys=capsys)
    clusters = clusters_of(tmp_path)
    for accounts in (golden.MARCO_ACCOUNTS, golden.ROSSI_ACCOUNTS, golden.ROSSI_CARD_ACCOUNTS):
        mine = clusters.filter(pl.col("customer_id").is_in(accounts))
        ids = mine["cluster_id"].unique().to_list()
        assert len(ids) == 1 and ids[0] is not None, accounts
        assert sorted(clusters.filter(pl.col("cluster_id") == ids[0])["customer_id"]) == sorted(
            accounts
        )
    rossi = clusters.filter(pl.col("customer_id") == "C-ROSSI-CARD-1").row(0, named=True)
    marco = clusters.filter(pl.col("customer_id") == "C-MARCO-1").row(0, named=True)
    # What tells them apart: old accounts, no welcome coupons, distinct cards, normal returns.
    assert rossi["creation_spread_days"] > 365 > 30 > marco["creation_spread_days"]
    assert rossi["welcome_coupons_per_account"] == 0.0 < marco["welcome_coupons_per_account"]
    assert rossi["cluster_return_rate"] < 0.5 < marco["cluster_return_rate"]
    assert clusters.filter(pl.col("customer_id") == golden.GIULIA)["cluster_id"].item() is None


def test_cluster_ids_are_opaque(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(store, tmp_path, capsys=capsys)
    ids = clusters_of(tmp_path)["cluster_id"].drop_nulls().unique().to_list()
    assert ids
    assert all(re.fullmatch(r"CL-[0-9a-f]{12}", i) for i in ids)
    truth_ids = (golden.MARCO_RING, golden.ROSSI_FAMILY, golden.ROSSI_CARD_FAMILY)
    assert not [i for i in ids if any(t in i for t in truth_ids)]


def test_the_build_is_deterministic(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(store, tmp_path / "a", capsys=capsys)
    run(store, tmp_path / "b", capsys=capsys)
    for name in OUTPUTS:
        a = (tmp_path / "a" / f"{name}.parquet").read_bytes()
        b = (tmp_path / "b" / f"{name}.parquet").read_bytes()
        assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest(), name


def test_an_earlier_decision_time_sees_less(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Marco's accounts open from 2 March 2026: on 1 March they do not exist (rule 7)."""
    summary = run(store, tmp_path, "--as-of", "2026-03-01T00:00:00Z", capsys=capsys)
    assert summary["as_of"] == "2026-03-01T00:00:00+00:00"
    clusters = clusters_of(tmp_path)
    assert not clusters.filter(pl.col("customer_id").is_in(golden.MARCO_ACCOUNTS)).height
    links, provenance = read_table("features/links", tmp_path)
    assert str(provenance.period_end) == "2026-03-01"
    assert (
        max(links.column("last_shared_at").to_pylist()).isoformat() <= "2026-03-01T00:00:00+00:00"
    )


def test_disputes_from_a_file(
    store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    disputes = tmp_path / "disputed.json"
    disputes.write_text(json.dumps([["C-ROSSI-2", "C-ROSSI-1"], ["C-ROSSI-1", "C-ROSSI-3"]]))
    summary = run(store, tmp_path, "--disputed", str(disputes), capsys=capsys)
    assert summary["disputed_links"] == 2
    clusters = clusters_of(tmp_path)
    alone = clusters.filter(pl.col("customer_id") == "C-ROSSI-1").row(0, named=True)
    assert alone["cluster_id"] is None
    pair = clusters.filter(pl.col("customer_id").is_in(["C-ROSSI-2", "C-ROSSI-3"]))
    assert pair["cluster_size"].to_list() == [2, 2]


def test_a_malformed_dispute_file_is_refused(store: Path, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"C-ROSSI-1": "C-ROSSI-2"}))
    with pytest.raises(ValueError):
        main(["--store", str(store), "--out", str(tmp_path), "--disputed", str(bad)])


def test_a_naive_as_of_is_a_usage_error(store: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--store", str(store), "--as-of", "2026-03-01T00:00:00"])
    assert exc.value.code == 2


def _strings(path: Path) -> list[str]:
    """String literals of a module, docstrings excluded."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_rule12_only_the_evaluation_reads_the_ground_truth() -> None:
    """The graph build and the dispute run in services (US-310 in the API): they never name
    truth/. Only pipeline/graph/evaluate.py, an offline job, reads it."""
    modules = sorted((ROOT / "pipeline" / "graph").glob("*.py"))
    assert len(modules) >= 7
    for path in modules:
        found = [s for s in _strings(path) if "truth" in s.lower()]
        if path.name == "evaluate.py":
            assert found, "the evaluation reads truth/labels"
        else:
            assert not found, (path, found)
        if path.name != "evaluate.py":
            source = path.read_text(encoding="utf-8")
            assert "pipeline.generator" not in source, path  # no generator ground truth either
