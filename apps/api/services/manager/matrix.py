"""GET /api/portfolio/matrix: every scored customer as a risk-value point (US-601, ADR-04).

The points are the batch scores of the serving snapshot (scores/scores: the customer model's
risk, the evidence quality and the value at the scoring instant). A customer with a case in the
queue is that case card (ADR-15 section 4): the risk given to the engine (the request model's,
on the withdrawal path with sufficient evidence; the customer's batch risk when the open return
is a defect, which is never scored, rule 4), the value of the customer snapshot the card shows,
its evidence quality and the cluster the engine was given. For every customer, returns_12m and
welcome_coupons count the order lines returned and the orders with a welcome coupon in the 12
months to the time the matrix is served, as the order history of a card does (returns sent by
the shop adapter included).

The thresholds are those of the batch (spec 8.6): the customer model's risk threshold (precision
>= 0.8 on validation; 1.0 flags no one when none reached it) and the 70th percentile of value at
the scoring instant. Each quadrant is policy.engine.quadrant_for at these thresholds; with
insufficient evidence a point has no risk and no quadrant (US-307). A card's quadrant was
decided at the request model's threshold, so a case whose risk lies between the two thresholds
sits in another quadrant of the matrix than on its card.

About 20,000 points at full scale: the customer snapshot of the cards is read in one pass
(case_values, the same rule as Serving.customer_context) and the order lines of the snapshot are
kept as arrays per snapshot (line_arrays), so a matrix costs one pass over them.

Customer value orients benefits and service, never the risk (rule 6): the legend says so.
"""

from __future__ import annotations

import weakref
from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

import numpy as np
import numpy.typing as npt
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.provenance import from_file, provenance
from apps.api.serving import Serving
from contracts.sql import decisions, return_requests
from ml.risk.data import latest_before
from pipeline.features.snapshot import utc
from policy.engine import Thresholds, quadrant_for
from policy.types import CaseScores, EvidenceQuality, Path, Quadrant

YEAR: Final = np.timedelta64(365, "D")
CENT: Final = Decimal("0.01")


@dataclass
class QuadrantTotal:
    customers: int = 0
    value_eur: Decimal = Decimal(0)
    returns_12m: int = 0


@dataclass(frozen=True)
class CasePoint:
    """What the case card of a customer's latest return says about the customer."""

    return_id: str
    risk: float | None
    value_eur: Decimal
    evidence_quality: EvidenceQuality
    cluster_id: str | None


@dataclass(frozen=True)
class LineArrays:
    """The order lines of a serving snapshot as arrays (one row per line, UTC instants)."""

    customers: npt.NDArray[np.object_]  # the distinct customer ids
    customer: npt.NDArray[np.intp]  # index into customers, per line
    line_id: npt.NDArray[np.object_]
    ordered_at: npt.NDArray[np.datetime64]
    return_known_at: npt.NDArray[np.datetime64]  # NaT without a return
    first_of_order: npt.NDArray[np.bool_]  # one line per order, to count orders
    welcome: npt.NDArray[np.bool_]  # the order used a welcome coupon


# What the matrix keeps per loaded snapshot (its order-line arrays, the customer contexts computed
# for new accounts): a snapshot never changes once loaded, and the entry goes with it.
_CACHES: dict[int, tuple[weakref.ref[Serving], dict[str, Any]]] = {}


def snapshot_cache(serving: Serving) -> dict[str, Any]:
    key = id(serving)
    held = _CACHES.get(key)
    if held is None or held[0]() is not serving:
        held = (weakref.ref(serving), {})
        _CACHES[key] = held
        weakref.finalize(serving, _CACHES.pop, key, None)
    return held[1]


def _naive_utc(series: pd.Series) -> npt.NDArray[np.datetime64]:
    values: npt.NDArray[np.datetime64] = (
        series.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[us]")
    )
    return values


def line_arrays(serving: Serving) -> LineArrays:
    """The arrays of a snapshot's order lines, computed once per loaded snapshot."""
    cache = snapshot_cache(serving)
    held = cache.get("lines")
    if isinstance(held, LineArrays):
        return held
    lines = serving.lines
    codes, customers = pd.factorize(lines["customer_id"])
    coupons = serving.frame("store/coupons")
    kinds = dict(zip(coupons["coupon_code"], coupons["kind"], strict=True))
    arrays = LineArrays(
        customers=np.asarray(customers, dtype=object),
        customer=np.asarray(codes, dtype=np.intp),
        line_id=lines["line_id"].to_numpy(dtype=object),
        ordered_at=_naive_utc(lines["ordered_at"]),
        return_known_at=_naive_utc(lines["return_known_at"]),
        first_of_order=(~lines["order_id"].duplicated()).to_numpy(dtype=bool),
        welcome=lines["coupon_code"].map(kinds).eq("welcome").to_numpy(dtype=bool),
    )
    cache["lines"] = arrays
    return arrays


def history_counts(
    serving: Serving, at: datetime, adapter_lines: Set[str]
) -> tuple[dict[str, int], dict[str, int]]:
    """customer_id -> returned order lines, and -> orders with a welcome coupon, among the orders
    placed in the 12 months to `at` (the order history of a case card, for every customer)."""
    arrays = line_arrays(serving)
    end = np.datetime64(utc(at).replace(tzinfo=None), "us")
    window = (arrays.ordered_at > end - YEAR) & (arrays.ordered_at <= end)
    returned = arrays.return_known_at <= end
    if adapter_lines:
        returned |= np.isin(arrays.line_id, np.asarray(sorted(adapter_lines), dtype=object))
    size = len(arrays.customers)
    returns = np.bincount(arrays.customer[window & returned], minlength=size)
    welcome = np.bincount(
        arrays.customer[window & arrays.first_of_order & arrays.welcome], minlength=size
    )
    names = [str(c) for c in arrays.customers]
    return dict(zip(names, returns.tolist(), strict=True)), dict(
        zip(names, welcome.tolist(), strict=True)
    )


def case_values(serving: Serving, known: Mapping[str, datetime]) -> dict[str, Decimal]:
    """customer_id -> the value of the customer snapshot strictly before the request became
    known (MOD-08): Serving.customer_context, read for every case in one pass. A customer with
    no snapshot then (a new account) gets the context computed at the request, as on its card."""
    if not known:
        return {}
    customers = list(known)
    instants = latest_before(
        pd.Series([pd.Timestamp(known[c]) for c in customers]), serving.feature_instants
    )
    wanted = pd.DataFrame({"customer_id": customers, "as_of": instants})
    stamps = set(instants.dropna())
    value = serving.frame("features/customer_value")
    value = value.loc[value["as_of"].isin(stamps), ["customer_id", "as_of", "value_eur"]]
    evidence = serving.frame("features/evidence_quality")
    evidence = evidence.loc[evidence["as_of"].isin(stamps), ["customer_id", "as_of"]]
    merged = wanted.merge(value, on=["customer_id", "as_of"], how="left").merge(
        evidence.assign(judged=True), on=["customer_id", "as_of"], how="left"
    )
    live: dict[tuple[str, datetime], Decimal] = snapshot_cache(serving).setdefault("live", {})
    out: dict[str, Decimal] = {}
    for row in merged.drop_duplicates("customer_id").itertuples(index=False):
        customer = str(row.customer_id)
        if pd.isna(row.as_of) or pd.isna(row.value_eur) or pd.isna(row.judged):
            key = (customer, known[customer])
            if key not in live:
                context = serving.customer_context(customer, known[customer])
                live[key] = Decimal(str(context.value["value_eur"])).quantize(CENT)
            out[customer] = live[key]
        else:
            out[customer] = Decimal(str(row.value_eur)).quantize(CENT)
    return out


async def case_points(
    conn: AsyncConnection, serving: Serving
) -> tuple[dict[str, CasePoint], set[str]]:
    """customer_id -> the card of the customer's latest return (by request time, then id), and
    the order lines of the returns the shop adapter sent."""
    rets = {
        r["return_id"]: dict(r)
        for r in (await conn.execute(select(return_requests))).mappings().all()
    }
    adapter = {r["line_id"] for r in rets.values() if r["source"] == "adapter"}
    latest: dict[str, dict[str, Any]] = {}
    query = select(
        decisions.c.return_id,
        decisions.c.version,
        decisions.c.path,
        decisions.c.evidence_quality,
        decisions.c.risk,
        decisions.c.cluster_id,
    )
    for version in (await conn.execute(query)).mappings().all():
        found = latest.get(version["return_id"])
        if found is None or version["version"] > found["version"]:
            latest[version["return_id"]] = dict(version)
    batch = serving.frame("scores/scores").set_index("customer_id")["risk"]
    chosen: dict[str, dict[str, Any]] = {}
    for return_id in latest:
        ret = rets[return_id]
        if ret["customer_id"] not in batch.index:  # not scored by the batch: no point
            continue
        key = (ret["requested_at"], return_id)
        held = chosen.get(ret["customer_id"])
        if held is None or key > (held["requested_at"], held["return_id"]):
            chosen[ret["customer_id"]] = ret
    store = serving.frame("store/returns").set_index("return_id")["available_at"]
    known = {  # when each request became known: its decision time (services.cases.record_of)
        customer: (
            utc(pd.Timestamp(store[ret["return_id"]]).to_pydatetime())
            if ret["return_id"] in store.index
            else utc(ret["requested_at"])
        )
        for customer, ret in chosen.items()
    }
    values = case_values(serving, known)
    out: dict[str, CasePoint] = {}
    for customer, ret in chosen.items():
        row = latest[ret["return_id"]]
        evidence = EvidenceQuality(row["evidence_quality"])
        risk: float | None = None
        if evidence is EvidenceQuality.SUFFICIENT:
            scored = row["path"] == Path.WITHDRAWAL.value and row["risk"] is not None
            risk = float(row["risk"]) if scored else float(batch[customer])
        out[customer] = CasePoint(
            return_id=ret["return_id"],
            risk=risk,
            value_eur=values[customer],
            evidence_quality=evidence,
            cluster_id=row["cluster_id"],
        )
    return out, adapter


def _metric(value: float | int, unit: s.Unit, prov: s.Provenance) -> s.Metric:
    return s.Metric(value=value, unit=unit, provenance=prov)


def _clusters(serving: Serving, at: datetime) -> Mapping[str, str]:
    """customer_id -> the cluster of 2 accounts or more at the scoring instant."""
    frame = serving.at("features/clusters", at)
    rows = frame.loc[frame["cluster_id"].notna() & (frame["cluster_size"] >= 2)]
    return dict(zip(rows["customer_id"], rows["cluster_id"].astype(str), strict=True))


async def portfolio_matrix(
    conn: AsyncConnection, serving: Serving, now: datetime
) -> s.PortfolioMatrix:
    scores = serving.frame("scores/scores").sort_values("customer_id", kind="stable")
    scored_at = serving.as_of
    model = serving.models.customer
    risk_threshold = model.threshold if model.threshold is not None else 1.0
    thresholds = Thresholds(risk=risk_threshold, value_eur=serving.value_threshold(scored_at))
    cases, adapter = await case_points(conn, serving)
    returns, welcome = history_counts(serving, now, adapter)
    clusters = _clusters(serving, scored_at)
    names = serving.display_names
    points: list[s.MatrixPoint] = []
    totals = {q: QuadrantTotal() for q in Quadrant}
    insufficient = 0
    columns = zip(
        scores["customer_id"].astype(str),
        scores["risk"].astype(float),
        scores["evidence_quality"].astype(str),
        scores["value_eur"],
        strict=True,
    )
    for customer, batch_risk, batch_evidence, batch_value in columns:
        case = cases.get(customer)
        if case is not None:
            evidence, value = case.evidence_quality, case.value_eur
            risk, cluster = case.risk, case.cluster_id
        else:
            evidence = EvidenceQuality(batch_evidence)
            value = Decimal(str(batch_value)).quantize(CENT)
            risk = batch_risk if evidence is EvidenceQuality.SUFFICIENT else None
            cluster = clusters.get(customer)
        quadrant = None
        if risk is not None:
            quadrant = quadrant_for(CaseScores(risk, value, evidence, cluster), thresholds)
        returned, coupons = returns.get(customer, 0), welcome.get(customer, 0)
        if quadrant is None:
            insufficient += 1
        else:
            total = totals[quadrant]
            total.customers += 1
            total.value_eur += value
            total.returns_12m += returned
        points.append(
            s.MatrixPoint(
                customer_id=customer,
                display_name=names.get(customer),
                risk=risk,
                value_eur=float(value),
                evidence_quality=evidence,
                quadrant=quadrant,
                cluster_id=cluster,
                returns_12m=returned,
                welcome_coupons=coupons,
            )
        )
    day, scored_day = now.date(), scored_at.date()
    p = serving.provenance
    scores_prov = from_file(
        p["scores/scores"],
        f"customer scores of {model.version} at {scored_day}",
        scored_day,
    )
    points_prov = from_file(
        p["scores/scores"],
        f"customer scores of {model.version} at {scored_day}; a customer with an open case "
        "shows the risk, value, evidence and cluster of its case card, or the customer's batch "
        "risk when the case follows the guarantee path, which is never scored (rule 4); "
        f"returns and welcome coupons in the 12 months to {day}",
        day,
    )
    value_prov = from_file(p["features/customer_value"], f"customer value at {scored_day}", day)
    store_prov = from_file(
        p["store/order_lines"], f"order lines returned in the 12 months to {day}", day
    )
    evidence_prov = from_file(
        p["features/evidence_quality"],
        "customers with insufficient evidence, outside the quadrants (US-307)",
        day,
    )
    risk_prov = provenance(
        f"risk threshold of the customer model {model.version}: the lowest calibrated risk "
        "with precision >= 0.8 on the validation groups (ml.risk, spec 8.6)",
        scores_prov.period_start,
        scores_prov.period_end,
        scores_prov.nature,
    )
    limit_prov = from_file(
        p["features/customer_value"],
        f"value threshold: 70th percentile of customer value at {scored_day} (spec 8.6)",
        scored_day,
    )
    risk_basis = (
        f"Lowest risk with precision >= 0.8 on the validation months ({model.version})"
        if model.threshold is not None
        else "No risk reached precision >= 0.8 on validation: 1.0 flags no one"
    )
    return s.PortfolioMatrix(
        as_of=now,
        thresholds=s.MatrixThresholds(
            risk=_metric(risk_threshold, "probability", risk_prov),
            value_eur=_metric(float(thresholds.value_eur), "eur", limit_prov),
            risk_basis=risk_basis,
            value_basis=f"70th percentile of customer value at {scored_day}",
        ),
        quadrants=[
            s.QuadrantSummary(
                quadrant=q,
                customers=_metric(totals[q].customers, "count", scores_prov),
                value_eur=_metric(float(totals[q].value_eur), "eur", value_prov),
                returns_12m=_metric(totals[q].returns_12m, "count", store_prov),
            )
            for q in Quadrant
        ],
        insufficient_evidence=_metric(insufficient, "count", evidence_prov),
        points=points,
        points_provenance=points_prov,
    )
