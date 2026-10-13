"""Convert the ASOS GraphReturns pickles to the raw/asos_* Parquet contracts (US-101).

Loading a pickle runs code (ADR-10, review findings ASOS-3, ASOS-6, ASOS-11). So each file is:

1. read once, with its size capped at the pinned size;
2. checked against the sha256 pinned in asos_download.EXPECTED, on the very bytes that are
   then unpickled (no second read of the file);
3. unpickled by RestrictedUnpickler. Only the 11 globals that the six OSF files reference
   resolve, and persistent ids are refused.

The files were written by pandas < 2 and numpy < 2. RestrictedUnpickler builds on pandas' own
compatibility unpickler (pandas.compat.pickle_compat), which rebuilds the old BlockManager. It
maps the old names to their pandas 3 and numpy 2 equivalents: Int64Index becomes Index,
numpy.core becomes numpy._core.

to_table() is a pure function from a source DataFrame to a contract table (ADR-13 section 4):

- it renames the columns and adds the split;
- it keeps both reason labels "D" by position (D and D2);
- it drops the one-hot columns after checking that each one repeats its categorical column.

Anything unexpected fails closed: an unknown column, a missing one, a value that does not fit
the contract type.
"""

from __future__ import annotations

import hashlib
import io
import pickle
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn

import numpy as np
import pandas as pd
import pandas._libs.internals as pandas_internals
import pyarrow as pa
import pyarrow.compute as pc
from numpy._core.multiarray import _reconstruct
from pandas.compat import pickle_compat
from pandas.core.indexes.base import _new_Index
from pandas.core.internals.managers import BlockManager

from contracts.io import Provenance, write_table
from contracts.parquet import ASOS_REASON_CODES, ASOS_SPLITS, SCHEMAS
from pipeline.ingest.asos_download import (
    IntegrityError,
    Pinned,
    pinned_files,
    target_path,
    verify,
)

# ---- Provenance (rule 11) -----------------------------------------------------------------------

ORIGIN = "ASOS GraphReturns, McGowan et al. 2023, arXiv:2302.14096, OSF c793h, CC BY 4.0"
# The paper gives months: train September-October 2021, test October-November 2021.
SPLIT_PERIODS: Mapping[str, tuple[date, date]] = MappingProxyType(
    {
        "train": (date(2021, 9, 1), date(2021, 10, 31)),
        "test": (date(2021, 10, 1), date(2021, 11, 30)),
    }
)
PROVENANCE = Provenance(
    origin=ORIGIN
    + "; split periods per the paper: "
    + ", ".join(f"{s} {a.isoformat()}..{b.isoformat()}" for s, (a, b) in SPLIT_PERIODS.items()),
    period_start=SPLIT_PERIODS["train"][0],
    period_end=SPLIT_PERIODS["test"][1],
    nature="real",
)

# ---- Restricted unpickling ----------------------------------------------------------------------

# The complete set of globals in the six OSF files (ADR-10), with what each resolves to here.
ALLOWED_GLOBALS: Mapping[tuple[str, str], object] = MappingProxyType(
    {
        ("builtins", "slice"): slice,
        ("numpy", "ndarray"): np.ndarray,
        ("numpy", "dtype"): np.dtype,
        ("numpy.core.multiarray", "_reconstruct"): _reconstruct,  # numpy 2: numpy._core
        ("pandas._libs.internals", "_unpickle_block"): pandas_internals._unpickle_block,
        ("pandas.core.frame", "DataFrame"): pd.DataFrame,
        ("pandas.core.internals.managers", "BlockManager"): BlockManager,
        ("pandas.core.indexes.base", "_new_Index"): _new_Index,
        ("pandas.core.indexes.base", "Index"): pd.Index,
        ("pandas.core.indexes.range", "RangeIndex"): pd.RangeIndex,
        # Removed in pandas 2; pandas.compat.pickle_compat maps it to Index as well.
        ("pandas.core.indexes.numeric", "Int64Index"): pd.Index,
    }
)


class RestrictedUnpickler(pickle_compat.Unpickler):  # type: ignore[misc]
    """pandas' compatibility unpickler, restricted to ALLOWED_GLOBALS and without persistent ids."""

    def find_class(self, module: str, name: str) -> object:
        try:
            return ALLOWED_GLOBALS[(module, name)]
        except KeyError:
            raise pickle.UnpicklingError(
                f"global {module}.{name} is not allowed (ADR-10 allowlist)"
            ) from None

    def persistent_load(self, pid: object) -> NoReturn:
        raise pickle.UnpicklingError("persistent ids are not allowed")


def load_pinned(path: Path, expected: Mapping[str, Pinned] | None = None) -> pd.DataFrame:
    """Unpickle a pinned ASOS file, only if its bytes match the pin; the frame it holds."""
    files = pinned_files(expected)
    if path.name not in files:
        raise ValueError(f"unknown ASOS file {path.name!r}: expected one of {sorted(files)}")
    pin = files[path.name]
    with path.open("rb") as f:
        data = f.read(pin.size + 1)
    if len(data) != pin.size:
        raise IntegrityError(f"{path}: not the pinned size of {pin.size:,} bytes")
    digest = hashlib.sha256(data).hexdigest()
    if digest != pin.sha256:
        raise IntegrityError(f"{path}: sha256 {digest}, expected {pin.sha256}")
    loaded = RestrictedUnpickler(io.BytesIO(data)).load()
    if type(loaded) is not pd.DataFrame:
        raise pickle.UnpicklingError(f"{path}: not a pandas DataFrame")
    return loaded


# ---- Source layouts (ADR-13 section 4) ----------------------------------------------------------


class ConversionError(ValueError):
    """Source data that does not have the expected layout or values."""


@dataclass(frozen=True)
class Layout:
    """How the columns of one kind of source file map to a raw contract."""

    columns: Mapping[str, str]  # source label -> contract column
    reason_prefix: str | None = None  # the 13 reason shares, in source order
    one_hot: Mapping[str, str] = field(default_factory=dict)  # prefix -> categorical label


# The reason labels in source order: "D" appears twice (ADR-13).
SOURCE_REASON_LABELS = ("A", "B", "C", "D", "E", "D", "F", "G", "H", "I", "J", "K", "L")

LAYOUTS: Mapping[str, Layout] = MappingProxyType(
    {
        "raw/asos_events": Layout(
            {
                "hash(variantID)": "variant_hash",
                "hash(customerId)": "customer_hash",
                "isReturned": "is_returned",
            }
        ),
        "raw/asos_customers": Layout(
            {
                "hash(customerId)": "customer_hash",
                "salesPerCustomer": "sales",
                "returnsPerCustomer": "returns",
                "customerReturnRate": "return_rate",
                "premier": "premier",
                # Descriptive only, never a feature (rule 6, ADR-11).
                "isMale": "is_male",
                "shippingCountry": "shipping_country",
                "yearOfBirth": "year_of_birth",
            },
            reason_prefix="customerId_level_return_code_",
            one_hot={"Country_": "shippingCountry"},
        ),
        "raw/asos_variants": Layout(
            {
                "hash(variantID)": "variant_hash",
                "hash(productID)": "product_hash",
                "hash(supplierRef)": "supplier_hash",
                "productType": "product_type",
                "brandDesc": "brand",
                "avgGbpPrice": "avg_price_gbp",
                "avgDiscountValue": "avg_discount",
                "salesPerProduct": "sales",
                "returnsPerProduct": "returns",
                "productReturnRate": "return_rate",
            },
            reason_prefix="variantID_level_return_code_",
            one_hot={"Brand_": "brandDesc", "productType_": "productType"},
        ),
    }
)

# The OSF file of each contract and split.
SOURCES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "raw/asos_events": {"train": "event_table_training.p", "test": "event_table_testing.p"},
        "raw/asos_customers": {
            "train": "customer_nodes_training.p",
            "test": "customer_nodes_testing.p",
        },
        "raw/asos_variants": {
            "train": "product_nodes_training.p",
            "test": "product_nodes_testing.p",
        },
    }
)


def _is_one_hot(label: str, layout: Layout) -> bool:
    return any(label.startswith(p) and len(label) > len(p) for p in layout.one_hot)


def _positions(labels: list[str], layout: Layout) -> dict[str, int]:
    """Contract column -> position of its source column; reason codes are matched by position."""
    found: dict[str, int] = {}
    reasons: list[tuple[str, int]] = []
    unexpected: list[str] = []
    for pos, label in enumerate(labels):
        if label in layout.columns:
            if layout.columns[label] in found:
                raise ConversionError(f"repeated source column {label!r}")
            found[layout.columns[label]] = pos
        elif layout.reason_prefix is not None and label.startswith(layout.reason_prefix):
            reasons.append((label.removeprefix(layout.reason_prefix), pos))
        elif not _is_one_hot(label, layout):
            unexpected.append(label)
    if unexpected:
        raise ConversionError(f"unexpected source columns {unexpected}")
    missing = [label for label, column in layout.columns.items() if column not in found]
    if missing:
        raise ConversionError(f"missing source columns {missing}")
    if layout.reason_prefix is not None:
        codes = tuple(code for code, _ in reasons)
        if codes != SOURCE_REASON_LABELS:
            raise ConversionError(
                f"reason codes {list(codes)}, expected {list(SOURCE_REASON_LABELS)} in this order"
            )
        for code, (_, pos) in zip(ASOS_REASON_CODES, reasons, strict=True):
            found[f"reason_share_{code}"] = pos
    return found


def _check_one_hot(frame: pd.DataFrame, labels: list[str], layout: Layout) -> None:
    """Each one-hot column is 0/1 and is the indicator of one value of its categorical column,
    with no two columns for the same value, so that dropping it loses nothing."""
    for prefix, category_label in layout.one_hot.items():
        category = frame.iloc[:, labels.index(category_label)]
        indicated: dict[object, str] = {}
        for pos, label in enumerate(labels):
            if not (label.startswith(prefix) and len(label) > len(prefix)):
                continue
            column = frame.iloc[:, pos]
            ones = column == 1
            if not bool((ones | (column == 0)).all()):
                raise ConversionError(f"{label}: values other than 0 and 1")
            values = pd.unique(category[ones])
            if len(values) == 0:  # never set: repeats nothing, loses nothing
                continue
            value = values[0]
            if len(values) > 1 or not bool((ones == (category == value)).all()):
                raise ConversionError(f"{label} does not repeat {category_label}")
            if value in indicated:
                raise ConversionError(f"{label} and {indicated[value]} both mark {value!r}")
            indicated[value] = label


def _boolean(series: pd.Series, label: str) -> pa.Array:
    if pd.api.types.is_bool_dtype(series.dtype):
        return pa.Array.from_pandas(series).cast(pa.bool_())
    if not pd.api.types.is_integer_dtype(series.dtype):
        raise ConversionError(f"{label}: 0/1 integers expected, found {series.dtype}")
    array = pa.Array.from_pandas(series)
    if pc.any(pc.and_(pc.not_equal(array, 0), pc.not_equal(array, 1))).as_py():
        raise ConversionError(f"{label}: values other than 0 and 1")
    return pc.equal(array, 1)


def _arrow(series: pd.Series, label: str, target: pa.DataType) -> pa.Array:
    """One source column as an Arrow array of the contract type; NaN becomes null."""
    if pa.types.is_boolean(target):
        return _boolean(series, label)
    if pa.types.is_integer(target) and not pd.api.types.is_integer_dtype(series.dtype):
        raise ConversionError(f"{label}: integer column expected, found {series.dtype}")
    try:
        array = pa.Array.from_pandas(series)
        kind = array.type
        if pa.types.is_string(target) and not (
            pa.types.is_string(kind) or pa.types.is_large_string(kind) or pa.types.is_null(kind)
        ):
            raise ConversionError(f"{label}: strings expected, found {kind}")
        if pa.types.is_floating(target) and not (
            pa.types.is_floating(kind) or pa.types.is_integer(kind) or pa.types.is_null(kind)
        ):
            raise ConversionError(f"{label}: numbers expected, found {kind}")
        return array.cast(target, safe=True)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise ConversionError(f"{label}: {exc}") from None


def to_table(contract: str, frame: pd.DataFrame, split: str) -> pa.Table:
    """A source DataFrame (layout of ADR-10) as a table of the raw contract, with its split."""
    if contract not in LAYOUTS:
        raise ValueError(f"{contract!r} is not an ASOS raw contract: {sorted(LAYOUTS)}")
    if split not in ASOS_SPLITS:
        raise ValueError(f"split must be one of {ASOS_SPLITS}, not {split!r}")
    layout = LAYOUTS[contract]
    labels = list(frame.columns)
    if not all(isinstance(label, str) for label in labels):
        raise ConversionError("column labels must be strings")
    positions = _positions(labels, layout)
    _check_one_hot(frame, labels, layout)
    schema = SCHEMAS[contract]
    arrays = [
        pa.array([split] * len(frame), pa.string())
        if f.name == "split"
        else _arrow(frame.iloc[:, positions[f.name]], labels[positions[f.name]], f.type)
        for f in schema
    ]
    return pa.Table.from_arrays(arrays, names=schema.names)


def convert(
    src_dir: Path, root: Path, expected: Mapping[str, Pinned] | None = None
) -> dict[str, dict[str, int]]:
    """Write raw/asos_{events,customers,variants}.parquet under `root` from the six pinned
    pickles in `src_dir`; rows written per contract and split. Nothing is written unless every
    file matches its pin."""
    files = pinned_files(expected)
    problems = verify(src_dir, files)
    if problems:
        raise IntegrityError("; ".join(f"{name}: {reason}" for name, reason in problems.items()))
    rows: dict[str, dict[str, int]] = {}
    for contract, sources in SOURCES.items():
        parts: dict[str, pa.Table] = {}
        for split, name in sources.items():
            frame = load_pinned(target_path(src_dir, name, files), files)
            parts[split] = to_table(contract, frame, split)
            del frame  # one source frame in memory at a time
        write_table(contract, pa.concat_tables(parts.values()), root, PROVENANCE)
        rows[contract] = {split: table.num_rows for split, table in parts.items()}
    return rows
