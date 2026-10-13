"""DATA-01, DATA-04 and MOD-08 at the file boundary: contracts/io.py and contracts/checks.py.

Every number in these tables is a made-up test fixture (nature: synthetic).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from contracts.checks import CHECKS, InRange, NotAfter, OneOf, PresentWhen, Unique, checks_for
from contracts.io import (
    META_CONTRACT,
    META_PROVENANCE,
    META_VERSION,
    ContractError,
    Provenance,
    path_for,
    read_table,
    validate_table,
    write_table,
)
from contracts.parquet import CONTRACT_VERSION, SCHEMAS

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
SYNTH = Provenance(
    origin="Navigli Fashion generator, test fixture",
    period_start=date(2025, 10, 1),
    period_end=date(2026, 9, 30),
    nature="synthetic",
)


def at(days: float) -> datetime:
    return T0 + timedelta(days=days)


def table(name: str, rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMAS[name])


def returns_rows() -> list[dict[str, Any]]:
    return [
        {
            "return_id": "RET-1",
            "line_id": "L-1",
            "reason": "size",
            "days_since_delivery": 3,
            "event_at": at(0),
            "available_at": at(0),
        },
        {
            "return_id": "RET-2",
            "line_id": "L-2",
            "reason": "defective",
            "days_since_delivery": 10,
            "event_at": at(5),
            "available_at": at(5.5),
        },
    ]


def links_rows() -> list[dict[str, Any]]:
    return [
        {
            "customer_a": "C-MARCO-1",
            "customer_b": "C-MARCO-2",
            "kinds": ["card", "device"],
            "weight": 0.9,
            "weight_decayed": 0.8,
            "first_shared_at": at(-30),
            "last_shared_at": at(-2),
            "n_shared_events": 4,
            "max_sharers": 7,
            "disputed": False,
            "as_of": at(0),
        }
    ]


# ---- Provenance ---------------------------------------------------------------------------------


def test_provenance_round_trips_as_canonical_json() -> None:
    text = SYNTH.to_json()
    assert json.loads(text) == {
        "nature": "synthetic",
        "origin": "Navigli Fashion generator, test fixture",
        "period_end": "2026-09-30",
        "period_start": "2025-10-01",
    }
    assert list(json.loads(text)) == sorted(json.loads(text))
    assert Provenance.from_json(text) == SYNTH
    assert Provenance.from_json(text.encode()) == SYNTH


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "  "},
        {"nature": "estimated"},
        {"period_start": date(2026, 10, 1), "period_end": date(2026, 9, 1)},
        {"period_start": datetime(2025, 10, 1, tzinfo=UTC)},
        {"period_end": "2026-09-30"},
    ],
)
def test_provenance_rejects_incomplete_or_inconsistent_values(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "origin": SYNTH.origin,
        "period_start": SYNTH.period_start,
        "period_end": SYNTH.period_end,
        "nature": SYNTH.nature,
    }
    with pytest.raises(ContractError):
        Provenance(**{**base, **kwargs})


def test_provenance_from_json_rejects_missing_fields() -> None:
    with pytest.raises(ContractError):
        Provenance.from_json('{"origin": "x"}')
    with pytest.raises(ContractError):
        Provenance.from_json("[]")
    with pytest.raises(ContractError):
        Provenance.from_json("not json")
    bad_nature = SYNTH.to_json().replace("synthetic", "estimated")
    with pytest.raises(ContractError, match="nature"):
        Provenance.from_json(bad_nature)


# ---- write_table / read_table ------------------------------------------------------------------


def test_write_then_read_round_trip_with_metadata(tmp_path: Path) -> None:
    t = table("store/returns", returns_rows())
    path = write_table("store/returns", t, tmp_path, SYNTH)
    assert path == tmp_path / "store" / "returns.parquet" == path_for("store/returns", tmp_path)
    meta = pq.read_schema(path).metadata
    assert meta[META_CONTRACT] == b"store/returns"
    assert meta[META_VERSION] == CONTRACT_VERSION.encode()
    assert Provenance.from_json(meta[META_PROVENANCE]) == SYNTH
    assert set(meta) - {b"ARROW:schema"} == {META_CONTRACT, META_VERSION, META_PROVENANCE}
    back, provenance = read_table("store/returns", tmp_path)
    assert provenance == SYNTH
    assert back.num_columns == 6
    assert back.schema.remove_metadata().equals(SCHEMAS["store/returns"])
    assert back.to_pylist() == t.to_pylist()


def test_same_input_gives_byte_identical_files(tmp_path: Path) -> None:
    """DATA-04: no wall-clock value, fixed compression, chunking does not matter."""
    rows = [
        {**row, "return_id": f"RET-{i}-{j}", "days_since_delivery": i % 30}
        for i in range(50)
        for j, row in enumerate(returns_rows())
    ]
    one = write_table("store/returns", table("store/returns", rows), tmp_path / "a", SYNTH)
    chunked = pa.concat_tables([table("store/returns", rows[:7]), table("store/returns", rows[7:])])
    assert chunked.column(0).num_chunks == 2
    two = write_table("store/returns", chunked, tmp_path / "b", SYNTH)
    digest = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (one, two)]
    assert digest[0] == digest[1]
    again = write_table("store/returns", table("store/returns", rows), tmp_path / "a", SYNTH)
    assert hashlib.sha256(again.read_bytes()).hexdigest() == digest[0]
    assert not list((tmp_path / "a" / "store").glob("*.tmp"))


def test_columns_in_any_order_are_written_in_contract_order(tmp_path: Path) -> None:
    t = table("store/returns", returns_rows())
    shuffled = t.select(list(reversed(t.column_names)))
    path = write_table("store/returns", shuffled, tmp_path, SYNTH)
    assert pq.read_schema(path).names == SCHEMAS["store/returns"].names


def test_read_table_as_of_filters_on_available_at(tmp_path: Path) -> None:
    """Rule 7: the second return is requested at day 5 but only available at day 5.5."""
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    at_5, _ = read_table("store/returns", tmp_path, as_of=at(5))
    assert at_5.column("return_id").to_pylist() == ["RET-1"]
    at_6, _ = read_table("store/returns", tmp_path, as_of=at(6))
    assert at_6.column("return_id").to_pylist() == ["RET-1", "RET-2"]
    before, _ = read_table("store/returns", tmp_path, as_of=at(-1))
    assert before.num_rows == 0
    # 23:30 in Rome is 21:30 UTC, before RET-2 is available (22:00 UTC).
    rome = datetime(2026, 9, 6, 23, 30, tzinfo=ZoneInfo("Europe/Rome"))
    assert read_table("store/returns", tmp_path, as_of=rome)[0].num_rows == 1


def test_read_table_as_of_needs_an_aware_datetime_and_an_event_table(tmp_path: Path) -> None:
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    with pytest.raises(ContractError, match="timezone"):
        read_table("store/returns", tmp_path, as_of=datetime(2026, 9, 6))
    write_table("features/links", table("features/links", links_rows()), tmp_path, SYNTH)
    with pytest.raises(ContractError, match="event table"):
        read_table("features/links", tmp_path, as_of=at(0))
    links, _ = read_table("features/links", tmp_path)
    assert links.column("kinds").to_pylist() == [["card", "device"]]


def test_read_table_rejects_files_of_another_contract_or_version(tmp_path: Path) -> None:
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    src = path_for("store/returns", tmp_path)
    pf = pq.read_table(src)
    meta = dict(pf.schema.metadata)

    def rewrite(name: str, changes: dict[bytes, bytes]) -> None:
        dest = path_for(name, tmp_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pf.replace_schema_metadata({**meta, **changes}), dest)

    rewrite("store/deliveries", {})  # a returns file saved under the deliveries name
    with pytest.raises(ContractError, match="contract"):
        read_table("store/deliveries", tmp_path)
    rewrite("store/returns", {META_VERSION: b"1.0.0"})
    with pytest.raises(ContractError, match="version"):
        read_table("store/returns", tmp_path)
    pq.write_table(pf.replace_schema_metadata({}), src)
    with pytest.raises(ContractError, match="contract"):
        read_table("store/returns", tmp_path)


def test_read_table_rejects_a_schema_that_drifted(tmp_path: Path) -> None:
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    src = path_for("store/returns", tmp_path)
    pf = pq.read_table(src)
    drifted = pf.drop_columns(["days_since_delivery"]).replace_schema_metadata(pf.schema.metadata)
    pq.write_table(drifted, src)
    with pytest.raises(ContractError, match="schema"):
        read_table("store/returns", tmp_path)


def test_unknown_contract_name(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="unknown"):
        write_table("store/sessions", pa.table({"x": [1]}), tmp_path, SYNTH)
    with pytest.raises(ContractError, match="unknown"):
        read_table("../etc/passwd", tmp_path)


# ---- Structural validation ---------------------------------------------------------------------


def test_missing_extra_and_mistyped_columns(tmp_path: Path) -> None:
    t = table("store/returns", returns_rows())
    found = validate_table(
        "store/returns", t.drop_columns(["reason"]).append_column("x", pa.array([1, 2]))
    )
    assert any("missing" in e and "reason" in e for e in found)
    assert any("unexpected" in e and "x" in e for e in found)
    wrong = t.set_column(3, "days_since_delivery", pa.array([3, 10], pa.int64()))
    assert any(
        "days_since_delivery" in e and "type" in e for e in validate_table("store/returns", wrong)
    )
    naive = t.set_column(
        4, "event_at", pa.array([at(0).replace(tzinfo=None)] * 2, pa.timestamp("us"))
    )
    assert any("event_at" in e for e in validate_table("store/returns", naive))
    with pytest.raises(ContractError) as info:
        write_table("store/returns", wrong, tmp_path, SYNTH)
    assert info.value.errors
    assert not path_for("store/returns", tmp_path).exists()


def test_nulls_in_non_nullable_columns() -> None:
    rows = returns_rows()
    rows[0]["line_id"] = None
    assert any(
        "line_id" in e and "null" in e
        for e in validate_table("store/returns", table("store/returns", rows))
    )


def test_duplicate_column_names() -> None:
    order = pa.array(["O-1"])
    stamp = pa.array([at(0)], pa.timestamp("us", tz="UTC"))
    dup = pa.Table.from_arrays(
        [order, order, stamp, stamp], names=["order_id", "order_id", "event_at", "available_at"]
    )
    assert any("duplicate" in e for e in validate_table("store/deliveries", dup))


def test_range_checks_with_one_bound_or_none() -> None:
    t = pa.table({"x": pa.array([0.5, 2.0, None])})
    assert InRange("x", max=1.0).violations(t) == ["x: values outside [None, 1.0]: ['2.0']"]
    assert InRange("x", min=1.0).violations(t) == ["x: values outside [1.0, None]: ['0.5']"]
    assert InRange("x").violations(t) == []


def test_nan_is_never_a_value() -> None:
    rows = links_rows()
    rows[0]["weight"] = float("nan")
    found = validate_table("features/links", table("features/links", rows))
    assert any("weight" in e and "NaN" in e for e in found)


def test_list_columns_accept_any_element_field_name_but_no_null_element() -> None:
    t = table("features/links", links_rows())
    plain = t.set_column(2, "kinds", pa.array([["card", "device"]], pa.list_(pa.string())))
    assert validate_table("features/links", plain) == []
    with_null = t.set_column(2, "kinds", pa.array([["card", None]], pa.list_(pa.string())))
    assert any("kinds" in e and "null" in e for e in validate_table("features/links", with_null))
    ints = t.set_column(2, "kinds", pa.array([[1]], pa.list_(pa.int64())))
    assert any("kinds" in e and "type" in e for e in validate_table("features/links", ints))


# ---- Temporal rules (CON-01, rule 7) -----------------------------------------------------------


def test_event_at_after_available_at_is_rejected() -> None:
    rows = returns_rows()
    rows[1]["available_at"] = at(4)
    found = validate_table("store/returns", table("store/returns", rows))
    assert any("event_at" in e and "available_at" in e for e in found)


def test_any_other_timestamp_after_available_at_is_rejected() -> None:
    """Generic rule: every timestamp of a row is known by its available_at (or as_of)."""
    assert NotAfter("event_at", "available_at") in checks_for("store/orders")
    assert NotAfter("first_shared_at", "as_of") in checks_for("features/links")
    assert NotAfter("last_shared_at", "as_of") in checks_for("features/links")
    rows = links_rows()
    rows[0]["last_shared_at"] = at(1)  # after as_of: a future fact
    assert any(
        "last_shared_at" in e
        for e in validate_table("features/links", table("features/links", rows))
    )


def test_store_tables_have_no_timestamp_other_than_event_and_available_at() -> None:
    """With delivered_at gone, every late fact is its own row; the generic rule still holds."""
    for name, schema in SCHEMAS.items():
        if name.startswith("store/"):
            stamps = [f.name for f in schema if pa.types.is_timestamp(f.type)]
            assert stamps == ["event_at", "available_at"], name


# ---- Value checks (CON-09) ---------------------------------------------------------------------


def test_return_reason_must_be_a_policy_reason() -> None:
    rows = returns_rows()
    rows[0]["reason"] = "faulty"
    found = validate_table("store/returns", table("store/returns", rows))
    assert any("reason" in e and "faulty" in e for e in found)


def test_negative_days_since_delivery() -> None:
    rows = returns_rows()
    rows[0]["days_since_delivery"] = -3
    assert any(
        "days_since_delivery" in e
        for e in validate_table("store/returns", table("store/returns", rows))
    )


def test_terms_snapshot_window_never_below_14_days() -> None:
    row = {
        "terms_snapshot_id": "TS-1",
        "order_id": "O-1",
        "source": "base",
        "quadrant_at_purchase": None,
        "return_window_days": 7,
        "free_return_shipping": True,
        "instant_refund": True,
        "free_size_exchange": True,
        "coupons_enabled": True,
        "event_at": at(0),
        "available_at": at(0),
    }
    found = validate_table("store/terms_snapshots", table("store/terms_snapshots", [row]))
    assert any("return_window_days" in e for e in found)
    row.update(return_window_days=14, quadrant_at_purchase="Q9")
    found = validate_table("store/terms_snapshots", table("store/terms_snapshots", [row]))
    assert any("quadrant_at_purchase" in e for e in found)
    row.update(quadrant_at_purchase="Q4", source="  ")
    found = validate_table("store/terms_snapshots", table("store/terms_snapshots", [row]))
    assert any("source" in e for e in found)
    row.update(source="DEC-000123")
    assert validate_table("store/terms_snapshots", table("store/terms_snapshots", [row])) == []


def test_scores_reject_out_of_range_risk_and_unknown_enums() -> None:
    row = {
        "customer_id": "C-1",
        "risk": 1.7,
        "evidence_quality": "maybe",
        "value_eur": Decimal("-12.50"),
        "quadrant": "Q9",
        "shap_top3": None,
        "model_version": "risk-0",
        "as_of": at(0),
    }
    found = validate_table("scores/scores", table("scores/scores", [row]))
    assert any("risk" in e for e in found)
    assert any("evidence_quality" in e for e in found)
    assert any("quadrant" in e for e in found)
    row.update(risk=0.2, evidence_quality="sufficient", quadrant=None)
    assert validate_table("scores/scores", table("scores/scores", [row])) == []


def test_decimal_ranges() -> None:
    row = {
        "line_id": "L-1",
        "order_id": "O-1",
        "variant_id": "V-1",
        "size": "M",
        "price_eur": Decimal("-1.00"),
        "event_at": at(0),
        "available_at": at(0),
    }
    found = validate_table("store/order_lines", table("store/order_lines", [row]))
    assert any("price_eur" in e for e in found)


def test_duplicate_keys() -> None:
    rows = returns_rows()
    rows[1]["return_id"] = "RET-1"
    found = validate_table("store/returns", table("store/returns", rows))
    assert any("unique" in e and "return_id" in e for e in found)


def test_links_are_canonical_and_decayed_weight_is_not_above_weight() -> None:
    rows = links_rows()
    rows[0].update(customer_a="C-MARCO-2", customer_b="C-MARCO-1", weight_decayed=0.95)
    rows[0]["kinds"] = ["card", "fingerprint"]
    found = validate_table("features/links", table("features/links", rows))
    assert any("customer_a" in e for e in found)
    assert any("weight_decayed" in e for e in found)
    assert any("kinds" in e and "fingerprint" in e for e in found)


def test_truth_labels_ring_and_family_rules() -> None:
    rows = [
        {
            "customer_id": "C-MARCO-1",
            "behaviour": "promo_ring",
            "ring_id": None,
            "family_id": None,
            "scenario": "main",
        },
        {
            "customer_id": "C-GIULIA",
            "behaviour": "size",
            "ring_id": "R-X",
            "family_id": None,
            "scenario": "main",
        },
        {
            "customer_id": "C-ROSSI-1",
            "behaviour": "shared_benign",
            "ring_id": None,
            "family_id": None,
            "scenario": "elsewhere",
        },
    ]
    found = validate_table("truth/labels", table("truth/labels", rows))
    assert any("ring_id" in e and "required" in e for e in found)
    assert any("ring_id" in e and "only" in e for e in found)
    assert any("family_id" in e for e in found)
    assert any("scenario" in e for e in found)
    rows[0]["ring_id"] = "R-MARCO"
    rows[1]["ring_id"] = None
    rows[2].update(family_id="F-ROSSI", scenario="ood")
    assert validate_table("truth/labels", table("truth/labels", rows)) == []


def test_truth_variants_defect_rules() -> None:
    row = {
        "variant_id": "V-JKT-DEF",
        "base_return_rate": 0.3,
        "defective": True,
        "defect_reason": None,
        "defect_start": at(10),
        "defect_end": at(2),
    }
    found = validate_table("truth/variants", table("truth/variants", [row]))
    assert any("defect_reason" in e for e in found)
    assert any("defect_start" in e and "defect_end" in e for e in found)
    row.update(defect_reason="defective", defect_end=at(40))
    assert validate_table("truth/variants", table("truth/variants", [row])) == []
    ok = {
        **row,
        "defective": False,
        "defect_reason": None,
        "defect_start": None,
        "defect_end": None,
    }
    assert validate_table("truth/variants", table("truth/variants", [ok])) == []
    bad = {**ok, "defect_reason": "defective"}
    assert any(
        "defect_reason" in e
        for e in validate_table("truth/variants", table("truth/variants", [bad]))
    )


def test_checks_reference_existing_columns_with_matching_types() -> None:
    for name, checks in CHECKS.items():
        schema = SCHEMAS[name]
        for check in checks:
            for column in check.columns():
                assert column in schema.names, (name, check)
            if isinstance(check, InRange):
                field_type = schema.field(check.column).type
                assert (
                    pa.types.is_integer(field_type)
                    or pa.types.is_floating(field_type)
                    or pa.types.is_decimal(field_type)
                ), (name, check)
            if isinstance(check, OneOf | Unique | PresentWhen):
                assert check.columns(), (name, check)


def test_every_enum_column_has_a_check() -> None:
    expected = {
        ("store/identifiers", "kind"),
        ("store/terms_snapshots", "quadrant_at_purchase"),
        ("store/returns", "reason"),
        ("store/return_events", "state"),
        ("store/coupons", "kind"),
        ("truth/labels", "behaviour"),
        ("truth/labels", "scenario"),
        ("truth/variants", "defect_reason"),
        ("features/links", "kinds"),
        ("features/product_anomalies", "reason"),
        ("features/product_anomalies", "status"),
        ("scores/scores", "evidence_quality"),
        ("scores/scores", "quadrant"),
        ("raw/asos_events", "split"),
        ("raw/asos_customers", "split"),
        ("raw/asos_variants", "split"),
    }
    actual = {(n, c.column) for n, cs in CHECKS.items() for c in cs if isinstance(c, OneOf)}
    assert expected <= actual


def test_a_valid_table_for_every_contract_round_trips(tmp_path: Path) -> None:
    """Every contract can be written and read with an empty table (schema-only check)."""
    for name, schema in SCHEMAS.items():
        empty = schema.empty_table()
        write_table(name, empty, tmp_path, SYNTH)
        back, _ = read_table(name, tmp_path)
        assert back.num_rows == 0 and back.schema.remove_metadata().equals(schema), name
