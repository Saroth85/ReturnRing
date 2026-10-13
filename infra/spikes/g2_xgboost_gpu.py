"""SPK-G2: same data and parameters on CPU and on the MI300X (benchmark rules, spec section 12)."""

from __future__ import annotations

import time

import numpy as np
import xgboost as xgb

rng = np.random.default_rng(42)
X = rng.normal(size=(1_400_000, 40)).astype(np.float32)
y = (X[:, 0] + 0.5 * X[:, 1] + rng.normal(scale=0.5, size=len(X)) > 0).astype(np.int32)
dtrain = xgb.DMatrix(X, label=y)

for device in ("cpu", "cuda"):
    params = {
        "tree_method": "hist",
        "device": device,
        "max_depth": 4,
        "eta": 0.1,
        "objective": "binary:logistic",
    }
    start = time.perf_counter()
    xgb.train(params, dtrain, num_boost_round=200)
    print(f"{device}: {time.perf_counter() - start:.1f} s for 200 rounds on {len(X):,} rows")
