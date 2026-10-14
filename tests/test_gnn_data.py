"""US-202 and A4 without PyTorch: the ASOS graph and its features per protocol (ADR-10), the
customer-identifier graph of a store snapshot, the standardiser, the features/customer_embeddings
contract (ADR-13 amendment 6), the EmbeddingSource of ml/risk, the report, and rule 12.

Every table here is a tiny made-up fixture (nature: synthetic). The model and the jobs, which need
PyTorch, are tested in tests/test_gnn_model.py and tests/test_gnn_jobs.py.
"""

from __future__ import annotations

import ast
import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
import pyarrow as pa
import pytest

from contracts.checks import checks_for
from contracts.io import ContractError, Provenance, read_table, validate_table, write_table
from contracts.parquet import (
    EMBEDDING_COLUMNS,
    EMBEDDING_DIM,
    SCHEMAS,
    forbidden_features,
    label_or_key_features,
)
from ml.gnn.asos_data import (
    BOUGHT_BY,
    BUYS,
    CUSTOMER,
    VARIANT,
    customer_sample,
    feature_spec,
    split_graph,
    standardizer,
)
from ml.gnn.graph import HeteroGraph, Relation, bipartite, index_keys
from ml.gnn.identity_graph import (
    CUSTOMER_NODE_FEATURES,
    IDENTIFIER,
    IDENTIFIER_NODE_FEATURES,
    identity_graph,
    months_before,
)
from ml.gnn.report import load_inputs, render
from ml.gnn.scaling import Standardizer
from ml.propensity.features import (
    FeatureError,
    Vocabulary,
    feature_names,
    join_events,
    population_masks,
)
from ml.risk.embeddings import FileEmbeddingSource, embedding_source
from ml.risk.features import EMBEDDING_PREFIX, check_feature_names
from ml.risk.run import months_before as risk_months_before
from pipeline.graph.snapshot import load_snapshot
from pipeline.ingest.asos_profile import load_raw
from tests.graph_fixtures import Line, MiniStore, Order, day
from tests.propensity_fixtures import random_raw, write_raw

ROOT = Path(__file__).resolve().parents[1]


# ---- ASOS graph ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw(tmp_path_factory: pytest.TempPathFactory) -> Any:
    root = tmp_path_factory.mktemp("asos-gnn")
    write_raw(root, *random_raw(seed=11, n_customers=120, n_variants=60, events_per_split=600))
    return load_raw(root)


@pytest.fixture(scope="module")
def vocabulary(raw: Any) -> Vocabulary:
    return Vocabulary.from_variants(raw.variants.filter(pl.col("split") == "train"))


def test_protocol_b_nodes_carry_no_return_count_rate_or_share(vocabulary: Vocabulary) -> None:
    spec = feature_spec("B", vocabulary)
    assert spec.customer == ("customer_sales_loo", "has_customer_features", "customer_degree")
    assert spec.variant[:3] == ("variant_sales_loo", "avg_price_gbp", "avg_discount")
    for name in (*spec.customer, *spec.variant):
        assert "return" not in name and "rate" not in name and "share" not in name, name
    # The event's own leave-one-out counts go to the edge head: the 30 protocol B features.
    assert spec.edge == tuple(feature_names("B", vocabulary))
    assert "customer_returns_loo" in spec.edge and "variant_returns_loo" in spec.edge


def test_protocol_a_is_the_papers_node_features_without_forbidden_ones(
    vocabulary: Vocabulary,
) -> None:
    spec = feature_spec("A", vocabulary)
    assert "customer_return_rate" in spec.customer and "variant_return_rate" in spec.variant
    assert spec.edge == ()
    names = (*spec.customer, *spec.variant)
    assert not forbidden_features(names) and not label_or_key_features(names)
    with pytest.raises(FeatureError):
        feature_spec("C", vocabulary)  # type: ignore[arg-type]


def test_split_graph_indexes_nodes_and_keeps_event_order(raw: Any, vocabulary: Vocabulary) -> None:
    spec = feature_spec("B", vocabulary)
    g = split_graph(raw.events, raw.customers, raw.variants, "train", spec, vocabulary)
    events = raw.events.filter(pl.col("split") == "train")
    assert g.n_events == events.height
    assert np.array_equal(g.y, events["is_returned"].to_numpy().astype(np.int8))
    # Node ids are dense, one per distinct hash, and the two relations are each other's reverse.
    _, c_idx = index_keys(events["customer_hash"].to_numpy())
    assert np.array_equal(g.customer, c_idx)
    buys, bought = g.graph.relations
    assert (buys.name, bought.name) == (BUYS, BOUGHT_BY)
    assert np.array_equal(buys.src, bought.dst) and np.array_equal(buys.dst, bought.src)
    assert g.x_customer.shape == (g.graph.num_nodes[CUSTOMER], len(spec.customer))
    assert g.x_variant.shape == (g.graph.num_nodes[VARIANT], len(spec.variant))
    assert g.x_edge.shape == (g.n_events, len(spec.edge))
    # Populations are those of ml.propensity, on the same rows.
    joined = join_events(events, raw.customers, raw.variants)
    for name, mask in population_masks(joined).items():
        assert np.array_equal(g.masks[name], mask), name


def test_node_features_hold_leave_one_out_sales_and_degree(
    raw: Any, vocabulary: Vocabulary
) -> None:
    spec = feature_spec("B", vocabulary)
    g = split_graph(raw.events, raw.customers, raw.variants, "train", spec, vocabulary)
    events = raw.events.filter(pl.col("split") == "train")
    customers = raw.customers.filter(pl.col("split") == "train")
    keys, _ = index_keys(events["customer_hash"].to_numpy())
    sales = dict(
        zip(customers["customer_hash"].to_list(), customers["sales"].to_list(), strict=True)
    )
    degree = np.bincount(g.customer, minlength=keys.size)
    for i, h in enumerate(keys.tolist()):
        expected = max(sales[h] - 1, 0) if h in sales else np.nan
        got = g.x_customer[i, 0]
        assert (np.isnan(got) and np.isnan(expected)) or got == expected, h
        assert g.x_customer[i, 1] == (1.0 if h in sales else 0.0)
        assert g.x_customer[i, 2] == degree[i]


def test_protocol_b_message_passing_inputs_do_not_depend_on_labels(
    raw: Any, vocabulary: Vocabulary
) -> None:
    """Flip every event label with the node tables unchanged: the node features of protocol B
    (all that travels along the graph) stay identical; only the edge head's returns change."""
    spec = feature_spec("B", vocabulary)
    flipped = raw.events.with_columns(~pl.col("is_returned"))
    a = split_graph(raw.events, raw.customers, raw.variants, "train", spec, vocabulary)
    b = split_graph(flipped, raw.customers, raw.variants, "train", spec, vocabulary)
    assert np.array_equal(a.x_customer, b.x_customer, equal_nan=True)
    assert np.array_equal(a.x_variant, b.x_variant, equal_nan=True)
    changed = {
        spec.edge[j]
        for j in range(len(spec.edge))
        if not np.array_equal(a.x_edge[:, j], b.x_edge[:, j], equal_nan=True)
    }
    assert changed <= {"customer_returns_loo", "variant_returns_loo"} and changed


def test_customer_sample_is_seeded_and_per_customer(raw: Any, vocabulary: Vocabulary) -> None:
    hashes = np.arange(-50_000, 50_000, dtype=np.int64)
    a = customer_sample(hashes, 0.25, 42)
    assert np.array_equal(a, customer_sample(hashes, 0.25, 42))
    assert not np.array_equal(a, customer_sample(hashes, 0.25, 7))
    assert abs(a.mean() - 0.25) < 0.01
    assert customer_sample(hashes, 1.0, 42).all()
    with pytest.raises(ValueError):
        customer_sample(hashes, 0.0, 42)
    # A sampled split keeps every event of a kept customer.
    events = raw.events.filter(pl.col("split") == "train")
    keep = customer_sample(events["customer_hash"].to_numpy(), 0.5, 42)
    spec = feature_spec("B", vocabulary)
    g = split_graph(raw.events, raw.customers, raw.variants, "train", spec, vocabulary, keep)
    assert g.n_events == int(keep.sum())
    kept = set(events.filter(pl.Series(keep))["customer_hash"].to_list())
    dropped = set(events.filter(pl.Series(~keep))["customer_hash"].to_list())
    assert not kept & dropped


def test_standardizer_log1p_mean_std_and_missing_values() -> None:
    x = np.array([[0.0, 1.0], [3.0, np.nan], [7.0, 5.0]], dtype=np.float32)
    s = Standardizer.fit(x, ("n_sales", "price"), lambda n: n == "n_sales")
    z = s.transform(x)
    logged = np.log1p([0.0, 3.0, 7.0])
    assert np.allclose(z[:, 0], (logged - logged.mean()) / logged.std(), atol=1e-6)
    assert z[1, 1] == 0.0  # a missing value becomes the mean
    assert np.allclose(z[[0, 2], 1], [-1.0, 1.0], atol=1e-6)
    assert Standardizer.from_dict(s.as_dict()) == s
    flat = Standardizer.fit(np.ones((3, 1), dtype=np.float32), ("c",), lambda _n: False)
    assert flat.std == (1.0,) and np.all(flat.transform(np.ones((2, 1))) == 0.0)
    with pytest.raises(ValueError):
        s.transform(np.zeros((2, 3)))
    edge = standardizer(np.array([[np.nan], [np.nan]], dtype=np.float32), ("customer_sales_loo",))
    assert edge.mean == (0.0,) and edge.log == (True,)


def test_hetero_graph_checks_indices_and_hides_edges() -> None:
    g = bipartite("a", "b", "ab", "ba", np.array([0, 1, 1]), np.array([0, 0, 1]), 2, 2)
    assert g.num_edges() == 6
    hidden = g.without_edges(("ab", "ba"), np.array([True, False, True]))
    assert [r.src.tolist() for r in hidden.relations] == [[0, 1], [0, 1]]
    with pytest.raises(ValueError):
        HeteroGraph({"a": 1}, (Relation("a", "aa", "a", np.array([0]), np.array([1])),))
    with pytest.raises(ValueError):
        HeteroGraph({"a": 2}, (Relation("a", "x", "a", np.array([0]), np.array([1])),) * 2)


# ---- customer-identifier graph ------------------------------------------------------------------


def mini_store() -> MiniStore:
    store = MiniStore()
    store.account("A", day(0), card="card-1", email="mail-a", device="dev-1")
    store.account("B", day(1), card="card-1", email="mail-b")
    store.account("C", day(2), address="pickup")
    store.account("D", day(3), address="pickup")
    store.account("E", day(30), card="card-9")  # created after the decision time below
    store.orders.append(
        Order(
            "O1",
            "A",
            day(3),
            day(5),
            (
                Line("L1", returned="size", returned_at=day(7)),
                Line("L2"),
                Line("L3", returned="defective", returned_at=day(8)),
            ),
        )
    )
    store.coupons.append(("W-A", "A", "welcome", day(2), "O1", day(3)))
    return store


def test_identity_graph_from_the_store_known_at_the_decision_time(tmp_path: Path) -> None:
    root = mini_store().write(tmp_path)
    g = identity_graph(load_snapshot(root, day(10)))
    assert g.customer_ids == ("A", "B", "C", "D")  # E does not exist yet (rule 7)
    assert g.graph.num_nodes[IDENTIFIER] == 5  # card-1, mail-a, mail-b, dev-1, pickup
    assert g.n_edges == 7
    col = {name: j for j, name in enumerate(CUSTOMER_NODE_FEATURES)}
    a = g.x_customer[0]
    assert a[col["n_card_values"]] == 1 and a[col["n_email_values"]] == 1
    assert a[col["n_delivered_lines"]] == 3
    # One withdrawal return over the two lines not returned on the guarantee path (rule 4).
    assert a[col["withdrawal_return_share"]] == pytest.approx(0.5)
    assert a[col["n_welcome_coupons_used"]] == 1
    assert np.isnan(g.x_customer[1, col["withdrawal_return_share"]])
    assert g.x_customer[1, col["has_delivered_lines"]] == 0
    assert g.x_customer[0, col["tenure_days"]] == pytest.approx(10.0)
    sharers = g.x_identifier[:, IDENTIFIER_NODE_FEATURES.index("n_sharers")]
    assert sorted(sharers.tolist()) == [1, 1, 1, 2, 2]
    assert g.summary()["values_shared_by_two_or_more"] == 2


def test_encoder_inputs_are_allowed_features() -> None:
    for names in (CUSTOMER_NODE_FEATURES, IDENTIFIER_NODE_FEATURES):
        assert not forbidden_features(names) and not label_or_key_features(names)


def test_fit_time_matches_the_risk_jobs_train_end() -> None:
    for moment in (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)):
        assert months_before(moment, 3) == risk_months_before(moment, 3)


# ---- contract and EmbeddingSource ---------------------------------------------------------------


def embedding_table(customers: list[str], as_of: datetime, value: float = 0.1) -> pa.Table:
    n = len(customers)
    columns: dict[str, Any] = {"customer_id": customers}
    for j, name in enumerate(EMBEDDING_COLUMNS):
        columns[name] = [value * (j + 1)] * n
    columns["encoder_version"] = ["test-encoder"] * n
    columns["as_of"] = [as_of] * n
    return pa.table(columns, schema=SCHEMAS["features/customer_embeddings"])


def test_the_embedding_contract() -> None:
    schema = SCHEMAS["features/customer_embeddings"]
    assert EMBEDDING_DIM == 16
    assert [n for n in schema.names if n.startswith(EMBEDDING_PREFIX)] == list(EMBEDDING_COLUMNS)
    check_feature_names(EMBEDDING_COLUMNS)  # allowed as risk features (FAIR-01)
    t = datetime(2026, 7, 1, tzinfo=UTC)
    assert validate_table("features/customer_embeddings", embedding_table(["A", "B"], t)) == []
    duplicate = embedding_table(["A", "A"], t)
    assert validate_table("features/customer_embeddings", duplicate)
    bad = embedding_table(["A"], t, value=np.nan)
    assert any("NaN" in e for e in validate_table("features/customer_embeddings", bad))
    blank = embedding_table(["A"], t).set_column(
        schema.get_field_index("encoder_version"), "encoder_version", pa.array([" "])
    )
    assert validate_table("features/customer_embeddings", blank)
    assert checks_for("features/customer_embeddings")


def test_the_file_source_reads_exact_instants_of_the_same_store(tmp_path: Path) -> None:
    root = mini_store().write(tmp_path / "store_a")
    _, store = read_table("store/identifiers", root)
    t1, t2 = datetime(2026, 1, 11, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    assert embedding_source(root, store) is None  # no file: A4 is not run
    table = pa.concat_tables([embedding_table(["A", "B"], t1), embedding_table(["A"], t2, 0.2)])
    derived = store.derive("test embeddings", date(2026, 1, 1), date(2026, 2, 1))
    write_table("features/customer_embeddings", table, root, derived)
    source = embedding_source(root, store)
    assert isinstance(source, FileEmbeddingSource)
    rows = source.embeddings_at(t1)
    assert list(rows.columns) == ["customer_id", *EMBEDDING_COLUMNS]
    assert rows["customer_id"].tolist() == ["A", "B"]
    assert source.embeddings_at(t2)["graphsage_0"].tolist() == [pytest.approx(0.2)]
    with pytest.raises(ValueError, match="no embeddings at"):
        source.embeddings_at(datetime(2026, 1, 20, tzinfo=UTC))
    assert source.describe()["encoder_versions"] == ["test-encoder"]
    # A file computed from another store is refused (review F22).
    other = Provenance("another store", date(2026, 1, 1), date(2026, 1, 31), "synthetic")
    with pytest.raises(ContractError):
        FileEmbeddingSource(root, other)


def test_with_embeddings_keeps_every_customer_row() -> None:
    from ml.risk.features import with_embeddings

    class Source:
        def embeddings_at(self, as_of: datetime) -> pd.DataFrame:
            frame = embedding_table(["A"], as_of).to_pandas()
            return frame[["customer_id", *EMBEDDING_COLUMNS]]

    frame = pd.DataFrame({"customer_id": ["A", "B"], "n_orders": [1.0, 2.0]})
    out, _ = with_embeddings(frame, Source(), datetime(2026, 1, 1, tzinfo=UTC))
    assert len(out) == 2 and np.isnan(out.loc[1, "graphsage_0"])


# ---- report -------------------------------------------------------------------------------------


def metrics(f1: float = 0.7) -> dict[str, Any]:
    t = {"n": 100, "positives": 50, "threshold": 0.4, "precision": 0.6, "recall": 0.8}
    t |= {"f1": f1, "f1_at_half": f1 - 0.01, "cross_entropy": 0.6, "ece": 0.01}
    return {
        p: dict(t)
        for p in ("all", "fully_featured", "decision_time", "decision_time_fully_featured")
    }


def neural(protocol: str, kind: str, device: str) -> dict[str, Any]:
    return {
        "title": f"{protocol}, {kind}",
        "kind": kind,
        "protocol": protocol,
        "protocol_label": f"label {protocol}",
        "test": metrics(),
        "test_uncalibrated": {p: {"cross_entropy": 0.61, "ece": 0.02} for p in metrics()},
        "training": {
            "rows_fit": 90,
            "epochs_run": 3,
            "best_epoch": 2,
            "train_seconds": 2.0 if device == "cpu" else 0.5,
            "epoch_seconds_median": 0.6 if device == "cpu" else 0.1,
            "load_average_before": 1.0,
            "load_average_after": 1.0,
        },
        "inference": {"seconds": 0.2 if device == "cpu" else 0.05, "events": 100},
    }


def asos_doc(device: str, sample: float = 1.0) -> dict[str, Any]:
    models = {k: neural("B", k, device) for k in ("graphsage", "mlp")}
    models["xgboost"] = {
        **neural("B", "xgboost", device),
        "training": {
            "threads": 4,
            "rows_fit": 90,
            "trees_kept": 10,
            "train_seconds": 1.0,
            "load_average_before": 1.0,
            "load_average_after": 1.0,
        },
    }
    a = {k: neural("A", k, device) for k in ("graphsage",)}
    a["graphsage"]["test"]["all"]["f1"] = 0.79
    return {
        "provenance": {"origin": "fixture", "nature": "synthetic"},
        "labels": {"test": {"period": "2021-10-01..2021-11-30"}},
        "config": {"sample": sample},
        "comparable": {"sample": sample, "seed": 42},
        "environment": {
            "device": "cuda:0" if device == "gpu" else "cpu",
            "backend": "dgl" if device == "gpu" else "torch",
            "torch": "2.8.0",
            "torch_hip": "7.0" if device == "gpu" else None,
            "dgl": "2.4.0" if device == "gpu" else None,
            "cpu": "test cpu",
            "cpu_count": 4,
            "torch_threads": 4,
            "gpu_name": "AMD Instinct MI300X",
            "gpu_arch": "gfx942",
            "gpu_total_gb": 205.8,
        },
        "timings": {
            "cold_start": {"import_torch_seconds": 1.0},
            "read_raw_seconds": 1.0,
            "job_seconds_after_cold_start": 10.0,
            "nature": "measured",
        },
        "protocols": {
            "B": {
                "protocol_label": "label B",
                "models": models,
                "data_loading_seconds": 1.0,
                "features": {"customer_node": ["c"], "variant_node": ["v"], "edge": ["e"]},
            },
            "A": {
                "protocol_label": "label A",
                "models": a,
                "data_loading_seconds": 1.0,
                "features": {"customer_node": ["c"], "variant_node": ["v"], "edge": []},
            },
        },
        "paper": {
            "source": "paper table",
            "protocol_label": "label A",
            "models": {"GNN": {"f1": 0.792, "cross_entropy": 0.489}},
            "notes": ["a note"],
        },
        "mod02": {
            "measured": True,
            "label": "label A",
            "f1_at_half": 0.78,
            "f1_tuned": 0.79,
            "threshold_spec": 0.78,
            "pass_at_half": True,
            "pass_tuned": True,
        },
    }


def write_docs(root: Path, docs: dict[str, dict[str, Any]]) -> Path:
    for name, doc in docs.items():
        path = root / "eval" / "gnn" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_the_report_puts_protocol_b_first_and_quotes_the_paper(tmp_path: Path) -> None:
    root = write_docs(tmp_path, {"us-202-asos-cpu.json": asos_doc("cpu", sample=0.25)})
    text = render(load_inputs(root))
    assert text.index("B, graphsage") < text.index("A, graphsage")
    assert "Paper: GNN" in text and "0.7920" in text and "not reported" in text
    assert "a seeded sample of 25% of the customers" in text
    assert "Not measured yet: no GPU run file" in text
    assert "never evidence about rings" in text
    assert "Not measured yet: no `artifacts/eval/gnn/us-202-embeddings-main.json`" in text


def test_the_speedup_needs_two_comparable_runs(tmp_path: Path) -> None:
    root = write_docs(
        tmp_path, {"us-202-asos-cpu.json": asos_doc("cpu"), "us-202-asos-gpu.json": asos_doc("gpu")}
    )
    text = render(load_inputs(root))
    assert "AMD Instinct MI300X" in text and "6.00x" in text  # 0.6 s / 0.1 s per epoch
    other = copy.deepcopy(asos_doc("gpu"))
    other["comparable"]["seed"] = 7
    write_docs(tmp_path, {"us-202-asos-gpu.json": other})
    assert "no speedup is computed" in render(load_inputs(tmp_path))


def test_the_report_without_any_run(tmp_path: Path) -> None:
    text = render(load_inputs(tmp_path))
    assert text.count("Not measured yet") >= 2


# ---- rule 12 ------------------------------------------------------------------------------------


def _strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
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


def test_rule12_the_gnn_jobs_never_name_the_ground_truth() -> None:
    """ml/gnn trains without labels (link prediction) or on ASOS raw tables: no module but the
    report's text names truth/, and none imports the label readers or the generator."""
    modules = sorted((ROOT / "ml" / "gnn").glob("*.py"))
    assert len(modules) >= 10
    for path in modules:
        source = path.read_text(encoding="utf-8")
        assert "ml.risk.labels" not in source and "pipeline.generator" not in source, path
        if path.name != "report.py":
            assert not [s for s in _strings(path) if "truth" in s.lower()], path
