"""llm_case inputs for B3 and for the batch generation step of B4, built from the synthetic store.

Every open return request of scores/request_scores (the nightly batch scoring) becomes the case
the operator would open: the engine decides (policy.engine.decide, with the thresholds the
scoring stored) and llm.case.build_case serializes the decision with its facts, exactly the path
the API takes (apps/api builds its cases the same way). Golden customers come first (spec 12:
Giulia, the Marco ring, the Rossi family, Elena), then the other open returns by id, so a batch of
32 holds 32 distinct cases.

Decision time (rule 7): the batch scoring instant (`as_of` of request_scores), when the nightly
batch generation runs. The customer snapshot is the one the scoring used (context_as_of), or for
a guarantee request (never scored, rule 4) the last snapshot before the request; the cluster is the
last account-graph snapshot at or before as_of. Nothing after as_of is read, and data/truth never
is (rule 12). The store has no customer free text: customer_text stays empty.

A row the engine or the case builder refuses, or whose engine quadrant differs from the stored
one, is left out with its reason (`skipped`), never patched.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from contracts.io import Provenance, read_table
from contracts.parquet import IDENTIFIER_KINDS
from llm.case import CaseError, ClusterSummary, ScoreFactor, build_case
from llm.decision import ExpectedDecision, serialize_decision
from llm.validator import check_case
from pipeline.generator.golden import GOLDEN_CUSTOMERS
from policy.engine import NAVIGLI_BASE_TERMS, PolicyError, Thresholds, decide
from policy.types import CaseScores, EvidenceQuality, ReturnReason, ReturnRequest, ReturnTerms
from policy.types import Path as ReturnPath

# The policy text of the demo merchant, as in the API examples (scripts/build_api_examples.py).
POLICY_TEXT = (
    "Navigli Fashion return policy (demo): returns within the window of the order terms, refund "
    "within 14 days of the notice, the legal guarantee for defects."
)
# Score factors measured on the cluster: their facts have the entity "cluster" (llm/case.py).
CLUSTER_FEATURES = frozenset(
    {
        "welcome_coupons_per_account",
        "creation_spread_days",
        "strong_id_share",
        "cluster_return_rate",
        "n_distinct_cards",
        "cluster_size",
    }
)
NO_SNAPSHOT = "no customer snapshot before the request"


@dataclass(frozen=True)
class BenchCase:
    return_id: str
    customer_id: str
    golden: bool
    template_key: str  # guarantee, insufficient, Q1..Q4
    case: dict[str, Any]
    expected: ExpectedDecision


@dataclass(frozen=True)
class CaseSet:
    cases: list[BenchCase]
    provenance: Provenance  # of scores/request_scores
    as_of: datetime
    skipped: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for c in self.cases:
            kinds[c.template_key] = kinds.get(c.template_key, 0) + 1
        return {
            "cases": len(self.cases),
            "golden": [c.return_id for c in self.cases if c.golden],
            "by_decision": dict(sorted(kinds.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "as_of": self.as_of.isoformat(),
            "origin": self.provenance.origin,
            "nature": self.provenance.nature,
        }


def _frame(name: str, root: Path) -> tuple[pl.DataFrame, Provenance]:
    table, provenance = read_table(name, root)
    frame = pl.from_arrow(table)
    assert isinstance(frame, pl.DataFrame)
    return frame, provenance


def _day(moment: datetime) -> date:
    return moment.date()


def _snapshot(
    by_customer: Mapping[str, list[tuple[datetime, dict[str, Any]]]],
    customer: str,
    limit: datetime,
    exact: datetime | None,
) -> tuple[datetime, dict[str, Any]] | None:
    """The row of the snapshot `exact`, else the last one strictly before `limit`."""
    rows = by_customer.get(customer, [])
    if exact is not None:
        return next(((at, row) for at, row in rows if at == exact), None)
    before = [(at, row) for at, row in rows if at < limit]
    return max(before, key=lambda item: item[0]) if before else None


def _group(frame: pl.DataFrame, key: str = "customer_id") -> dict[str, list[tuple[datetime, Any]]]:
    out: dict[str, list[tuple[datetime, Any]]] = {}
    for row in frame.iter_rows(named=True):
        out.setdefault(row[key], []).append((row["as_of"], row))
    return out


def _terms(row: Mapping[str, Any]) -> ReturnTerms:
    return ReturnTerms(
        return_window_days=int(row["return_window_days"]),
        free_return_shipping=bool(row["free_return_shipping"]),
        instant_refund=bool(row["instant_refund"]),
        free_size_exchange=bool(row["free_size_exchange"]),
        coupons_enabled=bool(row["coupons_enabled"]),
    )


def _factors(shap_top3: str | None, clustered: bool) -> list[ScoreFactor]:
    factors = []
    for item in json.loads(shap_top3) if shap_top3 else []:
        on_cluster = clustered and item["feature"] in CLUSTER_FEATURES
        factors.append(
            ScoreFactor(
                item["feature"],
                item["value"],
                item["phrase_en"],
                "cluster" if on_cluster else "customer",
            )
        )
    return factors


class _Store:
    """The tables a case needs, indexed once."""

    def __init__(self, root: Path, return_ids: Sequence[str], as_of: datetime) -> None:
        returns, _ = _frame("store/returns", root)
        returns = returns.filter(pl.col("return_id").is_in(list(return_ids)))
        self.returns = {r["return_id"]: r for r in returns.iter_rows(named=True)}
        lines, _ = _frame("store/order_lines", root)
        lines = lines.filter(pl.col("line_id").is_in(returns["line_id"].to_list()))
        self.line_order = {r["line_id"]: r["order_id"] for r in lines.iter_rows(named=True)}
        orders, _ = _frame("store/orders", root)
        orders = orders.filter(pl.col("order_id").is_in(list(self.line_order.values())))
        self.orders = {r["order_id"]: r for r in orders.iter_rows(named=True)}
        deliveries, _ = _frame("store/deliveries", root)
        self.delivered = {
            r["order_id"]: r["event_at"]
            for r in deliveries.filter(pl.col("order_id").is_in(list(self.orders))).iter_rows(
                named=True
            )
        }
        terms, _ = _frame("store/terms_snapshots", root)
        snapshot_ids = [o["terms_snapshot_id"] for o in self.orders.values()]
        self.terms = {
            r["terms_snapshot_id"]: _terms(r)
            for r in terms.filter(pl.col("terms_snapshot_id").is_in(snapshot_ids)).iter_rows(
                named=True
            )
        }
        customers = [o["customer_id"] for o in self.orders.values()]
        evidence, _ = _frame("features/evidence_quality", root)
        self.evidence = _group(evidence.filter(pl.col("customer_id").is_in(customers)))
        value, _ = _frame("features/customer_value", root)
        self.value = _group(value.filter(pl.col("customer_id").is_in(customers)))
        clusters, _ = _frame("features/clusters", root)
        clusters = clusters.filter(pl.col("as_of") <= as_of)
        self.cluster_as_of: datetime | None = clusters["as_of"].max() if clusters.height else None  # type: ignore[assignment]
        latest = clusters.filter(pl.col("as_of") == self.cluster_as_of)
        self.cluster_of = {
            r["customer_id"]: r["cluster_id"]
            for r in latest.iter_rows(named=True)
            if r["cluster_id"] is not None
        }
        self.members: dict[str, list[str]] = {}
        for customer, cluster in self.cluster_of.items():
            self.members.setdefault(cluster, []).append(customer)
        links, _ = _frame("features/links", root)
        self.links = links.filter(pl.col("as_of") == self.cluster_as_of)

    def cluster(self, cluster_id: str) -> ClusterSummary | None:
        members = sorted(self.members.get(cluster_id, []))
        if len(members) < 2:
            return None
        inside = self.links.filter(
            pl.col("customer_a").is_in(members) & pl.col("customer_b").is_in(members)
        )
        kinds = {k for row in inside["kinds"].to_list() for k in row}
        return ClusterSummary(
            cluster_id, len(members), tuple(k for k in IDENTIFIER_KINDS if k in kinds)
        )


def _order(row: Mapping[str, Any], golden: frozenset[str]) -> tuple[int, str]:
    return (0 if row["customer_id"] in golden else 1, row["return_id"])


def build_cases(data_root: Path, limit: int | None = None) -> CaseSet:
    """The cases of the open returns of data_root (golden first), at most `limit` of them."""
    scores, provenance = _frame("scores/request_scores", data_root)
    if scores.height == 0:
        raise CaseError(f"{data_root}: scores/request_scores has no open return")
    as_of: datetime = scores["as_of"].max()  # type: ignore[assignment]
    golden = frozenset(GOLDEN_CUSTOMERS)
    rows = sorted(scores.iter_rows(named=True), key=lambda r: _order(r, golden))
    store = _Store(data_root, [r["return_id"] for r in rows], as_of)
    with_thresholds = [r for r in rows if r["risk_threshold"] is not None]
    if not with_thresholds:
        raise CaseError("no scored request: the risk and value thresholds are unknown")
    default_thresholds = Thresholds(
        risk=float(with_thresholds[0]["risk_threshold"]),
        value_eur=Decimal(str(with_thresholds[0]["value_threshold_eur"])),
    )
    cases: list[BenchCase] = []
    skipped: dict[str, str] = {}
    for row in rows:
        if limit is not None and len(cases) >= limit:
            break
        try:
            cases.append(
                _case(
                    row,
                    store,
                    as_of,
                    default_thresholds,
                    provenance.nature,
                    row["customer_id"] in golden,
                )
            )
        except (CaseError, PolicyError, KeyError, ValueError) as exc:
            skipped[row["return_id"]] = f"{type(exc).__name__}: {exc}"[:300]
    if not cases:
        raise CaseError(f"no case could be built from {data_root}: {skipped}")
    return CaseSet(cases, provenance, as_of, skipped)


def _case(
    row: Mapping[str, Any],
    store: _Store,
    as_of: datetime,
    default_thresholds: Thresholds,
    nature: Any,
    golden: bool,
) -> BenchCase:
    ret = store.returns[row["return_id"]]
    order_id = store.line_order[ret["line_id"]]
    order = store.orders[order_id]
    if order["customer_id"] != row["customer_id"]:
        raise ValueError("the order and the score name different customers")
    request = ReturnRequest(
        return_id=row["return_id"],
        order_id=order_id,
        customer_id=row["customer_id"],
        reason=ReturnReason(ret["reason"]),
        delivered_on=_day(store.delivered[order_id]),
        notice_on=_day(ret["event_at"]),
        order_terms=store.terms[order["terms_snapshot_id"]],
    )
    path = ReturnPath(row["path"])
    context = row["context_as_of"]
    evidence = _snapshot(store.evidence, request.customer_id, row["decided_at"], context)
    value = _snapshot(store.value, request.customer_id, row["decided_at"], context)
    if row["evidence_quality"] is not None:
        quality = EvidenceQuality(row["evidence_quality"])
    elif evidence is not None:
        quality = EvidenceQuality(evidence[1]["evidence_quality"])
    else:
        quality = EvidenceQuality.INSUFFICIENT
    missing: tuple[str, ...] = ()
    if quality is EvidenceQuality.INSUFFICIENT:
        missing = tuple(evidence[1]["missing_evidence"]) if evidence else (NO_SNAPSHOT,)
        missing = missing or (NO_SNAPSHOT,)
    if row["value_eur"] is not None:
        value_eur = Decimal(str(row["value_eur"]))
    else:
        value_eur = Decimal(str(value[1]["value_eur"])) if value else Decimal(0)
    thresholds = (
        Thresholds(float(row["risk_threshold"]), Decimal(str(row["value_threshold_eur"])))
        if row["risk_threshold"] is not None and row["value_threshold_eur"] is not None
        else default_thresholds
    )
    cluster_id = store.cluster_of.get(request.customer_id)
    scores = CaseScores(
        risk=float(row["risk"]) if row["risk"] is not None else 0.0,  # unscored: guarantee path
        value_eur=value_eur,
        evidence_quality=quality,
        cluster_id=cluster_id,
    )
    decision = decide(request, scores, NAVIGLI_BASE_TERMS, thresholds, _day(as_of))
    stored = row["quadrant"]
    found = decision.quadrant.value if decision.quadrant is not None else None
    if path is ReturnPath.WITHDRAWAL and found != stored:
        raise ValueError(f"engine quadrant {found} differs from the scoring's {stored}")
    scored = path is ReturnPath.WITHDRAWAL and quality is EvidenceQuality.SUFFICIENT
    cluster = store.cluster(cluster_id) if scored and cluster_id else None
    case = build_case(
        decision,
        request,
        NAVIGLI_BASE_TERMS,
        policy_text=POLICY_TEXT,
        scores=scores if scored else None,
        factors=_factors(row["shap_top3"], cluster is not None) if scored else (),
        cluster=cluster,
        missing_evidence=missing,
        nature=nature,
        as_of=_day(as_of),
    )
    _, expected = serialize_decision(decision, NAVIGLI_BASE_TERMS)
    issues = check_case(case, expected)
    if issues:
        raise ValueError("; ".join(f"{i.code}: {i.detail}" for i in issues))
    return BenchCase(
        return_id=request.return_id,
        customer_id=request.customer_id,
        golden=golden,
        template_key=expected.template_key,
        case=case,
        expected=expected,
    )
