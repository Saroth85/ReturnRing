"""Feature standardisation for the GraphSAGE inputs (no torch).

log1p first for skewed non-negative columns (counts, degrees, prices, durations), then
(x - mean) / std with the mean and deviation of the fitting rows; a missing value (NaN) becomes 0,
the mean, next to the flag that tells it is missing. The fitted values are stored with the model,
so an encoder applied at a later decision time transforms its inputs the same way.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

F32 = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Standardizer:
    names: tuple[str, ...]
    log: tuple[bool, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]

    @classmethod
    def fit(
        cls, x: npt.ArrayLike, names: Sequence[str], is_log: Callable[[str], bool]
    ) -> Standardizer:
        matrix = np.array(x, dtype=np.float64)
        if matrix.ndim != 2 or matrix.shape[1] != len(names):
            raise ValueError(f"expected a matrix with {len(names)} columns, got {matrix.shape}")
        log = tuple(is_log(n) for n in names)
        _log1p_inplace(matrix, log)
        known = ~np.isnan(matrix)
        count = known.sum(axis=0)
        filled = np.where(known, matrix, 0.0)
        mean = np.divide(filled.sum(axis=0), count, out=np.zeros(len(names)), where=count > 0)
        square = np.where(known, (matrix - mean) ** 2, 0.0).sum(axis=0)
        var = np.divide(square, count, out=np.ones(len(names)), where=count > 0)
        std = np.sqrt(var)
        std = np.where(std > 1e-12, std, 1.0)
        return cls(tuple(names), log, tuple(float(v) for v in mean), tuple(float(v) for v in std))

    def transform(self, x: npt.ArrayLike) -> F32:
        z = np.array(x, dtype=np.float32)
        if z.ndim != 2 or z.shape[1] != len(self.names):
            raise ValueError(f"expected {len(self.names)} columns, got {z.shape}")
        _log1p_inplace(z, self.log)
        z -= np.asarray(self.mean, dtype=np.float32)
        z /= np.asarray(self.std, dtype=np.float32)
        np.nan_to_num(z, copy=False, nan=0.0)
        return np.ascontiguousarray(z)

    def as_dict(self) -> dict[str, Any]:
        return {
            "names": list(self.names),
            "log1p": list(self.log),
            "mean": list(self.mean),
            "std": list(self.std),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Standardizer:
        names = tuple(str(n) for n in data["names"])
        out = cls(
            names,
            tuple(bool(v) for v in data["log1p"]),
            tuple(float(v) for v in data["mean"]),
            tuple(float(v) for v in data["std"]),
        )
        if not len(out.log) == len(out.mean) == len(out.std) == len(names):
            raise ValueError("a standardiser needs one log flag, mean and std per name")
        return out


def _log1p_inplace(z: npt.NDArray[np.floating[Any]], log: tuple[bool, ...]) -> None:
    columns = np.flatnonzero(np.asarray(log, dtype=bool))
    if columns.size:
        z[:, columns] = np.log1p(np.clip(z[:, columns], 0.0, None))
