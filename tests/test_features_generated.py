"""Features, residual, value and evidence on a small generated store (MOD-08, rule 8, US-205,
US-206, US-209; DATA-01 for the three feature tables).

The store is the Navigli Fashion generator at scale 0.05, seed 7, from the illustrative example
calibration (contracts/examples/calibration.json): every number here is synthetic and none is a
result. Results come from python -m ml.reference.report on the full store.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from contracts.io import ContractError, read_table, write_table
from contracts.parquet import SCHEMAS, forbidden_features
from ml.reference.checkout import CartItem, checkout_probabilities
from ml.reference.features import (
    CUSTOMER_HISTORY_FEATURES,
    PERSONALIZED_FEATURES,
    REFERENCE_FEATURES,
    Encoding,
    design,
)
from ml.reference.model import (
    P_FLOOR,
    TrainConfig,
    crossfit,
    fit,
    observed_frame,
    observed_line_probabilities,
)
from pipeline.features.__main__ import main as features_cli
from pipeline.features.__main__ import store_month_ends
from pipeline.features.build import TABLES, features_at, tables
from pipeline.features.lines import line_frame
from pipeline.features.requests import REQUEST_FEATURE_COLUMNS, request_features
from pipeline.features.snapshot import TABLES as STORE_TABLES
from pipeline.features.snapshot import load_snapshot
from pipeline.features.value import reconcile
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import STORE_TABLES as ALL_STORE_TABLES
from pipeline.generator.core import generate

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "contracts" / "examples" / "calibration.json"
T = datetime(2026, 6, 1, tzinfo=UTC)
LATER = datetime(2026, 8, 1, tzinfo=UTC)
FAST = TrainConfig(folds=3, min_train_lines=500, min_validation_lines=100)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    generate(load_calibration(EXAMPLE), out, seed=7, scale=0.05, scenario="main")
    return out


@pytest.fixture(scope="module")
def ood(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated-ood")
    generate(load_calibration(EXAMPLE), out, seed=7, scale=0.05, scenario="ood")
    return out / "ood"


def rewrite(store: Path, out: Path, edit: Any) -> Path:
    """A copy of the store whose tables pass through edit(name, frame) -> frame."""
    for name in ALL_STORE_TABLES:
        table, prov = read_table(name, store)
        frame = edit(name, table.to_pandas())
        rebuilt = pa.Table.from_pandas(frame, schema=SCHEMAS[name], preserve_index=False)
        write_table(name, rebuilt, out, prov)
    return out


def late_facts(store: Path) -> dict[str, pd.DataFrame]:
    """Rows that happened by T but were recorded after it: a delivery of an order that has none,
    a return of a kept line, a new identifier."""
    limit = pd.Timestamp(T)
    late = {"event_at": limit - timedelta(hours=1), "available_at": limit + timedelta(hours=1)}
    frames = {n: read_table(n, store)[0].to_pandas() for n in ALL_STORE_TABLES}
    orders, deliveries = frames["store/orders"], frames["store/deliveries"]
    placed = orders[orders["event_at"] <= limit - timedelta(days=2)]
    undelivered = placed[~placed["order_id"].isin(deliveries["order_id"])]
    delivered = deliveries[deliveries["available_at"] <= limit - timedelta(days=1)]
    lines = frames["store/order_lines"]
    kept = lines[
        lines["order_id"].isin(delivered["order_id"])
        & ~lines["line_id"].isin(frames["store/returns"]["line_id"])
    ]
    rows = {
        "store/returns": pd.DataFrame(
            [{"return_id": "RET-LATE", "line_id": kept["line_id"].iloc[0], "reason": "size",
              "days_since_delivery": 1, **late}]
        ),
        "store/identifiers": pd.DataFrame(
            [{"customer_id": orders["customer_id"].iloc[0], "kind": "phone",
              "value_hash": "late-phone", **late}]
        ),
    }  # fmt: skip
    if len(undelivered):
        rows["store/deliveries"] = pd.DataFrame(
            [{"order_id": undelivered["order_id"].iloc[0], **late}]
        )
    return rows


def test_mod08_feature_tables_ignore_every_row_available_after_the_decision(
    store: Path, tmp_path: Path
) -> None:
    """MOD-08: the same tables from the full store, from the rows known at T only, and from the
    full store plus late facts (event before T, recorded after T)."""
    limit = pd.Timestamp(T)
    truncated = rewrite(store, tmp_path / "truncated", lambda n, f: f[f["available_at"] <= limit])
    extra = late_facts(store)
    augmented = rewrite(
        store,
        tmp_path / "augmented",
        lambda n, f: pd.concat([f, extra[n]], ignore_index=True) if n in extra else f,
    )
    built = [
        tables([features_at(load_snapshot(root, T), config=FAST)])
        for root in (store, truncated, augmented)
    ]
    for name in TABLES:
        assert built[0][name].num_rows > 0, name
        assert built[0][name].equals(built[1][name]), name
        assert built[0][name].equals(built[2][name]), name


def test_feature_tables_are_valid_contract_files(store: Path, tmp_path: Path) -> None:
    """DATA-01: written through contracts.io, read back at the decision time; the residual and
    the value reconcile."""
    out = tmp_path / "features"
    code = features_cli(
        ["--store", str(store), "--out", str(out), "--as-of", T.isoformat(), LATER.isoformat()]
    )
    assert code == 0
    for name in TABLES:
        table, prov = read_table(name, out, as_of=LATER)
        assert prov.nature == "synthetic" and "pipeline.features" in prov.origin
        assert set(table.column("as_of").to_pylist()) == {T, LATER}
        with pytest.raises(ContractError, match="computed after"):
            read_table(name, out, as_of=T)
    features, _ = read_table("features/customer_features", out)
    frame = features.to_pandas()
    assert frame["residual_z"].notna().any()
    assert np.isfinite(frame["residual"]).all()
    with_obs = frame[frame["n_observations"] > 0]
    assert with_obs["residual_z"].notna().all()


def test_before_any_settled_line_there_is_nothing_to_price(store: Path) -> None:
    early = datetime(2025, 10, 20, tzinfo=UTC)
    fs = features_at(load_snapshot(store, early), config=FAST)
    assert len(fs.priced.lines) == 0
    version = str(fs.customer_features["reference_model_version"].iloc[0])
    assert version.startswith("reference-no-settled-line-v1")
    assert fs.customer_features["residual_z"].isna().all()
    assert (fs.evidence["evidence_quality"] == "insufficient").all()


def test_default_decision_times_are_the_month_ends_of_the_store(store: Path) -> None:
    ends = store_month_ends(store)
    assert ends[0] == datetime(2025, 11, 1, tzinfo=UTC)
    assert ends[-1] == datetime(2026, 10, 1, tzinfo=UTC)


def test_customer_value_reconciles_on_the_generated_store(store: Path) -> None:
    fs = features_at(load_snapshot(store, LATER), config=FAST)
    assert reconcile(fs.value).empty
    assert (fs.value["n_lines"] > 0).sum() > 100


def test_rule8_reference_inputs_never_hold_the_customers_own_outcomes(
    store: Path, tmp_path: Path
) -> None:
    """Rule 8: flipping a customer's withdrawal returns changes none of the reference features of
    that customer's lines; it does change the personalized ones."""
    full = observed_frame(line_frame(load_snapshot(store, LATER)))
    per = full.groupby("customer_id").agg(
        n=("line_id", "size"), returned=("returned", "sum"), history=("customer_r", "max")
    )
    customer = str(per[(per["n"] >= 4) & (per["returned"] >= 2) & (per["history"] >= 1)].index[0])
    mine = set(full.loc[full["customer_id"] == customer, "line_id"])
    returns = read_table("store/returns", store)[0].to_pandas()
    withdrawal = {"size", "changed_mind", "not_as_expected", "other"}
    dropped = set(
        returns.loc[returns["line_id"].isin(mine) & returns["reason"].isin(withdrawal), "return_id"]
    )

    def drop_returns(name: str, frame: pd.DataFrame) -> pd.DataFrame:
        if name in ("store/returns", "store/return_events"):
            return frame[~frame["return_id"].isin(dropped)]
        return frame

    other = rewrite(store, tmp_path / "flipped", drop_returns)
    a = observed_frame(line_frame(load_snapshot(store, LATER)))
    b = observed_frame(line_frame(load_snapshot(other, LATER)))
    a, b = (f[f["customer_id"] == customer].set_index("line_id") for f in (a, b))
    assert a["returned"].sum() >= 2 and b["returned"].sum() == 0
    enc = Encoding(10.0, ("x",), ("y",))
    ref_a = design(a, enc, REFERENCE_FEATURES)
    ref_b = design(b.loc[a.index], enc, REFERENCE_FEATURES)
    pd.testing.assert_frame_equal(ref_a, ref_b)
    pers_a = design(a, enc, PERSONALIZED_FEATURES)[list(CUSTOMER_HISTORY_FEATURES)]
    pers_b = design(b.loc[a.index], enc, PERSONALIZED_FEATURES)[list(CUSTOMER_HISTORY_FEATURES)]
    assert not pers_a.equals(pers_b)


def test_reference_and_personalized_feature_lists() -> None:
    """FAIR-01 and rule 8 on the feature lists themselves."""
    assert forbidden_features(PERSONALIZED_FEATURES) == []
    assert not set(REFERENCE_FEATURES) & set(CUSTOMER_HISTORY_FEATURES)
    assert not any(name.startswith("customer") for name in REFERENCE_FEATURES)


def test_models_train_with_xgboost_or_fall_back_and_stay_in_bounds(store: Path) -> None:
    lines = line_frame(load_snapshot(store, LATER))
    frame = observed_frame(lines)
    y = frame["returned"].to_numpy(dtype=float)
    groups = frame["customer_id"].to_numpy()
    model = fit(frame, y, groups, "reference", LATER, FAST)
    assert model.kind == "xgboost" and model.isotonic is not None
    assert model.version.startswith("reference-xgboost-v1@2026-08-01")
    p = model.predict(frame)
    assert p.min() >= P_FLOOR and p.max() <= 1 - P_FLOOR
    small = fit(frame.iloc[:50], y[:50], groups[:50], "reference", LATER, FAST)
    assert small.kind == "smoothed" and small.booster is None
    assert np.all((small.predict(frame) >= P_FLOOR) & (small.predict(frame) <= 1 - P_FLOOR))
    single = fit(frame.iloc[:600], np.zeros(600), groups[:600], "personalized", LATER, FAST)
    assert single.kind == "smoothed"
    one_customer = np.full(800, "C-ONLY")
    alone = fit(frame.iloc[:800], y[:800], one_customer, "reference", LATER, FAST)
    assert alone.kind == "smoothed" and "no validation customer" in alone.notes[0]
    oof, models = crossfit(frame, y, groups, "reference", LATER, FAST)
    assert len(models) == FAST.folds and oof.shape == y.shape
    assert np.all((oof >= P_FLOOR) & (oof <= 1 - P_FLOOR))


def test_out_of_distribution_store_is_priced_by_a_model_trained_on_main(
    store: Path, ood: Path
) -> None:
    """US-105: the OOD store is never trained on; its lines are priced by the main model."""
    priced = observed_line_probabilities(
        line_frame(load_snapshot(ood, LATER)),
        LATER,
        config=FAST,
        source=line_frame(load_snapshot(store, LATER)),
    )
    assert "trained on another store" in priced.version
    assert priced.lines["p"].between(P_FLOOR, 1 - P_FLOOR).all()
    with pytest.raises(ValueError, match="same decision time"):
        features_at(load_snapshot(ood, LATER), source=load_snapshot(store, T))


def test_mod08_request_features_equal_those_computed_when_the_request_arrived(
    store: Path,
) -> None:
    big = load_snapshot(store, LATER)
    rows = request_features(big).set_index("return_id")
    assert set(REQUEST_FEATURE_COLUMNS) <= set(rows.columns)
    assert (rows["path"] == "withdrawal").all()
    assert rows["variant_n"].max() > 0
    for return_id in rows.index[:: max(1, len(rows) // 5)][:5]:
        at = rows.at[return_id, "decided_at"].to_pydatetime()
        alone = request_features(big.restrict(at), [return_id]).set_index("return_id")
        pd.testing.assert_series_equal(rows.loc[return_id], alone.loc[return_id])


def test_guarantee_requests_are_left_out_unless_asked(store: Path) -> None:
    snap = load_snapshot(store, LATER)
    every = request_features(snap, include_guarantee=True)
    assert set(every["path"]) == {"withdrawal", "guarantee"}
    assert len(request_features(snap)) == (every["path"] == "withdrawal").sum()
    with pytest.raises(KeyError, match="not known"):
        request_features(snap, ["RET-DOES-NOT-EXIST"])
    lines = line_frame(snap)
    frame = observed_frame(lines)
    model = fit(
        frame, frame["returned"].to_numpy(float), frame["customer_id"].to_numpy(), "reference",
        LATER, FAST,
    )  # fmt: skip
    priced = request_features(snap, model=model)
    assert priced["reference_p"].between(P_FLOOR, 1 - P_FLOOR).all()


def test_checkout_prices_a_cart_with_its_order_context(store: Path) -> None:
    snap = load_snapshot(store, LATER)
    frame = observed_frame(line_frame(snap))
    model = fit(
        frame, frame["returned"].to_numpy(float), frame["customer_id"].to_numpy(),
        "personalized", LATER, FAST,
    )  # fmt: skip
    variants = snap["store/variants"]
    product = variants.groupby("product_id")["variant_id"].agg(list)
    pair = next(v for v in product if len(v) >= 2)
    customer = str(frame["customer_id"].iloc[0])
    cart = [CartItem(pair[0], "M"), CartItem(pair[1], "L", 1999)]
    p = checkout_probabilities(snap, model, customer, cart)
    assert p.shape == (2,) and np.all((p >= P_FLOOR) & (p <= 1 - P_FLOOR))
    assert checkout_probabilities(snap, model, customer, []).shape == (0,)
    with pytest.raises(KeyError, match="catalogue"):
        checkout_probabilities(snap, model, customer, [CartItem("V-NOPE")])


def test_store_tables_read_by_the_features_exist(store: Path) -> None:
    assert set(STORE_TABLES) <= set(ALL_STORE_TABLES)
    assert replace(FAST, folds=2).folds == 2
