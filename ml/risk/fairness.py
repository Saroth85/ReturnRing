"""FAIR-02 and FAIR-03 on the batch scores (spec 12; US-605). Offline: reads the ground truth.

FAIR-02, false positives by segment. A false positive is a non-abusive customer (normal, size or
shared_benign in the ground truth) that the product treats as high risk, counted two ways:

- flagged: risk >= the customer model's threshold (the model alone);
- restrictive: placed in Q3 or Q4 (risk above the threshold, sufficient evidence: what the
  customer would see, after human approval).

Per segment, the false-positive rate is false positives over the segment's non-abusive customers,
and the gap is that rate minus the rate of the whole population, in percentage points. A warning
is raised when a gap exceeds 5 points either way (spec 12: "scarto <= 5 punti"). Segments: new
customers (account opened less than 90 days before the decision time), linked accounts (in a graph
cluster) and families (shared_benign in the ground truth), low-data groups (fewer settled lines
than the evidence threshold), tenure bands, price bands (thirds of the mean line price, cut on the
population itself) and the customer's main category (most lines ordered).

FAIR-03: no shared_benign customer in Q3 or Q4 because of the cluster alone. Counterfactual: the
same customer scored with the cluster features computed on the account alone (as if it were linked
to nobody), with the same value, evidence and thresholds; "because of the cluster alone" means
Q3 or Q4 with the cluster and neither without it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Final

import numpy as np
import pandas as pd

from ml.risk.features import customer_frame
from ml.risk.model import RiskModel
from ml.risk.split import SplitConfig, split_customers, split_keys
from ml.scoring.batch import Inputs, ScoringResult, quadrant, thresholds
from pipeline.features.evidence import EVIDENCE_THRESHOLDS
from pipeline.features.snapshot import StoreSnapshot

GAP_LIMIT_PP: Final = 5.0
NEW_CUSTOMER_DAYS: Final = 90.0
RESTRICTIVE: Final = ("Q3", "Q4")
TENURE_BANDS: Final = (
    (0.0, 90.0, "under 90 days"),
    (90.0, 365.0, "90 days to 1 year"),
    (365.0, 730.0, "1 to 2 years"),
    (730.0, float("inf"), "2 years or more"),
)
NO_LINE: Final = "no line ordered"


def main_category(snap: StoreSnapshot) -> pd.Series:
    """customer_id -> the category of most of the lines ordered by the snapshot's as_of (ties:
    the first in alphabetical order)."""
    lines = snap["store/order_lines"][["order_id", "variant_id"]]
    orders = snap["store/orders"][["order_id", "customer_id"]]
    variants = snap["store/variants"][["variant_id", "category"]]
    merged = lines.merge(orders, on="order_id").merge(variants, on="variant_id")
    counts = merged.groupby(["customer_id", "category"]).size().rename("n").reset_index()
    counts = counts.sort_values(["customer_id", "n", "category"], ascending=[True, False, True])
    return counts.drop_duplicates("customer_id").set_index("customer_id")["category"]


def segment_labels(
    frame: pd.DataFrame,
    behaviour: pd.Series,
    categories: pd.Series,
    population: pd.Series,
    min_observations: int = EVIDENCE_THRESHOLDS.min_observations,
) -> tuple[pd.DataFrame, tuple[float, float]]:
    """The segment of every customer of `frame` (a customer_frame) in each FAIR-02 dimension,
    and the price-band cuts (thirds of mean_line_price_eur over `population`)."""
    f = frame.set_index("customer_id")
    out = pd.DataFrame(index=f.index)
    tenure = f["tenure_days"]
    out["New customers"] = np.where(
        tenure < NEW_CUSTOMER_DAYS, "opened under 90 days ago", "opened 90 days ago or more"
    )
    out["Linked accounts"] = np.where(
        f["cluster_size"] >= 2, "in a group of linked accounts", "no linked account"
    )
    out["Families (ground truth)"] = np.where(
        behaviour.reindex(f.index) == "shared_benign", "family member", "not a family member"
    )
    k = min_observations
    out["Low-data groups"] = np.where(
        f["n_observations"] < k, f"fewer than {k} settled lines", f"{k} or more settled lines"
    )
    bands = pd.Series("", index=f.index)
    for low, high, name in TENURE_BANDS:
        bands[(tenure >= low) & (tenure < high)] = name
    out["Tenure"] = bands
    price = f["mean_line_price_eur"]
    known = price[population.reindex(f.index).fillna(False).astype(bool) & price.notna()]
    cuts = (
        (float(np.quantile(known, 1 / 3)), float(np.quantile(known, 2 / 3)))
        if len(known)
        else (0.0, 0.0)
    )
    band = np.where(
        price.isna(),
        NO_LINE,
        np.where(
            price < cuts[0],
            f"under {cuts[0]:.2f} EUR",
            np.where(
                price < cuts[1], f"{cuts[0]:.2f} to {cuts[1]:.2f} EUR", f"{cuts[1]:.2f} EUR or more"
            ),
        ),
    )
    out["Price band (mean line price)"] = band
    out["Main category"] = categories.reindex(f.index).fillna(NO_LINE)
    return out, cuts


@dataclass(frozen=True)
class SegmentRow:
    dimension: str
    segment: str
    non_abusers: int
    flagged: int
    fpr: float | None
    restrictive: int
    fpr_restrictive: float | None
    gap_pp: float | None
    gap_restrictive_pp: float | None
    warning: bool


@dataclass(frozen=True)
class FairnessReport:
    population: str
    non_abusers: int
    flagged: int
    restrictive: int
    fpr: float | None
    fpr_restrictive: float | None
    threshold: float | None
    price_cuts: tuple[float, float]
    rows: list[SegmentRow] = field(default_factory=list)

    @property
    def warnings(self) -> list[SegmentRow]:
        return [r for r in self.rows if r.warning]

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["warnings"] = [f"{r.dimension}: {r.segment}" for r in self.warnings]
        return out


def _rate(n: int, d: int) -> float | None:
    return n / d if d else None


def _gap(rate: float | None, overall: float | None) -> float | None:
    return None if rate is None or overall is None else 100.0 * (rate - overall)


def fair02(
    scores: pd.DataFrame,
    segments: pd.DataFrame,
    abusive: pd.Series,
    population: pd.Series,
    threshold: float | None,
    price_cuts: tuple[float, float],
    name: str,
) -> FairnessReport:
    """FAIR-02 over the customers of `population` (a boolean Series by customer_id)."""
    s = scores.set_index("customer_id")
    keep = population.reindex(s.index).fillna(False).astype(bool)
    negative = (abusive.reindex(s.index) == 0) & keep
    flagged = (s["risk"] >= threshold) if threshold is not None else pd.Series(False, index=s.index)
    restrictive = s["quadrant"].isin(RESTRICTIVE)
    n = int(negative.sum())
    fp, fr = int((negative & flagged).sum()), int((negative & restrictive).sum())
    overall, overall_r = _rate(fp, n), _rate(fr, n)
    rows: list[SegmentRow] = []
    for dimension in segments.columns:
        seg = segments[dimension].reindex(s.index)
        for value in sorted(seg[negative].dropna().unique()):
            mask = negative & (seg == value)
            d = int(mask.sum())
            f, r = int((mask & flagged).sum()), int((mask & restrictive).sum())
            gap, gap_r = _gap(_rate(f, d), overall), _gap(_rate(r, d), overall_r)
            warn = any(g is not None and abs(g) > GAP_LIMIT_PP for g in (gap, gap_r))
            rows.append(
                SegmentRow(
                    dimension, str(value), d, f, _rate(f, d), r, _rate(r, d), gap, gap_r, warn
                )
            )
    return FairnessReport(name, n, fp, fr, overall, overall_r, threshold, price_cuts, rows)


@dataclass(frozen=True)
class Fair03:
    population: str
    families: int
    restrictive: int
    because_of_cluster: int
    customers: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.because_of_cluster == 0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


def counterfactual_quadrants(
    model: RiskModel, inputs: Inputs, scores: pd.DataFrame, as_of: datetime
) -> pd.Series:
    """customer_id -> the quadrant each customer would get with the cluster features computed
    on its account alone (same value, evidence and thresholds as `scores`)."""
    alone = customer_frame(inputs.features, inputs.graph.singletons_at(as_of), as_of)
    risk = pd.Series(model.predict(alone), index=alone["customer_id"])
    limits = thresholds(model, inputs.value, as_of)
    s = scores.set_index("customer_id")
    return pd.Series(
        [
            quadrant(float(risk[c]), s.at[c, "value_eur"], str(s.at[c, "evidence_quality"]), limits)
            for c in s.index
        ],
        index=s.index,
        dtype=object,
    )


def fair03(
    scores: pd.DataFrame,
    alone: pd.Series,
    behaviour: pd.Series,
    population: pd.Series,
    name: str,
) -> Fair03:
    """FAIR-03 over the shared_benign customers of `population`."""
    s = scores.set_index("customer_id")
    keep = population.reindex(s.index).fillna(False).astype(bool)
    family = keep & (behaviour.reindex(s.index) == "shared_benign")
    restrictive = s["quadrant"].isin(RESTRICTIVE)
    without = alone.reindex(s.index).isin(RESTRICTIVE)
    because = family & restrictive & ~without
    return Fair03(
        name,
        int(family.sum()),
        int((family & restrictive).sum()),
        int(because.sum()),
        tuple(sorted(s.index[because])),
    )


def segment_summary(report: FairnessReport) -> list[dict[str, Any]]:
    return [asdict(r) for r in report.rows]


def dimensions(report: FairnessReport) -> Sequence[str]:
    seen: list[str] = []
    for row in report.rows:
        if row.dimension not in seen:
            seen.append(row.dimension)
    return seen


def evaluate_scores(
    result: ScoringResult,
    inputs: Inputs,
    snap: StoreSnapshot,
    model: RiskModel,
    labels: pd.DataFrame,
    held_out: pd.Series,
) -> tuple[FairnessReport, list[Fair03]]:
    """FAIR-02 on the held-out customers and FAIR-03 on all and on held-out customers, from the
    batch scores at result.as_of (labels: ml.risk.labels.read_labels)."""
    as_of = result.as_of
    frame = customer_frame(inputs.features, inputs.graph.clusters_at(as_of), as_of)
    segments, cuts = segment_labels(frame, labels["behaviour"], main_category(snap), held_out)
    report = fair02(
        result.scores,
        segments,
        labels["abusive"],
        held_out,
        model.threshold,
        cuts,
        "held-out test customers",
    )
    alone = counterfactual_quadrants(model, inputs, result.scores, as_of)
    everyone = pd.Series(True, index=result.scores["customer_id"])
    checks = [
        fair03(result.scores, alone, labels["behaviour"], everyone, "all customers"),
        fair03(result.scores, alone, labels["behaviour"], held_out, "held-out test customers"),
    ]
    return report, checks


def held_out(inputs: Inputs, labels: pd.DataFrame, manifest: dict[str, Any]) -> pd.Series:
    """customer_id -> True for the customers of the test groups of the training run described by
    `manifest` (same keys, configuration and grouping instant as ml.risk.run)."""
    eval_end = datetime.fromisoformat(str(manifest["eval_end"]))
    clusters = inputs.graph.clusters_at(eval_end)
    keys = split_keys(clusters, labels)
    config = SplitConfig(**manifest["split"])
    split = split_customers(clusters["customer_id"], keys, labels["behaviour"], config)
    return pd.Series((split["side"] == "test").to_numpy(), index=split["customer_id"])
