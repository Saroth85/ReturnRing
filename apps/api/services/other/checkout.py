"""The simulated checkout (moment M1, flow F2; US-401..US-403, ADR-05, E2E-02; ADR-15 section 4
"Checkout"; rules 3, 5 and 11).

POST /api/checkout/evaluate, in this order:

1. the return probability of each cart line from the personalized line predictor of the
   serving snapshot (ml.reference, spec 8.1): the reference features of the line plus the
   customer's own settled history, at the snapshot instant (history_at_snapshot);
2. size advice exactly when p_return >= P_RETURN_THRESHOLD (US-401). A specific size needs
   data on the customer's measurements and on exchange outcomes, which the store does not
   hold: the advice is the size guide of a sized item, or a plain "no reliable advice" when
   the variant has no size range or a flagged size or description anomaly (spec 8.9: its size
   guide may be what is wrong);
3. the terms of the latest decision in force for the customer that sets them (US-403): its
   levers on future orders, with its review date. A proposal that awaits a person is not in
   force (rule 5), and a restriction lapses at its review date (US-607); otherwise the merchant
   base terms. The quadrant is the engine's only while its levers stand (proposed, approved):
   a person's override shows its own levers with no quadrant, and an override (a lift) to the
   standard levers leaves the base terms (1.5.0, H3: no permanent label);
4. the coupon block: a confirmed application of the general cluster coupon rule, in force,
   naming the customer among its linked accounts (US-402), shown in the neutral words of
   llm.phrasebook (rule 11). It turns the coupons of the terms shown off and the snapshot
   stores it with its review date (1.5.0, H12);
5. the terms shown are stored as a terms_snapshots row with no order and logged
   (checkout_terms_shown) before the answer.

POST /api/checkout/confirm binds that row to a new order: 409 when the cart or the customer
differ from the evaluated ones, when it is already confirmed, on or after the review date of
its terms (D3), or when the decision the terms come from, or the coupon block shown, is no
longer in force (E8); the client then evaluates again. A coupon code applies only when the
terms shown allowed coupons and the store issued that code to the customer, unredeemed.
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.errors import conflict, not_found
from apps.api.services import decisions as store
from apps.api.services.audit import record as audit
from apps.api.services.cases import variant_anomaly
from apps.api.services.other.common import line_prices, next_number, view_at
from apps.api.services.prices import paid_price
from apps.api.services.provenance import from_file
from apps.api.serving import BASE_TERMS, Serving
from contracts.sql import TERM_COLUMNS, coupon_rule_applications, decisions, terms_snapshots
from ml.reference.checkout import CartItem, cart_queries
from pipeline.features.lines import LEVELS, observations
from policy.types import ReturnReason

# Size advice above this return probability (spec 8.6, "Consiglio di taglia al checkout: se
# probabilità sopra soglia"). A design hypothesis of the checkout, like the thresholds of D-15.
P_RETURN_THRESHOLD: Final = 0.35
# A flagged anomaly for these reasons may come from the size guide itself (spec 8.9: "the size
# chart does not match the garment"): the checkout then does not point to it.
GUIDE_DOUBTS: Final = frozenset({ReturnReason.SIZE.value, ReturnReason.NOT_AS_DESCRIBED.value})
SNAPSHOT_PREFIX: Final = "TS-CHK-"
ORDER_PREFIX: Final = "ORD-CHK-"
BASE_SOURCE: Final = "base"


# ---- Line probabilities (spec 8.1, US-401) -------------------------------------------------------


@dataclass(frozen=True)
class _Counts:
    """Observed lines and returns of the snapshot: in the store and per variant, product and
    category (pipeline.features.lines.LEVELS)."""

    n: int
    r: int
    levels: Mapping[str, tuple[pd.Series, pd.Series]]


_COUNTS: dict[int, tuple[weakref.ref[Serving], _Counts]] = {}


def _group(events: pd.DataFrame, level: str) -> tuple[pd.Series, pd.Series]:
    returned = events["returned"].astype(np.int64)
    grouped = returned.groupby(events[level].to_numpy(dtype=object), sort=False)
    return grouped.size(), grouped.sum()


def _counts(serving: Serving) -> _Counts:
    """The counts of the snapshot, computed once per loaded snapshot."""
    found = _COUNTS.get(id(serving))
    if found is not None and found[0]() is serving:
        return found[1]
    events = observations(serving.lines)
    counts = _Counts(
        n=len(events),
        r=int(events["returned"].sum()),
        levels={level: _group(events, level) for level in LEVELS},
    )
    _COUNTS[id(serving)] = (weakref.ref(serving), counts)
    return counts


def warm(serving: Serving) -> None:
    """What the first checkout would compute once (US-403: the first evaluation under 1 s too):
    the counts of the snapshot and the price paid of its lines."""
    _counts(serving)
    paid_price(serving, "")


def _lookup(keys: pd.Series, values: pd.Series) -> npt.NDArray[np.int64]:
    """The count of each key (0 for a key without lines)."""
    return np.asarray(keys.astype(object).map(values).fillna(0).to_numpy(), dtype=np.int64)


def history_at_snapshot(serving: Serving, queries: pd.DataFrame) -> pd.DataFrame:
    """pipeline.features.lines.line_history of cart queries at the snapshot instant.

    Every observation of the snapshot is settled by its instant (line_frame: observed means
    settled_at <= as_of), so the as-of counts of line_history are the counts of each group,
    leaving the query's customer out as line_history does, and the customer's own lines; from
    per-group totals instead of a pass over every line, so the checkout answers in well under
    a second at full scale (US-403). tests/test_api_other_checkout.py compares the two."""
    if queries.empty:
        return pd.DataFrame(index=queries.index)
    if queries["t"].nunique() != 1 or pd.Timestamp(queries["t"].iloc[0]) != pd.Timestamp(
        serving.store.as_of
    ):
        raise ValueError("cart queries are priced at the snapshot instant")
    customers = set(queries["customer_id"])
    if len(customers) != 1:
        raise ValueError("a cart belongs to one customer")
    base = _counts(serving)
    own = observations(serving.customer_lines(str(customers.pop())))
    own_r = int(own["returned"].sum())
    out = pd.DataFrame(index=queries.index)
    for level in LEVELS:
        n_all, r_all = base.levels[level]
        n_own, r_own = _group(own, level)
        keys = queries[level]
        name = level.removesuffix("_id")
        out[f"{name}_n"] = _lookup(keys, n_all) - _lookup(keys, n_own)
        out[f"{name}_r"] = _lookup(keys, r_all) - _lookup(keys, r_own)
    out["all_n"] = np.int64(base.n - len(own))
    out["all_r"] = np.int64(base.r - own_r)
    out["customer_n"] = np.int64(len(own))
    out["customer_r"] = np.int64(own_r)
    return out


def line_probabilities(
    serving: Serving, customer_id: str, lines: Sequence[s.CartLine]
) -> list[float]:
    """p_return of each cart line (ml.reference.checkout.checkout_probabilities, on the counts
    of the snapshot), rounded to 4 decimals as shown."""
    items = [CartItem(line.variant_id, line.size) for line in lines]
    queries = cart_queries(serving.store, customer_id, items)
    frame = pd.concat([queries, history_at_snapshot(serving, queries)], axis=1)
    return [round(float(p), 4) for p in serving.checkout_model.predict(frame)]


def advice_for(size_range: object, anomaly_reason: str | None) -> s.SizeAdvice:
    """US-401 without measurement data, for a variant's size range and the reason of its flagged
    anomaly (None without one): the size guide, or no reliable recommendation. Never a specific
    size: the store holds no measurements or exchange outcomes (the contract examples use this
    too, ADR-15 section 18)."""
    if not size_range or anomaly_reason in GUIDE_DOUBTS:
        return s.SizeAdvice.of("no_reliable_recommendation")
    return s.SizeAdvice.of("measurement_guide")


def size_advice(serving: Serving, variant_id: str, at: datetime) -> s.SizeAdvice:
    """US-401 without measurement data: the size guide, or no reliable recommendation."""
    variant = serving.by_key("store/variants", variant_id) or {}
    anomaly = variant_anomaly(serving, variant_id, at)
    reason = None if anomaly is None else str(anomaly["reason"])
    return advice_for(variant.get("size_range"), reason)


# ---- Terms and coupon block (US-402, US-403, US-607) ---------------------------------------------


async def _latest_rows(conn: AsyncConnection, customer_id: str) -> list[dict[str, Any]]:
    """The latest version of each decision about the customer's returns."""
    query = (
        select(decisions)
        .where(decisions.c.customer_id == customer_id)
        .order_by(decisions.c.return_id, decisions.c.version)
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in (await conn.execute(query)).mappings().all():
        latest[str(row["return_id"])] = dict(row)
    return list(latest.values())


def engine_levers_stand(view: s.DecisionView) -> bool:
    """The engine's levers apply as proposed (proposed or approved), not a person's choice."""
    return view.state in ("proposed", "approved")


def sets_terms(view: s.DecisionView) -> bool:
    """Whether a decision in force sets the terms of future orders: the engine's proposal of a
    quadrant, or a person's override to levers other than the standard ones (H3)."""
    if engine_levers_stand(view):
        return view.quadrant is not None
    return view.future_terms != view.inputs.base_terms


async def decision_in_force(
    conn: AsyncConnection, serving: Serving, customer_id: str, at: datetime
) -> s.DecisionView | None:
    """The latest decision in force at `at` that sets the terms of future orders (sets_terms):
    approved or overridden, or proposed without a need for approval; never a restriction on or
    after its review date (DecisionView.in_force)."""
    rows = await _latest_rows(conn, customer_id)
    prices = await line_prices(conn, serving, [str(r["return_id"]) for r in rows])
    best: tuple[tuple[datetime, str], s.DecisionView] | None = None
    for row in rows:
        view = await store.view_of(conn, row, at, line_price=prices.get)
        if not view.in_force or not sets_terms(view):
            continue
        key = (view.approved_at or view.created_at, view.decision_id)
        if best is None or key > best[0]:
            best = (key, view)
    return None if best is None else best[1]


async def coupon_block(
    conn: AsyncConnection, serving: Serving, customer_id: str, at: datetime
) -> s.CouponBlock | None:
    """The confirmed cluster coupon application in force that names the customer (the latest
    confirmed when several do): confirmed, not reversed, its decision in force, before its
    review date (CouponRuleRef.in_force). Its text speaks of previous returns only to the
    customer of the case; every other account sees the text that gives no cause, with the
    review date (rule 11; contract 1.7.0)."""
    rows = (
        (
            await conn.execute(
                select(coupon_rule_applications).where(
                    coupon_rule_applications.c.state == "confirmed"
                )
            )
        )
        .mappings()
        .all()
    )
    best: tuple[tuple[datetime, str], s.CouponBlock] | None = None
    for app in rows:
        if customer_id not in app["affected_customer_ids"] or s.utc_day(at) >= app["review_at"]:
            continue
        row = await store.decision_row_by_id(conn, str(app["decision_id"]))
        if row is None:
            continue
        rule = (await view_at(conn, serving, row, at)).coupon_rule
        if rule is None or rule.application_id != app["application_id"] or not rule.in_force:
            continue
        block = s.CouponBlock(
            rule_version=app["rule_version"],
            application_id=app["application_id"],
            review_at=app["review_at"],
            text=(
                s.COUPON_BLOCK
                if row["customer_id"] == customer_id
                else s.cluster_coupon_block(app["review_at"])
            ),
        )
        key = (app["confirmed_at"], str(app["application_id"]))
        if best is None or key > best[0]:
            best = (key, block)
    return None if best is None else best[1]


def _provenance(serving: Serving) -> s.Provenance:
    model = serving.checkout_model
    day = serving.as_of.date()
    return from_file(
        serving.provenance["store/order_lines"],
        f"return probability of the personalized line predictor {model.version} (ml.reference), "
        f"store history at the snapshot of {day}",
        day,
    )


def _cart_json(lines: Sequence[s.CartLine], coupon_code: str | None) -> dict[str, Any]:
    return {"lines": [line.model_dump() for line in lines], "coupon_code": coupon_code}


def _check_cart(serving: Serving, body: s.CheckoutEvaluateRequest) -> None:
    if body.customer_id not in serving.display_names:
        raise not_found(f"customer {body.customer_id}")
    catalogue = set(serving.frame("store/variants")["variant_id"])
    unknown = sorted({line.variant_id for line in body.lines} - catalogue)
    if unknown:
        raise not_found(f"variant {', '.join(unknown)} in the catalogue")


async def evaluate(
    conn: AsyncConnection,
    serving: Serving,
    body: s.CheckoutEvaluateRequest,
    at: datetime,
    request_id: str | None = None,
) -> s.CheckoutEvaluateResponse:
    """POST /api/checkout/evaluate (see the module docstring)."""
    _check_cart(serving, body)
    probabilities = line_probabilities(serving, body.customer_id, body.lines)
    lines = [
        s.CheckoutLine(
            variant_id=line.variant_id,
            size=line.size,
            p_return=p,
            size_advice=(
                size_advice(serving, line.variant_id, at) if p >= P_RETURN_THRESHOLD else None
            ),
        )
        for line, p in zip(body.lines, probabilities, strict=True)
    ]
    base = s.Terms.from_policy(BASE_TERMS)
    view = await decision_in_force(conn, serving, body.customer_id, at)
    terms = base if view is None else view.future_terms
    source = BASE_SOURCE if view is None else view.decision_id
    review_at = None if view is None else view.review_at
    quadrant = view.quadrant if view is not None and engine_levers_stand(view) else None
    block = await coupon_block(conn, serving, body.customer_id, at)
    if block is not None:  # the block turns the coupons of the terms shown off (H12)
        terms = terms.model_copy(update={"coupons_enabled": False})
    number = await next_number(conn, SNAPSHOT_PREFIX.rstrip("-"), terms_snapshots)
    snapshot_id = f"{SNAPSHOT_PREFIX}{number:06d}"
    response = s.CheckoutEvaluateResponse(
        customer_id=body.customer_id,
        quadrant=quadrant,
        lines=lines,
        terms=terms,
        base_terms=base,
        coupon_eligible=terms.coupons_enabled and block is None,
        coupon_block=block,
        terms_snapshot_id=snapshot_id,
        terms_source=source,
        review_at=review_at,
        shown_at=at,
        p_return_threshold=P_RETURN_THRESHOLD,
        model_version=serving.checkout_model.version,
        provenance=_provenance(serving),
    )
    await conn.execute(
        insert(terms_snapshots).values(
            terms_snapshot_id=snapshot_id,
            customer_id=body.customer_id,
            order_id=None,
            source=source,
            source_decision_id=None if view is None else view.decision_id,
            quadrant_at_purchase=None if response.quadrant is None else response.quadrant.value,
            **terms.model_dump(),
            **{f"base_{k}": v for k, v in base.model_dump().items()},
            review_at=review_at,
            coupon_application_id=None if block is None else block.application_id,
            coupon_review_at=None if block is None else block.review_at,
            cart=_cart_json(body.lines, body.coupon_code),
            shown_at=at,
            confirmed_at=None,
        )
    )
    await audit(
        conn,
        at=at,
        actor="system",
        action="checkout_terms_shown",
        entity_type="terms_snapshot",
        entity_id=snapshot_id,
        after={"customer_id": body.customer_id, "terms_source": source},
        request_id=request_id,
    )
    return response


# ---- Confirm (US-403, ADR-05) --------------------------------------------------------------------


def snapshot_view(row: Mapping[str, Any]) -> s.TermsSnapshotView:
    """The API view of a terms_snapshots row."""
    return s.TermsSnapshotView(
        terms_snapshot_id=row["terms_snapshot_id"],
        order_id=row["order_id"],
        customer_id=row["customer_id"],
        source=row["source"],
        quadrant_at_purchase=row["quadrant_at_purchase"],
        terms=s.Terms.model_validate({c: row[c] for c in TERM_COLUMNS}),
        base_terms=s.Terms.model_validate({c: row[f"base_{c}"] for c in TERM_COLUMNS}),
        review_at=row["review_at"],
        coupon_application_id=row["coupon_application_id"],
        coupon_review_at=row["coupon_review_at"],
        shown_at=row["shown_at"],
        confirmed_at=row["confirmed_at"],
    )


async def _still_in_force(
    conn: AsyncConnection, serving: Serving, row: Mapping[str, Any], at: datetime
) -> str | None:
    """Why the terms of a shown snapshot can no longer be bound to an order, if they cannot."""
    sid = row["terms_snapshot_id"]
    review = row["review_at"]
    if review is not None and s.utc_day(at) >= review:
        return (
            f"{sid} can no longer be confirmed: its terms lapsed at their review date, "
            f"{review.isoformat()} (US-607); evaluate the cart again"
        )
    decision_id = row["source_decision_id"]
    if decision_id is not None:
        decision = await store.decision_row_by_id(conn, str(decision_id))
        if decision is None or not (await view_at(conn, serving, decision, at)).in_force:
            return (
                f"{sid} can no longer be confirmed: its terms come from {decision_id}, which is "
                "no longer in force; evaluate the cart again"
            )
    application_id = row["coupon_application_id"]
    block = None
    if application_id is not None:
        block = await coupon_block(conn, serving, str(row["customer_id"]), at)
    if application_id is not None and (block is None or block.application_id != application_id):
        return (
            f"{sid} can no longer be confirmed: the coupon block {application_id} shown with "
            "its terms no longer applies; evaluate the cart again"
        )
    return None


async def _coupon_applies(
    conn: AsyncConnection,
    serving: Serving,
    row: Mapping[str, Any],
    code: str | None,
) -> bool:
    """A code applies when the terms shown allowed coupons (no block) and the store issued it to
    the customer, never redeemed there nor in an earlier simulated order."""
    if code is None or not row["coupons_enabled"] or row["coupon_application_id"] is not None:
        return False
    coupons = serving.frame("store/coupons")
    rows = coupons.loc[coupons["coupon_code"] == code]
    if rows.empty or set(rows["customer_id"]) != {row["customer_id"]}:
        return False
    if rows["order_id"].notna().any():
        return False
    earlier = (
        await conn.execute(
            select(terms_snapshots.c.cart).where(
                (terms_snapshots.c.customer_id == row["customer_id"])
                & terms_snapshots.c.order_id.is_not(None)
            )
        )
    ).scalars()
    return not any(c.get("coupon_applied") and c.get("coupon_code") == code for c in earlier)


async def confirm(
    conn: AsyncConnection,
    serving: Serving,
    body: s.CheckoutConfirmRequest,
    at: datetime,
    request_id: str | None = None,
) -> s.CheckoutConfirmResponse:
    """POST /api/checkout/confirm (see the module docstring)."""
    sid = body.terms_snapshot_id
    found = (
        (
            await conn.execute(
                select(terms_snapshots).where(terms_snapshots.c.terms_snapshot_id == sid)
            )
        )
        .mappings()
        .first()
    )
    if found is None:
        raise not_found(f"terms snapshot {sid}")
    row = dict(found)
    if row["order_id"] is not None:
        raise conflict(f"{sid} is already bound to order {row['order_id']}")
    if row["customer_id"] != body.customer_id:
        raise conflict(f"{sid} was shown to another customer: evaluate the cart again")
    shown = _cart_json(body.lines, None)["lines"]
    if row["cart"]["lines"] != shown:
        raise conflict(f"the cart differs from the cart evaluated in {sid}: evaluate it again")
    # The order counter serializes confirmations. Read redeemed coupons after acquiring its
    # lock: another snapshot may have spent the same coupon while this request waited.
    number = await next_number(
        conn, ORDER_PREFIX.rstrip("-"), terms_snapshots, terms_snapshots.c.order_id.is_not(None)
    )
    problem = await _still_in_force(conn, serving, row, at)
    if problem is not None:
        raise conflict(problem)
    applied = await _coupon_applies(conn, serving, row, body.coupon_code)
    order_id = f"{ORDER_PREFIX}{number:06d}"
    cart = {**row["cart"], "coupon_code": body.coupon_code, "coupon_applied": applied}
    result = await conn.execute(
        update(terms_snapshots)
        .where((terms_snapshots.c.terms_snapshot_id == sid) & terms_snapshots.c.order_id.is_(None))
        .values(order_id=order_id, confirmed_at=at, cart=cart)
    )
    if result.rowcount != 1:
        raise conflict(f"{sid} was confirmed meanwhile")
    await audit(
        conn,
        at=at,
        actor="system",
        action="checkout_confirmed",
        entity_type="terms_snapshot",
        entity_id=sid,
        after={"order_id": order_id, "coupon_applied": applied},
        request_id=request_id,
    )
    return s.CheckoutConfirmResponse(
        order_id=order_id,
        terms_snapshot=snapshot_view({**row, "order_id": order_id, "confirmed_at": at}),
        coupon_applied=applied,
    )
