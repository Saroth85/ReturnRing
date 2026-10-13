"""US-201: return probability per purchase on ASOS, protocols A and B (ADR-10, MOD-01, MOD-06).

Every table here is a tiny made-up fixture (nature: synthetic), hand-built or from the seeded
replica of tests/propensity_fixtures.py; the real run is the CLI (docs/results/us-201-*.md).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pyarrow as pa
import pytest
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from contracts.parquet import DESCRIPTIVE_ONLY, SCHEMAS, forbidden_features
from ml.propensity import __main__ as cli
from ml.propensity import baseline as bl
from ml.propensity import features as ft
from ml.propensity import metrics as mt
from ml.propensity import train as tr
from ml.propensity.calibration import PROBABILITY_CLIP, IsotonicCalibrator
from ml.propensity.run import RunConfig, run, training_data
from tests.propensity_fixtures import (
    customer_row,
    event_row,
    random_raw,
    variant_row,
    write_raw,
)

C1, C2, C3, C4 = 101, 102, 103, 104
V1, V2, V3, V9 = 901, 902, 903, 909


def frame(name: str, rows: list[dict[str, Any]]) -> pl.DataFrame:
    out = pl.from_arrow(pa.Table.from_pylist(rows, schema=SCHEMAS[name]))
    assert isinstance(out, pl.DataFrame)
    return out


def joined(
    events: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    variants: list[dict[str, Any]],
) -> pl.DataFrame:
    return ft.join_events(
        frame("raw/asos_events", events),
        frame("raw/asos_customers", customers),
        frame("raw/asos_variants", variants),
    )


def hand_tables() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    events = [
        event_row("train", V1, C1, True),  # 0: C1 4 sales 2 returns; V1 10 sales 5 returns
        event_row("train", V2, C1, False),  # 1
        event_row("train", V1, C3, True),  # 2: C3's only return -> selected by its own return
        event_row("train", V9, C4, True),  # 3: no variant row, no customer row
        event_row("train", V3, C2, False),  # 4: V3 inconsistent (3 of 3 returned)
        event_row("test", V1, C1, False),  # 5: test rows of C1 and V1
    ]
    customers = [
        customer_row("train", C1, 4, 2),
        customer_row("train", C2, 5, 1),
        customer_row("train", C3, 3, 1),
        customer_row("test", C1, 6, 1),
    ]
    variants = [
        variant_row("train", V1, 10, 5, avg_price_gbp=10.0),
        variant_row("train", V2, 8, 2, product_type="productType_A", brand="Pull&Bear"),
        variant_row("train", V3, 3, 3),
        variant_row("test", V1, 10, 5, avg_price_gbp=11.0, product_type="productType_Z"),
    ]
    return events, customers, variants


def value(f: pl.DataFrame, row: int, column: str) -> Any:
    return f[column][row]


# ---- Leave-one-out counts and joins (rule 7: features known without the event's own label) ---


def test_leave_one_out_counts_remove_the_event_itself() -> None:
    f = joined(*hand_tables())
    # Event 0: customer C1 (4 sales, 2 returns), returned -> 3 sales, 1 return without it.
    assert value(f, 0, "customer_sales_loo") == 3
    assert value(f, 0, "customer_returns_loo") == 1
    # Event 1: same customer, kept -> its returns stay 2.
    assert value(f, 1, "customer_returns_loo") == 2
    # Variant V1 (10 sales, 5 returns), event 0 returned -> 9 and 4.
    assert value(f, 0, "variant_sales_loo") == 9
    assert value(f, 0, "variant_returns_loo") == 4
    # Test event 5 uses the test rows: C1 6 sales 1 return, kept -> 5 and 1.
    assert value(f, 5, "customer_sales_loo") == 5
    assert value(f, 5, "customer_returns_loo") == 1


def test_missing_rows_give_explicit_nulls_and_flags() -> None:
    f = joined(*hand_tables())
    assert value(f, 3, "has_customer_features") is False
    assert value(f, 3, "has_variant_features") is False
    assert value(f, 3, "customer_sales_loo") is None
    assert value(f, 3, "variant_returns_loo") is None
    assert value(f, 3, "fully_featured") is False
    vocab = ft.Vocabulary.from_variants(frame("raw/asos_variants", hand_tables()[2][:3]))
    x, names = ft.feature_matrix(f, "B", vocab)
    row = dict(zip(names, x[3], strict=True))
    assert row["has_customer_features"] == 0 and row["has_variant_features"] == 0
    for name in ("customer_sales_loo", "variant_returns_loo", "avg_price_gbp", "brand_brand_k"):
        assert math.isnan(row[name]), name


def test_product_attributes_come_from_the_events_own_split() -> None:
    events, customers, variants = hand_tables()
    f = joined(events, customers, variants)
    assert value(f, 0, "avg_price_gbp") == 10.0  # train row of V1
    assert value(f, 5, "avg_price_gbp") == 11.0  # test row of V1


def test_inconsistent_aggregates_are_clipped_and_counted() -> None:
    f = joined(*hand_tables())
    # V3: 3 sales, 3 returns, but event 4 is kept: 3 returns among 2 other sales -> clipped to 2.
    assert value(f, 4, "variant_sales_loo") == 2
    assert value(f, 4, "variant_returns_loo") == 2
    assert value(f, 4, "variant_loo_clipped") is True
    assert f["variant_loo_clipped"].sum() == 1
    assert f["customer_loo_clipped"].sum() == 0
    # A single sale counted as returned while the event is kept: 0 other sales, 0 other returns.
    g = joined(
        [event_row("train", V1, C1, False)],
        [customer_row("train", C1, 4, 2)],
        [variant_row("train", V1, 1, 1)],
    )
    assert (value(g, 0, "variant_sales_loo"), value(g, 0, "variant_returns_loo")) == (0, 0)
    assert value(g, 0, "variant_loo_clipped") is True
    assert value(g, 0, "selected_by_own_return") is True  # left out, and reported as clipped


def test_events_selected_by_their_own_return_are_flagged() -> None:
    f = joined(*hand_tables())
    selected = f["selected_by_own_return"].to_list()
    # Event 2: C3 has 1 return, the event itself -> its row exists because it was returned.
    assert selected[2] is True
    # Event 0: C1 keeps another return; event 3 has no row at all; event 4 keeps 2 other returns.
    assert selected[0] is False and selected[3] is False and selected[4] is False
    masks = ft.population_masks(f)
    assert masks["decision_time"].tolist() == [not s for s in selected]
    assert (
        masks["decision_time_fully_featured"].sum()
        == (masks["decision_time"] & masks["fully_featured"]).sum()
    )


def test_in_the_replica_selected_events_are_always_returned() -> None:
    events, customers, variants = random_raw(seed=3)
    f = joined(events, customers, variants)
    selected = f.filter(pl.col("selected_by_own_return"))
    assert selected.height > 0
    assert selected["is_returned"].all()  # the leak the decision-time population removes


def bump(
    rows: list[dict[str, Any]], key: str, h: int, delta: int, split: str | None
) -> list[dict[str, Any]]:
    """Node rows with `delta` more returns for node h (in `split`, or in every split)."""
    out = []
    for row in rows:
        row = dict(row)
        if row[key] == h and (split is None or row["split"] == split):
            row["returns"] += delta
            row["return_rate"] = row["returns"] / row["sales"]
        out.append(row)
    return out


def test_decision_time_features_ignore_the_events_own_label() -> None:
    """MOD-08 for ASOS: flipping an event's label, with the aggregates that count it updated as
    the source would, leaves its protocol B features unchanged; protocol A ones move."""
    events, customers, variants = random_raw(seed=5)
    f = joined(events, customers, variants)
    train_variants = frame("raw/asos_variants", variants).filter(pl.col("split") == "train")
    v = ft.Vocabulary.from_variants(train_variants)
    candidates = f.with_row_index("i").filter(
        (pl.col("split") == "train")
        & pl.col("fully_featured")
        & (pl.col("customer_returns") >= 2)
        & (pl.col("variant_returns") >= 2)
        & ~pl.col("customer_loo_clipped")
        & ~pl.col("variant_loo_clipped")
    )
    assert candidates.height >= 5
    for i in candidates["i"].to_list()[:5]:
        flipped_events = [dict(e) for e in events]
        e = flipped_events[i]
        delta = -1 if e["is_returned"] else 1
        e["is_returned"] = not e["is_returned"]
        g = joined(
            flipped_events,
            # Customer aggregates are per split; variant aggregates span both splits (ADR-10).
            bump(customers, "customer_hash", e["customer_hash"], delta, split=e["split"]),
            bump(variants, "variant_hash", e["variant_hash"], delta, split=None),
        )
        for protocol, same in (("B", True), ("A", False)):
            before, names = ft.feature_matrix(f[i], protocol, v)
            after, _ = ft.feature_matrix(g[i], protocol, v)
            assert np.array_equal(before, after, equal_nan=True) is same, (protocol, names)
        assert f["selected_by_own_return"][i] == g["selected_by_own_return"][i]


def test_join_refuses_duplicate_node_rows() -> None:
    events, customers, variants = hand_tables()
    with pytest.raises(ft.FeatureError, match="not unique"):
        ft.join_events(
            frame("raw/asos_events", events),
            pl.concat([frame("raw/asos_customers", customers)] * 2),
            frame("raw/asos_variants", variants),
        )


# ---- Feature lists (rule 6, FAIR-01, ADR-10) ----------------------------------------------------


def vocab() -> ft.Vocabulary:
    return ft.Vocabulary(("Jeans", "productType_A"), ("Brand_K", "Pull&Bear"))


def test_no_model_holds_a_forbidden_feature() -> None:
    for protocol in ft.PROTOCOLS:
        names = ft.feature_names(protocol, vocab())
        assert forbidden_features(names) == []
        assert not set(names) & DESCRIPTIVE_ONLY
        assert not any("hash" in n for n in names)


def test_join_drops_hashes_and_descriptive_only_columns() -> None:
    f = joined(*hand_tables())
    assert forbidden_features(f.columns) == []


def test_protocol_b_holds_only_leave_one_out_counts_and_product_attributes() -> None:
    a = ft.feature_names("A", vocab())
    b = ft.feature_names("B", vocab())
    assert "customer_return_rate" in a and "variant_reason_share_D2" in a
    assert "customer_premier" in a
    assert not [n for n in b if "rate" in n or "share" in n or "premier" in n]
    assert b[:4] == list(ft.LOO_COUNTS)
    assert {"avg_price_gbp", "avg_discount", "product_type_jeans", "brand_pull_bear"} <= set(b)
    assert b[-2:] == list(ft.FLAGS)


@pytest.mark.parametrize(
    ("protocol", "names"),
    [
        ("A", ["customer_sales", "customer_is_male"]),
        ("A", ["shipping_country"]),
        ("A", ["variant_hash"]),
        ("B", ["customer_return_rate"]),
        ("B", ["customer_reason_share_A"]),
        ("B", ["customer_returns"]),
        ("B", ["customer_premier"]),
        ("A", ["avg_price_gbp", "avg_price_gbp"]),
    ],
)
def test_check_feature_names_fails_closed(protocol: ft.Protocol, names: list[str]) -> None:
    with pytest.raises(ft.FeatureError):
        ft.check_feature_names(protocol, names)


def test_one_hot_vocabulary_comes_from_train_and_unknown_categories_are_counted() -> None:
    events, customers, variants = hand_tables()
    f = joined(events, customers, variants)
    v = ft.Vocabulary.from_variants(frame("raw/asos_variants", variants[:3]))
    assert v.product_type == ("Jeans", "productType_A")
    assert v.brand == ("Brand_K", "Pull&Bear")
    test = f.filter(pl.col("split") == "test")
    assert ft.unknown_categories(test, v) == {"product_type": 1, "brand": 0}
    x, names = ft.feature_matrix(test, "B", v)
    row = dict(zip(names, x[0], strict=True))
    assert row["product_type_jeans"] == 0 and row["product_type_producttype_a"] == 0
    assert row["brand_brand_k"] == 1


def test_one_hot_names_must_not_collide() -> None:
    with pytest.raises(ft.FeatureError, match="feature name"):
        ft.Vocabulary(("Pull&Bear", "pull bear"), ()).columns()


def test_validation_mask_is_seeded_and_ten_percent() -> None:
    a = ft.validation_mask(1000, 0.1, seed=42)
    assert a.sum() == 100
    assert np.array_equal(a, ft.validation_mask(1000, 0.1, seed=42))
    assert not np.array_equal(a, ft.validation_mask(1000, 0.1, seed=43))
    assert ft.validation_mask(3, 0.1, seed=1).sum() == 1
    with pytest.raises(ft.FeatureError):
        ft.validation_mask(1, 0.1, seed=1)
    with pytest.raises(ft.FeatureError):
        ft.validation_mask(10, 1.0, seed=1)


# ---- Metrics (computed by hand) -----------------------------------------------------------------


def test_precision_recall_f1_by_hand() -> None:
    y = np.array([1, 1, 0, 0, 1])
    p = np.array([0.9, 0.4, 0.6, 0.1, 0.8])
    # p >= 0.5 -> 1, 0, 1, 0, 1: TP 2, FP 1, FN 1.
    assert mt.precision_recall_f1(y, p, 0.5) == pytest.approx((2 / 3, 2 / 3, 2 / 3))
    assert mt.precision_recall_f1(y, p, 0.95) == (0.0, 0.0, 0.0)


def test_cross_entropy_by_hand() -> None:
    y = np.array([1, 0])
    p = np.array([0.8, 0.4])
    assert mt.cross_entropy(y, p) == pytest.approx(-(math.log(0.8) + math.log(0.6)) / 2)
    with pytest.raises(ValueError, match="strictly inside"):
        mt.cross_entropy(y, np.array([1.0, 0.4]))


def test_expected_calibration_error_by_hand() -> None:
    y = np.array([0, 1, 0, 1, 1])
    p = np.array([0.05, 0.15, 0.15, 0.95, 1.0])
    # Bins: [0, .1): |0 - .05| x 1; [.1, .2): |.5 - .15| x 2; [.9, 1]: |1 - .975| x 2.
    expected = (0.05 + 0.35 * 2 + 0.025 * 2) / 5
    assert mt.expected_calibration_error(y, p) == pytest.approx(expected)


def test_best_f1_threshold_by_hand() -> None:
    y = np.array([1, 1, 0, 1, 0])
    p = np.array([0.9, 0.8, 0.7, 0.6, 0.1])
    # F1 at t = .9, .8, .7, .6, .1: 0.5, 0.8, 0.667, 0.857, 0.75 -> 0.6.
    assert mt.best_f1_threshold(y, p) == 0.6
    # Equal predictions are one threshold, never split: at 0.6 F1 is 2/5, at 0.3 it is 4/8. A
    # split after the first 0.6 (a returned event) would claim 2/3 and pick 0.6.
    y = np.array([1, 0, 0, 1, 0, 0])
    p = np.array([0.6, 0.6, 0.6, 0.3, 0.3, 0.3])
    assert mt.best_f1_threshold(y, p) == 0.3


def test_binary_metrics_and_input_checks() -> None:
    y = np.array([1, 0, 1, 1])
    p = np.array([0.7, 0.3, 0.45, 0.9])
    m = mt.binary_metrics(y, p, threshold=0.4)
    assert (m.n, m.positives, m.f1, m.f1_at_half) == (4, 3, 1.0, pytest.approx(0.8))
    with pytest.raises(ValueError, match="0 and 1"):
        mt.binary_metrics(np.array([2, 0]), np.array([0.5, 0.5]), 0.5)
    with pytest.raises(ValueError, match="in \\[0, 1\\]"):
        mt.binary_metrics(np.array([1, 0]), np.array([0.5, np.nan]), 0.5)
    with pytest.raises(ValueError, match="positive"):
        mt.best_f1_threshold(np.array([0, 0]), np.array([0.5, 0.5]))


# ---- Calibration --------------------------------------------------------------------------------


def test_isotonic_calibrator_is_monotone_clipped_and_round_trips() -> None:
    rng = np.random.default_rng(0)
    s = rng.random(2000)
    y = (rng.random(2000) < s**2).astype(int)
    cal = IsotonicCalibrator.fit(s, y)
    grid = np.linspace(-1, 2, 301)
    out = cal.predict(grid)
    assert (np.diff(out) >= 0).all()
    assert out.min() >= PROBABILITY_CLIP and out.max() <= 1 - PROBABILITY_CLIP
    again = IsotonicCalibrator.from_dict(json.loads(json.dumps(cal.to_dict())))
    assert np.array_equal(again.predict(grid), out)
    # The thresholds reproduce sklearn's isotonic predictions (clipped).
    iso = IsotonicRegression(out_of_bounds="clip").fit(s, y)
    expected = np.clip(iso.predict(grid), PROBABILITY_CLIP, 1 - PROBABILITY_CLIP)
    assert np.allclose(out, expected)


@pytest.mark.filterwarnings("ignore:Confidence interval of the Spearman")  # 4 points only
def test_calibrator_handles_missing_scores_and_decreasing_features() -> None:
    s = np.array([np.nan, np.nan, np.nan, 0.0, 1.0, 2.0, 3.0])
    y = np.array([1, 1, 0, 1, 1, 0, 0])
    cal = IsotonicCalibrator.fit(s, y, increasing="auto")
    assert cal.predict(np.array([np.nan]))[0] == pytest.approx(2 / 3)
    out = cal.predict(np.array([0.0, 3.0]))
    assert out[0] > out[1]  # direction found from the data
    full = IsotonicCalibrator.fit(np.array([0.0, 1.0]), np.array([0, 1]))
    assert full.missing_probability == 0.5  # no missing score: the overall rate
    with pytest.raises(ValueError, match="every score is missing"):
        IsotonicCalibrator.fit(np.array([np.nan]), np.array([1]))
    with pytest.raises(ValueError, match="not a"):
        IsotonicCalibrator.from_dict({"format": "other"})


# ---- Baseline -----------------------------------------------------------------------------------


def test_baseline_picks_the_best_single_feature_rule() -> None:
    rng = np.random.default_rng(1)
    n = 3000
    y = (rng.random(n) < 0.5).astype(int)
    good = np.where(y == 1, rng.uniform(0.3, 1.0, n), rng.uniform(0.0, 0.5, n))
    good[:100] = np.nan  # missing values, mostly returned below
    y[:100] = (rng.random(100) < 0.9).astype(int)
    values = {name: rng.random(n) for name in bl.CANDIDATES}
    values["variant_loo_return_rate"] = good
    best, rules = bl.fit_baseline(values, y)
    assert best.feature == "variant_loo_return_rate"
    assert [r.feature for r in rules] == list(bl.CANDIDATES)
    assert best.validation_f1 == max(r.validation_f1 for r in rules)
    assert best.rule.startswith("returned if variant_loo_return_rate >= ")
    assert best.rule.endswith("missing -> returned")
    assert json.loads(json.dumps(best.to_dict()))["feature"] == "variant_loo_return_rate"


def test_candidate_values_are_decision_time_features() -> None:
    f = joined(*hand_tables())
    values = bl.candidate_values(f)
    assert values["customer_loo_return_rate"][0] == pytest.approx(1 / 3)  # C1: 1 of 3 others
    assert math.isnan(values["customer_loo_return_rate"][3])  # no customer row
    assert values["variant_returns_loo"][0] == 4


# ---- Training (device argument for benchmark B1) -------------------------------------------------


def small_xy(seed: int = 0, n: int = 600) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 3)).astype(np.float32)
    x[rng.random(n) < 0.1, 1] = np.nan
    y = (x[:, 0] + rng.normal(scale=0.7, size=n) > 0).astype(int)
    return x, y


def test_train_booster_on_cpu_keeps_the_best_trees() -> None:
    x, y = small_xy()
    names = ["f_a", "f_b", "f_c"]
    out = tr.train_booster(x[:500], y[:500], x[500:], y[500:], names, seed=1, threads=1)
    assert out.threads == 1
    assert out.device_requested == "cpu" and out.device_effective == "cpu"
    assert out.booster.num_boosted_rounds() == out.best_iteration + 1
    assert out.rounds_run <= out.best_iteration + 1 + tr.EARLY_STOPPING_ROUNDS
    assert out.booster.feature_names == ["f_a", "f_b", "f_c"]
    assert out.summary()["trees_kept"] == out.best_iteration + 1
    p = tr.predict(out.booster, x[500:])
    assert p.shape == (100,) and ((p > 0) & (p < 1)).all()
    config = json.loads(out.booster.save_config())["learner"]["gradient_booster"]
    assert config["tree_train_param"]["max_depth"] == "4"
    again = tr.train_booster(x[:500], y[:500], x[500:], y[500:], names, seed=1, threads=1)
    assert np.array_equal(tr.predict(again.booster, x[500:]), p)  # deterministic on CPU


def test_train_booster_validates_the_device_and_rounds() -> None:
    x, y = small_xy()
    with pytest.raises(ValueError, match="device"):
        tr.train_booster(x, y, x, y, ["a", "b", "c"], device="hip")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_rounds"):
        tr.train_booster(x, y, x, y, ["a", "b", "c"], max_rounds=0)


def test_a_gpu_run_that_fell_back_to_cpu_is_refused() -> None:
    tr.check_device("cpu", "cpu")
    tr.check_device("cuda", "cuda:0")
    with pytest.raises(RuntimeError, match="GPU not used"):
        tr.check_device("cuda", "cpu")


def test_paper_hyperparameters() -> None:
    assert dict(tr.PAPER_PARAMS) == {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "max_depth": 4,
        "eta": 0.1,
        "gamma": 1.0,
        "min_child_weight": 1.0,
        "lambda": 2.0,
    }
    assert tr.EARLY_STOPPING_ROUNDS == 5


# ---- End to end on the replica ------------------------------------------------------------------


@pytest.fixture(scope="module")
def replica_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("propensity")
    raw = write_raw(root / "data", *random_raw(seed=11, events_per_split=3000))
    out, doc = root / "artifacts", root / "docs" / "us-201.md"
    # One thread: tiny matrices train faster alone than on CPUs shared with other jobs.
    args = ["--raw", str(raw), "--out", str(out), "--doc", str(doc), "--max-rounds", "60"]
    code = cli.main([*args, "--seed", "42", "--device", "cpu", "--threads", "1"])
    assert code == 0
    metrics = json.loads((out / "metrics" / "us-201-propensity.json").read_text())
    return out, doc, metrics


def test_cli_writes_models_calibrators_metrics_and_report(
    replica_run: tuple[Path, Path, dict[str, Any]],
) -> None:
    out, doc, metrics = replica_run
    for protocol in ("A", "B"):
        booster = xgb.Booster(model_file=str(out / "models" / f"propensity_{protocol}.json"))
        assert forbidden_features(booster.feature_names or []) == []
        assert booster.attr("returnring_protocol") == protocol
        assert json.loads(booster.attr("returnring_provenance") or "")["nature"] == "synthetic"
        cal = json.loads((out / "models" / f"propensity_{protocol}_calibrator.json").read_text())
        assert IsotonicCalibrator.from_dict(cal).predict(np.array([0.5]))[0] > 0
        assert cal["protocol_label"] == ft.PROTOCOL_LABELS[protocol]
    assert not (out / "models" / "propensity_B_all_events.json").exists()
    rule = json.loads((out / "models" / "propensity_baseline.json").read_text())
    assert rule["feature"] in bl.CANDIDATES
    assert metrics["provenance"]["nature"] == "synthetic"
    assert set(metrics["models"]) == {"A", "B", "B_all_events"}
    for model in [*metrics["models"].values(), metrics["baseline"]]:
        assert set(model["test"]) == set(ft.POPULATIONS)
    assert metrics["models"]["B"]["trained_on"] == "decision_time"
    for split in ("train", "test"):
        own = metrics["data"][split]["selected_by_own_return"]
        assert own["consistent_counts"]["events"] + own["clipped_counts"]["events"] == own["events"]
        assert own["consistent_counts"]["returned_share"] == 1.0
        assert metrics["data"][split]["decision_time_events"] == (
            metrics["data"][split]["events"] - own["events"]
        )
    go = metrics["go_no_go"]
    assert set(go["checks"]) == {
        "b_f1_beats_baseline",
        "b_cross_entropy_beats_baseline",
        "b_f1_beats_every_candidate",
        "b_cross_entropy_beats_every_candidate",
        "b_ece_within_mod06",
    }
    assert go["go"] == all(go["checks"].values())
    b = metrics["models"]["B"]["test"]["decision_time"]
    assert go["checks"]["b_f1_beats_baseline"] == (
        b["f1"] > metrics["baseline"]["test"]["decision_time"]["f1"]
    )
    text = doc.read_text()
    assert "Nature: synthetic" in text and "nature: real" not in text.lower()
    assert ft.PROTOCOL_LABELS["A"] in text
    assert "Decision-time test population" in text and "Fully featured test subset" in text


def test_every_number_is_labelled_with_its_provenance(
    replica_run: tuple[Path, Path, dict[str, Any]],
) -> None:
    _, _, metrics = replica_run
    label = metrics["labels"]["test"]
    assert label["period"] == "2021-10-01..2021-11-30" and label["nature"] == "synthetic"
    assert metrics["labels"]["train"]["period"] == "2021-09-01..2021-10-31"
    for model in [*metrics["models"].values(), metrics["baseline"]]:
        assert model["protocol_label"] in ft.PROTOCOL_LABELS.values()
        assert model["labels"] == {
            "origin": metrics["provenance"]["origin"],
            "nature": "synthetic",
            "split": "test",
            "period": "2021-10-01..2021-11-30",
            "protocol": model["protocol_label"],
        }


def test_the_run_is_reproducible_apart_from_timings(
    replica_run: tuple[Path, Path, dict[str, Any]], tmp_path: Path
) -> None:
    out, _, first = replica_run
    raw = out.parent / "data" / "raw"
    second = run(raw, tmp_path / "artifacts", None, RunConfig(max_rounds=60, threads=1))

    def strip(doc: dict[str, Any]) -> dict[str, Any]:
        doc = json.loads(json.dumps(doc))
        doc.pop("argv")
        for model in doc["models"].values():
            for key in (
                "dmatrix_seconds",
                "train_seconds",
                "load_average_before",
                "load_average_after",
            ):
                model["training"].pop(key)
        return doc

    assert strip(first) == strip(second)


def test_training_data_is_what_the_run_trained_on(
    replica_run: tuple[Path, Path, dict[str, Any]],
) -> None:
    """Benchmark B1 times train_booster on these matrices: they must be the run's own."""
    out, _, metrics = replica_run
    raw = out.parent / "data" / "raw"
    for protocol in ("A", "B"):
        data = training_data(raw, protocol)
        summary = metrics["models"][protocol]["training"]
        assert (data.y_fit.size, data.y_valid.size) == (summary["rows_fit"], summary["rows_valid"])
        assert data.feature_names == metrics["models"][protocol]["features"]
        assert data.x_fit.shape == (data.y_fit.size, len(data.feature_names))
    trained = tr.train_booster(
        data.x_fit,
        data.y_fit,
        data.x_valid,
        data.y_valid,
        data.feature_names,
        max_rounds=5,
        threads=1,
    )
    assert trained.device_effective == "cpu"


def test_run_refuses_a_folder_that_is_not_raw(tmp_path: Path) -> None:
    with pytest.raises(ft.FeatureError, match="raw/ folder"):
        run(tmp_path, tmp_path / "out", None)


def test_the_cli_device_follows_the_device_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICE", "gpu")
    assert cli._default_device() == "cuda"
    monkeypatch.setenv("DEVICE", "cpu")
    assert cli._default_device() == "cpu"
