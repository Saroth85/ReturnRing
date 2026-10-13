"""US-101 converter: hash check before unpickling, allowlist unpickler, raw/asos_* tables.

Review findings covered: ASOS-3 (load only pinned bytes), ASOS-6 (pickle to Parquet with the raw
contracts of ADR-13), ASOS-11 (allowlist unpickler). The frames are small made-up replicas
(nature: synthetic, tests/asos_fixtures.py); the real files are tested in test_asos_real.py.
"""

from __future__ import annotations

import io
import os
import pickle
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pytest

from contracts.io import read_table, validate_table
from contracts.parquet import ASOS_REASON_CODES, SCHEMAS
from pipeline.ingest import asos_convert as ac
from pipeline.ingest import asos_download as dl
from tests.asos_fixtures import (
    ADR10_GLOBALS,
    customer,
    customers_frame,
    events_frame,
    legacy_pickle,
    pin_of,
    replica_frames,
    variant,
    variants_frame,
    write_replica,
)

# ---- The unpickler ------------------------------------------------------------------------------


class Reducer:
    def __init__(self, func: Any, *args: Any) -> None:
        self.func, self.args = func, args

    def __reduce__(self) -> tuple[Any, ...]:
        return self.func, self.args


def global_call(module: str, name: str, arg: str = "x") -> bytes:
    """Protocol 0: call module.name(arg) through the GLOBAL opcode."""
    return f"c{module}\n{name}\n(S'{arg}'\ntR.".encode()


def stack_global_call(module: str, name: str) -> bytes:
    """Protocol 4: push module.name through STACK_GLOBAL, as the OSF files do."""
    mod, nm = module.encode(), name.encode()
    body = b"\x8c" + bytes([len(mod)]) + mod + b"\x8c" + bytes([len(nm)]) + nm + b"\x93" + b")R."
    return b"\x80\x04" + body


def test_allowlist_is_exactly_the_globals_of_adr10() -> None:
    assert set(ac.ALLOWED_GLOBALS) == ADR10_GLOBALS


@pytest.mark.parametrize("protocol", [0, 2, 4])
def test_unpickler_refuses_os_system_and_runs_nothing(tmp_path: Path, protocol: int) -> None:
    marker = tmp_path / "pwned"
    payload = pickle.dumps(Reducer(os.system, f"touch {marker}"), protocol=protocol)
    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        ac.RestrictedUnpickler(io.BytesIO(payload)).load()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("os", "system"),
        ("posix", "system"),
        ("subprocess", "Popen"),
        ("builtins", "eval"),
        ("builtins", "exec"),
        ("builtins", "getattr"),
        ("builtins", "__import__"),
        ("numpy", "load"),
        ("numpy", "fromfile"),
        ("numpy._core.multiarray", "_reconstruct"),  # modern path: not in the files, refused
        ("pandas", "read_pickle"),
        ("pandas.core.frame", "DataFrame.to_pickle"),  # dotted name inside an allowed module
        ("pandas.core.frame", "DataFrame.__init__"),
        ("pandas.compat.pickle_compat", "loads"),
        ("numpy", "ndarray.tofile"),
    ],
)
def test_unpickler_refuses_every_global_outside_the_allowlist(module: str, name: str) -> None:
    for payload in (global_call(module, name), stack_global_call(module, name)):
        with pytest.raises(pickle.UnpicklingError, match="not allowed"):
            ac.RestrictedUnpickler(io.BytesIO(payload)).load()


@pytest.mark.parametrize("payload", [b"Pfoo\n.", b"X\x03\x00\x00\x00fooQ."])
def test_unpickler_refuses_persistent_ids(payload: bytes) -> None:
    with pytest.raises(pickle.UnpicklingError, match="persistent"):
        ac.RestrictedUnpickler(io.BytesIO(payload)).load()


def test_unpickler_maps_the_legacy_names_to_pandas_3_and_numpy_2() -> None:
    """A replica written with the 2022 global names loads into an equal pandas 3 frame."""
    seen: set[tuple[str, str]] = set()

    class Recording(ac.RestrictedUnpickler):  # type: ignore[misc]
        def find_class(self, module: str, name: str) -> Any:
            seen.add((module, name))
            return super().find_class(module, name)

    for (kind, _), df in replica_frames().items():
        loaded = Recording(io.BytesIO(legacy_pickle(df))).load()
        assert type(loaded) is pd.DataFrame
        assert list(loaded.columns) == list(df.columns)
        assert loaded.index.tolist() == df.index.tolist()
        for i in range(df.shape[1]):
            assert loaded.iloc[:, i].tolist() == df.iloc[:, i].tolist()
        if kind != "events":
            assert ("pandas.core.indexes.numeric", "Int64Index") in seen
    assert seen == ADR10_GLOBALS  # the replica uses every legacy name, and only those


# ---- Loading a pinned file -----------------------------------------------------------------------


def test_load_pinned_checks_the_hash_before_unpickling(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    payload = pickle.dumps(Reducer(os.system, f"touch {marker}"), protocol=4)
    path = tmp_path / "event_table_training.p"
    path.write_bytes(payload)
    other = pin_of(b"the published bytes")
    with pytest.raises(dl.IntegrityError, match="event_table_training.p"):
        ac.load_pinned(path, {"event_table_training.p": other})
    same_size = dl.Pinned("0" * 64, len(payload), "fake0")
    with pytest.raises(dl.IntegrityError, match="sha256"):
        ac.load_pinned(path, {"event_table_training.p": same_size})
    # Even bytes that match their pin go through the allowlist.
    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        ac.load_pinned(path, {"event_table_training.p": pin_of(payload)})
    assert not marker.exists()


def test_load_pinned_refuses_unknown_names_and_non_frames(tmp_path: Path) -> None:
    path = tmp_path / "notes.p"
    path.write_bytes(pickle.dumps(slice(1, 2), protocol=4))
    with pytest.raises(ValueError, match="unknown ASOS file"):
        ac.load_pinned(path, {})
    with pytest.raises(pickle.UnpicklingError, match="not a pandas DataFrame"):
        ac.load_pinned(path, {"notes.p": pin_of(path.read_bytes())})


def test_load_pinned_returns_the_frame(tmp_path: Path) -> None:
    df = replica_frames()[("customers", "train")]
    data = legacy_pickle(df)
    path = tmp_path / "customer_nodes_training.p"
    path.write_bytes(data)
    loaded = ac.load_pinned(path, {"customer_nodes_training.p": pin_of(data)})
    assert loaded.shape == df.shape


# ---- Pure conversion -----------------------------------------------------------------------------


def converted(contract: str, df: pd.DataFrame, split: str = "train") -> pa.Table:
    table = ac.to_table(contract, df, split)
    assert table.schema.names == SCHEMAS[contract].names
    assert validate_table(contract, table) == []
    return table


def test_events_are_renamed_typed_and_split() -> None:
    table = converted("raw/asos_events", events_frame([(5, 7, 1), (6, 7, 0)]), "test")
    assert table.to_pylist() == [
        {"split": "test", "variant_hash": 5, "customer_hash": 7, "is_returned": True},
        {"split": "test", "variant_hash": 6, "customer_hash": 7, "is_returned": False},
    ]


def test_customers_keep_both_d_codes_by_position_and_drop_the_country_one_hot() -> None:
    df = customers_frame(
        [
            customer(1, 4, 2, {"D": 0.25, "D2": 0.75}, "Country_B", 1900, 1, 1),
            customer(2, 3, 3, {"A": 1.0}, "Country_I", 2001, 0, 0),
        ]
    )
    table = converted("raw/asos_customers", df)
    first = table.to_pylist()[0]
    assert first["reason_share_D"] == 0.25 and first["reason_share_D2"] == 0.75
    assert first["sales"] == 4 and first["returns"] == 2 and first["return_rate"] == 0.5
    assert first["premier"] is True and first["is_male"] is True
    assert first["shipping_country"] == "Country_B" and first["year_of_birth"] == 1900
    assert table.schema.field("year_of_birth").type == pa.int32()
    assert not any(name.startswith("Country_") for name in table.column_names)


def test_variants_are_renamed_and_the_one_hot_columns_dropped() -> None:
    df = variants_frame(
        [
            variant(10, "Jeans", 45.0, 30, 9, {"D2": 1.0}, "Pull&Bear"),
            variant(11, "productType_A", 9.5, 4, 1, {"L": 1.0}, "Brand_A"),
        ]
    )
    table = converted("raw/asos_variants", df)
    first = table.to_pylist()[0]
    assert first["product_type"] == "Jeans" and first["brand"] == "Pull&Bear"
    assert first["product_hash"] == 1 and first["supplier_hash"] == 10 % 7
    assert first["avg_price_gbp"] == 45.0 and first["sales"] == 30 and first["returns"] == 9
    assert first["reason_share_D2"] == 1.0 and first["reason_share_D"] == 0.0
    assert [f"reason_share_{c}" for c in ASOS_REASON_CODES] == [
        n for n in table.column_names if n.startswith("reason_share_")
    ]


def test_nan_becomes_null_never_a_value() -> None:
    df = variants_frame([variant(10), variant(11)])
    df["avgGbpPrice"] = [float("nan"), 3.0]
    table = converted("raw/asos_variants", df)
    assert table.column("avg_price_gbp").to_pylist() == [None, 3.0]


def test_string_dtype_columns_are_accepted() -> None:
    df = customers_frame([customer(1), customer(2)])
    df["shippingCountry"] = df["shippingCountry"].astype("str")
    assert converted("raw/asos_customers", df).column("shipping_country").to_pylist() == [
        "Country_G",
        "Country_G",
    ]


def test_boolean_dtype_columns_are_accepted() -> None:
    df = customers_frame([customer(1), customer(2)])
    df["premier"] = [True, False]
    assert converted("raw/asos_customers", df).column("premier").to_pylist() == [True, False]


def with_column(df: pd.DataFrame, label: str, values: Any) -> pd.DataFrame:
    out = df.copy()
    out.insert(len(out.columns), label, values, allow_duplicates=True)
    return out


def bad_customers(change: str) -> pd.DataFrame:
    df = customers_frame([customer(1), customer(2, country="Country_A")])
    if change == "unknown column":
        return with_column(df, "email", ["a@x", "b@x"])
    if change == "missing column":
        return df.drop(columns=["premier"])
    if change == "repeated column":
        return with_column(df, "salesPerCustomer", [1, 1])
    if change == "reason codes":
        return df.loc[:, [c != "customerId_level_return_code_L" for c in df.columns]]
    if change == "one-hot":
        df["Country_A"] = [1, 1]
        return df
    if change == "one-hot values":
        df["Country_G"] = [2, 0]
        return df
    if change == "boolean":
        df["premier"] = [0, 2]
        return df
    if change == "integer":
        df["salesPerCustomer"] = [1.5, 2.0]
        return df
    if change == "int32":
        df["yearOfBirth"] = [2**40, 1990]
        return df
    if change == "string":
        df["shippingCountry"] = pd.Series([1, 2], dtype=object, index=df.index)
        return df
    if change == "number":
        df["customerReturnRate"] = pd.Series(["0.5", "1"], dtype=object, index=df.index)
        return df
    if change == "float boolean":
        df["premier"] = [0.0, 1.0]
        return df
    if change == "two one-hot":
        return with_column(df, "Country_X", df["Country_G"].to_numpy())
    if change == "labels":
        df.columns = [0, *df.columns[1:]]
        return df
    raise AssertionError(change)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("unknown column", "unexpected source columns \\['email'\\]"),
        ("missing column", "missing source columns \\['premier'\\]"),
        ("repeated column", "repeated source column 'salesPerCustomer'"),
        ("reason codes", "reason codes"),
        ("one-hot", "Country_A does not repeat shippingCountry"),
        ("one-hot values", "Country_G"),
        ("boolean", "premier: values other than 0 and 1"),
        ("integer", "salesPerCustomer: integer column expected"),
        ("int32", "yearOfBirth"),
        ("string", "shippingCountry: strings expected, found int64"),
        ("number", "customerReturnRate: numbers expected, found string"),
        ("float boolean", "premier: 0/1 integers expected, found float64"),
        ("two one-hot", "Country_X and Country_G both mark 'Country_G'"),
        ("labels", "column labels must be strings"),
    ],
)
def test_unexpected_source_data_fails_closed(change: str, message: str) -> None:
    with pytest.raises(ac.ConversionError, match=message):
        ac.to_table("raw/asos_customers", bad_customers(change), "train")


def test_unknown_contract_and_split_are_refused() -> None:
    df = events_frame([(1, 2, 0)])
    with pytest.raises(ValueError, match="not an ASOS raw contract"):
        ac.to_table("store/orders", df, "train")
    with pytest.raises(ValueError, match="split"):
        ac.to_table("raw/asos_events", df, "validation")


# ---- End to end ---------------------------------------------------------------------------------


def test_convert_writes_the_three_contracts_with_provenance(tmp_path: Path) -> None:
    src = tmp_path / "asos"
    pins = write_replica(src)
    rows = ac.convert(src, tmp_path / "data", pins)
    assert rows == {
        "raw/asos_events": {"train": 5, "test": 3},
        "raw/asos_customers": {"train": 3, "test": 2},
        "raw/asos_variants": {"train": 3, "test": 2},
    }
    for contract in rows:
        table, provenance = read_table(contract, tmp_path / "data")
        assert table.num_rows == sum(rows[contract].values())
        assert provenance.nature == "real"
        assert provenance.period_start == date(2021, 9, 1)
        assert provenance.period_end == date(2021, 11, 30)
        for text in ("ASOS GraphReturns", "arXiv:2302.14096", "OSF c793h", "CC BY 4.0"):
            assert text in provenance.origin
        assert "train 2021-09-01..2021-10-31" in provenance.origin
        assert "test 2021-10-01..2021-11-30" in provenance.origin
    assert (tmp_path / "data/raw/asos_events.parquet").exists()


def test_convert_writes_nothing_when_a_file_does_not_match(tmp_path: Path) -> None:
    src = tmp_path / "asos"
    pins = write_replica(src)
    (src / "product_nodes_testing.p").write_bytes(b"tampered")
    with pytest.raises(dl.IntegrityError, match="product_nodes_testing.p"):
        ac.convert(src, tmp_path / "data", pins)
    assert not (tmp_path / "data").exists()
