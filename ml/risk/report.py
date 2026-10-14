"""docs/results/us-204-risk.md, rendered from the metrics document of ml.risk.run (no wall-clock
value, so the same data gives the same file). Every number carries its origin, period and nature
(rule 11): all are synthetic, measured on the generated Navigli Fashion store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from ml.risk.fairness import GAP_LIMIT_PP

COMMAND: Final = "python -m ml.risk --data data --out artifacts --scores data"
MOD05_ORDER: Final = ("customers_test", "requests_test", "customers_ood", "requests_ood")
GPU_NOTE: Final = "not run: phase 6 (GPU), EmbeddingSource hook in ml/risk/features.py"


def _not_run(r: dict[str, Any]) -> str:
    """Why a step of the ablation did not run (older documents: the phase 6 note)."""
    status = str(r.get("status", ""))
    return status if status.startswith("not run") else GPU_NOTE


def _a4(doc: dict[str, Any]) -> dict[str, Any] | None:
    return next((r for r in doc["ablation"] if r["set"] == "A4"), None)


def _f(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:,.{digits}f}"


def _pct(x: float | None, digits: int = 2) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def _n(x: int | None) -> str:
    return "n/a" if x is None else f"{x:,}"


def _pp(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.2f}"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


METRIC_HEADER: Final = (
    "AUPRC",
    "P@50",
    "P@100",
    "Risk threshold (validation)",
    "Flagged",
    "Precision",
    "Recall",
    "FPR",
    "FPR high-value (FP / high-value non-abusive customers)",
    "ECE",
    "Brier",
)
# The request unit counts requests (review F12): a customer with several requests counts once
# per request, and "high-value" means a request of a non-abusive customer at or above the cut.
REQUEST_METRIC_HEADER: Final = (
    *METRIC_HEADER[:8],
    "FPR high-value (FP requests / requests of high-value non-abusive customers)",
    *METRIC_HEADER[9:],
)


def _metric_cells(m: dict[str, Any], threshold: float | None) -> list[str]:
    at = m["precision_at"]
    return [
        _f(m["auprc"]),
        _f(at.get("50")),
        _f(at.get("100")),
        "not reached" if threshold is None else _f(threshold),
        _n(m["flagged"]),
        _f(m["precision"]),
        _f(m["recall"]),
        _pct(m["fpr"]),
        f"{_pct(m['fpr_high_value'])} ({m['high_value_false_positives']} / "
        f"{_n(m['high_value_negatives'])})",
        _f(m["ece"], 4),
        _f(m["brier"], 4),
    ]


def _ablation_rows(doc: dict[str, Any], population: str) -> list[list[str]]:
    rows = []
    blank = ["-"] * (len(METRIC_HEADER) - 1)
    for r in doc["ablation"]:
        name = f"{r['set']} ({r['purpose']})"
        if r["status"] != "run" or population not in r:
            note = _not_run(r) if r["status"] != "run" else "not run: no embeddings for this store"
            rows.append([name, str(len(r["features"])), note, *blank])
            continue
        rows.append([name, str(len(r["features"])), *_metric_cells(r[population], r["threshold"])])
    return rows


# Every metric the tables report, in table order, and whether a higher value is better (review
# F9: a regression is any of them getting worse, rule 13).
COMPARED: Final[tuple[tuple[str, str, bool], ...]] = (
    ("AUPRC", "auprc", True),
    ("P@50", "precision_at.50", True),
    ("P@100", "precision_at.100", True),
    ("precision", "precision", True),
    ("recall", "recall", True),
    ("FPR", "fpr", False),
    ("FPR high-value", "fpr_high_value", False),
    ("ECE", "ece", False),
    ("Brier", "brier", False),
)
DELTA_HEADER: Final = (
    "Step",
    "AUPRC change",
    "Precision change (points)",
    "Recall change (points)",
    "FPR change (points)",
    "FPR high-value change (points)",
    "ECE change",
    "Worse (every reported metric compared)",
)


def _get(m: dict[str, Any], key: str) -> float | None:
    if "." in key:
        outer, inner = key.split(".", 1)
        value = m[outer].get(inner)
    else:
        value = m[key]
    return None if value is None else float(value)


def _delta(a: dict[str, Any], b: dict[str, Any], key: str) -> float | None:
    x, y = _get(a, key), _get(b, key)
    return None if x is None or y is None else x - y


def regressions(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The reported metrics that get worse from `before` to `after`; a metric missing on either
    side (no row flagged: no precision) is not compared."""
    worse = []
    for label, key, higher_is_better in COMPARED:
        d = _delta(after, before, key)
        if d is not None and (d < 0 if higher_is_better else d > 0):
            worse.append(label)
    return worse


def _deltas(doc: dict[str, Any], population: str) -> list[list[str]]:
    run = [r for r in doc["ablation"] if r["status"] == "run" and population in r]
    rows = []
    for before, after in zip(run, run[1:], strict=False):
        b, a = before[population], after[population]
        worse = regressions(b, a)

        def points(key: str, a: dict[str, Any] = a, b: dict[str, Any] = b) -> str:
            d = _delta(a, b, key)
            return "n/a" if d is None else _pp(100 * d)

        d_auprc, d_ece = _delta(a, b, "auprc"), _delta(a, b, "ece")
        rows.append(
            [
                f"{before['set']} -> {after['set']}",
                "n/a" if d_auprc is None else f"{d_auprc:+.4f}",
                points("precision"),
                points("recall"),
                points("fpr"),
                points("fpr_high_value"),
                "n/a" if d_ece is None else f"{d_ece:+.4f}",
                ", ".join(worse) if worse else "none",
            ]
        )
    return rows


def _origin_line(doc: dict[str, Any], which: str, period: str, population: str) -> str:
    store = "main store" if which == "main" else "out-of-distribution store (US-105)"
    return (
        f"Origin: {store} (see the header). Period: {period}. Population: {population}. "
        "Nature: synthetic."
    )


def render(doc: dict[str, Any]) -> str:
    main, ood = doc["data"]["main"], doc["data"]["ood"]
    train_end, eval_end = doc["train_end"][:10], doc["eval_end"][:10]
    rows = doc["customer_rows"]
    test_period = f"features at {doc['eval_end']} (the store up to {eval_end}), labels static"
    train_period = f"features at {doc['train_end']} (the store before {train_end})"
    lines: list[str] = [
        "# US-204: abuse risk per customer and per return request",
        "",
        f"<!-- Generated by `{COMMAND}` from the data (no wall-clock value). Do not edit by hand: "
        "rerun the command. -->",
        "",
        "Story US-204 (spec 8.4 and 8.6; tests MOD-04 to MOD-08, MOD-12, FAIR-01 to FAIR-03). "
        "Method and limits: [docs/risk-model.md](../risk-model.md).",
        "",
        "**Every number below is synthetic**: measured on the generated Navigli Fashion store, "
        "whose behaviours are design hypotheses planted by the generator and calibrated on real "
        "ASOS aggregates. None is a measure on real customers or a promise for a real shop. The "
        "labels come from the generator's ground truth (truth/labels, read by this offline job "
        "only, rule 12).",
        "",
        f"- Main store, features: {_lineage(main)}; period {main['period']}; nature "
        f"{main['nature']}.",
    ]
    if ood is not None:
        lines.append(
            f"- Out-of-distribution store, features: {_lineage(ood)}; period {ood['period']}; "
            f"nature {ood['nature']}. Never used to train, calibrate or choose anything (MOD-12)."
        )
    lines += [
        f"- Train end {doc['train_end']} (the first nine months); evaluation at {doc['eval_end']} "
        "(the last three months added). Labels: wardrober and promo_ring 1; normal, size and "
        "shared_benign 0.",
        "",
        f"Reproduce from the repository root: `{COMMAND} --results "
        "docs/results/us-204-risk.md`. Outputs: the models in `artifacts/risk/models/` (XGBoost "
        "JSON, calibrators, rules, manifest), every number in `artifacts/eval/risk/metrics.json` "
        "(offline outputs, never mounted by a service), the "
        "batch scores in `data/scores/scores.parquet` and `data/scores/request_scores.parquet` "
        "(contract scores/*, with provenance).",
        "",
    ]
    lines += _summary(doc)
    lines += [
        "## Ablation A0 to A4, customer unit (MOD-04)",
        "",
        _origin_line(
            doc,
            "main",
            test_period,
            f"held-out test groups, every customer known at the evaluation time "
            f"({_n(rows['test']['rows'])} customers, {_n(rows['test']['positives'])} abusive)",
        ),
        "",
        "Same split and same rows for every model. AUPRC and precision at k rank the score before "
        "calibration (isotonic calibration is monotone but merges scores into ties); the risk "
        "threshold, precision, recall, false-positive rates and ECE use the calibrated risk. The "
        "threshold is the lowest calibrated risk with precision >= 0.8 on the validation groups "
        "(spec 8.6). FPR: non-abusive customers flagged, over non-abusive customers. FPR "
        "high-value: the same among the non-abusive customers in the top 30% by customer value "
        f"(value >= {_f(rows['high_value_cut_eur']['test'], 2)} EUR at the evaluation time); value "
        "only chooses who is counted, it is never a feature (rule 6).",
        "",
        *table(("Set", "Features", *METRIC_HEADER), _ablation_rows(doc, "test")),
        "",
        "Change from each step to the next, held-out test customers. A regression is any reported "
        "metric that gets worse (AUPRC, P@50, P@100, precision, recall, FPR, FPR high-value, ECE "
        "or Brier), listed as measured (rule 13):",
        "",
        *table(DELTA_HEADER, _deltas(doc, "test")),
        "",
        *_behaviour_section(doc, "test", "held-out test customers"),
    ]
    if ood is not None:
        lines += [
            "### Out-of-distribution scenario, a separate row (MOD-12)",
            "",
            _origin_line(
                doc,
                "ood",
                f"features at {doc['eval_end']}",
                f"every customer of the OOD store ({_n(rows['ood']['rows'])} customers, "
                f"{_n(rows['ood']['positives'])} abusive); models, calibrations and thresholds "
                "from the main store",
            ),
            "",
            f"High-value cut: {_f(rows['high_value_cut_eur']['ood'], 2)} EUR. The OOD rings are "
            "smaller, slower and share fewer identifiers (docs/synthetic-store.md); the account "
            "graph finds few of them (docs/results/account-graph.md). **The wardrober rules are "
            "the same in both stores** (pipeline/generator/config.py: the OOD scenario changes "
            "rings, families and missing identifiers only), so the pooled row mixes "
            "in-distribution wardrobers with shifted rings: read the rows by behaviour below.",
            "",
            *table(("Set", "Features", *METRIC_HEADER), _ablation_rows(doc, "ood")),
            "",
            *table(DELTA_HEADER, _deltas(doc, "ood")),
            "",
            *_behaviour_section(doc, "ood", "every customer of the OOD store"),
        ]
    lines += _features_section(doc)
    lines += _request_section(doc, test_period)
    lines += _thresholds_section(doc)
    lines += _fair02_section(doc)
    lines += _fair03_section(doc)
    lines += _golden_section(doc)
    lines += _over_time_section(doc)
    lines += _data_section(doc, train_period)
    return "\n".join(lines).rstrip() + "\n"


def _lineage(p: dict[str, Any]) -> str:
    """The full chain of origins of a provenance entry (its origin for an older document)."""
    return str(p.get("lineage") or p["origin"])


BEHAVIOUR_HEADER: Final = (
    "Model",
    "Abusive behaviour",
    "Rows",
    "Of the behaviour",
    "AUPRC",
    "Precision",
    "Recall",
)


def _behaviour_rows(entries: list[tuple[str, dict[str, Any]]], key: str) -> list[list[str]]:
    rows = []
    for name, entry in entries:
        for behaviour, m in entry.get(f"{key}_by_behaviour", {}).items():
            rows.append(
                [
                    name,
                    behaviour,
                    _n(m["n"]),
                    _n(m["positives"]),
                    _f(m["auprc"]),
                    _f(m["precision"]),
                    _f(m["recall"]),
                ]
            )
    return rows


def _behaviour_section(doc: dict[str, Any], key: str, population: str) -> list[str]:
    """The rows of one population split by abusive behaviour (review F4)."""
    entries = [(r["set"], r) for r in doc["ablation"] if r["status"] == "run"]
    rows = _behaviour_rows(entries, key)
    if not rows:
        return []
    return [
        f"The same rows by abusive behaviour ({population}): each behaviour against every "
        "non-abusive row, with the same models and thresholds. AUPRC and precision depend on "
        "the share of the behaviour among the rows; recall does not.",
        "",
        *table(BEHAVIOUR_HEADER, rows),
        "",
    ]


def _summary(doc: dict[str, Any]) -> list[str]:
    mod05 = doc["mod05"]
    ok05 = all(c["rows"] == c["with_three"] and not c["forbidden"] for c in mod05.values())
    customer = next(r for r in doc["ablation"] if r["set"] == "A3")
    request = doc["request_models"][0]
    eces = [
        ("customer A3, test", customer["test"]["ece"]),
        ("request, test", request["test"]["ece"]),
    ]
    if "ood" in customer:
        eces += [
            ("customer A3, OOD", customer["ood"]["ece"]),
            ("request, OOD", request["ood"]["ece"]),
        ]
    ok06 = all(e <= 0.05 for _, e in eces)
    shared = doc["split"]["mod07_shared_keys"]
    ok07 = not any(shared.values())
    ctx = doc["request_rows"]["context_main"]
    ok08 = bool(ctx["snapshot_strictly_before_request"])
    f02 = doc["fair02"]
    f03 = doc["fair03"]
    checks = doc["golden"]["checks"]
    a4 = _a4(doc)
    a4_text = (
        "A0 to A4 reported on the same rows (A4: GraphSAGE embeddings, US-202)"
        if a4 is not None and a4["status"] == "run"
        else "A0 to A3 reported on the same rows; A4 " + (_not_run(a4) if a4 else GPU_NOTE)
    )
    rows = [
        [
            "MOD-04",
            "A0 to A4 on the same split, regressions included",
            a4_text,
            "reported",
        ],
        [
            "MOD-05",
            "3 factors for 100% of the cases, none forbidden",
            "; ".join(
                f"{k.replace('_', ' ')}: {_n(mod05[k]['with_three'])} of {_n(mod05[k]['rows'])}"
                for k in MOD05_ORDER
                if k in mod05
            ),
            "pass" if ok05 else "fail",
        ],
        [
            "MOD-06",
            "ECE <= 0.05",
            "; ".join(f"{k} {_f(v, 4)}" for k, v in eces),
            "pass" if ok06 else "fail",
        ],
        [
            "MOD-07",
            "no cluster in both train and test",
            ", ".join(f"{k}: {shared[k]}" for k in ("cluster", "ring", "family")) + " on two sides",
            "pass" if ok07 else "fail",
        ],
        [
            "MOD-08",
            "no feature computed with events after the decision",
            "customer rows computed at the decision time; every request takes the last "
            f"snapshot strictly before it (median {_f(ctx['median_days_since_snapshot'], 1)} "
            f"days before, at most {_f(ctx['max_days_since_snapshot'], 1)})",
            "pass" if ok08 else "fail",
        ],
        ["MOD-12", "OOD on a separate row", "rows of their own below", "reported"],
        [
            "FAIR-01",
            "no forbidden feature in any model",
            "checked on every feature list before training (contracts.parquet)",
            "pass",
        ],
        [
            "FAIR-02",
            f"gap <= {GAP_LIMIT_PP:.0f} points between segments (per dimension, the largest "
            "segment rate minus the smallest, flagged and in Q3/Q4)",
            "; ".join(
                f"{d['dimension']} {d['gap_pp']:.2f} / {d['gap_restrictive_pp']:.2f}"
                for d in f02["dimensions"]
            )
            + f"; above {GAP_LIMIT_PP:.0f} points: "
            + (", ".join(f02["failures"]) if f02["failures"] else "none"),
            "pass" if f02["passed"] else "fail",
        ],
        [
            "FAIR-03",
            "no shared_benign in Q3/Q4 because of the cluster alone",
            "; ".join(
                f"{f['population']}: {f['because_of_cluster']} of {f['families']} "
                f"({f['restrictive']} in Q3/Q4 for any reason)"
                for f in f03
            ),
            "pass" if all(f["passed"] for f in f03) else "fail",
        ],
        *_fair03_over_time_row(doc),
        [
            "Golden cases",
            "demo cases, never a measure of accuracy (US-906)",
            "; ".join(f"{k}: {'yes' if v else 'no'}" for k, v in checks.items()),
            "as expected" if all(checks.values()) else "not as expected",
        ],
    ]
    return [
        "## Tests of spec 12",
        "",
        "Origin: main store at the evaluation time and the OOD store (separate rows). Nature: "
        "synthetic.",
        "",
        *table(("Test", "Target", "Measured", "Result"), rows),
        "",
    ]


def _fair03_over_time_row(doc: dict[str, Any]) -> list[list[str]]:
    """FAIR-03 at every month end (review F21): the instants where it fails, marked in-sample
    before the train end."""
    history = doc.get("over_time") or []
    if not history:
        return []
    bad = [
        f"{h['as_of'][:10]}{' (in-sample backtest)' if h['in_sample'] else ''}, "
        f"{f['population']}: {f['because_of_cluster']} of {f['families']}"
        for h in history
        for f in h["fair03"]
        if not f["passed"]
    ]
    return [
        [
            "FAIR-03, every month end",
            "the same check at each month-end snapshot (last section)",
            "; ".join(bad) if bad else f"0 at each of {len(history)} month ends",
            "fail" if bad else "pass",
        ]
    ]


def _features_section(doc: dict[str, Any]) -> list[str]:
    lines = ["### Feature sets and largest contributions", ""]
    for r in doc["ablation"]:
        if r["status"] != "run":
            lines.append(
                f"- **{r['set']}**: the {len(r['features'])} features of A3 plus GraphSAGE "
                f"embedding columns; {_not_run(r)}."
            )
            continue
        lines.append(
            f"- **{r['set']}** ({r['learner']}, {len(r['features'])} features): "
            + ", ".join(f"`{f}`" for f in r["features"])
            + "."
        )
        if r.get("embeddings"):
            e = r["embeddings"]
            cover = r.get("embedding_coverage", {})
            lines.append(
                f"  Embeddings: `features/customer_embeddings`, encoder "
                f"{', '.join(e['encoder_versions'])}; "
                f"origin {e['origin']}; period {e['period']}; nature {e['nature']}. Customer "
                "rows without an embedding: "
                + "; ".join(
                    f"{k} {v['without_embedding']} of {_n(v['rows'])}" for k, v in cover.items()
                )
                + ". Trained by link prediction on the customer-identifier graph, no label "
                "(spec 8.3); method and timings in docs/results/us-202-graphsage.md."
            )
        if r.get("rules"):
            lines.append(
                "  Rules fitted on the training rows (quantile with the best single-rule F1): "
                + "; ".join(f"`{x}`" for x in r["rules"])
                + "."
            )
        if r.get("gain_shares"):
            lines.append(
                f"  Rounds kept {r['rounds']}. Largest shares of total gain (contribution to the "
                "score, not a cause): "
                + ", ".join(f"`{n}` {v:.2f}" for n, v in r["gain_shares"])
                + "."
            )
    lines += [
        "- An account with no linked account has its cluster features computed on itself "
        "(pipeline.graph), so for most customers `cluster_return_rate` is close to "
        "`return_rate_to_date`: gain moves between correlated features, and the gain shares do "
        "not measure what the graph adds. The ablation rows do.",
        "- No feature set holds a forbidden feature (gender, country, age, names, hashes, raw "
        "identifiers: `contracts.parquet.forbidden_features`, FAIR-01) or a customer value column "
        "(rule 6); both are checked when the module loads and before every training.",
        "",
    ]
    return lines


def _request_section(doc: dict[str, Any], test_period: str) -> list[str]:
    req = doc["request_rows"]
    rows = []
    for r in doc["request_models"]:
        rows.append([r["model"], *_metric_cells(r["test"], r["threshold"])])
    lines = [
        "## Request unit (spec 8.4: the score at the moment of the request)",
        "",
        _origin_line(
            doc,
            "main",
            f"withdrawal-path requests that arrived from {doc['train_end'][:10]} to the "
            f"evaluation time; {test_period}",
            f"requests of the held-out test groups ({_n(req['test']['rows'])} requests, "
            f"{_n(req['test']['positives'])} from abusive customers)",
        ),
        "",
        "Each request is scored as known when it arrived: days since delivery, reason, price and "
        "the item's return rate among other customers at that instant, plus the customer's row "
        "from the last nightly snapshot computed strictly before the request (here the monthly "
        "snapshots of features/customer_features and the clusters built at the same instants). "
        f"{_n(req['context_main']['without_snapshot'])} of {_n(req['context_main']['requests'])} "
        "requests of the main store came before any snapshot of their account and have no "
        "customer features; the batch scoring gives such a request insufficient evidence (no "
        "quadrant, standard terms). Defect and non-conformity requests follow the guarantee path "
        "and are never scored (rule 4). A request inherits its customer's label. The rates count "
        "requests: a customer with several requests counts once per request, and FPR "
        "high-value is over the requests of non-abusive customers whose value is at or above "
        "the customer cut above.",
        "",
        *table(("Model", *REQUEST_METRIC_HEADER), rows),
        "",
        *_request_behaviour(doc, "test", "held-out test groups"),
    ]
    if req["ood"] is not None:
        ood_rows = [
            [r["model"], *_metric_cells(r["ood"], r["threshold"])] for r in doc["request_models"]
        ]
        lines += [
            _origin_line(
                doc,
                "ood",
                f"requests that arrived from {doc['train_end'][:10]} to the evaluation time",
                f"every such request of the OOD store ({_n(req['ood']['rows'])} requests, "
                f"{_n(req['ood']['positives'])} from abusive customers); thresholds from the main "
                "store (MOD-12, separate row)",
            ),
            "",
            *table(("Model", *REQUEST_METRIC_HEADER), ood_rows),
            "",
            *_request_behaviour(doc, "ood", "OOD store"),
        ]
    lines += [
        "Request model: largest shares of total gain (contribution to the score, not a cause): "
        + ", ".join(f"`{n}` {v:.2f}" for n, v in doc["request_gain_shares"])
        + ".",
        "",
    ]
    return lines


def _request_behaviour(doc: dict[str, Any], key: str, population: str) -> list[str]:
    rows = _behaviour_rows([(r["model"], r) for r in doc["request_models"]], key)
    if not rows:
        return []
    return [
        f"The same requests by abusive behaviour of the customer ({population}):",
        "",
        *table(BEHAVIOUR_HEADER, rows),
        "",
    ]


def _thresholds_section(doc: dict[str, Any]) -> list[str]:
    s = doc["scoring"]
    th = s["thresholds"]
    quadrants = s["quadrants"]
    rows = [[q, _n(quadrants.get(q, 0))] for q in ("Q1", "Q2", "Q3", "Q4", "none")]
    return [
        "## Thresholds and quadrants at the evaluation time (spec 8.6)",
        "",
        f"Origin: batch scoring (`python -m ml.scoring`) of every customer of the main store at "
        f"{s['as_of']}. Period: {s['period'][0]} to {s['period'][1]}. Nature: {s['nature']}.",
        "",
        f"- Customer risk threshold: {_f(th['customer_risk'], 4)}; request risk threshold: "
        f"{_f(th['request_risk'], 4)} (lowest calibrated risk with precision >= 0.8 on the "
        "validation groups).",
        f"- Value threshold: {th['value_eur']} EUR, the 70th percentile of the customer value at "
        "the evaluation time. Value orients the quadrant, never the risk (rule 6).",
        f"- Customers with insufficient evidence get no quadrant (standard terms, US-307): "
        f"{_n(s['insufficient_evidence'])} of {_n(s['customers'])}.",
        f"- Open returns at the evaluation time: {_n(s['open_returns'])}, of which "
        f"{_n(s['open_returns_scored'])} scored and {_n(s['open_returns_guarantee_path'])} on the "
        "guarantee path (not scored); "
        f"{_n(s['open_returns_without_snapshot'])} scored without an earlier snapshot of the "
        "account.",
        "",
        *table(("Quadrant", "Customers"), rows),
        "",
        "Q3 and Q4 are proposals: a human approves every restrictive action (rule 5).",
        "",
    ]


def _fair02_section(doc: dict[str, Any]) -> list[str]:
    f = doc["fair02"]
    limit = f.get("gap_limit_pp", GAP_LIMIT_PP)
    dims = [
        [
            d["dimension"],
            _n(d["segments"]),
            _n(d["smallest_segment"]),
            f"{d['gap_pp']:.2f} ({d['highest']} vs {d['lowest']})",
            f"{d['gap_restrictive_pp']:.2f} ({d['highest_restrictive']} vs "
            f"{d['lowest_restrictive']})",
            "pass" if d["passed"] else "**fail**",
        ]
        for d in f["dimensions"]
    ]
    rows = [
        [
            r["dimension"],
            r["segment"],
            _n(r["non_abusers"]),
            f"{_n(r['flagged'])} ({_pct(r['fpr'])})",
            f"{_n(r['restrictive'])} ({_pct(r['fpr_restrictive'])})",
        ]
        for r in f["rows"]
    ]
    cuts = f["price_cuts"]
    return [
        "## FAIR-02: false positives by segment",
        "",
        f"Origin: batch scores at {doc['eval_end']} of the {f['population']} (main store). "
        "Period: the store up to the evaluation time. Nature: synthetic.",
        "",
        "A false positive is a non-abusive customer flagged by the model (risk >= threshold) or "
        "placed in Q3/Q4 (high risk with sufficient evidence: what a customer would see after "
        "approval). Spec 12 sets the gap between segments: per dimension, the largest segment "
        f"rate minus the smallest segment rate, in percentage points; above {limit:.0f} points "
        "FAIR-02 fails, for either measure. For reference, all "
        f"{_n(f['non_abusers'])} non-abusive customers: {_pct(f['fpr'])} flagged, "
        f"{_pct(f['fpr_restrictive'])} in Q3/Q4. Segments with few customers are noisy: every "
        "rate is printed with the size of its segment. Price bands are the thirds of the mean "
        f"line price (cuts {cuts[0]:.2f} and {cuts[1]:.2f} EUR).",
        "",
        *table(
            (
                "Dimension",
                "Segments",
                "Smallest segment (non-abusive customers)",
                "Gap flagged (points, highest vs lowest)",
                "Gap in Q3/Q4 (points, highest vs lowest)",
                "Result",
            ),
            dims,
        ),
        "",
        *table(
            ("Dimension", "Segment", "Non-abusive customers", "Flagged (rate)", "In Q3/Q4 (rate)"),
            rows,
        ),
        "",
    ]


def _fair03_section(doc: dict[str, Any]) -> list[str]:
    rows = [
        [
            f["population"],
            _n(f["families"]),
            _n(f["restrictive"]),
            _n(f["because_of_cluster"]),
            "pass" if f["passed"] else "fail",
        ]
        for f in doc["fair03"]
    ]
    return [
        "## FAIR-03: shared-benign accounts and the cluster",
        "",
        f"Origin: batch scores at {doc['eval_end']} (main store), with a counterfactual where "
        "each account's cluster features are computed on the account alone. Nature: synthetic. "
        "The all-customers row includes training groups, which the model saw with label 0. "
        "Counts only: a customer named here would carry its ground-truth behaviour (rule 12). The "
        "same check at every month end is in the last section.",
        "",
        *table(
            (
                "Population",
                "shared_benign customers",
                "In Q3/Q4",
                "In Q3/Q4 because of the cluster alone",
                "Result",
            ),
            rows,
        ),
        "",
    ]


def _over_time_section(doc: dict[str, Any]) -> list[str]:
    history = doc.get("over_time") or []
    if not history:
        return []
    rows = []
    for h in history:
        f03 = {f["population"]: f for f in h["fair03"]}
        rows.append(
            [
                h["as_of"][:10],
                "in-sample backtest" if h["in_sample"] else "after the train end",
                _n(h["customers"]),
                _n(h["quadrants"].get("Q3", 0) + h["quadrants"].get("Q4", 0)),
                h["golden_quadrants"].get("C-GIULIA") or "none",
                f"{h['marco_high_risk']} of {h['marco_accounts']}",
                ", ".join(h["rossi_restrictive"]) or "none",
                *(
                    f"{f03[p]['because_of_cluster']} of {f03[p]['families']} "
                    f"({f03[p]['restrictive']} in Q3/Q4)"
                    for p in ("all customers", "held-out test customers")
                ),
            ]
        )
    passed = all(f["passed"] for h in history for f in h["fair03"])
    return [
        "## Golden cases and FAIR-03 at every month end",
        "",
        "Origin: the product's customer model (A3) scoring every customer of the main store at "
        "each month-end snapshot of features/customer_features, with the clusters and the "
        "evidence of that instant (review F21). Nature: synthetic. Instants before the train end "
        "are in-sample backtests: the model was fitted on the train groups' labels and the batch "
        "scoring refuses them outside an offline check (rule 7); they show whether a golden case "
        "or a family would ever have been restricted, not a held-out accuracy.",
        "",
        *table(
            (
                "Month end",
                "Instant",
                "Customers",
                "In Q3/Q4",
                "C-GIULIA",
                "R-MARCO high risk",
                "F-ROSSI accounts in Q3/Q4",
                "FAIR-03, all customers (because of the cluster alone)",
                "FAIR-03, held-out test customers",
            ),
            rows,
        ),
        "",
        f"FAIR-03 at every month end: {'pass' if passed else 'fail'}.",
        "",
    ]


def _golden_section(doc: dict[str, Any]) -> list[str]:
    g = doc["golden"]
    rows = []
    for c in g["customers"]:
        opened = "; ".join(
            f"{o['return_id']} {o['path']}"
            + (
                ""
                if o["risk"] is None
                else f", risk {_f(o['risk'], 4)}, {o['quadrant'] or 'no quadrant'}"
            )
            for o in c["open_returns"]
        )
        rows.append(
            [
                c["customer_id"],
                c.get("side") or "n/a",
                _f(c["risk"], 4),
                "yes" if c["high_risk"] else "no",
                c["evidence_quality"],
                c["value_eur"],
                c["quadrant"] or "none (insufficient evidence)",
                ", ".join(f"`{x}`" for x in c["factors"]),
                opened or "none",
            ]
        )
    return [
        "## Golden cases (demo cases, never a measure of accuracy)",
        "",
        f"Origin: batch scores at {doc['eval_end']} (main store). Nature: synthetic. The golden "
        "accounts are scripted by the generator, and the split side says whether the models saw "
        "their labels: a train or validation row is not a held-out result (US-906).",
        "",
        *table(
            (
                "Customer",
                "Split side",
                "Risk",
                "High risk",
                "Evidence",
                "Value (EUR)",
                "Quadrant",
                "Top 3 factors",
                "Open returns (request score)",
            ),
            rows,
        ),
        "",
        *table(("Check", "Result"), [[k, "yes" if v else "no"] for k, v in g["checks"].items()]),
        "",
    ]


def _data_section(doc: dict[str, Any], train_period: str) -> list[str]:
    split = doc["split"]
    rows = doc["customer_rows"]
    req = doc["request_rows"]
    counts = split["customers"]
    sides = ("train", "validation", "test")
    strata = ("ring", "wardrober", "family", "other")
    srows = [[s, *(_n(counts.get(f"{s}/{t}", 0)) for t in strata)] for s in sides]
    cfg = split["config"]
    clusters = doc["data"]["clusters"]
    return [
        "## Data, split and method",
        "",
        "Origin: main store. Nature: synthetic.",
        "",
        f"Split (MOD-07): groups of customers joined by a graph cluster at the evaluation time, a "
        f"ground-truth ring or a ground-truth family; groups stratified by their most specific "
        f"behaviour (ring, wardrober, family, other), shuffled with seed {cfg['seed']}; "
        f"{cfg['test_share']:.0%} of each stratum to test, {cfg['validation_share']:.0%} of the "
        "rest to validation. Customers known at the evaluation time, by side and group stratum:",
        "",
        *table(("Side", *strata), srows),
        "",
        *table(
            ("Rows", "Customers", "Abusive", "Requests", "From abusive customers"),
            [
                [
                    f"train ({train_period})",
                    _n(rows["train"]["rows"]),
                    _n(rows["train"]["positives"]),
                    _n(req["train"]["rows"]),
                    _n(req["train"]["positives"]),
                ],
                [
                    "validation (same instant, other groups)",
                    _n(rows["validation"]["rows"]),
                    _n(rows["validation"]["positives"]),
                    _n(req["validation"]["rows"]),
                    _n(req["validation"]["positives"]),
                ],
                [
                    f"test (features at {doc['eval_end']})",
                    _n(rows["test"]["rows"]),
                    _n(rows["test"]["positives"]),
                    _n(req["test"]["rows"]),
                    _n(req["test"]["positives"]),
                ],
            ],
        ),
        "",
        "Clusters per decision time: " + "; ".join(f"{k}: {v}" for k, v in clusters.items()) + ".",
        "",
        "- XGBoost on CPU (xgboost-cpu 3.2.0, hist, one thread), depth 4, learning rate 0.1, "
        "gamma 1, lambda 2, early stopping after 5 rounds on the validation log loss, seed 42; "
        "isotonic calibration on validation. The same code runs on the MI300X with "
        "device=cuda in phase 6.",
        "- Contributions: XGBoost's native TreeSHAP (pred_contribs), top 3 by absolute value "
        "among the features with a known value, shown as fixed phrases (Italian and English) "
        "that say they are contributions to the score, not causes.",
        "- Circularity: the generator plants the behaviours the features look for, so the main "
        "store measures consistency with the generator. The OOD store changes the ring, family "
        "and identifier rules but not the wardrober rules: its ring rows (by behaviour, above) "
        "are the stress test; its wardrober rows are in distribution.",
        "",
    ]
