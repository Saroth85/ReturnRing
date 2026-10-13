"""Risk models (US-204; MOD-05, MOD-06): XGBoost with isotonic calibration, the validation
threshold, native TreeSHAP and the saved artifacts. Trained on small random tables (made-up test
fixtures, nature: synthetic)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.risk.features import A1
from ml.risk.metrics import precision_recall_at
from ml.risk.model import RiskModel, TrainConfig, fit_rules, fit_xgboost

T = datetime(2026, 7, 1, tzinfo=UTC)


def table(n: int, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
    """A1 features drawn at random; abusive customers return more and faster."""
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.15).astype(int)
    frame = pd.DataFrame({name: rng.random(n) for name in A1.features})
    frame["return_rate_to_date"] = np.clip(0.25 + 0.5 * y + rng.normal(0, 0.15, n), 0, 1)
    frame["median_days_to_return"] = np.where(y == 1, 3.0, 8.0) + rng.normal(0, 2, n)
    frame.loc[rng.random(n) < 0.1, "return_rate"] = np.nan  # missing values are allowed
    frame.insert(0, "customer_id", [f"C{i:05d}" for i in range(n)])
    return frame, y


@pytest.fixture(scope="module")
def trained() -> tuple[RiskModel, pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray]:
    train, y_train = table(3000, 1)
    val, y_val = table(1000, 2)
    model = fit_xgboost("A1", A1.features, train, y_train, val, y_val, T, TrainConfig())
    return model, train, y_train, val, y_val


def test_calibrated_risk_and_the_validation_threshold(
    trained: tuple[RiskModel, pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray],
) -> None:
    model, _, _, val, y_val = trained
    risk = model.predict(val)
    assert ((risk > 0) & (risk < 1)).all()
    assert model.threshold is not None
    precision, _, flagged = precision_recall_at(y_val, risk, model.threshold)
    assert flagged > 0 and precision >= 0.8  # spec 8.6, on the validation split
    assert model.sizes == {
        "train_rows": 3000,
        "train_positives": int(trained[2].sum()),
        "validation_rows": 1000,
        "validation_positives": int(y_val.sum()),
    }
    assert model.version == "risk-A1-xgboost-v1@2026-07-01T00:00Z"


def test_treeshap_contributions_add_up_to_the_raw_margin(
    trained: tuple[RiskModel, pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray],
) -> None:
    model, _, _, val, _ = trained
    contributions = model.contributions(val.head(50))
    assert contributions.shape == (50, len(A1.features) + 1)
    margin = np.log(model.raw(val.head(50)) / (1 - model.raw(val.head(50))))
    assert np.allclose(contributions.sum(axis=1), margin, atol=1e-4)


def test_saved_models_predict_the_same(
    trained: tuple[RiskModel, pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray],
    tmp_path: Path,
) -> None:
    model, _, _, val, _ = trained
    paths = model.save(tmp_path)
    assert [p.name for p in paths] == ["A1.json", "A1.calibrator.json", "A1.meta.json"]
    again = RiskModel.load(tmp_path, "A1")
    assert again.features == model.features and again.threshold == model.threshold
    assert np.array_equal(again.predict(val), model.predict(val))
    assert np.allclose(again.contributions(val.head(5)), model.contributions(val.head(5)))


def test_training_is_deterministic(
    trained: tuple[RiskModel, pd.DataFrame, np.ndarray, pd.DataFrame, np.ndarray],
) -> None:
    model, train, y_train, val, y_val = trained
    again = fit_xgboost("A1", A1.features, train, y_train, val, y_val, T, TrainConfig())
    assert again.booster.save_raw("json") == model.booster.save_raw("json")
    assert again.threshold == model.threshold


def test_a_training_set_needs_both_classes() -> None:
    train, _ = table(200, 3)
    with pytest.raises(ValueError, match="both classes"):
        fit_xgboost("A1", A1.features, train, np.zeros(200), train, np.zeros(200), T)


def test_forbidden_or_value_features_are_refused_before_training() -> None:
    train, y = table(200, 4)
    train["value_eur"] = 1.0
    with pytest.raises(ValueError, match="rule 6"):
        fit_xgboost("X", (*A1.features, "value_eur"), train, y, train, y, T)


def test_the_rule_model_is_calibrated_like_the_others(tmp_path: Path) -> None:
    train, y_train = table(2000, 5)
    val, y_val = table(800, 6)
    model = fit_rules("A0", train, y_train, val, y_val, T)
    risk = model.predict(val)
    assert len(np.unique(model.raw(val))) <= 4  # 0, 1/3, 2/3, 1
    assert np.all(np.diff(risk[np.argsort(model.raw(val), kind="stable")]) >= 0)  # monotone
    names = [p.name for p in model.save(tmp_path)]
    assert names == ["A0.rules.json", "A0.calibrator.json", "A0.meta.json"]
