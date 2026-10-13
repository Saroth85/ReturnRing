"""DATA-01, DATA-04 and MOD-08 at the file boundary: contracts/io.py and contracts/checks.py.

Every number in these tables is a made-up test fixture (nature: synthetic).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
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
    LINEAGE_SEPARATOR,
    MAX_ORIGIN_CHARS,
    META_CONTRACT,
    META_LINEAGE,
    META_PROVENANCE,
    META_VERSION,
    ContractError,
    Provenance,
    path_for,
    read_table,
    require_derived,
    validate_table,
    write_table,
)
from contracts.parquet import CONTRACT_VERSION, SCHEMAS, TS

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
    assert [p.name for p in (tmp_path / "a" / "store").iterdir()] == ["returns.parquet"]


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


def test_read_table_as_of_needs_an_aware_datetime(tmp_path: Path) -> None:
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    with pytest.raises(ContractError, match="timezone"):
        read_table("store/returns", tmp_path, as_of=datetime(2026, 9, 6))
    write_table("features/links", table("features/links", links_rows()), tmp_path, SYNTH)
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
        "risk_threshold": 0.25,
        "evidence_quality": "maybe",
        "value_eur": Decimal("-12.50"),
        "value_threshold_eur": Decimal("100.00"),
        "quadrant": "Q9",
        "shap_top3": None,
        "model_version": "risk-0",
        "as_of": at(0),
    }
    found = validate_table("scores/scores", table("scores/scores", [row]))
    assert any("risk" in e for e in found)
    assert any("evidence_quality" in e for e in found)
    assert any("quadrant" in e for e in found)
    row.update(risk=0.2, evidence_quality="sufficient", quadrant="Q2")
    assert validate_table("scores/scores", table("scores/scores", [row])) == []


def score_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "customer_id": "C-1",
        "risk": 0.3,
        "risk_threshold": 0.2,
        "evidence_quality": "sufficient",
        "value_eur": Decimal("50.00"),
        "value_threshold_eur": Decimal("100.00"),
        "quadrant": "Q4",
        "shap_top3": None,
        "model_version": "risk-0",
        "as_of": at(0),
    }
    return {**row, **changes}


def test_f15_the_quadrant_follows_the_thresholds_stored_with_it() -> None:
    """F15: every score row carries the risk and value thresholds of its quadrant, and the
    quadrant is policy.engine.quadrant_for of them whenever the evidence is sufficient."""
    ok = [
        score_row(),
        score_row(customer_id="C-2", risk=0.1, quadrant="Q2"),
        score_row(customer_id="C-3", value_eur=Decimal("100.00"), quadrant="Q3"),
        score_row(customer_id="C-4", risk=0.2, value_eur=Decimal("100.01"), quadrant="Q3"),
        score_row(customer_id="C-5", evidence_quality="insufficient", quadrant=None),
    ]
    assert validate_table("scores/scores", table("scores/scores", ok)) == []
    for wrong in (score_row(quadrant="Q2"), score_row(quadrant=None), score_row(quadrant="Q3")):
        found = validate_table("scores/scores", table("scores/scores", [wrong]))
        assert any("quadrant" in e and "threshold" in e for e in found), wrong
    found = validate_table("scores/scores", table("scores/scores", [score_row(risk_threshold=1.5)]))
    assert any("risk_threshold" in e for e in found)
    for column in ("risk_threshold", "value_threshold_eur"):
        assert not SCHEMAS["scores/scores"].field(column).nullable


def request_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "return_id": "RET-1",
        "customer_id": "C-1",
        "decided_at": at(0),
        "path": "withdrawal",
        "context_as_of": at(-10),
        "risk": 0.3,
        "risk_threshold": 0.4,
        "evidence_quality": "sufficient",
        "value_eur": Decimal("50.00"),
        "value_threshold_eur": Decimal("100.00"),
        "quadrant": "Q2",
        "shap_top3": "[]",
        "model_version": "risk-request-0",
        "as_of": at(1),
    }
    return {**row, **changes}


GUARANTEE_ROW = request_row(
    return_id="RET-2",
    path="guarantee",
    context_as_of=None,
    risk=None,
    risk_threshold=None,
    evidence_quality=None,
    value_eur=None,
    value_threshold_eur=None,
    quadrant=None,
    shap_top3=None,
    model_version=None,
)
NO_SNAPSHOT_ROW = request_row(
    return_id="RET-3",
    context_as_of=None,
    evidence_quality="insufficient",
    value_eur=None,
    value_threshold_eur=None,
    quadrant=None,
)


def _request_errors(*rows: dict[str, Any]) -> list[str]:
    name = "scores/request_scores"
    return validate_table(name, table(name, list(rows)))


def test_f19_f20_request_scores_by_path() -> None:
    """F20: the guarantee path has no model version (it is not scored, rule 4); a scored request
    always has one. F19: a scored request without an earlier snapshot has insufficient evidence,
    never a null one. F15: a value comes with its threshold."""
    assert _request_errors(request_row(), GUARANTEE_ROW, NO_SNAPSHOT_ROW) == []
    sentence = {**GUARANTEE_ROW, "model_version": "not scored: guarantee path (rule 4)"}
    assert any("model_version" in e for e in _request_errors(sentence))
    assert any("model_version" in e for e in _request_errors(request_row(model_version=None)))
    unknown = {**NO_SNAPSHOT_ROW, "evidence_quality": None}
    assert any("evidence_quality" in e for e in _request_errors(unknown))
    lone = request_row(value_threshold_eur=None)
    assert any("value_threshold_eur" in e for e in _request_errors(lone))
    assert any("quadrant" in e for e in _request_errors(request_row(quadrant="Q4")))
    assert any("risk_threshold" in e for e in _request_errors(request_row(risk_threshold=None)))


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
        write_table(name, empty, tmp_path, REAL if name.startswith("raw/") else SYNTH)
        back, _ = read_table(name, tmp_path)
        assert back.num_rows == 0 and back.schema.remove_metadata().equals(schema), name


# ---- Review findings C1..C12 (13 Oct 2026) -----------------------------------------------------


def _order_lines(n: int) -> pa.Table:
    """n order lines at spec volume, built column by column (fixture values, nature synthetic)."""
    stamps = pa.array([T0 + timedelta(minutes=k % 40_000) for k in range(n)], TS)
    return pa.table(
        {
            "line_id": [f"L-{k:08d}" for k in range(n)],
            "order_id": [f"O-{k // 3:07d}" for k in range(n)],
            "variant_id": [f"V-{(k * 7919) % 5000:06d}" for k in range(n)],
            "size": [("XS", "S", "M", "L", None)[k % 5] for k in range(n)],
            "price_eur": pa.array([Decimal(100 + k % 20_000) / 100 for k in range(n)]).cast(
                pa.decimal128(12, 2)
            ),
            "event_at": stamps,
            "available_at": stamps,
        }
    )


def test_c1_bytes_do_not_depend_on_the_chunking_at_spec_volume(tmp_path: Path) -> None:
    """DATA-04 at 200,000 order lines: one chunk and 64 chunks give the same file."""
    whole = _order_lines(200_000)
    assert whole.column("line_id").num_chunks == 1
    step = 200_000 // 64 + 1
    chunked = pa.concat_tables([whole.slice(k, step) for k in range(0, 200_000, step)])
    assert chunked.column("line_id").num_chunks == 64
    one = write_table("store/order_lines", whole, tmp_path / "one", SYNTH)
    many = write_table("store/order_lines", chunked, tmp_path / "many", SYNTH)
    assert hashlib.sha256(one.read_bytes()).digest() == hashlib.sha256(many.read_bytes()).digest()


def anomaly_row(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "variant_id": "V-JKT-DEF",
        "reason": "not_as_described",
        "week": date(2026, 8, 31),
        "observed": 9,
        "expected": 2.5,
        "z": 4.1,
        "n": 24,
        "status": "open",
        "as_of": at(0),  # 1 Sep 2026
    }
    return {**row, **changes}


def test_c2_anomaly_week_is_not_after_as_of() -> None:
    """Rule 7: a week that starts after as_of is a future fact."""
    ok = table("features/product_anomalies", [anomaly_row(week=date(2026, 9, 1))])
    assert validate_table("features/product_anomalies", ok) == []
    future = table("features/product_anomalies", [anomaly_row(week=date(2026, 9, 7))])
    found = validate_table("features/product_anomalies", future)
    assert any("week" in e and "as_of" in e for e in found)


def test_c3_as_of_tables_refuse_a_read_before_their_as_of(tmp_path: Path) -> None:
    write_table("features/links", table("features/links", links_rows()), tmp_path, SYNTH)
    links, _ = read_table("features/links", tmp_path, as_of=at(0))
    assert links.num_rows == 1
    assert read_table("features/links", tmp_path, as_of=at(3))[0].num_rows == 1
    with pytest.raises(ContractError, match="as_of"):
        read_table("features/links", tmp_path, as_of=at(-1))
    with pytest.raises(ContractError, match="timezone"):
        read_table("features/links", tmp_path, as_of=datetime(2026, 9, 6))


def test_c3_static_tables_refuse_as_of(tmp_path: Path) -> None:
    labels = [
        {
            "customer_id": "C-1",
            "behaviour": "normal",
            "ring_id": None,
            "family_id": None,
            "scenario": "main",
        }
    ]
    write_table("truth/labels", table("truth/labels", labels), tmp_path, SYNTH)
    with pytest.raises(ContractError, match="static"):
        read_table("truth/labels", tmp_path, as_of=at(0))


def test_c3_as_of_table_keys_include_as_of() -> None:
    """Two snapshots of one pair are two rows; the same pair twice in one snapshot is not."""
    later = {**links_rows()[0], "as_of": at(7)}
    snapshots = table("features/links", [links_rows()[0], later])
    assert validate_table("features/links", snapshots) == []
    twice = table("features/links", [links_rows()[0], links_rows()[0]])
    assert any("unique" in e for e in validate_table("features/links", twice))
    for name in sorted(n for n in SCHEMAS if n.startswith(("features/", "scores/"))):
        keys = [c.keys for c in CHECKS[name] if isinstance(c, Unique)]
        assert keys and all(k[-1] == "as_of" for k in keys), name


@pytest.mark.parametrize(
    ("kinds", "message"),
    [(None, "null"), ([], "empty"), (["card", "card"], "repeat")],
)
def test_c7_link_kinds_are_present_non_empty_and_distinct(kinds: Any, message: str) -> None:
    rows = links_rows()
    rows[0]["kinds"] = kinds
    found = validate_table("features/links", table("features/links", rows))
    assert any("kinds" in e and message in e for e in found), found


def coupon_rows() -> list[dict[str, Any]]:
    issue = {
        "coupon_code": "WLC-1",
        "customer_id": "C-1",
        "kind": "welcome",
        "discount_eur": Decimal("0.00"),
        "order_id": None,
        "event_at": at(0),
        "available_at": at(0),
    }
    redeem = {**issue, "discount_eur": Decimal("7.50"), "order_id": "O-1"}
    return [issue, {**redeem, "event_at": at(1), "available_at": at(1)}]


def test_c8_coupon_log_valid() -> None:
    assert validate_table("store/coupons", table("store/coupons", coupon_rows())) == []
    issued_only = coupon_rows()[:1]
    assert validate_table("store/coupons", table("store/coupons", issued_only)) == []


def test_c8_redemption_without_issue_row() -> None:
    found = validate_table("store/coupons", table("store/coupons", coupon_rows()[1:]))
    assert any("coupon_code" in e and "issue" in e for e in found), found


def test_c8_two_issue_rows_for_one_code() -> None:
    rows = coupon_rows()
    rows.append({**rows[0], "event_at": at(0.5), "available_at": at(0.5)})
    found = validate_table("store/coupons", table("store/coupons", rows))
    assert any("coupon_code" in e and "issue" in e for e in found), found


def test_c8_two_redemptions_for_one_code() -> None:
    rows = coupon_rows()
    rows.append({**rows[1], "order_id": "O-2", "event_at": at(2), "available_at": at(2)})
    found = validate_table("store/coupons", table("store/coupons", rows))
    assert any("coupon_code" in e and "redemption" in e for e in found), found


def test_c8_redemption_before_issue() -> None:
    rows = coupon_rows()
    rows[1].update(event_at=at(-1), available_at=at(-1))
    found = validate_table("store/coupons", table("store/coupons", rows))
    assert any("coupon_code" in e and "before" in e for e in found), found


def test_c9_defect_end_only_on_defective_variants() -> None:
    row = {
        "variant_id": "V-1",
        "base_return_rate": 0.3,
        "defective": False,
        "defect_reason": None,
        "defect_start": None,
        "defect_end": at(40),
    }
    found = validate_table("truth/variants", table("truth/variants", [row]))
    assert any("defect_end" in e for e in found), found
    never_fixed = {**row, "defective": True, "defect_reason": "defective", "defect_start": at(1)}
    never_fixed["defect_end"] = None
    assert validate_table("truth/variants", table("truth/variants", [never_fixed])) == []


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_c10_non_finite_floats_are_rejected(value: float) -> None:
    rows = links_rows()
    rows[0]["weight_decayed"] = value
    found = validate_table("features/links", table("features/links", rows))
    assert any("weight_decayed" in e and "NaN/inf" in e for e in found), found


REAL = Provenance("ASOS GraphReturns, test", date(2021, 9, 1), date(2021, 11, 30), "real")
SIMULATED = Provenance("Policy simulator, test", date(2025, 10, 1), date(2026, 9, 30), "simulated")


def asos_event_rows() -> list[dict[str, Any]]:
    return [{"split": "train", "variant_hash": 1, "customer_hash": 2, "is_returned": True}]


def test_c11_each_family_has_its_nature(tmp_path: Path) -> None:
    events = table("raw/asos_events", asos_event_rows())
    write_table("raw/asos_events", events, tmp_path, REAL)
    with pytest.raises(ContractError, match="nature"):
        write_table("raw/asos_events", events, tmp_path, SYNTH)
    returns = table("store/returns", returns_rows())
    with pytest.raises(ContractError, match="nature"):
        write_table("store/returns", returns, tmp_path, replace(SYNTH, nature="real"))
    labels = [
        {"customer_id": "C-1", "behaviour": "normal", "ring_id": None, "family_id": None}
        | {"scenario": "main"}
    ]
    with pytest.raises(ContractError, match="nature"):
        write_table("truth/labels", table("truth/labels", labels), tmp_path, SIMULATED)
    links = table("features/links", links_rows())
    write_table("features/links", links, tmp_path, SIMULATED)
    write_table("features/links", links, tmp_path, SYNTH)
    with pytest.raises(ContractError, match="nature"):
        write_table("features/links", links, tmp_path, replace(SYNTH, nature="real"))


def test_c11_nature_override_is_explicit(tmp_path: Path) -> None:
    """A test fixture shaped like ASOS is synthetic: it says so, and the caller says it knows."""
    events = table("raw/asos_events", asos_event_rows())
    path = write_table("raw/asos_events", events, tmp_path, SYNTH, nature_override="synthetic")
    assert read_table("raw/asos_events", tmp_path)[1].nature == "synthetic"
    assert path.exists()
    with pytest.raises(ContractError, match="override"):
        write_table("raw/asos_events", events, tmp_path, SYNTH, nature_override="simulated")


@pytest.mark.parametrize("days", [-400.0, 40.0])
def test_c11_event_at_lies_in_the_provenance_period(tmp_path: Path, days: float) -> None:
    rows = returns_rows()
    rows[0].update(event_at=at(days), available_at=at(days))  # SYNTH ends on 30 Sep 2026
    with pytest.raises(ContractError, match="period"):
        write_table("store/returns", table("store/returns", rows), tmp_path, SYNTH)
    assert not path_for("store/returns", tmp_path).exists()


def test_c12_missing_provenance_is_a_contract_error(tmp_path: Path) -> None:
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    src = path_for("store/returns", tmp_path)
    pf = pq.read_table(src)
    meta = {k: v for k, v in pf.schema.metadata.items() if k != META_PROVENANCE}
    pq.write_table(pf.replace_schema_metadata(meta), src)
    with pytest.raises(ContractError, match="provenance"):
        read_table("store/returns", tmp_path)


# ---- P3: one atomic write, never through a planted symlink -------------------------------------

# Temporary names a writer could use next to its target: the shared helper's (contracts.files)
# and the one write_table used before review P3. A symlink is planted at each.
TEMP_NAMES = ("{}.tmp", ".{}.part")


def plant_symlinks(target: Path, victims: Path) -> dict[str, Path]:
    """A symlink at every temporary name of `target`, each to its own victim file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    victims.mkdir(exist_ok=True)
    planted = {}
    for i, pattern in enumerate(TEMP_NAMES):
        victim = victims / f"victim-{i}.txt"
        victim.write_text("keep me")
        name = pattern.format(target.name)
        (target.parent / name).symlink_to(victim)
        planted[name] = victim
    return planted


def test_p3_write_table_never_writes_through_a_planted_symlink(tmp_path: Path) -> None:
    """Review P3: a symlink planted at the temporary name must not redirect the Parquet bytes to
    another file, and the target must end up a regular file, not a link to the victim."""
    target = path_for("store/returns", tmp_path / "out")
    planted = plant_symlinks(target, tmp_path / "victims")
    write_table("store/returns", table("store/returns", returns_rows()), tmp_path / "out", SYNTH)
    assert all(victim.read_text() == "keep me" for victim in planted.values())
    assert target.is_file() and not target.is_symlink()
    assert read_table("store/returns", tmp_path / "out")[0].num_rows == len(returns_rows())
    left = {p.name for p in target.parent.iterdir()} - set(planted)
    assert left == {target.name}


def test_p3_write_table_leaves_nothing_when_a_directory_is_in_the_way(tmp_path: Path) -> None:
    """Review P3: the rename onto a directory fails; no temporary file is left behind."""
    target = path_for("store/returns", tmp_path)
    target.mkdir(parents=True)
    with pytest.raises(OSError):
        write_table("store/returns", table("store/returns", returns_rows()), tmp_path, SYNTH)
    assert target.is_dir()
    assert [p.name for p in target.parent.iterdir()] == [target.name]


# ---- Phase 2 review F17 and F22: short origin, lineage, joins on the same data ------------------


def test_f17_origin_holds_at_most_300_characters() -> None:
    """F17: the API's Provenance.origin (ADR-15) holds at most 300 characters; a longer origin
    could never reach a case card, so it is refused where it is made and where it is read."""
    assert MAX_ORIGIN_CHARS == 300
    Provenance("x" * 300, SYNTH.period_start, SYNTH.period_end, "synthetic")
    with pytest.raises(ContractError, match="300"):
        Provenance("x" * 301, SYNTH.period_start, SYNTH.period_end, "synthetic")
    text = SYNTH.to_json().replace(SYNTH.origin, "y" * 301)
    with pytest.raises(ContractError, match="300"):
        Provenance.from_json(text)


def test_f17_the_lineage_has_its_own_metadata_key(tmp_path: Path) -> None:
    """F17: the full chain of origins goes to returnring.lineage; returnring.provenance keeps the
    four fields of the API's Provenance."""
    child = SYNTH.derive("ReturnRing features, test", date(2025, 10, 1), date(2026, 9, 30))
    assert child.origin == "ReturnRing features, test"
    assert child.lineage == f"ReturnRing features, test{LINEAGE_SEPARATOR}{SYNTH.origin}"
    assert child.chain == child.lineage and SYNTH.chain == SYNTH.origin
    grandchild = child.derive(
        "ReturnRing scores, test", date(2025, 10, 1), date(2026, 9, 30), detail="as of T"
    )
    assert grandchild.chain.endswith(LINEAGE_SEPARATOR + child.chain)
    assert grandchild.chain.startswith("ReturnRing scores, test; as of T")
    rows = table("store/returns", returns_rows())
    path = write_table("store/returns", rows, tmp_path, grandchild)
    meta = pq.read_schema(path).metadata
    fields = set(json.loads(meta[META_PROVENANCE]))
    assert fields == {"origin", "period_start", "period_end", "nature"}
    assert meta[META_LINEAGE].decode() == grandchild.lineage
    assert read_table("store/returns", tmp_path)[1] == grandchild


def test_f17_a_lineage_must_start_with_its_origin() -> None:
    with pytest.raises(ContractError, match="lineage"):
        Provenance("a", SYNTH.period_start, SYNTH.period_end, "synthetic", lineage="b <- a")


def test_f22_a_table_joined_to_another_store_is_refused() -> None:
    """F22: a features table built from another store (an older seed) is refused when joined."""
    other = replace(SYNTH, origin="Navigli Fashion generator, another seed")
    features = SYNTH.derive("ReturnRing features, test", date(2025, 10, 1), date(2026, 9, 30))
    require_derived(features, SYNTH, "features/customer_features", "store/orders")
    with pytest.raises(ContractError, match="features/customer_features"):
        require_derived(features, other, "features/customer_features", "store/orders")
    # a suffix of the chain that is not a whole origin does not count
    tail = replace(SYNTH, origin=SYNTH.origin[5:])
    with pytest.raises(ContractError):
        require_derived(features, tail, "features/customer_features", "store/orders")
