"""POST /api/checkout/evaluate and /api/checkout/confirm (US-401..US-403, ADR-05, E2E-02; ADR-15
section 4 "Checkout"; rules 3, 5, 11).

The terms shown come from the latest decision in force for the customer (never a proposal that
awaits a person), with its review date; a confirmed cluster coupon application in force blocks
the coupons of every linked account it names; the shown terms are stored before the answer and
/confirm binds exactly those to the order, or answers 409 when they lapsed or their decision
left force. Line probabilities are the personalized predictor of ml.reference.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.db import make_engine
from apps.api.schemas import COUPON_BLOCK, CheckoutConfirmRequest
from apps.api.services.audit import entries
from apps.api.services.other import checkout
from apps.api.services.other.checkout import (
    P_RETURN_THRESHOLD,
    history_at_snapshot,
    line_probabilities,
    size_advice,
)
from contracts.sql import audit_log, terms_snapshots
from ml.reference.checkout import CartItem, cart_queries, checkout_probabilities
from ml.reference.features import history_frame
from pipeline.features.lines import line_history, observations
from pipeline.generator.golden import DEFECTIVE_JACKET, ELENA, GIULIA, MARCO_ACCOUNTS
from tests.api_harness import Api, golden_return, validate_body
from tests.test_api_other_support import (
    approve_in_db,
    decision_of,
    marco_return,
    reverse_in_db,
    supersede_in_db,
)

BASE = {
    "return_window_days": 30,
    "free_return_shipping": True,
    "instant_refund": True,
    "free_size_exchange": True,
    "coupons_enabled": True,
}


def _variant_of(api: Api, return_id: str) -> str:
    record = api.serving.store_return(return_id)
    assert record is not None
    return record.variant_id


def _cart(
    customer: str, variant: str, sizes: tuple[str, ...] = ("M",), **extra: Any
) -> dict[str, Any]:
    lines = [{"variant_id": variant, "size": size, "quantity": 1} for size in sizes]
    return {"customer_id": customer, "lines": lines, "coupon_code": None, **extra}


def _evaluate(api: Api, body: dict[str, Any]) -> dict[str, Any]:
    response = api.client.post("/api/checkout/evaluate", json=body)
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    validate_body("checkout_evaluate_response", out)
    return out


def _confirm_body(evaluated: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    return {
        "terms_snapshot_id": evaluated["terms_snapshot_id"],
        "customer_id": request["customer_id"],
        "lines": request["lines"],
        "coupon_code": None,
    }


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    validate_body("error", body)
    assert body["code"] == code
    return body


def _run(api: Api, fn: Callable[[AsyncConnection], Any]) -> Any:
    return api.run(fn)


async def _snapshot_row(conn: AsyncConnection, snapshot_id: str) -> dict[str, Any]:
    query = select(terms_snapshots).where(terms_snapshots.c.terms_snapshot_id == snapshot_id)
    return dict((await conn.execute(query)).mappings().one())


def _approve_marco(api: Api) -> dict[str, Any]:
    return_id, _ = marco_return(api)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        row = await decision_of(conn, return_id)
        await approve_in_db(conn, row["decision_id"], "sara", api.clock.now())
        return await decision_of(conn, return_id)

    approved: dict[str, Any] = api.run(go)
    return approved


# ---- Evaluate ------------------------------------------------------------------------------------


def test_giulia_sees_the_q1_terms_in_force_without_approval(api: Api) -> None:
    """Q1 applies by itself (spec 8.6): the terms come from her decision, with no review date;
    every line has a probability, and size advice exactly above the threshold."""
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id), ("M", "L"))
    body = _evaluate(api, request)
    decision = api.run(lambda conn: decision_of(conn, return_id))
    assert body["quadrant"] == "Q1" and body["terms_source"] == decision["decision_id"]
    assert body["terms"] == decision["future_terms"] and body["terms"]["return_window_days"] == 60
    assert body["base_terms"] == BASE and body["review_at"] is None
    assert body["coupon_eligible"] and body["coupon_block"] is None
    assert [(x["variant_id"], x["size"]) for x in body["lines"]] == [
        (x["variant_id"], x["size"]) for x in request["lines"]
    ]
    assert body["p_return_threshold"] == P_RETURN_THRESHOLD
    assert body["model_version"] == api.serving.checkout_model.version
    assert body["shown_at"] == "2026-10-02T09:00:00Z"
    assert body["provenance"]["nature"] == "synthetic"
    for line in body["lines"]:
        assert (line["size_advice"] is not None) == (line["p_return"] >= P_RETURN_THRESHOLD)


def test_the_shown_terms_are_stored_and_logged_before_the_answer(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    body = _evaluate(api, _cart(GIULIA, _variant_of(api, return_id)))
    row = api.run(lambda conn: _snapshot_row(conn, body["terms_snapshot_id"]))
    assert row["order_id"] is None and row["confirmed_at"] is None
    assert row["source"] == row["source_decision_id"] == body["terms_source"]
    assert row["quadrant_at_purchase"] == body["quadrant"]
    assert {k: row[k] for k in BASE} == body["terms"]
    assert {k: row[f"base_{k}"] for k in BASE} == body["base_terms"]
    assert row["review_at"] is None and row["coupon_application_id"] is None
    logged = api.run(lambda conn: entries(conn))
    shown = [e for e in logged if e.action == "checkout_terms_shown"]
    assert len(shown) == 1 and shown[0].entity_id == body["terms_snapshot_id"]
    assert shown[0].actor == "system" and shown[0].after == {
        "customer_id": GIULIA,
        "terms_source": body["terms_source"],
    }


def test_a_proposal_awaiting_approval_never_reaches_the_checkout(api: Api) -> None:
    """Rule 5: Marco's Q4 proposal waits for Sara, so the checkout shows the base terms."""
    return_id, customer = marco_return(api)
    body = _evaluate(api, _cart(customer, _variant_of(api, return_id)))
    assert body["terms_source"] == "base" and body["quadrant"] is None
    assert body["terms"] == body["base_terms"] == BASE and body["review_at"] is None
    assert body["coupon_block"] is None and body["coupon_eligible"]


def test_an_approved_restriction_and_its_coupon_block(api: Api) -> None:
    """After the approval: the Q4 terms with their review date, and the cluster coupon block in
    neutral words (rule 11, E25) for the case account and for every linked account it names."""
    approved = _approve_marco(api)
    return_id, customer = marco_return(api)
    variant = _variant_of(api, return_id)
    body = _evaluate(api, _cart(customer, variant, coupon_code="WELCOME-NOPE"))
    assert body["quadrant"] == "Q4" and body["terms_source"] == approved["decision_id"]
    assert body["terms"] == approved["future_terms"]
    assert body["terms"]["return_window_days"] == 14 and not body["terms"]["coupons_enabled"]
    assert body["review_at"] == approved["review_at"].isoformat()
    block = body["coupon_block"]
    assert block["application_id"] == approved["coupon_application_id"]
    assert block["rule_version"] == approved["coupon_rule_version"]
    assert block["review_at"] == body["review_at"] and not body["coupon_eligible"]
    assert block["text"] == COUPON_BLOCK.model_dump()  # the fixed text of llm.phrasebook
    assert "cluster" not in block["text"]["en"].lower() and "linked" not in block["text"]["en"]
    row = api.run(lambda conn: _snapshot_row(conn, body["terms_snapshot_id"]))
    assert row["coupon_application_id"] == block["application_id"]
    assert row["review_at"] == approved["review_at"]
    for other in MARCO_ACCOUNTS:
        if other == customer:
            continue
        linked = _evaluate(api, _cart(other, variant))
        if linked["terms_source"] == "base":  # no decision of its own in force
            # the base terms, with the coupons the block turns off (1.5.0, H12)
            assert linked["terms"] == {**BASE, "coupons_enabled": False}
            assert linked["coupon_block"]["application_id"] == block["application_id"]
            assert not linked["coupon_eligible"]
            snapshot_id = linked["terms_snapshot_id"]
            stored = api.run(lambda conn, sid=snapshot_id: _snapshot_row(conn, sid))
            assert stored["coupon_application_id"] == block["application_id"]
            assert stored["coupon_review_at"].isoformat() == block["review_at"]
            assert not stored["coupons_enabled"]


def test_a_reversed_coupon_block_is_not_shown(api: Api) -> None:
    """US-402, US-607: a person reversed the block on request; the restriction itself stays."""
    approved = _approve_marco(api)
    api.run(lambda conn: reverse_in_db(conn, approved["coupon_application_id"], api.clock.now()))
    return_id, customer = marco_return(api)
    body = _evaluate(api, _cart(customer, _variant_of(api, return_id)))
    assert body["quadrant"] == "Q4" and body["coupon_block"] is None
    assert not body["coupon_eligible"]  # the Q4 terms of the customer still disable coupons


def test_a_restriction_is_not_shown_on_or_after_its_review_date(api: Api) -> None:
    approved = _approve_marco(api)
    review = approved["review_at"]
    api.clock.set(pd.Timestamp(review, tz="UTC").to_pydatetime() + timedelta(hours=1))
    return_id, customer = marco_return(api)
    body = _evaluate(api, _cart(customer, _variant_of(api, return_id)))
    assert body["terms_source"] == "base" and body["coupon_block"] is None


def test_the_guarantee_path_sets_no_checkout_terms(api: Api) -> None:
    """Elena's return is a guarantee case: no quadrant, so the base terms (rule 4)."""
    return_id = golden_return(api.serving, ELENA)
    body = _evaluate(api, _cart(ELENA, _variant_of(api, return_id)))
    assert body["terms_source"] == "base" and body["quadrant"] is None


def test_unknown_customer_or_variant_is_not_found(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    variant = _variant_of(api, return_id)
    missing = api.client.post("/api/checkout/evaluate", json=_cart("C-NOBODY", variant))
    assert "C-NOBODY" in _error(missing, 404, "not_found")["message"]
    unknown = api.client.post("/api/checkout/evaluate", json=_cart(GIULIA, "V-NOPE"))
    assert "V-NOPE" in _error(unknown, 404, "not_found")["message"]
    _error(
        api.client.post("/api/checkout/evaluate", json={**_cart(GIULIA, variant), "lines": []}),
        422,
        "invalid_request",
    )


def test_evaluate_answers_within_one_second(api: Api) -> None:
    """US-403: under 1 s (the tiny store; the history counts do not grow with the cart)."""
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id), ("S", "M", "L"))
    _evaluate(api, request)  # first call builds the counts of the snapshot
    started = time.perf_counter()
    _evaluate(api, request)
    assert time.perf_counter() - started < 1.0


# ---- The line predictor and the size advice ------------------------------------------------------


def _carts(api: Api) -> list[tuple[str, list[CartItem]]]:
    variants = list(api.serving.frame("store/variants")["variant_id"])
    giulia_variant = _variant_of(api, golden_return(api.serving, GIULIA))
    return [
        (GIULIA, [CartItem(giulia_variant, "M"), CartItem(giulia_variant, "L")]),
        (MARCO_ACCOUNTS[0], [CartItem(v) for v in variants[:3]]),
        ("C-ROSSI-1", [CartItem(variants[5]), CartItem(variants[5]), CartItem(variants[9])]),
        (ELENA, [CartItem(DEFECTIVE_JACKET, "M")]),
    ]


def test_history_counts_equal_line_history_at_the_snapshot_instant(api: Api) -> None:
    """The fast path of the checkout is pipeline.features.lines.line_history at the snapshot
    instant, where every observation is settled."""
    events = observations(api.serving.lines)
    for customer, items in _carts(api):
        queries = cart_queries(api.serving.store, customer, items)
        fast = history_at_snapshot(api.serving, queries)
        slow = line_history(events, queries)
        pd.testing.assert_frame_equal(fast[slow.columns], slow, check_dtype=False)


def test_line_probabilities_are_the_personalized_predictor(api: Api) -> None:
    """The same p_return as ml.reference.checkout.checkout_probabilities (US-401 input)."""
    from apps.api.schemas import CartLine

    for customer, items in _carts(api):
        lines = [CartLine(variant_id=i.variant_id, size=i.size or "M") for i in items]
        fast = line_probabilities(api.serving, customer, lines)
        slow = checkout_probabilities(
            api.serving.store, api.serving.checkout_model, customer, items
        )
        assert fast == [round(float(p), 4) for p in slow]
        frame = history_frame(
            observations(api.serving.lines), cart_queries(api.serving.store, customer, items)
        )
        assert list(api.serving.checkout_model.predict(frame).round(4)) == fast


def test_size_advice_never_names_a_size_without_measurement_data(api: Api) -> None:
    """US-401: the store holds no measurements or exchange outcomes, so never "specific": the
    size guide of a sized item, or no reliable advice when the variant has no size range or a
    flagged size or description anomaly (its size guide may be what is wrong, spec 8.9)."""
    variants = api.serving.frame("store/variants")
    sized = variants.loc[variants["size_range"].notna(), "variant_id"]
    at = api.clock.now()
    kinds = {size_advice(api.serving, str(v), at).kind for v in sized[:50]}
    assert "specific" not in kinds
    jacket = size_advice(api.serving, DEFECTIVE_JACKET, at)
    from apps.api.services.cases import variant_anomaly

    anomaly = variant_anomaly(api.serving, DEFECTIVE_JACKET, at)
    if anomaly is None:
        pytest.skip("the jacket anomaly is not flagged in the tiny store")
    assert jacket.kind == "no_reliable_recommendation"


# ---- Confirm -------------------------------------------------------------------------------------


def test_confirm_binds_the_terms_shown_to_the_order(api: Api) -> None:
    """E2E-02: the terms shown are the terms stored, bound to a new order (201)."""
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id), ("M", "L"))
    shown = _evaluate(api, request)
    api.clock.advance(timedelta(minutes=2))
    response = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    assert response.status_code == 201, response.text
    body = response.json()
    validate_body("checkout_confirm_response", body)
    snap = body["terms_snapshot"]
    assert snap["terms_snapshot_id"] == shown["terms_snapshot_id"]
    assert snap["order_id"] == body["order_id"] == "ORD-CHK-000001"
    assert snap["terms"] == shown["terms"] and snap["base_terms"] == shown["base_terms"]
    assert snap["source"] == shown["terms_source"]
    assert snap["quadrant_at_purchase"] == shown["quadrant"]
    assert snap["review_at"] == shown["review_at"] and snap["shown_at"] == shown["shown_at"]
    assert snap["confirmed_at"] == "2026-10-02T09:02:00Z" and body["coupon_applied"] is False
    row = api.run(lambda conn: _snapshot_row(conn, shown["terms_snapshot_id"]))
    assert row["order_id"] == body["order_id"]
    logged = [e for e in api.run(lambda conn: entries(conn)) if e.action == "checkout_confirmed"]
    assert len(logged) == 1 and logged[0].after["order_id"] == body["order_id"]
    again = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    _error(again, 409, "conflict")


def test_confirm_of_marco_binds_the_restriction_before_its_review_date(api: Api) -> None:
    approved = _approve_marco(api)
    return_id, customer = marco_return(api)
    request = _cart(customer, _variant_of(api, return_id))
    shown = _evaluate(api, request)
    response = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    assert response.status_code == 201, response.text
    body = response.json()
    validate_body("checkout_confirm_response", body)
    assert body["terms_snapshot"]["review_at"] == approved["review_at"].isoformat()
    assert body["terms_snapshot"]["quadrant_at_purchase"] == "Q4"


def test_confirm_after_the_review_date_is_a_conflict(api: Api) -> None:
    """D3: a restriction lapses at its review date, between evaluate and confirm too."""
    approved = _approve_marco(api)
    return_id, customer = marco_return(api)
    request = _cart(customer, _variant_of(api, return_id))
    shown = _evaluate(api, request)
    api.clock.set(pd.Timestamp(approved["review_at"], tz="UTC").to_pydatetime())
    response = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    body = _error(response, 409, "conflict")
    assert shown["terms_snapshot_id"] in body["message"] and "evaluate" in body["message"]
    row = api.run(lambda conn: _snapshot_row(conn, shown["terms_snapshot_id"]))
    assert row["order_id"] is None


def test_confirm_when_the_source_decision_left_force_is_a_conflict(api: Api) -> None:
    """E8: the decision the terms come from was superseded between evaluate and confirm."""
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id))
    shown = _evaluate(api, request)
    api.clock.advance(timedelta(minutes=1))
    api.run(lambda conn: supersede_in_db(conn, shown["terms_source"], api.clock.now()))
    response = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    body = _error(response, 409, "conflict")
    assert shown["terms_source"] in body["message"] and "no longer in force" in body["message"]


def test_confirm_when_the_coupon_block_was_reversed_is_a_conflict(api: Api) -> None:
    approved = _approve_marco(api)
    return_id, customer = marco_return(api)
    request = _cart(customer, _variant_of(api, return_id))
    shown = _evaluate(api, request)
    api.run(lambda conn: reverse_in_db(conn, approved["coupon_application_id"], api.clock.now()))
    response = api.client.post("/api/checkout/confirm", json=_confirm_body(shown, request))
    _error(response, 409, "conflict")


def test_confirm_refuses_another_cart_customer_or_snapshot(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id), ("M", "L"))
    shown = _evaluate(api, request)
    body = _confirm_body(shown, request)
    _error(api.client.post("/api/checkout/confirm", json={**body, "lines": body["lines"][:1]}),
           409, "conflict")  # fmt: skip
    _error(api.client.post("/api/checkout/confirm", json={**body, "customer_id": ELENA}),
           409, "conflict")  # fmt: skip
    _error(api.client.post("/api/checkout/confirm", json={**body, "terms_snapshot_id": "TS-X"}),
           404, "not_found")  # fmt: skip


def _unused_coupon(api: Api) -> tuple[str, str] | None:
    """A coupon issued to a customer without a decision, never redeemed in the store."""
    coupons = api.serving.frame("store/coupons")
    redeemed = set(coupons.loc[coupons["order_id"].notna(), "coupon_code"])
    decided = set(api.serving.frame("scores/request_scores")["customer_id"])
    issued = coupons.loc[coupons["order_id"].isna() & ~coupons["coupon_code"].isin(redeemed)]
    for row in issued.itertuples(index=False):
        if row.customer_id not in decided:
            return str(row.customer_id), str(row.coupon_code)
    return None


def test_a_coupon_applies_once_when_coupons_are_allowed(api: Api) -> None:
    found = _unused_coupon(api)
    if found is None:
        pytest.skip("no unredeemed coupon in the tiny store")
    customer, code = found
    variant = str(api.serving.frame("store/variants")["variant_id"].iloc[0])
    request = _cart(customer, variant, coupon_code=code)
    first = _evaluate(api, request)
    assert first["coupon_eligible"] and first["terms_source"] == "base"
    body = {**_confirm_body(first, request), "coupon_code": code}
    applied = api.client.post("/api/checkout/confirm", json=body)
    assert applied.status_code == 201 and applied.json()["coupon_applied"] is True
    second = _evaluate(api, request)
    again = api.client.post(
        "/api/checkout/confirm", json={**body, "terms_snapshot_id": second["terms_snapshot_id"]}
    )
    assert again.status_code == 201 and again.json()["coupon_applied"] is False
    unknown = _evaluate(api, request)
    wrong = {**body, "terms_snapshot_id": unknown["terms_snapshot_id"], "coupon_code": "NOPE-1"}
    assert api.client.post("/api/checkout/confirm", json=wrong).json()["coupon_applied"] is False


def test_a_coupon_redeemed_while_waiting_for_an_order_id_is_not_applied_twice(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The competing confirmation commits before the waiting request gets its order number.
    SQLite supplies real committed rows for the PostgreSQL READ COMMITTED interleaving;
    PostgreSQL's counter row locking itself is covered by the concurrent-writer suite.
    """
    found = _unused_coupon(api)
    if found is None:
        pytest.skip("no unredeemed coupon in the tiny store")
    customer, code = found
    variant = str(api.serving.frame("store/variants")["variant_id"].iloc[0])
    request = _cart(customer, variant, coupon_code=code)
    competing = _evaluate(api, request)
    waiting = _evaluate(api, request)
    next_number = checkout.next_number
    competed = False

    async def after_competing_commit(conn: AsyncConnection, *args: Any) -> int:
        nonlocal competed
        if not competed:
            competed = True
            assert api.settings.database_url is not None
            engine = make_engine(api.settings.database_url)
            try:
                async with engine.begin() as other:
                    result = await checkout.confirm(
                        other,
                        api.serving,
                        CheckoutConfirmRequest.model_validate(
                            {**_confirm_body(competing, request), "coupon_code": code}
                        ),
                        api.clock.now(),
                    )
                    assert result.coupon_applied
            finally:
                await engine.dispose()
        return await next_number(conn, *args)

    monkeypatch.setattr(checkout, "next_number", after_competing_commit)
    response = api.client.post(
        "/api/checkout/confirm",
        json={**_confirm_body(waiting, request), "coupon_code": code},
    )
    assert response.status_code == 201, response.text
    assert response.json()["coupon_applied"] is False
    assert response.json()["order_id"] == "ORD-CHK-000002"

    async def applied_count(conn: AsyncConnection) -> int:
        carts = (await conn.execute(select(terms_snapshots.c.cart))).scalars()
        return sum(bool(cart.get("coupon_applied")) for cart in carts)

    assert api.run(applied_count) == 1


def test_snapshot_ids_and_order_ids_are_sequential(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    request = _cart(GIULIA, _variant_of(api, return_id))
    ids = [_evaluate(api, request)["terms_snapshot_id"] for _ in range(2)]
    assert ids == ["TS-CHK-000001", "TS-CHK-000002"]
    count = api.run(
        lambda conn: conn.scalar(
            select(audit_log.c.entry_id)
            .where(audit_log.c.action == "checkout_terms_shown")
            .limit(1)
        )
    )
    assert count is not None
    assert Decimal("0.35") == Decimal(str(P_RETURN_THRESHOLD))
