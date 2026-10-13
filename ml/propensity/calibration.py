"""Isotonic calibration of a score into a probability, stored as plain JSON (US-201, MOD-06).

Fitted with sklearn's IsotonicRegression on the validation split; only its thresholds are kept,
so applying it needs numpy alone: linear interpolation between the thresholds, constant beyond
them (out_of_bounds="clip"), the same as IsotonicRegression.predict. The probability is clipped to
[clip, 1 - clip]: a calibrated model never claims certainty, and the cross-entropy stays finite.

A missing score (NaN: a single-feature rule whose feature is null) gets missing_probability, the
return rate of the validation events with a missing score, or their overall rate when none is
missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from sklearn.isotonic import IsotonicRegression

PROBABILITY_CLIP = 1e-4
FORMAT = "returnring.isotonic.v1"


@dataclass(frozen=True)
class IsotonicCalibrator:
    x: tuple[float, ...]
    y: tuple[float, ...]
    missing_probability: float
    clip: float = PROBABILITY_CLIP

    def __post_init__(self) -> None:
        if not self.x or len(self.x) != len(self.y):
            raise ValueError("a calibrator needs as many y as x thresholds, at least one")
        if any(b < a for a, b in zip(self.x, self.x[1:], strict=False)):
            raise ValueError("calibrator x thresholds must be sorted")
        if not 0.0 < self.clip < 0.5:
            raise ValueError(f"clip must be in (0, 0.5), not {self.clip}")

    @classmethod
    def fit(
        cls,
        scores: npt.ArrayLike,
        labels: npt.ArrayLike,
        increasing: bool | Literal["auto"] = True,
        clip: float = PROBABILITY_CLIP,
    ) -> IsotonicCalibrator:
        s = np.asarray(scores, dtype=np.float64)
        y = np.asarray(labels, dtype=np.float64)
        if s.shape != y.shape or s.ndim != 1:
            raise ValueError("scores and labels must be 1-D arrays of the same length")
        known = ~np.isnan(s)
        if not known.any():
            raise ValueError("no score to calibrate: every score is missing")
        iso = IsotonicRegression(out_of_bounds="clip", increasing=increasing)
        iso.fit(s[known], y[known])
        missing = y[~known].mean() if (~known).any() else y.mean()
        return cls(
            x=tuple(float(v) for v in iso.X_thresholds_),
            y=tuple(float(v) for v in iso.y_thresholds_),
            missing_probability=float(missing),
            clip=clip,
        )

    def predict(self, scores: npt.ArrayLike) -> npt.NDArray[np.float64]:
        s = np.asarray(scores, dtype=np.float64)
        known = ~np.isnan(s)
        out = np.full(s.shape, self.missing_probability, dtype=np.float64)
        out[known] = np.interp(s[known], np.array(self.x), np.array(self.y))
        return np.clip(out, self.clip, 1.0 - self.clip)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "method": "isotonic regression (sklearn), linear interpolation, clipped",
            "x_thresholds": list(self.x),
            "y_thresholds": list(self.y),
            "missing_probability": self.missing_probability,
            "clip": self.clip,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IsotonicCalibrator:
        if data.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} calibrator: {data.get('format')!r}")
        return cls(
            x=tuple(float(v) for v in data["x_thresholds"]),
            y=tuple(float(v) for v in data["y_thresholds"]),
            missing_probability=float(data["missing_probability"]),
            clip=float(data["clip"]),
        )
