"""Small replicas of the ASOS GraphReturns pickles, for tests that must not need the real files.

Every number here is a made-up fixture (nature: synthetic). The frames follow the layout read from
the real pickle headers (ADR-10, ADR-13): column order, dtypes, the repeated reason label "D", the
one-hot columns, a RangeIndex from 1 for events and an Int64Index from 1 for the node tables.

legacy_pickle() writes a frame the way pandas < 2 and numpy < 2 did in 2022 (the OSF files): the
same opcodes as pandas 3, but with the old global names (numpy.core.multiarray._reconstruct,
pandas.core.frame.DataFrame, pandas.core.indexes.numeric.Int64Index, ...).
"""

from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy._core.multiarray import _reconstruct
from pandas.core.indexes.base import _new_Index

from pipeline.ingest import asos_download as dl

REASONS_SOURCE = ("A", "B", "C", "D", "E", "D", "F", "G", "H", "I", "J", "K", "L")
COUNTRIES = tuple(f"Country_{c}" for c in "ABCDEFGHI")
BRANDS = tuple(f"Brand_{c}" for c in "ABCDEFGIJK")  # no Brand_H, as in the source
TYPE_ONE_HOT = tuple(f"productType_{c}" for c in "ABCDEFGHIJK")
# The source one-hot letters are shifted against the productType values ('Jeans' is
# productType_D in the one-hot columns); the replica keeps a shift of the same kind.
TYPE_VALUES = (
    "productType_A",
    "productType_B",
    "productType_C",
    "Jeans",
    *(f"productType_{c}" for c in "DEFGHIJ"),
)

# The 11 globals of ADR-10, as found in the six OSF files.
ADR10_GLOBALS = frozenset(
    {
        ("builtins", "slice"),
        ("numpy.core.multiarray", "_reconstruct"),
        ("numpy", "ndarray"),
        ("numpy", "dtype"),
        ("pandas._libs.internals", "_unpickle_block"),
        ("pandas.core.frame", "DataFrame"),
        ("pandas.core.internals.managers", "BlockManager"),
        ("pandas.core.indexes.base", "_new_Index"),
        ("pandas.core.indexes.base", "Index"),
        ("pandas.core.indexes.range", "RangeIndex"),
        ("pandas.core.indexes.numeric", "Int64Index"),
    }
)

FILES = {
    ("events", "train"): "event_table_training.p",
    ("events", "test"): "event_table_testing.p",
    ("customers", "train"): "customer_nodes_training.p",
    ("customers", "test"): "customer_nodes_testing.p",
    ("variants", "train"): "product_nodes_training.p",
    ("variants", "test"): "product_nodes_testing.p",
}


# ---- Frames shaped like ADR-10 ------------------------------------------------------------------


def frame(columns: list[tuple[str, Any]], index: pd.Index) -> pd.DataFrame:
    """A DataFrame from (label, values) pairs; labels may repeat, as in the source."""
    df = pd.DataFrame({i: values for i, (_, values) in enumerate(columns)})
    df.columns = pd.Index([label for label, _ in columns], dtype=object)
    df.index = index
    return df


def strings(values: list[str]) -> pd.Series:
    return pd.Series(values, dtype=object)


def shares(rows: list[dict[str, float]]) -> list[list[float]]:
    """Per row, the 13 shares in source order; keys use D2 for the second D."""
    keys = ("A", "B", "C", "D", "E", "D2", "F", "G", "H", "I", "J", "K", "L")
    return [[row.get(k, 0.0) for row in rows] for k in keys]


def events_frame(rows: list[tuple[int, int, int]]) -> pd.DataFrame:
    variants, customers, returned = zip(*rows, strict=True)
    return frame(
        [
            ("hash(variantID)", np.array(variants, dtype=np.int64)),
            ("hash(customerId)", np.array(customers, dtype=np.int64)),
            ("isReturned", np.array(returned, dtype=np.int64)),
        ],
        pd.RangeIndex(1, len(rows) + 1),
    )


def customers_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """rows: hash, yob, male, country, premier, sales, returns, rate, shares (dict by code)."""

    def col(key: str) -> list[Any]:
        return [r[key] for r in rows]

    reason_columns = shares(col("shares"))
    countries = col("country")
    return frame(
        [
            ("hash(customerId)", np.array(col("hash"), dtype=np.int64)),
            ("yearOfBirth", np.array(col("yob"), dtype=np.int64)),
            ("isMale", np.array(col("male"), dtype=np.int64)),
            ("shippingCountry", strings(countries)),
            ("premier", np.array(col("premier"), dtype=np.int64)),
            ("salesPerCustomer", np.array(col("sales"), dtype=np.int64)),
            ("returnsPerCustomer", np.array(col("returns"), dtype=np.int64)),
            ("customerReturnRate", np.array(col("rate"), dtype=np.float64)),
            *(
                (f"customerId_level_return_code_{code}", np.array(values, dtype=np.float64))
                for code, values in zip(REASONS_SOURCE, reason_columns, strict=True)
            ),
            *(
                (name, np.array([int(c == name) for c in countries], dtype=np.int64))
                for name in COUNTRIES
            ),
        ],
        pd.Index(np.arange(1, len(rows) + 1, dtype=np.int64)),
    )


def variants_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """rows: hash, product, type, supplier, brand, price, discount, sales, returns, rate, shares."""

    def col(key: str) -> list[Any]:
        return [r[key] for r in rows]

    reason_columns = shares(col("shares"))
    types, brands = col("type"), col("brand")
    return frame(
        [
            ("hash(variantID)", np.array(col("hash"), dtype=np.int64)),
            ("hash(productID)", np.array(col("product"), dtype=np.int64)),
            ("productType", strings(types)),
            ("hash(supplierRef)", np.array(col("supplier"), dtype=np.int64)),
            ("brandDesc", strings(brands)),
            ("avgGbpPrice", np.array(col("price"), dtype=np.float64)),
            ("avgDiscountValue", np.array(col("discount"), dtype=np.float64)),
            ("salesPerProduct", np.array(col("sales"), dtype=np.int64)),
            ("returnsPerProduct", np.array(col("returns"), dtype=np.int64)),
            ("productReturnRate", np.array(col("rate"), dtype=np.float64)),
            *(
                (f"variantID_level_return_code_{code}", np.array(values, dtype=np.float64))
                for code, values in zip(REASONS_SOURCE, reason_columns, strict=True)
            ),
            *(
                (name, np.array([int(b == name) for b in brands], dtype=np.int64))
                for name in BRANDS
            ),
            *(
                (one_hot, np.array([int(t == value) for t in types], dtype=np.int64))
                for one_hot, value in zip(TYPE_ONE_HOT, TYPE_VALUES, strict=True)
            ),
        ],
        pd.Index(np.arange(1, len(rows) + 1, dtype=np.int64)),
    )


def customer(
    h: int,
    sales: int = 4,
    returns: int = 2,
    shares_: dict[str, float] | None = None,
    country: str = "Country_G",
    yob: int = 1990,
    male: int = 0,
    premier: int = 0,
) -> dict[str, Any]:
    return {
        "hash": h,
        "yob": yob,
        "male": male,
        "country": country,
        "premier": premier,
        "sales": sales,
        "returns": returns,
        "rate": returns / sales,
        "shares": shares_ or {"A": 1.0},
    }


def variant(
    h: int,
    type_: str = "productType_J",
    price: float = 20.0,
    sales: int = 10,
    returns: int = 4,
    shares_: dict[str, float] | None = None,
    brand: str = "Brand_K",
    discount: float = 5.0,
) -> dict[str, Any]:
    return {
        "hash": h,
        "product": h // 10,
        "type": type_,
        "supplier": h % 7,
        "brand": brand,
        "price": price,
        "discount": discount,
        "sales": sales,
        "returns": returns,
        "rate": returns / sales,
        "shares": shares_ or {"A": 0.5, "D2": 0.5},
    }


def replica_frames() -> dict[tuple[str, str], pd.DataFrame]:
    """A small dataset: two splits, joins with gaps, the repeated D, 'Jeans', 'Pull&Bear'."""
    return {
        ("events", "train"): events_frame(
            [(901, 11, 1), (901, 12, 0), (902, 11, 1), (903, 13, 0), (999, 14, 1)]
        ),
        ("events", "test"): events_frame([(901, 11, 1), (904, 15, 0), (902, 15, 1)]),
        ("customers", "train"): customers_frame(
            [
                customer(11, 5, 2, {"A": 0.5, "D": 0.25, "D2": 0.25}, "Country_G", 1990, 1, 0),
                customer(12, 2, 1, {"B": 1.0}, "Country_E", 1900, 0, 1),
                customer(13, 3, 3, {"D2": 1.0}, "Country_A", 2022, 0, 0),
            ]
        ),
        ("customers", "test"): customers_frame(
            [customer(11, 6, 3), customer(15, 2, 1, {"C": 1.0}, "Country_I")]
        ),
        ("variants", "train"): variants_frame(
            [
                variant(901, "productType_J", 19.99, 10, 5, {"A": 1.0}, "Brand_K"),
                variant(902, "Jeans", 45.0, 30, 9, {"D": 0.5, "D2": 0.5}, "Pull&Bear"),
                variant(903, "productType_B", 12.5, 20, 2, {"L": 1.0}, "Brand_B"),
            ]
        ),
        ("variants", "test"): variants_frame(
            [
                variant(901, "productType_J", 19.99, 10, 5, {"A": 1.0}, "Brand_K"),
                variant(904, "productType_A", 80.0, 5, 1, {"E": 1.0}, "Brand_A"),
            ]
        ),
    }


# ---- Legacy pickles ----------------------------------------------------------------------------


class _Int64Index:
    """Stands, in the pickle stream, for the pandas < 2 Int64Index class."""


_LEGACY_NAMES: dict[int, tuple[str, str]] = {
    id(pd.DataFrame): ("pandas.core.frame", "DataFrame"),
    id(pd.Index): ("pandas.core.indexes.base", "Index"),
    id(pd.RangeIndex): ("pandas.core.indexes.range", "RangeIndex"),
    id(_reconstruct): ("numpy.core.multiarray", "_reconstruct"),
    id(_Int64Index): ("pandas.core.indexes.numeric", "Int64Index"),
}


class LegacyPickler(pickle._Pickler):
    """Protocol 4 pickles with the global names that pandas < 2 and numpy < 2 wrote."""

    def reducer_override(self, obj: Any) -> Any:
        if type(obj) is pd.Index and obj.dtype == np.int64:
            return _new_Index, (_Int64Index, {"data": obj.to_numpy(), "name": obj.name})
        return NotImplemented

    def save_global(self, obj: Any, name: str | None = None) -> None:
        legacy = _LEGACY_NAMES.get(id(obj))
        if legacy is None:
            super().save_global(obj, name)
            return
        self.save(legacy[0])
        self.save(legacy[1])
        self.write(pickle.STACK_GLOBAL)
        self.memoize(obj)


def legacy_pickle(df: pd.DataFrame) -> bytes:
    import io

    buffer = io.BytesIO()
    LegacyPickler(buffer, protocol=4).dump(df)
    return buffer.getvalue()


def pin_of(data: bytes, osf_id: str = "fake0") -> dl.Pinned:
    return dl.Pinned(hashlib.sha256(data).hexdigest(), len(data), osf_id)


def write_replica(directory: Path) -> dict[str, dl.Pinned]:
    """Write the six replica pickles under their OSF names; return their pins."""
    directory.mkdir(parents=True, exist_ok=True)
    pins: dict[str, dl.Pinned] = {}
    for i, (key, df) in enumerate(replica_frames().items()):
        data = legacy_pickle(df)
        (directory / FILES[key]).write_bytes(data)
        pins[FILES[key]] = pin_of(data, f"fake{i}")
    return pins
