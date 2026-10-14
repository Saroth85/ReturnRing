"""Files of a fitted line predictor, for the services that run it (ADR-16; spec 8.1, US-401).

The checkout prices the lines of a cart with the personalized predictor (ml.reference.checkout).
The API never trains: the serving job (python -m pipeline.serving) fits the predictor at the
snapshot instant and writes it with save_line_model; the API reads it with load_line_model.

A model is three things, all JSON, so the files are deterministic and diffable:

- <role>.meta.json: role, kind, encoding (k, categories, size ranges), trained_at, n_train,
  best_iteration, notes, the isotonic thresholds (x, y) when calibrated, and the version;
- <role>.json: the XGBoost booster (save_raw JSON), absent for the smoothed fallback.

The isotonic map is rebuilt by fitting IsotonicRegression on its own thresholds, in the dtype it
was fitted in: they are already monotone, so the refit keeps them and predict() interpolates
between the same points, bit for bit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression

from contracts.files import atomic_write
from ml.reference.features import Encoding
from ml.reference.model import LineModel, Role

FORMAT: Final = "returnring.line_model.v1"
ROLES: Final = ("reference", "personalized")


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write(path, lambda out: out.write(data))


def meta_path(directory: Path, role: Role) -> Path:
    return directory / f"{role}.meta.json"


def booster_path(directory: Path, role: Role) -> Path:
    return directory / f"{role}.json"


def save_line_model(model: LineModel, directory: Path) -> list[Path]:
    """Write `model` under `directory`; returns the files written (meta first)."""
    iso = model.isotonic
    meta: dict[str, Any] = {
        "format": FORMAT,
        "role": model.role,
        "kind": model.kind,
        "version": model.version,
        "features": list(model.features),
        "encoding": {
            "k": model.encoding.k,
            "categories": list(model.encoding.categories),
            "size_ranges": list(model.encoding.size_ranges),
        },
        "trained_at": model.trained_at.isoformat(),
        "n_train": model.n_train,
        "best_iteration": model.best_iteration,
        "isotonic": (
            None
            if iso is None
            else {
                "x": [float(v) for v in iso.X_thresholds_],
                "y": [float(v) for v in iso.y_thresholds_],
                "dtype": str(np.asarray(iso.X_thresholds_).dtype),
            }
        ),
        "notes": list(model.notes),
    }
    text = json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    written = [_write(meta_path(directory, model.role), text.encode("utf-8"))]
    target = booster_path(directory, model.role)
    if model.booster is not None:
        written.append(_write(target, bytes(model.booster.save_raw(raw_format="json"))))
    else:
        target.unlink(missing_ok=True)
    return written


def _isotonic(data: dict[str, Any] | None) -> IsotonicRegression | None:
    if data is None:
        return None
    dtype = np.dtype(str(data.get("dtype", "float64")))
    if dtype not in (np.dtype(np.float32), np.dtype(np.float64)):
        raise ValueError(f"isotonic thresholds of dtype {dtype}")
    x = np.asarray(data["x"], dtype=dtype)  # the dtype it was fitted in: the same predictions
    y = np.asarray(data["y"], dtype=dtype)
    if x.ndim != 1 or x.shape != y.shape or len(x) == 0:
        raise ValueError("isotonic thresholds: x and y of the same non-zero length")
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(x, y)
    return iso


def load_line_model(directory: Path, role: Role) -> LineModel:
    """The model saved under `directory` for `role` (ValueError on a wrong file)."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    meta = json.loads(meta_path(directory, role).read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or meta.get("format") != FORMAT or meta.get("role") != role:
        raise ValueError(f"{meta_path(directory, role)} is not a {role} line model ({FORMAT})")
    encoding = Encoding(
        k=float(meta["encoding"]["k"]),
        categories=tuple(str(c) for c in meta["encoding"]["categories"]),
        size_ranges=tuple(str(c) for c in meta["encoding"]["size_ranges"]),
    )
    booster: xgb.Booster | None = None
    if meta["kind"] == "xgboost":
        booster = xgb.Booster()
        booster.load_model(str(booster_path(directory, role)))
        booster.set_param({"nthread": 1})
    elif meta["kind"] != "smoothed":
        raise ValueError(f"unknown line model kind {meta['kind']!r}")
    model = LineModel(
        role=role,
        kind=meta["kind"],
        encoding=encoding,
        trained_at=datetime.fromisoformat(str(meta["trained_at"])),
        n_train=int(meta["n_train"]),
        booster=booster,
        best_iteration=None if meta["best_iteration"] is None else int(meta["best_iteration"]),
        isotonic=_isotonic(meta["isotonic"]),
        notes=[str(n) for n in meta["notes"]],
    )
    if model.version != meta["version"] or list(model.features) != meta["features"]:
        raise ValueError(f"{meta_path(directory, role)}: version or features do not match")
    return model
