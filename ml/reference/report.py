"""Offline evaluation of US-205, US-206 and US-209 on the generated store (MOD-06, MOD-09, MOD-12,
D-15). python -m ml.reference.report --data data --results docs/results/customer-quantities.md

This is an offline evaluation job: it reads the ground truth (truth/labels, rule 12) to report
metrics by behaviour, and the out-of-distribution store (data/ood/store, data/truth/ood) only to
evaluate, never to train or to choose a threshold. It rewrites the results file from the data
alone: no wall-clock value is written, so the same store gives the same file.

Split (spec 8.4): train end 2026-07-01T00:00Z (the first nine months), evaluation on the lines
ordered in the last three months and settled by the extraction instant 2026-10-01T00:00Z.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from contracts.io import Provenance, read_table
from ml.reference.features import Encoding
from ml.reference.metrics import Calibration, calibration, reliability
from ml.reference.model import (
    MODEL_VERSION,
    LineModel,
    TrainConfig,
    choose_k,
    fit,
    observed_frame,
    observed_line_probabilities,
)
from pipeline.features.build import FEATURES_VERSION, FeatureSet, features_at
from pipeline.features.evidence import EVIDENCE_THRESHOLDS, evidence_quality
from pipeline.features.lines import line_frame
from pipeline.features.residual import residuals
from pipeline.features.snapshot import StoreSnapshot, load_snapshot
from pipeline.features.value import VALUE_ASSUMPTIONS, reconcile
from pipeline.generator import golden

TRAIN_END: Final = datetime(2026, 7, 1, tzinfo=UTC)
EVAL_INSTANTS: Final = (
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 9, 1, tzinfo=UTC),
    datetime(2026, 10, 1, tzinfo=UTC),
)
DATA_END: Final = EVAL_INSTANTS[-1]
ABUSIVE: Final = ("wardrober", "promo_ring")
BEHAVIOURS: Final = ("normal", "size", "shared_benign", "wardrober", "promo_ring")
OBSERVATION_GRID: Final = (1, 2, 3, 4, 5, 6, 8, 10)
COMPLETENESS_GRID: Final = (0.4, 0.5, 0.6, 0.7, 0.8)
COHERENCE_GRID: Final = (0.3, 0.5, 0.7)
LAST_COUNT: Final = 9  # settled-line counts 1..8 one by one, then 9 and more
RULE_AUC: Final = 0.9  # D-15: the residual separates once its AUC reaches this (hypothesis)


def _f(x: float | None, digits: int = 3) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:,.{digits}f}"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _day(t: datetime) -> str:
    return f"{t:%Y-%m-%d %H:%M}Z"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def auc(labels: pd.Series, scores: pd.Series) -> float | None:
    mask = scores.notna()
    y = labels[mask].to_numpy(dtype=bool)
    if y.min() == y.max():
        return None
    return float(roc_auc_score(y, scores[mask].to_numpy(dtype=float)))


@dataclass
class Store:
    name: str
    snap: StoreSnapshot
    labels: pd.DataFrame  # customer_id, behaviour (truth: offline evaluation only)
    provenance: Provenance


def load_store(name: str, store_root: Path, truth_root: Path) -> Store:
    snap = load_snapshot(store_root, DATA_END)
    labels, _ = read_table("truth/labels", truth_root)
    frame = labels.select(["customer_id", "behaviour"]).to_pandas()
    return Store(name, snap, frame, snap.provenance["store/orders"])


# ---- A. Reference model calibration (MOD-06, US-206) ------------------------------------------


def calibration_rows(y: np.ndarray, models: Sequence[tuple[str, np.ndarray]]) -> list[list[str]]:
    rows = []
    for name, p in models:
        c: Calibration = calibration(y, p)
        rows.append(
            [
                name,
                f"{c.n:,}",
                _f(c.observed_rate),
                _f(c.mean_p),
                _f(c.log_loss, 4),
                _f(c.brier, 4),
                _f(c.ece, 4),
                _f(c.auc),
            ]
        )
    return rows


def section_calibration(main: Store, ood: Store, log: Callable[[str], None]) -> list[str]:
    train = observed_frame(line_frame(main.snap.restrict(TRAIN_END)))
    y_train = train["returned"].to_numpy(dtype=float)
    groups = train["customer_id"].to_numpy()
    ref = fit(train, y_train, groups, "reference", TRAIN_END)
    pers = fit(train, y_train, groups, "personalized", TRAIN_END)
    k = choose_k(train, y_train, TrainConfig().k_grid)
    smoothed = LineModel("reference", "smoothed", Encoding.from_lines(train, k), TRAIN_END, 0)
    log("A: models trained")

    def eval_lines(store: Store) -> pd.DataFrame:
        frame = observed_frame(line_frame(store.snap))
        return frame[frame["ordered_at"] >= pd.Timestamp(TRAIN_END)].reset_index(drop=True)

    ev, ev_ood = eval_lines(main), eval_lines(ood)
    header = ["Predictor", "Lines", "Observed rate", "Mean p", "Log loss", "Brier", "ECE", "AUC"]
    rows = calibration_rows(
        ev["returned"].to_numpy(dtype=float),
        [
            ("Smoothed variant rate (baseline)", smoothed.predict(ev)),
            ("Reference, XGBoost before isotonic", ref.raw(ev)),
            ("**Reference, XGBoost + isotonic (used by the residual)**", ref.predict(ev)),
            ("Personalized, XGBoost before isotonic", pers.raw(ev)),
            ("Personalized, XGBoost + isotonic (checkout)", pers.predict(ev)),
        ],
    )
    ood_rows = calibration_rows(
        ev_ood["returned"].to_numpy(dtype=float),
        [
            ("OOD: smoothed variant rate", smoothed.predict(ev_ood)),
            ("OOD: reference, XGBoost + isotonic", ref.predict(ev_ood)),
            ("OOD: personalized, XGBoost + isotonic", pers.predict(ev_ood)),
        ],
    )
    rel = reliability(ev["returned"].to_numpy(dtype=float), ref.predict(ev))
    rel_rows = [[f"{lo:.1f}-{hi:.1f}", f"{n:,}", _f(mp), _f(rate)] for lo, hi, n, mp, rate in rel]
    trained = (
        f"Trained at {_day(TRAIN_END)} on the {len(train):,} lines settled by then (each priced "
        f"with what was known when it was ordered); validation 10% of the customers; k = {k:g} "
        f"for the smoothed rates; reference early-stopped at {ref.best_iteration} rounds, "
        f"personalized at {pers.best_iteration}."
    )
    return [
        "## A. Reference model: calibration on the last three months (US-206, MOD-06)",
        "",
        f"Origin: {main.name} store, lines ordered {TRAIN_END:%Y-%m-%d} to 2026-09-30 and settled "
        f"by {_day(DATA_END)}. Period: July to September 2026. Nature: synthetic. {trained}",
        "",
        *table(header, rows),
        "",
        "Out-of-distribution store (US-105, MOD-12), a separate row whatever the result: the "
        "models trained on the main store at the train end price the OOD lines; the OOD history "
        "counts come from the OOD store itself; nothing is trained on it. Same period and "
        "nature (synthetic).",
        "",
        *table(header, ood_rows),
        "",
        "Reliability of the reference (main store, last three months, equal-width bins of p):",
        "",
        *table(["p bin", "Lines", "Mean p", "Observed rate"], rel_rows),
        "",
        "MOD-06 asks an expected calibration error of at most 0.05; ECE here uses 10 equal-width "
        "bins weighted by lines.",
        "",
    ]


# ---- B. Residual with two references (US-206, MOD-09) ------------------------------------------


def residual_rows(res: pd.DataFrame) -> list[list[str]]:
    rows = []
    for b in BEHAVIOURS:
        part = res[res["behaviour"] == b]
        z = part["residual_z"].dropna()
        if not len(z):
            continue
        rows.append(
            [
                b,
                f"{len(z):,}",
                _f(float(z.mean()), 2),
                _f(float(z.median()), 2),
                _pct(float((z >= 2).mean())),
            ]
        )
    return rows


def golden_extra(fs: FeatureSet, customer: str) -> list[str]:
    """Settled lines, evidence outcome and value of a golden customer at fs.as_of."""
    ev = fs.evidence.set_index("customer_id")
    value = fs.value.set_index("customer_id")
    return [
        str(int(ev.at[customer, "n_observations"])),
        str(ev.at[customer, "evidence_quality"]),
        f"{float(value.at[customer, 'value_eur']):,.2f}",
    ]


def _residuals(store: Store, priced: pd.DataFrame) -> pd.DataFrame:
    res = residuals(priced)
    return res.merge(store.labels, on="customer_id", how="left")


def section_residual(
    main: Store,
    ood: Store,
    sets: dict[datetime, FeatureSet],
    ood_set: FeatureSet,
    log: Callable[[str], None],
) -> list[str]:
    out = [
        "## B. Residual with two references (US-206, MOD-09)",
        "",
        "z_c of spec 8.2 over each customer's settled lines, with p_l from the product/cohort "
        "reference (the residual of features/customer_features) and, for comparison only, from "
        "the personalized predictor. Both are cross-fitted by customer at the decision time. "
        "Behaviours come from the ground truth (offline evaluation, rule 12). Abusive = wardrober "
        "or promo_ring. The residual is an indicator, not a probability of abuse.",
        "",
    ]
    header = ["Behaviour", "Customers with z", "Mean z", "Median z", "z >= 2"]
    summary = []
    golden_rows: dict[str, list[str]] = {}
    for at in (TRAIN_END, DATA_END):
        lines = line_frame(main.snap.restrict(at))
        ref = _residuals(main, sets[at].priced.lines)
        pers = _residuals(main, observed_line_probabilities(lines, at, "personalized").lines)
        log(f"B: residuals at {at:%Y-%m-%d}")
        for name, res in (("reference", ref), ("personalized", pers)):
            abusive = res["behaviour"].isin(ABUSIVE)
            summary.append([_day(at), name, f"{res['residual_z'].notna().sum():,}",
                            _f(auc(abusive, res["residual_z"]))])  # fmt: skip
            if at == DATA_END:
                out += [
                    f"Main store at {_day(at)}, {name} (origin: main store; period October 2025 "
                    "to the decision time; nature: synthetic):",
                    "",
                    *table(header, residual_rows(res)),
                    "",
                ]
                z = res.set_index("customer_id")["residual_z"]
                for c in golden.GOLDEN_CUSTOMERS:
                    if c in z.index:
                        golden_rows.setdefault(c, [c]).append(_f(float(z[c]), 2))
    ood_lines = line_frame(ood.snap)
    main_lines = line_frame(main.snap)
    ood_ref = _residuals(ood, ood_set.priced.lines)
    ood_pers = _residuals(
        ood,
        observed_line_probabilities(ood_lines, DATA_END, "personalized", source=main_lines).lines,
    )
    log("B: OOD residuals")
    for name, res in (("reference", ood_ref), ("personalized", ood_pers)):
        abusive = res["behaviour"].isin(ABUSIVE)
        summary.append([f"OOD {_day(DATA_END)}", name, f"{res['residual_z'].notna().sum():,}",
                        _f(auc(abusive, res["residual_z"]))])  # fmt: skip
    out += [
        f"Out-of-distribution store at {_day(DATA_END)}, reference trained on the main store "
        "(separate row, never trained on; nature: synthetic):",
        "",
        *table(header, residual_rows(ood_ref)),
        "",
        "AUC of z for abusive against every other behaviour (customers with at least one settled "
        "line; a description of the synthetic store, not a risk model result):",
        "",
        *table(["Decision time", "Reference", "Customers", "AUC of z"], summary),
        "",
        "Golden cases at the end of the data (demo cases, never a measure of accuracy, US-906):",
        "",
        *table(
            ["Customer", "z reference", "z personalized", "Settled lines", "Evidence", "V (EUR)"],
            [row + golden_extra(sets[DATA_END], row[0]) for row in golden_rows.values()],
        ),
        "",
    ]
    return out


# ---- C. Value (US-205, UNIT-03) -----------------------------------------------------------------

VALUE_HEADER: Final = (
    "Decision time",
    "Customers",
    "Mismatches",
    "Order totals",
    "Sum of V",
    "p10",
    "p30",
    "p50",
    "p70",
    "p90",
    "V < 0",
)


def section_value(main: Store, sets: dict[datetime, FeatureSet]) -> list[str]:
    a = VALUE_ASSUMPTIONS
    rows, by_behaviour = [], []
    for at, fs in sets.items():
        v = fs.value
        value = v["value_eur"].astype(float)
        q = value.quantile([0.1, 0.3, 0.5, 0.7, 0.9])
        rows.append(
            [
                _day(at),
                f"{len(v):,}",
                f"{len(reconcile(v)):,}",
                f"{v['orders_total_eur'].sum():,.2f}",
                f"{value.sum():,.2f}",
                *[f"{x:,.2f}" for x in q],
                _pct(float((value < 0).mean())),
            ]
        )
        if at == DATA_END:
            merged = v.merge(main.labels, on="customer_id", how="left")
            for b in BEHAVIOURS:
                part = merged.loc[merged["behaviour"] == b, "value_eur"].astype(float)
                by_behaviour.append([b, f"{len(part):,}", f"{part.median():,.2f}",
                                     _pct(float((part >= q.loc[0.7]).mean()))])  # fmt: skip
    return [
        "## C. Customer value (US-205, UNIT-03)",
        "",
        f"V_c = {a.gross_margin} x kept line prices - {a.logistics_cost_per_return_eur} EUR x "
        f"returns - coupon discounts, over the {a.window_days} days up to the decision time "
        f"(assumptions {a.version}: {a.source}). Origin: main store; nature: synthetic; amounts "
        "in EUR. Mismatches: customers whose kept + returned - discounts differs from the sum of "
        "their order totals by at least one cent.",
        "",
        *table(VALUE_HEADER, rows),
        "",
        f"By behaviour at {_day(DATA_END)} (truth, offline). Value orients benefits; it is never "
        "a sign of abuse and never feeds the risk score (rule 6). The last column is the share "
        "above the store's 70th percentile of value, the Q1/Q3 side of spec 8.6.",
        "",
        *table(["Behaviour", "Customers", "Median V (EUR)", "Above p70"], by_behaviour),
        "",
    ]


# ---- D. Evidence quality (US-209, D-15) ----------------------------------------------------------


def evidence_rows(store: Store, frame: pd.DataFrame, label: str) -> list[str]:
    merged = frame.merge(store.labels, on="customer_id", how="left")
    insufficient = merged["evidence_quality"] == "insufficient"
    abusive = merged["behaviour"].isin(ABUSIVE)

    def has(prefix: str) -> pd.Series:
        return merged["missing_evidence"].map(lambda m: any(x.startswith(prefix) for x in m))

    return [
        label,
        f"{len(merged):,}",
        _pct(float(insufficient.mean())),
        _pct(float(has("observations").mean())),
        _pct(float(has("identifiers").mean())),
        _pct(float(has("return reasons").mean())),
        _pct(float(insufficient[abusive].mean())),
        _pct(float(insufficient[~abusive].mean())),
    ]


def linked_evidence_sentence(frames: Sequence[pd.DataFrame]) -> str:
    """What the evidence frames say about the account graph, read from n_observations_linked
    (review F13: never a fixed sentence)."""
    known = sum(int(f["n_observations_linked"].notna().sum()) for f in frames)
    rows = sum(len(f) for f in frames)
    if known == 0:
        return (
            "n_observations_linked is null in every row: this evidence was computed without the "
            "account graph. The outcome counts each account's own settled lines only, with or "
            "without it (review F14)."
        )
    return (
        f"n_observations_linked is known for {known:,} of {rows:,} rows (the account graph was "
        "given); it is reported and never counted: the outcome counts each account's own settled "
        "lines only (review F14)."
    )


def section_evidence(
    main: Store, ood: Store, sets: dict[datetime, FeatureSet], ood_set: FeatureSet
) -> list[str]:
    t = EVIDENCE_THRESHOLDS
    header = ["Decision time", "Customers", "Insufficient", "Observations", "Identifiers",
              "Reasons", "Insufficient, abusive", "Insufficient, other"]  # fmt: skip
    rows = [evidence_rows(main, fs.evidence, _day(at)) for at, fs in sets.items()]
    rows.append(evidence_rows(ood, ood_set.evidence, f"OOD {_day(DATA_END)}"))

    # D-15 sweep on the train period only (never the OOD store).
    fs = sets[TRAIN_END]
    snap = main.snap.restrict(TRAIN_END)
    lines = line_frame(snap)
    feats = fs.customer_features.merge(main.labels, on="customer_id", how="left")
    abusive = feats["behaviour"].isin(ABUSIVE)
    sweep = []
    for n in OBSERVATION_GRID:
        enough = feats["n_observations"] >= n
        sweep.append(
            [
                str(n),
                _pct(float((~enough).mean())),
                _pct(float((~enough[abusive]).mean())),
                _pct(float((~enough[~abusive]).mean())),
                _f(auc(abusive[enough], feats.loc[enough, "residual_z"])),
            ]
        )
    by_n, aucs = [], []
    for lo in range(1, LAST_COUNT + 1):
        hi = lo if lo < LAST_COUNT else 10**9
        part = feats[(feats["n_observations"] >= lo) & (feats["n_observations"] <= hi)]
        value = auc(part["behaviour"].isin(ABUSIVE), part["residual_z"])
        aucs.append(value)
        by_n.append([f"{lo}" if lo < LAST_COUNT else f"{lo}+", f"{len(part):,}",
                     f"{int(part['behaviour'].isin(ABUSIVE).sum()):,}", _f(value)])  # fmt: skip
    suggested = next(
        (
            n
            for n in range(1, LAST_COUNT + 1)
            if all(a is not None and a >= RULE_AUC for a in aucs[n - 1 :])
        ),
        None,
    )
    agrees = "agrees with" if suggested == t.min_observations else "differs from"
    rule = (
        f"D-15 rule: the smallest settled-line count from which the AUC of z is at least "
        f"{RULE_AUC} at that count and at every larger one. On this store it gives "
        f"{suggested if suggested is not None else 'no count'}, which {agrees} the versioned "
        f"threshold {t.min_observations}. A different value is a decision for a new version, "
        "never an automatic change."
    )
    comp = []
    for c in COMPLETENESS_GRID:
        th = replace(t, min_identifier_completeness=c)
        ev = evidence_quality(snap, lines, th)
        ood_ev = evidence_quality(ood.snap, line_frame(ood.snap), th)
        comp.append([_f(c, 1), _pct(float((ev["identifier_completeness"] < c).mean())),
                     _pct(float((ood_ev["identifier_completeness"] < c).mean()))])  # fmt: skip
    coh = []
    ev = fs.evidence
    judged = ev["n_reasons"] >= t.min_reasons
    for c in COHERENCE_GRID:
        below = judged & (ev["reason_coherence"].astype(float) < c)
        coh.append([_f(c, 1), _pct(float(below.mean()))])
    return [
        "## D. Evidence quality (US-209, D-15)",
        "",
        f"Thresholds {t.version}: at least {t.min_observations} settled lines of the account's "
        f"own, identifier completeness at least "
        f"{t.min_identifier_completeness}, reason coherence at least {t.min_reason_coherence} "
        f"from {t.min_reasons} withdrawal returns on. Origin: main store at the decision times of "
        "the risk split, and the OOD store at the end (separate row); nature: synthetic. "
        + linked_evidence_sentence([fs.evidence for fs in (*sets.values(), ood_set)])
        + " Columns 4-6: share of customers missing that piece of evidence.",
        "",
        *table(header, rows),
        "",
        f"D-15, train period only ({_day(TRAIN_END)}, main store): the observations threshold "
        "against the share of customers it sends to insufficient evidence and the AUC of the "
        "reference z for abusive customers among those it keeps (truth, offline).",
        "",
        *table(["Min observations", "Insufficient", "Abusive", "Other", "AUC of z, kept"], sweep),
        "",
        "AUC of z by number of settled lines (same instant):",
        "",
        *table(["Settled lines", "Customers", "Abusive", "AUC of z"], by_n),
        "",
        rule,
        "",
        "Identifier completeness below each threshold (main at the train end; OOD at the end):",
        "",
        *table(["Threshold", "Main", "OOD"], comp),
        "",
        f"Reason coherence below each threshold, customers with at least {t.min_reasons} "
        "withdrawal returns (main, train end):",
        "",
        *table(["Threshold", "Customers below"], coh),
        "",
    ]


# ---- E. Feature signals ------------------------------------------------------------------------

SIGNALS: Final = (
    "n_observations",
    "return_rate",
    "return_rate_to_date",
    "changed_mind_share",
    "size_share",
    "median_days_to_return",
    "tenure_days",
    "n_welcome_coupons_used",
    "weekend_order_share",
    "mean_line_price_eur",
    "residual_z",
)


def section_signals(main: Store, sets: dict[datetime, FeatureSet]) -> list[str]:
    feats = sets[TRAIN_END].customer_features.merge(main.labels, on="customer_id", how="left")
    rows = []
    for column in SIGNALS:
        medians = feats.groupby("behaviour")[column].median()
        rows.append([column, *[_f(float(medians.get(b, np.nan)), 2) for b in BEHAVIOURS]])
    return [
        "## E. Customer features by behaviour",
        "",
        f"Medians at the train end {_day(TRAIN_END)} (main store, truth by behaviour, offline; "
        "nature: synthetic). Inputs for the risk model (US-204), not results.",
        "",
        *table(["Feature", *BEHAVIOURS], rows),
        "",
    ]


# ---- Report -------------------------------------------------------------------------------------


def report(data: Path, log: Callable[[str], None]) -> str:
    main = load_store("main", data, data)
    ood = load_store("out-of-distribution", data / "ood", data / "truth" / "ood")
    log("stores loaded")
    sets = {at: features_at(main.snap.restrict(at)) for at in (TRAIN_END, *EVAL_INSTANTS)}
    log("features at the risk-split instants")
    ood_set = features_at(ood.snap, source=main.snap)
    log("OOD features")
    lines = [
        "# Customer features, residual, value and evidence: measured results",
        "",
        "Stories US-205, US-206 and US-209 (tests UNIT-02, UNIT-03, MOD-06, MOD-08, MOD-09, "
        "MOD-12; decision D-15). Design and definitions: docs/customer-features.md.",
        "",
        "Rewritten by `python -m ml.reference.report --data data --results "
        "docs/results/customer-quantities.md` from the data alone (no wall-clock value), so the "
        "same store gives the same file. Every number below is synthetic: measured on the "
        "Navigli Fashion store, whose behaviours and rules are design hypotheses calibrated on "
        "real ASOS aggregates. None is a measure on real customers.",
        "",
        f"- Main store origin: {main.provenance.chain}; period {main.provenance.period_start} "
        f"to {main.provenance.period_end}; nature {main.provenance.nature}.",
        f"- OOD store origin: {ood.provenance.chain}; nature {ood.provenance.nature}.",
        f"- Code: {FEATURES_VERSION}, reference model {MODEL_VERSION}, value "
        f"{VALUE_ASSUMPTIONS.version}, evidence {EVIDENCE_THRESHOLDS.version}.",
        "",
        *section_calibration(main, ood, log),
        *section_residual(main, ood, sets, ood_set, log),
        *section_value(main, sets),
        *section_evidence(main, ood, sets, ood_set),
        *section_signals(main, sets),
    ]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ml.reference.report", description=__doc__)
    p.add_argument("--data", type=Path, default=Path("data"), help="root of store/ and truth/")
    p.add_argument("--results", type=Path, default=Path("docs/results/customer-quantities.md"))
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
