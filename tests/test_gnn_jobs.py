"""US-202 and A4 end to end on tiny data, on the CPU: `python -m ml.gnn asos` on the seeded ASOS
replica of tests/propensity_fixtures.py, `python -m ml.gnn embed` on a hand-built store, and
`python -m ml.risk` running A4 on a small generated store. Needs PyTorch (marker torch, skipped
without it). Every number here is a made-up fixture (nature: synthetic); the real runs are in
docs/results/us-202-graphsage.md.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from contracts.io import read_table  # noqa: E402
from contracts.parquet import EMBEDDING_COLUMNS  # noqa: E402
from ml.gnn.__main__ import main as gnn_main  # noqa: E402
from ml.gnn.report import load_inputs, render  # noqa: E402
from ml.propensity.features import FeatureError  # noqa: E402
from ml.propensity.run import RunConfig  # noqa: E402
from ml.propensity.run import run as propensity_run  # noqa: E402
from ml.risk.embeddings import FileEmbeddingSource  # noqa: E402
from tests.graph_fixtures import Line, MiniStore, Order, day  # noqa: E402
from tests.propensity_fixtures import random_raw, write_raw  # noqa: E402

pytestmark = pytest.mark.torch
TIMING_KEYS = (
    "training",
    "inference",
    "timings",
    "data_loading_seconds",
    "argv",
    "environment",
    "artifacts",
)


@pytest.fixture(scope="module")
def raw_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("gnn-asos")
    events, customers, variants = random_raw(seed=5, n_customers=200, n_variants=80)
    return write_raw(root, events, customers, variants)


def asos(raw_dir: Path, out: Path, *extra: str) -> dict[str, Any]:
    argv = ["asos", "--raw", str(raw_dir), "--out", str(out), "--max-epochs", "3"]
    assert gnn_main([*argv, "--backend", "torch", "--seed", "42", "--threads", "1", *extra]) == 0
    doc: dict[str, Any] = json.loads((out / "eval/gnn/us-202-asos-cpu.json").read_text())
    return doc


@pytest.fixture(scope="module")
def run(raw_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    out = tmp_path_factory.mktemp("gnn-out")
    return out, asos(raw_dir, out)


def test_every_model_and_number_is_labelled(run: tuple[Path, dict[str, Any]]) -> None:
    out, doc = run
    assert doc["config"]["protocols"] == ["B", "A"]  # the decision-time protocol first
    assert set(doc["protocols"]) == {"A", "B"}
    assert doc["scope"].endswith("never evidence about rings (spec US-202)")
    for protocol, entry in doc["protocols"].items():
        assert set(entry["models"]) == {"graphsage", "mlp", "xgboost"}
        for model in entry["models"].values():
            assert model["protocol"] == protocol
            assert model["labels"]["nature"] == "synthetic"  # the fixture's nature, not "real"
            assert model["labels"]["period"] == "2021-10-01..2021-11-30"
            assert set(model["test"]) == {
                "all",
                "fully_featured",
                "decision_time",
                "decision_time_fully_featured",
            }
            m = model["test"]["all"]
            assert 0 < m["cross_entropy"] < 2 and 0 <= m["ece"] <= 1 and 0 <= m["f1"] <= 1
    b = doc["protocols"]["B"]["models"]["graphsage"]
    assert b["trained_on"] == "decision_time"
    assert doc["protocols"]["A"]["models"]["graphsage"]["trained_on"] == "all"
    assert doc["mod02"]["measured"] and doc["paper"]["models"]
    assert (out / "gnn/models/asos_B_graphsage_cpu.pt").exists()


def test_times_are_taken_apart(run: tuple[Path, dict[str, Any]]) -> None:
    _, doc = run
    cold = doc["timings"]["cold_start"]
    assert {"import_torch_seconds", "device_init_and_first_kernels_seconds"} <= set(cold)
    assert doc["timings"]["read_raw_seconds"] >= 0
    for entry in doc["protocols"].values():
        assert entry["data_loading_seconds"] > 0
        g = entry["models"]["graphsage"]
        assert g["training"]["train_seconds"] > 0 and g["training"]["epoch_seconds_median"] > 0
        assert g["inference"]["seconds"] > 0 and g["inference"]["events"] > 0
    assert doc["environment"]["backend"] == "torch"
    assert doc["environment"]["deterministic_algorithms"] is True


def test_xgboost_is_ml_propensitys_own_model_on_the_same_rows(
    raw_dir: Path, run: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    """Same splits, same population, same validation: the XGBoost rows equal US-201's."""
    _, doc = run
    us201 = propensity_run(raw_dir, tmp_path, None, RunConfig(seed=42, threads=1))
    for key in ("A", "B"):
        mine = doc["protocols"][key]["models"]["xgboost"]["test"]
        assert mine == us201["models"][key]["test"], key


def _strip(doc: dict[str, Any]) -> Any:
    if isinstance(doc, dict):
        return {k: _strip(v) for k, v in doc.items() if k not in TIMING_KEYS}
    if isinstance(doc, list):
        return [_strip(v) for v in doc]
    return doc


def test_the_cpu_run_is_repeatable_apart_from_timings(
    raw_dir: Path, run: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    _, doc = run
    again = asos(raw_dir, tmp_path)
    assert _strip(again) == _strip(doc)


def test_a_customer_sample_runs_every_model_on_the_same_rows(raw_dir: Path, tmp_path: Path) -> None:
    doc = asos(raw_dir, tmp_path, "--sample", "0.5", "--protocols", "B", "--no-mlp")
    entry = doc["protocols"]["B"]
    assert doc["config"]["sample"] == 0.5 and set(entry["models"]) == {"graphsage", "xgboost"}
    n = {m["test"]["all"]["n"] for m in entry["models"].values()}
    assert len(n) == 1 and n.pop() < 2500


def test_the_job_refuses_a_folder_that_is_not_raw(tmp_path: Path) -> None:
    with pytest.raises(FeatureError):
        gnn_main(["asos", "--raw", str(tmp_path), "--out", str(tmp_path), "--backend", "torch"])


def test_a_gpu_run_without_a_gpu_exits_2(raw_dir: Path, tmp_path: Path) -> None:
    if torch.cuda.is_available():
        pytest.skip("a GPU is visible here")
    argv = ["asos", "--raw", str(raw_dir), "--out", str(tmp_path), "--device", "gpu"]
    assert gnn_main(argv) == 2


def test_the_report_renders_the_run(run: tuple[Path, dict[str, Any]], tmp_path: Path) -> None:
    out, _ = run
    text = render(load_inputs(out))
    assert "### Protocol B: Decision-time protocol" in text
    assert "B, GraphSAGE" in text and "B, XGBoost" in text and "Paper: GNN" in text
    path = tmp_path / "report.md"
    assert gnn_main(["report", "--artifacts", str(out), "--doc", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == text


# ---- A4 embeddings ------------------------------------------------------------------------------

T_FIT = datetime(2026, 1, 21, tzinfo=UTC)
T_LATER = datetime(2026, 2, 10, tzinfo=UTC)


def ring_store() -> MiniStore:
    """Three rings of four accounts sharing a card and a device, twelve unlinked accounts and a
    pickup point shared by eight of them; two accounts open after the fit time."""
    store = MiniStore()
    for r in range(3):
        for i in range(4):
            store.account(
                f"R{r}-{i}",
                day(r + i),
                card=f"ring-card-{r}",
                device=f"ring-dev-{r}",
                email=f"ring-{r}-{i}",
            )
    for i in range(12):
        store.account(
            f"S{i:02d}",
            day(i),
            card=f"card-{i}",
            email=f"mail-{i}",
            device=f"dev-{i}",
            address="pickup" if i < 8 else f"home-{i}",
        )
    store.account("L1", day(25), card="ring-card-0", email="late-1")
    store.account("L2", day(26), card="late-card", email="late-2")
    store.orders.append(
        Order("O1", "R0-0", day(3), day(5), (Line("L-1", returned="size", returned_at=day(8)),))
    )
    return store


def embed(store: Path, out: Path, art: Path, *extra: str) -> dict[str, Any]:
    argv = ["embed", "--store", str(store), "--out", str(out), "--models", str(art)]
    assert gnn_main([*argv, "--backend", "torch", "--max-epochs", "30", *extra]) == 0
    name = "ood" if store.name == "ood" else "main"
    doc: dict[str, Any] = json.loads((art / f"eval/gnn/us-202-embeddings-{name}.json").read_text())
    return doc


@pytest.fixture(scope="module")
def embedded(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = ring_store().write(tmp_path_factory.mktemp("ring-store"))
    art = tmp_path_factory.mktemp("ring-art")
    instants = ["--as-of", T_FIT.isoformat(), "--as-of", T_LATER.isoformat()]
    doc = embed(root, root, art, "--fit-as-of", T_FIT.isoformat(), *instants)
    return root, art, doc


def test_embeddings_are_written_through_the_contract(
    embedded: tuple[Path, Path, dict[str, Any]],
) -> None:
    root, _, doc = embedded
    table, provenance = read_table("features/customer_embeddings", root)
    frame = table.to_pandas()
    at_fit = frame.loc[frame["as_of"] == T_FIT]
    later = frame.loc[frame["as_of"] == T_LATER]
    assert len(at_fit) == 24 and len(later) == 26  # L1 and L2 exist only later (rule 7)
    assert provenance.nature == "synthetic" and "link prediction" in provenance.origin
    _, store = read_table("store/identifiers", root)
    source = FileEmbeddingSource(root, store)  # derived from this store (review F22)
    assert source.embeddings_at(T_LATER).shape == (26, 1 + len(EMBEDDING_COLUMNS))
    assert doc["encoder"]["trained_here"] and doc["encoder"]["training"]["epochs_run"] >= 1
    assert {"data_loading_seconds", "training_seconds", "inference_seconds"} <= set(doc["timings"])


def test_ring_accounts_end_up_closer_than_unlinked_ones(
    embedded: tuple[Path, Path, dict[str, Any]],
) -> None:
    """What link prediction should learn without any label: accounts that share a card and a
    device are closer to each other than to unlinked accounts (cosine similarity)."""
    root, _, _ = embedded
    frame = read_table("features/customer_embeddings", root)[0].to_pandas()
    rows = frame.loc[frame["as_of"] == T_FIT].set_index("customer_id")[list(EMBEDDING_COLUMNS)]
    z = rows.to_numpy()
    z = z / np.linalg.norm(z, axis=1, keepdims=True)
    sim = z @ z.T
    ids = list(rows.index)
    ring = [ids.index(f"R0-{i}") for i in range(4)]
    alone = [ids.index(f"S{i:02d}") for i in range(8, 12)]
    within = np.mean([sim[a, b] for a in ring for b in ring if a < b])
    across = np.mean([sim[a, b] for a in ring for b in alone])
    assert within > across


def test_the_encoder_is_applied_frozen_to_another_store_never_before_its_fit(
    embedded: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    root, art, doc = embedded
    other = tmp_path / "ood"
    shutil.copytree(root / "store", other / "store")
    encoder = str(art / "gnn/models/customer_encoder")
    applied = embed(other, other, art, "--encoder", encoder, "--as-of", T_LATER.isoformat())
    assert not applied["encoder"]["trained_here"]
    assert applied["encoder"]["version"] == doc["encoder"]["version"]
    first = read_table("features/customer_embeddings", root)[0].to_pandas()
    second = read_table("features/customer_embeddings", other)[0].to_pandas()
    a = first.loc[first["as_of"] == T_LATER].reset_index(drop=True)
    b = second.reset_index(drop=True)
    assert np.allclose(a[list(EMBEDDING_COLUMNS)], b[list(EMBEDDING_COLUMNS)], atol=1e-6)
    with pytest.raises(ValueError, match="rule 7"):
        embed(other, other, art, "--encoder", encoder, "--as-of", day(5).isoformat())


def test_the_embedding_job_is_repeatable(
    embedded: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    root, _, _ = embedded
    copy = tmp_path / "store"
    shutil.copytree(root / "store", copy / "store")
    embed(
        copy, copy, tmp_path / "art", "--fit-as-of", T_FIT.isoformat(), "--as-of", T_FIT.isoformat()
    )
    a = read_table("features/customer_embeddings", root)[0].to_pandas()
    b = read_table("features/customer_embeddings", copy)[0].to_pandas()
    a = a.loc[a["as_of"] == T_FIT].reset_index(drop=True)
    assert np.array_equal(
        a[list(EMBEDDING_COLUMNS)].to_numpy(), b[list(EMBEDDING_COLUMNS)].to_numpy()
    )
