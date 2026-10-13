"""Lever catalogue (POL-7, CON-05, ADR-13) and Levers.to_contract (UNIT-04 on the contract side)."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from contracts.levers import (
    LEVERS,
    LEVERS_BY_ID,
    LeverError,
    format_lever_id,
    lever_from_id,
    parse_lever_id,
)
from policy.engine import NAVIGLI_BASE_TERMS, levers_for, standard_levers
from policy.types import Levers, Quadrant, ReturnTerms

BASE = NAVIGLI_BASE_TERMS


def test_one_catalogue_entry_per_lever_field() -> None:
    """Every Levers field, with future_terms expanded into the ReturnTerms fields."""
    term_fields = [f.name for f in fields(ReturnTerms)]
    other_fields = [f.name for f in fields(Levers) if f.name != "future_terms"]
    assert [lever.id for lever in LEVERS] == term_fields + other_fields
    assert set(LEVERS_BY_ID) == set(term_fields + other_fields)


def test_scopes_follow_the_lever_table_of_section_8_6() -> None:
    scope = {lever.id: lever.scope for lever in LEVERS}
    assert scope == {
        "return_window_days": "future_orders",
        "free_return_shipping": "future_orders",
        "instant_refund": "future_orders",
        "free_size_exchange": "all_orders",
        "coupons_enabled": "future_orders",
        "cluster_coupons_disabled": "future_orders_cluster",
        "transparency_notice": "communication",
        "size_advice": "current_order",
        "priority_size_exchange": "all_orders",
    }
    assert {lever.id: lever.value_type for lever in LEVERS}["return_window_days"] == "integer"
    assert all(lever.value_type == "boolean" for lever in LEVERS[1:])


def test_to_contract_q4_with_cluster_is_six_levers_in_catalogue_order() -> None:
    q4 = levers_for(Quadrant.Q4, BASE, has_cluster=True)
    assert q4.to_contract(BASE) == [
        {"id": "return_window_days", "value": 14, "scope": "future_orders"},
        {"id": "free_return_shipping", "value": False, "scope": "future_orders"},
        {"id": "instant_refund", "value": False, "scope": "future_orders"},
        {"id": "coupons_enabled", "value": False, "scope": "future_orders"},
        {"id": "cluster_coupons_disabled", "value": True, "scope": "future_orders_cluster"},
        {"id": "transparency_notice", "value": True, "scope": "communication"},
    ]


def test_to_contract_lists_only_the_differences_from_standard_levers() -> None:
    assert standard_levers(BASE).to_contract(BASE) == []
    assert Levers.standard(BASE) == standard_levers(BASE)
    q1 = levers_for(Quadrant.Q1, BASE, has_cluster=False)
    assert q1.to_contract(BASE) == [
        {"id": "return_window_days", "value": 60, "scope": "future_orders"},
        {"id": "priority_size_exchange", "value": True, "scope": "all_orders"},
    ]
    q3 = levers_for(Quadrant.Q3, BASE, has_cluster=True)
    assert [x["id"] for x in q3.to_contract(BASE)] == ["instant_refund", "transparency_notice"]


def test_to_contract_on_a_minimal_base_upgrades_size_exchange_for_all_orders() -> None:
    minimal = ReturnTerms(14, False, False, False, False)
    q2 = levers_for(Quadrant.Q2, minimal, has_cluster=False)
    assert q2.to_contract(minimal) == []
    q4 = levers_for(Quadrant.Q4, minimal, has_cluster=False)
    assert q4.to_contract(minimal) == [
        {"id": "free_size_exchange", "value": True, "scope": "all_orders"},
        {"id": "transparency_notice", "value": True, "scope": "communication"},
    ]


def test_size_advice_off_is_a_visible_difference() -> None:
    no_advice = replace(standard_levers(BASE), size_advice=False)
    assert no_advice.to_contract(BASE) == [
        {"id": "size_advice", "value": False, "scope": "current_order"}
    ]
    assert no_advice.lever_ids(BASE) == ["size_advice:false"]


@pytest.mark.parametrize("quadrant", list(Quadrant))
@pytest.mark.parametrize("has_cluster", [False, True])
def test_lever_ids_are_the_id_value_strings_of_to_contract(
    quadrant: Quadrant, has_cluster: bool
) -> None:
    levers = levers_for(quadrant, BASE, has_cluster)
    objects = levers.to_contract(BASE)
    ids = levers.lever_ids(BASE)
    assert ids == [format_lever_id(x["id"], x["value"]) for x in objects]
    assert [lever_from_id(i) for i in ids] == objects


def test_format_and_parse_are_inverse() -> None:
    assert format_lever_id("instant_refund", False) == "instant_refund:false"
    assert format_lever_id("return_window_days", 14) == "return_window_days:14"
    assert parse_lever_id("instant_refund:false") == ("instant_refund", False)
    assert parse_lever_id("cluster_coupons_disabled:true") == ("cluster_coupons_disabled", True)
    assert parse_lever_id("return_window_days:60") == ("return_window_days", 60)
    assert lever_from_id("transparency_notice:true") == {
        "id": "transparency_notice",
        "value": True,
        "scope": "communication",
    }


@pytest.mark.parametrize(
    "text",
    [
        "refund_after_inspection:true",
        "welcome_coupon:false",
        "instant_refund",
        "instant_refund:False",
        "instant_refund:0",
        "return_window_days:7",
        "return_window_days:true",
        "return_window_days:-14",
        "return_window_days:1e3",
        ":true",
    ],
)
def test_parse_rejects_unknown_ids_and_bad_values(text: str) -> None:
    with pytest.raises(LeverError):
        parse_lever_id(text)


@pytest.mark.parametrize(
    "lever_id, value",
    [
        ("welcome_coupon", False),
        ("instant_refund", 0),
        ("return_window_days", True),
        ("return_window_days", 13),
    ],
)
def test_format_rejects_unknown_ids_and_bad_values(lever_id: str, value: int | bool) -> None:
    with pytest.raises(LeverError):
        format_lever_id(lever_id, value)
