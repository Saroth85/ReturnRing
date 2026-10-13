"""The Markdown report of US-201, rendered from the metrics document of run.run (never by hand).

Every table states origin, nature, split, period and protocol of its numbers (rule 11, NFR-16);
protocol A rows carry the paper-protocol label, so they cannot be read as product performance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ml.propensity.features import POPULATIONS

POPULATION_TITLES = {
    "all": "Full test set",
    "fully_featured": "Fully featured test subset",
    "decision_time": "Decision-time test population",
    "decision_time_fully_featured": "Decision-time test population, fully featured",
}


def _f(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _n(value: int | None) -> str:
    return "n/a" if value is None else f"{value:,}"


def _ok(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _header(*cells: str) -> list[str]:
    return [_row(*cells), _row(*("---" for _ in cells))]


def _period(doc: dict[str, Any], split: str) -> str:
    return str(doc["labels"][split]["period"])


def _source(doc: dict[str, Any], split: str = "test") -> str:
    p = doc["provenance"]
    return (
        f"Origin: {p['origin']}. Nature: {p['nature']}. {split.capitalize()} split, "
        f"{_period(doc, split)}."
    )


def _rows(doc: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(title, protocol label, result) of every model and the baseline, in report order."""
    models = doc["models"]
    order = [models["A"], models["B"], doc["baseline"], models["B_all_events"]]
    return [(m["title"], m["protocol_label"], m) for m in order]


def _go_no_go(doc: dict[str, Any]) -> list[str]:
    g = doc["go_no_go"]
    b = doc["models"]["B"]["test"]["decision_time"]
    base = doc["baseline"]["test"]["decision_time"]
    a = g["protocol_a"]
    c = g["checks"]
    lines = [
        "## Go/no-go (ADR-10, MOD-01, MOD-06)",
        "",
        f"{_source(doc)} Population: decision-time test events "
        f"({_n(b['n'])} events). Baseline: {doc['baseline']['rule']}.",
        "",
        *_header("Check", "Protocol B", "Baseline", "Result"),
        _row("F1 above the baseline", _f(b["f1"]), _f(base["f1"]), _ok(c["b_f1_beats_baseline"])),
        _row(
            "Cross-entropy below the baseline",
            _f(b["cross_entropy"]),
            _f(base["cross_entropy"]),
            _ok(c["b_cross_entropy_beats_baseline"]),
        ),
        _row(
            "F1 above every single-feature candidate (best test F1 among them)",
            _f(b["f1"]),
            _f(g["best_candidate_test_f1"]),
            _ok(c["b_f1_beats_every_candidate"]),
        ),
        _row(
            "Cross-entropy below every candidate (lowest test cross-entropy among them)",
            _f(b["cross_entropy"]),
            _f(g["best_candidate_test_cross_entropy"]),
            _ok(c["b_cross_entropy_beats_every_candidate"]),
        ),
        _row(
            f"Calibrated: ECE <= {doc['assumptions']['ece_max_mod06']} (MOD-06)",
            _f(b["ece"]),
            _f(base["ece"]),
            _ok(c["b_ece_within_mod06"]),
        ),
        "",
        f"**Protocol B: {'GO' if g['go'] else 'NO-GO'}.**",
        "",
        f"Protocol A ({a['label']}), full test set: F1 {_f(a['f1_at_half'])} at threshold 0.5 "
        f"and {_f(a['f1_tuned'])} at the validation threshold, against {a['paper_f1']} in the "
        f"paper; the spec threshold F1 >= {a['threshold_spec']} applies to protocol A only: "
        f"{_ok(a['meets_spec_at_half'])}. A protocol A number is never product performance "
        "(NFR-16).",
        "",
    ]
    return lines


def _population_tables(doc: dict[str, Any]) -> list[str]:
    lines = [
        "## Results side by side",
        "",
        "Threshold: the calibrated probability above which a purchase is predicted returned, "
        "chosen to maximise F1 on the validation split; precision and recall at that threshold. "
        "Cross-entropy in nats, ECE over ten equal-width bins, both on calibrated probabilities.",
        "",
    ]
    for population, title in POPULATION_TITLES.items():
        first = doc["models"]["A"]["test"][population]
        n = first["n"] if first else 0
        rate = first["positives"] / first["n"] if first else None
        lines += [
            f"### {title}",
            "",
            f"{_source(doc)} Population: {POPULATIONS[population]}; "
            f"{_n(n)} events, {_f(rate, 3)} returned.",
            "",
            *_header(
                "Model",
                "Protocol",
                "Threshold",
                "F1",
                "Precision",
                "Recall",
                "F1 at 0.5",
                "Cross-entropy",
                "ECE",
            ),
        ]
        for model_title, label, result in _rows(doc):
            m = result["test"][population]
            if m is None:
                lines.append(_row(model_title, label, *(["n/a"] * 7)))
                continue
            lines.append(
                _row(
                    model_title,
                    label,
                    _f(m["threshold"], 3),
                    _f(m["f1"]),
                    _f(m["precision"]),
                    _f(m["recall"]),
                    _f(m["f1_at_half"]),
                    _f(m["cross_entropy"]),
                    _f(m["ece"]),
                )
            )
        lines.append("")
    return lines


def _calibration(doc: dict[str, Any]) -> list[str]:
    lines = [
        "## Calibration: raw model output and isotonic calibration",
        "",
        f"{_source(doc)} Isotonic regression fitted on the validation split; probabilities "
        f"clipped to [{doc['assumptions']['probability_clip']}, "
        f"1 - {doc['assumptions']['probability_clip']}].",
        "",
        *_header(
            "Model",
            "Protocol",
            "Population",
            "CE raw",
            "CE calibrated",
            "ECE raw",
            "ECE calibrated",
        ),
    ]
    for key in ("A", "B"):
        m = doc["models"][key]
        for population in ("all", "decision_time"):
            raw, cal = m["test_uncalibrated"][population], m["test"][population]
            if raw is None or cal is None:
                continue
            lines.append(
                _row(
                    m["title"],
                    m["protocol_label"],
                    POPULATION_TITLES[population],
                    _f(raw["cross_entropy"]),
                    _f(cal["cross_entropy"]),
                    _f(raw["ece"]),
                    _f(cal["ece"]),
                )
            )
    return [*lines, ""]


def _training(doc: dict[str, Any]) -> list[str]:
    env = doc["environment"]
    lines = [
        "## Training time on CPU (reference for benchmark B1)",
        "",
        f"Measured once by this run, wall-clock, on {env['cpu']} ({env['cpu_count']} logical "
        f"CPUs), xgboost {env['xgboost']}, device {env['device']}; nature: measured on this "
        "machine, it varies between runs and machines. Training data: the ASOS train split "
        f"({doc['provenance']['nature']}, {_period(doc, 'train')}). The MI300X run of B1 uses "
        "the same data, parameters and seed through train_booster(device='cuda'). Load: the "
        "machine's 1-minute load average just before and after training; a load above zero "
        "means other jobs shared the CPUs and the time is an upper bound.",
        "",
        *_header(
            "Model",
            "Protocol",
            "Device",
            "Threads",
            "Fit rows",
            "Validation rows",
            "Features",
            "Rounds run",
            "Trees kept",
            "DMatrix s",
            "Train s",
            "Load before / after",
        ),
    ]
    for key in ("A", "B", "B_all_events"):
        m = doc["models"][key]
        t = m["training"]
        lines.append(
            _row(
                m["title"],
                m["protocol_label"],
                t["device_effective"],
                t["threads"],
                _n(t["rows_fit"]),
                _n(t["rows_valid"]),
                t["features"],
                t["rounds_run"],
                t["trees_kept"],
                _f(t["dmatrix_seconds"], 1),
                _f(t["train_seconds"], 1),
                f"{_f(t['load_average_before'], 2)} / {_f(t['load_average_after'], 2)}",
            )
        )
    return [*lines, ""]


def _candidates(doc: dict[str, Any]) -> list[str]:
    lines = [
        "## Single-feature leave-one-out rules (baseline candidates)",
        "",
        f"{_source(doc)} Protocol: {doc['baseline']['protocol_label']}. Each rule is fitted on "
        "the decision-time validation events; the baseline is the best validation F1.",
        "",
        *_header("Feature", "Rule", "Validation F1", "Test F1", "Test cross-entropy", "Test ECE"),
    ]
    for name, c in doc["baseline"]["candidates"].items():
        m = c["test"]["decision_time"]
        lines.append(
            _row(
                f"`{name}`",
                c["rule"],
                _f(c["validation_f1"]),
                _f(m["f1"] if m else None),
                _f(m["cross_entropy"] if m else None),
                _f(m["ece"] if m else None),
            )
        )
    return [*lines, ""]


def _group(g: dict[str, Any]) -> str:
    return f"{g['events']:,}, {_f(g['returned_share'])}"


def _data(doc: dict[str, Any]) -> list[str]:
    tr, te = doc["data"]["train"], doc["data"]["test"]

    def both(get: Any, fmt: Any = _n) -> list[str]:
        return [fmt(get(tr)), fmt(get(te))]

    lines = [
        "## Data and populations",
        "",
        f"Origin: {doc['provenance']['origin']}. Nature: {doc['provenance']['nature']}. "
        f"Train split {_period(doc, 'train')}, test split {_period(doc, 'test')}.",
        "",
        *_header("Count", "Train", "Test"),
        _row("Events", *both(lambda d: d["events"])),
        _row("Returned share", *both(lambda d: d["returned_share"], lambda v: _f(v, 4))),
        _row("With a customer row", *both(lambda d: d["with_customer_row"])),
        _row("With a variant row", *both(lambda d: d["with_variant_row"])),
        _row("Fully featured (both rows)", *both(lambda d: d["fully_featured"])),
        _row(
            "Selected by their own return (left out of the decision-time population)",
            *both(lambda d: d["selected_by_own_return"]["events"]),
        ),
        _row(
            "of which returned",
            *both(lambda d: d["selected_by_own_return"]["returned_share"], lambda v: _f(v, 4)),
        ),
        _row(
            "of which with counts that hold the event: events, returned share",
            *both(lambda d: _group(d["selected_by_own_return"]["consistent_counts"]), str),
        ),
        _row(
            "of which with clipped counts (the row does not hold the event): events, returned",
            *both(lambda d: _group(d["selected_by_own_return"]["clipped_counts"]), str),
        ),
        _row(
            "by the customer row / the variant row",
            *both(
                lambda d: (
                    f"{d['selected_by_own_return']['by_customer_row']:,} / "
                    f"{d['selected_by_own_return']['by_variant_row']:,}"
                ),
                str,
            ),
        ),
        _row("Decision-time events", *both(lambda d: d["decision_time_events"])),
        _row(
            "Leave-one-out counts clipped, customer / variant",
            *both(
                lambda d: f"{d['loo_clipped']['customer']:,} / {d['loo_clipped']['variant']:,}", str
            ),
        ),
        _row(
            "Variant categories unseen in train, product type / brand",
            *both(
                lambda d: (
                    f"{d['unknown_categories']['product_type']:,} / "
                    f"{d['unknown_categories']['brand']:,}"
                ),
                str,
            ),
        ),
        "",
        f"Validation: {doc['assumptions']['validation']} ({_n(doc['data']['validation_events'])} "
        "events).",
        "",
    ]
    return lines


def _features(doc: dict[str, Any]) -> list[str]:
    lines = ["## Features", ""]
    for key in ("A", "B"):
        m = doc["models"][key]
        top = ", ".join(
            f"`{t['feature']}` {t['share_of_total_gain']:.2f}"
            for t in m["top_features_by_gain"][:5]
        )
        lines += [
            f"- **{m['title']}** ({m['protocol_label']}), {len(m['features'])} features: "
            + ", ".join(f"`{f}`" for f in m["features"])
            + ".",
            f"  Largest shares of total gain (contribution to the score, not a cause): {top}.",
        ]
    lines += [
        "- No model holds a forbidden feature (rule 6, FAIR-01: gender, country, age, names, "
        "hashes and raw identifiers, checked by `contracts.parquet.is_forbidden_feature` before "
        "training); protocol B holds no rate, reason share or node aggregate.",
        "",
    ]
    return lines


def _method(doc: dict[str, Any]) -> list[str]:
    a = doc["assumptions"]
    params = ", ".join(f"{k} {v}" for k, v in a["xgboost_params"].items())
    return [
        "## Method and limitations",
        "",
        f"- XGBoost with the paper's hyperparameters ({params}), "
        f"early stopping after {a['early_stopping_rounds']} rounds on the validation log loss, "
        f"at most {a['max_rounds']} rounds (our cap), seed {a['seed']}.",
        f"- Same splits for every model: the ASOS train and test splits, and {a['validation']}.",
        "- Left joins by split with explicit nulls: an event without a customer or variant row "
        "keeps NaN features and has_customer_features / has_variant_features = 0. Product "
        "attributes come from the variant table of the event's own split.",
        "- Protocol B uses leave-one-out counts: the customer's and the variant's sales and "
        "returns minus the event itself. Every customer and variant row of the dataset has at "
        "least one return, so a row whose only return is the event exists because the event was "
        "returned: its leave-one-out returns are 0, and every such event whose row's counts hold "
        "it is returned (table above; the few exceptions are rows whose counts were clipped). "
        "These events are left out of the decision-time population, where protocol B is trained, "
        "calibrated and judged; this refines ADR-10, which did not foresee it. Left in training, "
        "they teach the model that a customer or variant with no other return returns this "
        "purchase, the opposite of what a store sees at decision time, and they inflate the "
        "full-test-set numbers. The diagnostic row 'B trained on every event' shows the same "
        "features trained with them; it is never saved or used.",
        "- Protocol B still sees other purchases of the same window: ASOS events carry no "
        "timestamp (ADR-10). The variant aggregates are identical in the train and test tables, "
        "so they also count test-period purchases.",
        "- The dataset holds only customers with at least one return (paper): its rates do not "
        "describe all ASOS customers, and the model meets customers without returns only in the "
        "synthetic store (distribution shift, risk R-07).",
        "- Protocol A aggregates are computed over the labelled window: its numbers measure the "
        "dataset, not the product, and serve benchmarks B1, B2 and the comparison with the paper.",
        "",
    ]


def render_markdown(doc: dict[str, Any], out_dir: Path) -> str:
    out = out_dir.as_posix()
    lines = [
        "# US-201: return probability per purchase on ASOS, protocols A and B",
        "",
        f"<!-- Generated by `{doc['reproduce']}` from raw/asos_*.parquet. Do not edit by hand: "
        "rerun the command. -->",
        "",
        f"Every number below comes from {doc['provenance']['origin']}; nature: "
        f"{doc['provenance']['nature']}; train split {_period(doc, 'train')}, test split "
        f"{_period(doc, 'test')} (the paper's months: events carry no timestamp). Protocols of "
        f'ADR-10: A = "{doc["protocols"]["A"]}"; B = "{doc["protocols"]["B"]}".',
        "",
        f"Reproduce from the repository root: `{doc['reproduce']}`. Outputs: "
        f"`{out}/models/propensity_A.json`, `{out}/models/propensity_B.json` (XGBoost JSON), "
        f"their `_calibrator.json` files, `{out}/models/propensity_baseline.json` and the "
        f"metrics with provenance in `{out}/metrics/us-201-propensity.json`.",
        "",
        *_go_no_go(doc),
        *_population_tables(doc),
        *_calibration(doc),
        *_training(doc),
        *_candidates(doc),
        *_data(doc),
        *_features(doc),
        *_method(doc),
    ]
    return "\n".join(lines).rstrip() + "\n"
