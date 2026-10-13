"""Feature sets and design matrices of the risk model (US-204): FAIR-01, rule 6 (no customer
value), MOD-08 (no feature after the decision time) on hand-built tables.

Every value here is a made-up test fixture (nature: synthetic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from contracts.parquet import SCHEMAS, forbidden_features
from ml.risk.data import latest_before
from ml.risk.features import (
    A0,
    A1,
    A2,
    A3,
    A4,
    ABLATION,
    CLUSTER_FEATURES,
    CUSTOMER_FEATURES_ALL,
    REQUEST_FEATURES,
    REQUEST_MODEL_FEATURES,
    RESIDUAL_FEATURES,
    VALUE_COLUMNS,
    all_feature_names,
    check_feature_names,
    customer_frame,
    matrix,
    request_frame,
    with_embeddings,
)

T1 = datetime(2026, 5, 1, tzinfo=UTC)
T2 = datetime(2026, 6, 1, tzinfo=UTC)
T3 = datetime(2026, 7, 1, tzinfo=UTC)


def features_rows(as_of: datetime, customers: dict[str, float]) -> pd.DataFrame:
    """customer_features rows at as_of; every feature of a customer equals its number."""
    rows = []
    for cid, x in customers.items():
        row: dict[str, Any] = {"customer_id": cid, "as_of": pd.Timestamp(as_of)}
        for name in SCHEMAS["features/customer_features"].names:
            if name not in row:
                row[name] = x
        row["reference_model_version"] = "ref"
        rows.append(row)
    return pd.DataFrame(rows)


def cluster_rows(as_of: datetime, customers: dict[str, float]) -> pd.DataFrame:
    rows = []
    for cid, x in customers.items():
        row: dict[str, Any] = {"customer_id": cid, "as_of": pd.Timestamp(as_of), "cluster_id": None}
        for name in CLUSTER_FEATURES:
            row[name] = 100 + x
        rows.append(row)
    return pd.DataFrame(rows)


def test_feature_sets_hold_no_forbidden_feature_and_no_customer_value() -> None:
    """FAIR-01 and rule 6: checked statically on every feature list of every model."""
    for fs in ABLATION:
        assert forbidden_features(fs.features) == [], fs.name
        assert not set(fs.features) & VALUE_COLUMNS, fs.name
    assert forbidden_features(REQUEST_MODEL_FEATURES) == []
    assert not set(REQUEST_MODEL_FEATURES) & VALUE_COLUMNS
    assert not set(all_feature_names()) & set(SCHEMAS["features/customer_value"].names) - {
        "n_orders",
        "n_lines",
        "n_returns",
    }


def test_the_ablation_adds_one_group_per_step() -> None:
    assert A0.learner == "rules"
    assert set(A0.features) < set(A1.features) < set(A2.features) < set(A3.features)
    assert set(A2.features) - set(A1.features) == set(CLUSTER_FEATURES)
    assert set(A3.features) - set(A2.features) == set(RESIDUAL_FEATURES)
    assert A4.needs_embeddings and A4.features == A3.features
    assert set(REQUEST_MODEL_FEATURES) == set(A3.features) | set(REQUEST_FEATURES)


@pytest.mark.parametrize(
    "names",
    [("n_orders", "value_eur"), ("n_orders", "shipping_country"), ("n_orders", "n_orders")],
)
def test_check_feature_names_refuses(names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        check_feature_names(names)


def test_customer_frame_takes_the_rows_computed_at_exactly_the_decision_time() -> None:
    features = pd.concat([features_rows(T1, {"A": 1.0}), features_rows(T2, {"A": 2.0, "B": 3.0})])
    clusters = pd.concat([cluster_rows(T1, {"A": 1.0}), cluster_rows(T2, {"A": 2.0, "B": 3.0})])
    frame = customer_frame(features, clusters, T1)
    assert frame["customer_id"].tolist() == ["A"]
    assert frame.loc[0, "n_orders"] == 1.0 and frame.loc[0, "cluster_size"] == 101.0
    with pytest.raises(ValueError, match="no rows computed"):
        customer_frame(features, clusters, T3)


def test_customer_frame_refuses_a_customer_without_its_cluster_row() -> None:
    features = features_rows(T1, {"A": 1.0, "B": 2.0})
    with pytest.raises(ValueError, match="without a features/clusters row"):
        customer_frame(features, cluster_rows(T1, {"A": 1.0}), T1)


def contexts() -> pd.DataFrame:
    frames = [
        customer_frame(features_rows(t, {"A": x}), cluster_rows(t, {"A": x}), t)
        for t, x in ((T1, 1.0), (T2, 2.0), (T3, 3.0))
    ]
    return pd.concat(frames, ignore_index=True)


def requests(*rows: tuple[str, str, datetime, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "return_id": [r[0] for r in rows],
            "customer_id": pd.array([r[1] for r in rows], dtype="string"),
            "decided_at": pd.to_datetime([r[2] for r in rows], utc=True),
            "path": "withdrawal",
            "reason": [r[3] for r in rows],
            "days_since_delivery": 4,
            "price_eur": 59.9,
            "variant_rate": 0.3,
            "variant_n": 12,
            "category_rate": 0.25,
        }
    )


def test_a_request_takes_the_last_snapshot_strictly_before_it() -> None:
    """MOD-08: the snapshot computed at the request instant already holds the request, and a
    later snapshot holds the future: neither is used."""
    req = requests(
        ("R1", "A", T2 + timedelta(days=3), "size"),  # after T2, before T3
        ("R2", "A", T2, "changed_mind"),  # exactly at T2: T1 is the last strictly before
        ("R3", "A", T1 - timedelta(seconds=1), "other"),  # before every snapshot
        ("R4", "B", T3 + timedelta(days=1), "not_as_expected"),  # no snapshot of B at all
    )
    frame = request_frame(req, contexts()).set_index("return_id")
    assert frame.loc["R1", "context_as_of"] == pd.Timestamp(T2)
    assert frame.loc["R1", "n_orders"] == 2.0
    assert frame.loc["R2", "context_as_of"] == pd.Timestamp(T1)
    assert frame.loc["R2", "n_orders"] == 1.0
    for rid in ("R3", "R4"):
        assert pd.isna(frame.loc[rid, "context_as_of"])
        assert frame.loc[rid, list(CUSTOMER_FEATURES_ALL)].isna().all()
    known = frame.loc[frame["context_as_of"].notna()]
    assert (known["context_as_of"] < known["decided_at"]).all()


def test_request_features_and_reason_one_hot() -> None:
    frame = request_frame(requests(("R1", "A", T3, "size")), contexts())
    row = frame.iloc[0]
    assert row["days_since_delivery"] == 4.0 and row["item_price_eur"] == 59.9
    assert row["variant_return_rate"] == 0.3 and row["variant_lines"] == 12.0
    assert row["category_return_rate"] == 0.25
    assert (row["reason_size"], row["reason_changed_mind"], row["reason_other"]) == (1.0, 0.0, 0.0)
    x = matrix(frame, REQUEST_MODEL_FEATURES)
    assert x.shape == (1, len(REQUEST_MODEL_FEATURES)) and x.dtype == np.float64


def test_later_snapshots_do_not_change_a_request_row() -> None:
    """Adding a snapshot computed after the request (late facts) changes nothing (MOD-08)."""
    req = requests(("R1", "A", T2 + timedelta(days=3), "size"))
    before = request_frame(req, contexts())
    late = customer_frame(
        features_rows(T3 + timedelta(days=1), {"A": 9.0}),
        cluster_rows(T3 + timedelta(days=1), {"A": 9.0}),
        T3 + timedelta(days=1),
    )
    after = request_frame(req, pd.concat([contexts(), late], ignore_index=True))
    pd.testing.assert_frame_equal(before, after)


def test_guarantee_requests_are_refused() -> None:
    req = requests(("R1", "A", T3, "size"))
    req["path"] = "guarantee"
    with pytest.raises(ValueError, match="rule 4"):
        request_frame(req, contexts())


def test_latest_before_is_strict() -> None:
    at = pd.Series(
        pd.to_datetime([T1, T1 + timedelta(microseconds=1), T3 + timedelta(1)], utc=True)
    )
    found = latest_before(at, [T3, T1, T2])
    assert pd.isna(found[0])
    assert found[1] == pd.Timestamp(T1)
    assert found[2] == pd.Timestamp(T3)


def test_matrix_refuses_missing_columns() -> None:
    with pytest.raises(KeyError):
        matrix(pd.DataFrame({"n_orders": [1.0]}), ("n_orders", "n_lines"))


class Embeddings:
    def __init__(self, columns: list[str]) -> None:
        self.columns = columns

    def embeddings_at(self, as_of: datetime) -> pd.DataFrame:
        return pd.DataFrame({"customer_id": ["A"], **{c: [0.5] for c in self.columns}})


def test_the_a4_hook_adds_embedding_columns() -> None:
    frame = customer_frame(features_rows(T1, {"A": 1.0}), cluster_rows(T1, {"A": 1.0}), T1)
    out, names = with_embeddings(frame, Embeddings(["graphsage_0", "graphsage_1"]), T1)
    assert names == (*A3.features, "graphsage_0", "graphsage_1")
    assert out.loc[0, "graphsage_1"] == 0.5
    with pytest.raises(ValueError):
        with_embeddings(frame, Embeddings(["other"]), T1)
