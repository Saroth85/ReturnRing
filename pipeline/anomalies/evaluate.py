"""Offline evaluation of US-208 and the data side of US-606 (MOD-10, MOD-12; E2E-04).

python -m pipeline.anomalies.evaluate --data data --results docs/results/us-208-product-anomalies.md

An offline evaluation job: it reads the ground truth (truth/variants, rule 12) to score the flags,
and the out-of-distribution store (data/ood/store, data/truth/ood) only to evaluate, never to choose
a threshold. The detector itself (detect, build) never reads the truth. The results file is
rewritten from the data alone: no wall-clock value is written, so the same store gives the same
file.

Decision times: every Monday 00:00 UTC of the store period and the instant after its last day (the
extraction), as python -m pipeline.anomalies writes them. A flagged variant is a true anomaly when
it is defective and at least one of its flagged cells has the defect reason and a sales week that
overlaps the defect window.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from contracts.io import Provenance, read_table
from contracts.parquet import SCHEMAS
from pipeline.anomalies.build import (
    ANOMALIES_VERSION,
    TABLE,
    anomalies_at,
    anomaly_rows,
    cells_at,
    load_sales,
    store_decision_times,
)
from pipeline.anomalies.catalogue import FOLLOW_WEEKS, BeforeAfter, before_after, catalogue_task
from pipeline.anomalies.detect import (
    ANOMALY_THRESHOLDS,
    PER_TEST_EXACT,
    REASONS,
    SPEC_LITERAL,
    AnomalyThresholds,
    expected_returns,
    flagged,
    sales_frame,
    z_scores,
)
from pipeline.generator import golden
from policy.engine import NAVIGLI_BASE_TERMS, Thresholds, choose_path, decide
from policy.types import CaseScores, EvidenceQuality, ReturnReason, ReturnRequest, ReturnTerms

PLACEBOS_PER_FIX: Final = 5  # non-defective best sellers of the category per fixed defect


@dataclass(frozen=True)
class Step:
    label: str
    thresholds: AnomalyThresholds
    succession: bool


STEPS: Final = (
    Step("1. Spec 8.9 read literally: E = h / S, z > 3", SPEC_LITERAL, False),
    Step("2. + rule of succession: E = (h + 1) / (S + 2) per line", SPEC_LITERAL, True),
    Step("3. + exact Poisson tail at the level of z > 3", PER_TEST_EXACT, True),
    Step(
        f"**4. + level shared by the {len(REASONS)} reasons of a variant-week "
        f"({ANOMALY_THRESHOLDS.version}, the product rule)**",
        ANOMALY_THRESHOLDS,
        True,
    ),
    Step("4 without step 2 (E = h / S)", ANOMALY_THRESHOLDS, False),
)
PRODUCT_STEP: Final = 3


def _f(x: float | None, digits: int = 3) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    if abs(x) < 0.5 * 10.0**-digits:
        x = 0.0  # no "-0.0"
    return f"{x:,.{digits}f}"


def _day(t: datetime | pd.Timestamp) -> str:
    return f"{pd.Timestamp(t):%Y-%m-%d %H:%M}Z"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


# ---- Scoring against the ground truth -----------------------------------------------------------


def load_truth(root: Path) -> pd.DataFrame:
    """truth/variants (offline evaluation only, rule 12)."""
    frame, _ = read_table("truth/variants", root)
    return frame.to_pandas()


def label_cells(cells: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """Adds defective and match: the cell has the variant's defect reason and a sales week that
    overlaps the defect window [defect_start, defect_end) (no end: never fixed)."""
    cols = ["variant_id", "defective", "defect_reason", "defect_start", "defect_end"]
    out = cells.merge(truth[cols], on="variant_id", how="left", validate="many_to_one")
    out["defective"] = out["defective"].fillna(False).astype(bool)
    week_end = out["week"] + pd.Timedelta(days=7)
    ended = out["defect_end"].notna() & (out["week"] >= out["defect_end"])
    out["match"] = (
        out["defective"]
        & (out["reason"] == out["defect_reason"])
        & (week_end > out["defect_start"]).fillna(False).astype(bool)
        & ~ended
    )
    return out


def with_estimator(cells: pd.DataFrame, succession: bool) -> pd.DataFrame:
    """The cells with E (and z, tail) from the rule of succession or from the plain share."""
    out = cells.copy()
    out["expected"] = expected_returns(
        out["baseline_hits"], out["n"], out["prior_sales"], succession=succession
    )
    out["z"], out["tail_p"] = z_scores(out["observed"], out["expected"])
    return out


@dataclass(frozen=True)
class Scores:
    flagged: int  # variants with at least one flagged cell
    true: int  # of which defective with a matching cell
    any_defect: int  # of which defective, whatever the reason and week
    defective: int  # defective variants in the store
    cells: int  # distinct flagged (variant, reason, week)
    true_cells: int

    @property
    def precision(self) -> float | None:
        return self.true / self.flagged if self.flagged else None

    @property
    def precision_any_defect(self) -> float | None:
        return self.any_defect / self.flagged if self.flagged else None

    @property
    def recall(self) -> float | None:
        return self.true / self.defective if self.defective else None

    @property
    def cell_precision(self) -> float | None:
        return self.true_cells / self.cells if self.cells else None


def score(cells: pd.DataFrame, flags: pd.Series, defective: int) -> Scores:
    """Variant-level and cell-level scores of the flagged cells (labelled by label_cells)."""
    hit = cells.loc[flags]
    per_variant = hit.groupby("variant_id").agg(
        defective=("defective", "first"), match=("match", "any")
    )
    distinct = hit.drop_duplicates(["variant_id", "reason", "week"])
    return Scores(
        flagged=len(per_variant),
        true=int(per_variant["match"].sum()),
        any_defect=int(per_variant["defective"].sum()),
        defective=defective,
        cells=len(distinct),
        true_cells=int(distinct["match"].sum()),
    )


def first_flags(cells: pd.DataFrame, flags: pd.Series, truth: pd.DataFrame) -> pd.Series:
    """Days from the defect start to the first decision time with a matching flag, per detected
    defective variant."""
    hit = cells.loc[flags & cells["match"]]
    first = hit.groupby("variant_id")["as_of"].min()
    start = truth.set_index("variant_id")["defect_start"].reindex(first.index)
    return (first - start).dt.total_seconds() / 86400.0


# ---- One store ---------------------------------------------------------------------------------


@dataclass
class Evaluated:
    name: str
    provenance: Provenance  # of store/order_lines
    truth: pd.DataFrame
    sales: pd.DataFrame  # known at the end
    cells: pd.DataFrame  # every decision time, labelled, with as_of
    instants: list[datetime]

    @property
    def end(self) -> datetime:
        return self.instants[-1]

    @property
    def defective(self) -> int:
        return int(self.truth["defective"].sum())

    def at_end(self) -> pd.DataFrame:
        return self.cells.loc[self.cells["as_of"] == pd.Timestamp(self.end)]


def evaluate_store(
    name: str, store_root: Path, truth_root: Path, log: Callable[[str], None]
) -> Evaluated:
    instants = store_decision_times(store_root)
    snap = load_sales(store_root, instants[-1])
    parts = []
    for t in instants:
        cells = cells_at(snap, t, ANOMALY_THRESHOLDS.min_sales)
        parts.append(cells.assign(as_of=pd.Timestamp(t)))
    truth = load_truth(truth_root)
    cells = label_cells(pd.concat(parts, ignore_index=True), truth)
    log(f"{name}: cells at {len(instants)} decision times")
    return Evaluated(
        name,
        snap.provenance["store/order_lines"],
        truth,
        sales_frame(snap),
        cells,
        instants,
    )


# ---- Sections ----------------------------------------------------------------------------------


def section_volume(stores: Sequence[Evaluated]) -> list[str]:
    rows = []
    for ev in stores:
        weekly = ev.sales.groupby(["variant_id", "week"]).size().rename("n").reset_index()
        weekly = label_cells(weekly.assign(reason=None), ev.truth)
        start = weekly["defect_start"]
        in_window = (
            weekly["defective"]
            & (weekly["week"] + pd.Timedelta(days=7) > start).fillna(False).astype(bool)
            & ~(weekly["defect_end"].notna() & (weekly["week"] >= weekly["defect_end"]))
        )
        tested = weekly["n"] >= ANOMALY_THRESHOLDS.min_sales
        variants = weekly.loc[tested, "variant_id"].unique()
        defective_ids = set(ev.truth.loc[ev.truth["defective"], "variant_id"])
        reach = weekly.loc[tested & in_window, "variant_id"].nunique()
        rows.append(
            [
                ev.name,
                f"{int(tested.sum()):,}",
                f"{int(tested.sum()) * len(REASONS):,}",
                f"{len(variants):,}",
                f"{len(defective_ids & set(variants))} of {ev.defective}",
                f"{reach} of {ev.defective}",
                f"{int((tested & in_window).sum()):,} of {int(in_window.sum()):,}",
            ]
        )
    return [
        "## A. Volume: who reaches the minimum of 20 sales in a week",
        "",
        "Weekly sales from store/order_lines known at the extraction instant, in Monday-to-Sunday "
        "sales weeks (UTC); no return and no label is used for the counts, the ground truth only "
        "tells which variants are defective and when (offline evaluation). Nature: synthetic.",
        "",
        *table(
            [
                "Store",
                "Variant-weeks with >= 20 sales",
                "Tests (x 8 reasons)",
                "Variants that ever reach 20",
                "Defective among them",
                "Defective variants with >= 20 sales in a window week",
                "Window weeks with >= 20 sales (of window weeks with a sale)",
            ],
            rows,
        ),
        "",
        "When every defective variant reaches the minimum in at least one week of its defect "
        "window, the single-week rule of spec 8.9 can see each of them. The multi-week window "
        "variant (pooling weeks under the minimum) is needed only if the single-week rule cannot "
        "work; it was not built, and sections B and C measure the single-week rule. The window "
        "weeks under 20 sales are mostly the weeks after each defect's 4-week sales surge "
        "(docs/synthetic-store.md, known limits).",
        "",
    ]


def _step_cells(ev: Evaluated, step: Step) -> tuple[pd.DataFrame, pd.Series]:
    cells = with_estimator(ev.cells, step.succession)
    return cells, flagged(cells, step.thresholds)


def section_steps(ev: Evaluated, letter: str, title: str) -> list[str]:
    end_rows, union_rows = [], []
    for step in STEPS:
        cells, flags = _step_cells(ev, step)
        at_end = cells["as_of"] == pd.Timestamp(ev.end)
        s = score(cells.loc[at_end], flags.loc[at_end], ev.defective)
        end_rows.append(
            [
                step.label,
                str(s.flagged),
                str(s.true),
                _f(s.precision),
                _f(s.precision_any_defect),
                f"{_f(s.recall)} ({s.true} of {s.defective})",
                str(s.cells),
                _f(s.cell_precision),
            ]
        )
        u = score(cells, flags, ev.defective)
        days = first_flags(cells, flags, ev.truth)
        union_rows.append(
            [
                step.label,
                str(u.flagged),
                _f(u.precision),
                f"{_f(u.recall)} ({u.true} of {u.defective})",
                _f(float(days.median()) if len(days) else None, 0),
                _f(float(days.quantile(0.9)) if len(days) else None, 0),
                _f(float(days.max()) if len(days) else None, 0),
            ]
        )
    period = f"{ev.provenance.period_start} to {_day(ev.end)}"
    return [
        f"## {letter}. {title}",
        "",
        f"Origin: {ev.name} store, sales weeks {period}; nature: synthetic. Truth: "
        "truth/variants (offline evaluation only). Each step adds one change to the one above; "
        "step 4 is what python -m pipeline.anomalies writes.",
        "",
        f"At the extraction instant {_day(ev.end)} (every sales week of the year, each cohort read "
        "with all its returns known by then):",
        "",
        *table(
            [
                "Rule",
                "Variants flagged",
                "True anomalies",
                "Precision (MOD-10)",
                "Precision, any defect",
                "Recall",
                "Cells flagged",
                "Cell precision",
            ],
            end_rows,
        ),
        "",
        f"Weekly operation: one run every Monday 00:00 UTC ({len(ev.instants) - 1} runs) and one "
        "at the extraction instant, each with what was known then. A variant flagged in any run "
        "would have opened a catalogue task; the delay runs from the defect start to the first run "
        "with a matching flag:",
        "",
        *table(
            [
                "Rule",
                "Catalogue tasks (variants)",
                "Precision",
                "Recall",
                "Delay median (days)",
                "Delay p90",
                "Delay max",
            ],
            union_rows,
        ),
        "",
    ]


def section_false_positives(stores: Sequence[Evaluated]) -> list[str]:
    rows = []
    for ev in stores:
        cells, flags = _step_cells(ev, STEPS[PRODUCT_STEP])
        wrong = cells.loc[flags & ~cells["match"]].sort_values("as_of")
        wrong = wrong.drop_duplicates(["variant_id", "reason", "week"])
        truth = ev.truth.merge(
            ev.sales.drop_duplicates("variant_id")[["variant_id", "category"]], on="variant_id"
        )
        relative = (
            truth.set_index("variant_id")["base_return_rate"]
            / truth.groupby("category")["base_return_rate"].transform("mean").to_numpy()
        )
        for r in wrong.itertuples(index=False):
            rows.append(
                [
                    ev.name,
                    str(r.variant_id),
                    str(r.category),
                    str(r.reason),
                    f"{pd.Timestamp(r.week):%Y-%m-%d}",
                    f"{pd.Timestamp(r.as_of):%Y-%m-%d}",
                    str(int(r.n)),
                    str(int(r.observed)),
                    _f(float(r.expected), 2),
                    _f(float(r.z), 1),
                    f"{float(r.tail_p):.1e}",
                    "yes" if r.defective else "no",
                    _f(float(relative.get(r.variant_id, np.nan)), 2),
                ]
            )
    header = [
        "Store",
        "Variant",
        "Category",
        "Reason",
        "Sales week",
        "First run",
        "n",
        "O",
        "E",
        "z",
        "P(X >= O)",
        "Defective",
        "Latent rate / category mean",
    ]
    return [
        "## D. Flagged cells that match no defect window (product rule, every run)",
        "",
        "Each cell flagged by step 4 in any run that does not match a defect window, at its first "
        "run. The latent return rate relative to the mean of the category's variants that sold "
        "comes from the ground truth (offline only): the store draws each variant's base rate "
        "from the real ASOS distribution "
        "of its product type, so some variants are returned much more than their category for "
        "every reason, and their returns are measured against the category.",
        "",
        *(table(header, rows) if rows else ["None."]),
        "",
    ]


def _elena_return(store: Path, end: datetime) -> tuple[ReturnRequest, str] | None:
    """Elena's return of the jacket, as the engine receives it, and when it was known."""
    lines, _ = read_table("store/order_lines", store, as_of=end)
    orders, _ = read_table("store/orders", store, as_of=end)
    returns, _ = read_table("store/returns", store, as_of=end)
    deliveries, _ = read_table("store/deliveries", store, as_of=end)
    terms, _ = read_table("store/terms_snapshots", store, as_of=end)
    o = orders.to_pandas()
    o = o.loc[o["customer_id"] == golden.ELENA]
    ln = lines.to_pandas()
    ln = ln.loc[ln["order_id"].isin(o["order_id"]) & (ln["variant_id"] == golden.DEFECTIVE_JACKET)]
    ret = returns.to_pandas().merge(ln[["line_id", "order_id"]], on="line_id")
    if ret.empty:
        return None
    r = ret.sort_values("event_at").iloc[0]
    order = o.set_index("order_id").loc[r["order_id"]]
    delivered = deliveries.to_pandas().set_index("order_id").loc[r["order_id"], "event_at"]
    snapshot = terms.to_pandas().set_index("terms_snapshot_id").loc[order["terms_snapshot_id"]]
    request = ReturnRequest(
        return_id=str(r["return_id"]),
        order_id=str(r["order_id"]),
        customer_id=golden.ELENA,
        reason=ReturnReason(r["reason"]),
        delivered_on=pd.Timestamp(delivered).date(),
        notice_on=pd.Timestamp(r["event_at"]).date(),
        order_terms=ReturnTerms(
            return_window_days=int(snapshot["return_window_days"]),
            free_return_shipping=bool(snapshot["free_return_shipping"]),
            instant_refund=bool(snapshot["instant_refund"]),
            free_size_exchange=bool(snapshot["free_size_exchange"]),
            coupons_enabled=bool(snapshot["coupons_enabled"]),
        ),
    )
    return request, _day(pd.Timestamp(r["available_at"]))


def section_golden(main: Evaluated, store: Path) -> list[str]:
    jacket, reason = golden.DEFECTIVE_JACKET, golden.JACKET_DEFECT_REASON
    cells, flags = _step_cells(main, STEPS[PRODUCT_STEP])
    mine = cells.loc[(cells["variant_id"] == jacket) & (cells["reason"] == reason)]
    first_run = mine.loc[flags.loc[mine.index], "as_of"].min()
    end = pd.Timestamp(main.end)
    series = before_after(main.sales, jacket, reason, main.end, main.end).series
    window = series.loc[series["week"] >= golden.JACKET_DEFECT_START - timedelta(weeks=4)]
    flagged_weeks = set(mine.loc[(mine["as_of"] == end) & flags.loc[mine.index], "week"])
    series_rows = [
        [
            f"{pd.Timestamp(r.week):%Y-%m-%d}",
            str(int(r.n)),
            str(int(r.observed)),
            _f(float(r.expected), 2) if pd.notna(r.expected) else "n/a",
            _f(float(r.z), 1) if pd.notna(r.z) else "n/a",
            "yes" if r.week in flagged_weeks else ("under 20 sales" if r.n < 20 else "no"),
            f"{r.comparison_observed} / {r.comparison_n:,}",
        ]
        for r in window.itertuples(index=False)
    ]
    rows_end = anomaly_rows(mine.loc[mine["as_of"] == end], main.end, ANOMALY_THRESHOLDS)
    task: dict[str, object] = {}
    if len(rows_end):
        variants, _ = read_table("store/variants", store, as_of=main.end)
        v = variants.to_pandas().set_index("variant_id").loc[jacket]
        # The file written by python -m pipeline.anomalies names the whole store origin; it is
        # shortened here because the top of this file already quotes it.
        prov = Provenance(
            f"ReturnRing {ANOMALIES_VERSION} (thresholds {ANOMALY_THRESHOLDS.version}), computed "
            "from the main Navigli Fashion store (origin at the top of this file)",
            main.provenance.period_start,
            (end - timedelta(microseconds=1)).date(),
            main.provenance.nature,
        )
        task = catalogue_task(
            rows_end, product_id=str(v["product_id"]), category=str(v["category"]), provenance=prov
        )
        task_block = [
            "The catalogue task at the extraction instant (pipeline.anomalies.catalogue."
            "catalogue_task; every number has the origin, period and nature of its provenance):",
            "",
            "```json",
            *json.dumps(task, indent=2, ensure_ascii=False).splitlines(),
            "```",
        ]
    else:
        task_block = ["No anomaly of V-JKT-DEF at the extraction instant: no catalogue task."]
    if pd.isna(first_run):
        first = "Not flagged by any run."
    else:
        days = (first_run - pd.Timestamp(golden.JACKET_DEFECT_START)).days
        first = f"First flagged by the weekly run of {_day(first_run)}, {days} days after the "
        first += "defect start."
    elena = _elena_return(store, main.end)
    out = [
        "## E. Golden case: Elena and the jacket V-JKT-DEF (US-208, US-606, E2E-04 data side)",
        "",
        f"V-JKT-DEF has a fit defect ({reason.replace('_', ' ')}) from "
        f"{golden.JACKET_DEFECT_START:%Y-%m-%d}, not fixed in the period. Its weekly series at the "
        f"extraction instant {_day(end)} for that reason (main store, nature synthetic; the "
        "comparison is the rest of its category in the same sales week, US-606):",
        "",
        *table(
            ["Sales week", "n", "O", "E", "z", "Flagged", "Category: returned / sold"], series_rows
        ),
        "",
        f"{first} Demo case: it shows the mechanism and is never a measure of accuracy (US-906).",
        "",
        *task_block,
        "",
    ]
    names = set(SCHEMAS[TABLE].names)
    customer_columns = sorted(n for n in names if "customer" in n)
    if elena is None:
        out += ["Elena's return of the jacket is not in the store.", ""]
        return out
    request, known = elena
    decision = decide(
        request,
        CaseScores(risk=1.0, value_eur=Decimal("0"), evidence_quality=EvidenceQuality.SUFFICIENT),
        NAVIGLI_BASE_TERMS,
        Thresholds(risk=0.5, value_eur=Decimal("100")),
        decided_on=request.notice_on,
    )
    restrictive = decision.levers.lever_ids(NAVIGLI_BASE_TERMS)
    out += [
        "Elena's return stays regular. What the data side guarantees (E2E-04 is completed by the "
        "API and UI tests):",
        "",
        *table(
            ["Check", "Result"],
            [
                [
                    "Her return of the jacket",
                    f"{request.return_id}, reason {request.reason.value}, requested "
                    f"{request.notice_on}, known {known}",
                ],
                ["Path (policy.engine.choose_path)", choose_path(request.reason).value],
                [
                    "Engine decision with the highest possible risk score (1.0)",
                    f"quadrant {decision.quadrant.value if decision.quadrant else 'none'}, "
                    "approval required "
                    f"{'yes' if decision.requires_approval else 'no'}, restrictive levers "
                    f"{restrictive or 'none'}",
                ],
                [
                    "Customer columns in features/product_anomalies",
                    ", ".join(customer_columns) or "none",
                ],
                [
                    "Customer fields in the catalogue task",
                    (", ".join(k for k in task if "customer" in k) or "none")
                    if task
                    else "no task",
                ],
            ],
        ),
        "",
    ]
    return out


def section_follow_up(main: Evaluated, log: Callable[[str], None]) -> list[str]:
    """US-606 on the defects fixed in the period: before/after at the fix, and placebos."""
    end = pd.Timestamp(main.end)
    horizon = end - pd.Timedelta(weeks=FOLLOW_WEEKS + 1)
    fixed = main.truth.loc[main.truth["defective"] & (main.truth["defect_end"] <= horizon)]
    category = main.sales.drop_duplicates("variant_id").set_index("variant_id")["category"]
    defective_ids = set(main.truth.loc[main.truth["defective"], "variant_id"])
    # The comparison leaves out the variants the detector flagged for the reason (its own output
    # at the extraction instant, as the API would read it from features/product_anomalies).
    cells, flags = _step_cells(main, STEPS[PRODUCT_STEP])
    at_end = flags & (cells["as_of"] == end)
    anomalous = cells.loc[at_end].groupby("reason")["variant_id"].agg(lambda v: sorted(set(v)))
    real: list[BeforeAfter] = []
    placebo: list[BeforeAfter] = []
    for r in fixed.sort_values("variant_id").itertuples(index=False):
        change = pd.Timestamp(r.defect_end)
        exclude = anomalous.get(r.defect_reason, [])
        args = (r.defect_reason, change, main.end)
        real.append(before_after(main.sales, r.variant_id, *args, exclude=exclude))
        lo, hi = (
            change - pd.Timedelta(weeks=FOLLOW_WEEKS),
            change + pd.Timedelta(weeks=FOLLOW_WEEKS),
        )
        near = main.sales.loc[
            (main.sales["category"] == category[r.variant_id])
            & ~main.sales["variant_id"].isin(defective_ids)
            & (main.sales["week"] >= lo)
            & (main.sales["week"] < hi)
        ]
        best = near["variant_id"].value_counts().sort_index(kind="stable")
        best = best.sort_values(ascending=False, kind="stable").head(PLACEBOS_PER_FIX).index
        placebo.extend(before_after(main.sales, v, *args, exclude=exclude) for v in best)
    log(f"US-606 follow-up: {len(real)} fixes, {len(placebo)} placebos")

    def summary(name: str, items: Sequence[BeforeAfter]) -> list[str]:
        ok = [b for b in items if b.interval is not None and b.difference is not None]
        fell = sum(1 for b in ok if b.interval is not None and b.interval[1] < 0)
        rose = sum(1 for b in ok if b.interval is not None and b.interval[0] > 0)
        raw_drop = sum(1 for b in ok if b.after.observed < b.before.observed)
        diffs = [100 * b.difference for b in ok if b.difference is not None]
        return [
            name,
            str(len(items)),
            str(len(ok)),
            f"{raw_drop} ({_f(raw_drop / len(ok) if ok else None, 2)})",
            f"{fell} ({_f(fell / len(ok) if ok else None, 2)})",
            str(rose),
            _f(float(np.median(diffs)) if diffs else None, 1),
        ]

    return [
        "## F. Follow-up after a change (US-606): a decrease alone does not prove the effect",
        "",
        f"For every defect that ended at least {FOLLOW_WEEKS + 1} weeks before the extraction "
        f"instant (in the generator the defective batch stops at defect_end; here that date "
        f"stands for the catalogue fix), before_after compares the {FOLLOW_WEEKS} sales weeks "
        "before and after the change for the defect reason, against the rest of the category in "
        f"the same weeks, read at {_day(end)}; the comparison leaves out the variants flagged "
        "for the reason at that instant (detector output). Placebo: the same comparison, at the "
        "same date and "
        f"for the same reason, on the {PLACEBOS_PER_FIX} best-selling non-defective variants of "
        "the same category in those weeks, where nothing changed. Origin: main store; nature: "
        "synthetic; the change dates come from the ground truth (offline evaluation).",
        "",
        *table(
            [
                "Variants",
                "Follow-ups",
                "Assessable (>= 20 sales each side)",
                "Fewer returns after (raw count)",
                "Rate fell vs category (95% interval below 0)",
                "Rate rose vs category",
                "Median difference (points)",
            ],
            [summary("Defects at their fix", real), summary("Placebos, same dates", placebo)],
        ),
        "",
        "A raw decrease of the returns count also happens where nothing was fixed (sales move, "
        "surges end), so the follow-up states the difference against the rest of the category "
        "over the same weeks, with its interval, and the fixed caveat of "
        "pipeline.anomalies.catalogue.FOLLOW_UP_NOTE. The interval is approximate (normal, "
        "independent lines).",
        "",
    ]


def section_limits(main: Evaluated) -> list[str]:
    at_end = main.at_end()
    plain = at_end.loc[~at_end["defective"]]
    rows = []
    for reason in REASONS:
        part = plain.loc[plain["reason"] == reason]
        o, e = int(part["observed"].sum()), float(part["expected"].sum())
        rows.append([reason, f"{len(part):,}", f"{o:,}", _f(e, 1), _f(o / e if e else None, 2)])
    return [
        "## G. Known limits, measured",
        "",
        "- **The baseline includes other defects.** The category baseline counts every other "
        "variant of the category, defective or not, so the expected returns for the defect "
        "reasons are inflated where other defects sold. Observed against expected over the "
        "non-defective cohorts tested at the extraction instant (main store, nature synthetic):",
        "",
        *table(["Reason", "Cells", "O", "E", "O / E"], rows),
        "",
        "  This makes the defect reasons harder to flag on any variant (fewer false alarms, later "
        "detection), and it is reported rather than corrected: excluding flagged cells from the "
        "baseline is a possible next step.",
        "- **Variants returned more than their category** for every reason produce most of the "
        "remaining false flags (section D). The category baseline cannot see a variant's own "
        "level, by design of spec 8.9; such a variant may still deserve a look at its page.",
        "- **Who returns.** The detector counts returns, not returners: a group of linked accounts "
        "returning one variant in one week could look like a product anomaly. The synthetic rings "
        "spread their purchases over the catalogue, so this case is not measured here.",
        "- **Synthetic mechanism.** Every defect starts with a 4-week sales surge, and matched "
        "non-defective controls surge alike (docs/synthetic-store.md). The OOD store changes the "
        "customer rules, not the defect mechanism, so its rows test the detector on another "
        "population of customers, not on another kind of defect.",
        "",
    ]


def section_availability(store: Path, ev: Evaluated, log: Callable[[str], None]) -> list[str]:
    """MOD-08: the rows at T do not depend on any fact known after T."""
    picks = list(dict.fromkeys([*ev.instants[12::13], ev.end]))
    full = load_sales(store, ev.end)
    lines = full["store/order_lines"]
    returns = full["store/returns"]
    rows = []
    for t in picks:
        cut = anomalies_at(full, t)
        read = anomalies_at(load_sales(store, t), t)
        limit = pd.Timestamp(t)
        rows.append(
            [
                _day(t),
                str(len(cut)),
                "yes" if cut.equals(read) else "NO",
                f"{int((lines['available_at'] > limit).sum()):,}",
                f"{int((returns['available_at'] > limit).sum()):,}",
                "yes" if (pd.to_datetime(cut["week"]) <= limit.tz_localize(None)).all() else "NO",
            ]
        )
    log("MOD-08 check")
    return [
        "## H. Decision-time availability (MOD-08)",
        "",
        f"Rows of features/product_anomalies at {len(picks)} decision times of the {ev.name} "
        "store, computed from the store read at T (contracts.io.read_table(as_of=T)) and from the "
        "store read at the extraction instant and cut at T: identical, although the later store "
        "holds the order lines and returns known after T counted below. Nature: synthetic.",
        "",
        *table(
            [
                "Decision time",
                "Rows",
                "Identical",
                "Order lines known after T",
                "Returns known after T",
                "Every week <= T",
            ],
            rows,
        ),
        "",
    ]


def report(data: Path, log: Callable[[str], None]) -> str:
    main = evaluate_store("main", data, data, log)
    ood = evaluate_store("out-of-distribution", data / "ood", data / "truth" / "ood", log)
    t = ANOMALY_THRESHOLDS
    lines = [
        "# Product anomalies: measured results",
        "",
        "Stories US-208 and US-606, data side (tests MOD-10, MOD-08, MOD-12, E2E-04; decision "
        "D-15). Design and definitions: docs/product-anomalies.md.",
        "",
        "Rewritten by `python -m pipeline.anomalies.evaluate --data data --results "
        "docs/results/us-208-product-anomalies.md` from the data alone (no wall-clock value), so "
        "the same store gives the same file. Every number below is synthetic: measured on the "
        "Navigli Fashion store, whose defects and their sales surges are design hypotheses "
        "calibrated on real ASOS aggregates. None is a measure on real products.",
        "",
        f"- Main store origin: {main.provenance.origin}; nature {main.provenance.nature}.",
        f"- OOD store origin: {ood.provenance.origin}; nature {ood.provenance.nature}.",
        f"- Code: {ANOMALIES_VERSION}; product thresholds {t.version}: at least {t.min_sales} "
        f"sales in the week, z > {t.z:g}, and P(X >= O) <= {t.level:.3g} for X ~ Poisson(E) (the "
        f"normal tail of z = {t.z:g} shared by the {len(REASONS)} reasons).",
        "",
        *section_volume([main, ood]),
        *section_steps(main, "B", "MOD-10 on the main store"),
        *section_steps(
            ood,
            "C",
            "MOD-12: the out-of-distribution store, a separate row whatever the result",
        ),
        *section_false_positives([main, ood]),
        *section_golden(main, data),
        *section_follow_up(main, log),
        *section_limits(main),
        *section_availability(data, main, log),
    ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline.anomalies.evaluate", description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"), help="root of store/ and truth/")
    p.add_argument("--results", type=Path, default=Path("docs/results/us-208-product-anomalies.md"))
    args = p.parse_args(argv)
    started = time.perf_counter()  # for the log only, never written to the file

    def log(message: str) -> None:
        print(f"[{time.perf_counter() - started:7.1f} s] {message}", file=sys.stderr)

    text = report(args.data, log)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(text, encoding="utf-8")
    log(f"wrote {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
