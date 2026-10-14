"""docs/results/us-202-graphsage.md, rendered from the metrics files (no torch, never by hand).

Inputs, all optional, under the artifacts folder:

- eval/gnn/us-202-asos-cpu.json and us-202-asos-gpu.json: `python -m ml.gnn asos` on each device;
- eval/gnn/us-202-embeddings-main.json and -ood.json: `python -m ml.gnn embed`;
- eval/risk/metrics.json: `python -m ml.risk` (the A4 rows, when the embeddings existed).

Every table states origin, period, nature and protocol of its numbers (rule 11): ASOS numbers are
real data, the A4 numbers synthetic, the paper's numbers quoted from the paper, every time
measured on the machine named next to it. A missing input is reported as not measured, never
filled in.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

COMMAND: Final = (
    "python -m ml.gnn report --artifacts artifacts --doc docs/results/us-202-graphsage.md"
)
POPULATION_TITLES: Final = {
    "all": "Full test set",
    "fully_featured": "Fully featured test subset",
    "decision_time": "Decision-time test population",
    "decision_time_fully_featured": "Decision-time test population, fully featured",
}
MODEL_ORDER: Final = ("graphsage", "mlp", "xgboost")
DEVICES: Final = ("cpu", "gpu")
A4_METRICS: Final = (
    ("AUPRC", "auprc"),
    ("P@50", "precision_at.50"),
    ("P@100", "precision_at.100"),
    ("Precision", "precision"),
    ("Recall", "recall"),
    ("FPR", "fpr"),
    ("FPR high-value", "fpr_high_value"),
    ("ECE", "ece"),
)


def _f(x: Any, digits: int = 4) -> str:
    return "n/a" if x is None else f"{float(x):.{digits}f}"


def _n(x: Any) -> str:
    return "n/a" if x is None else f"{int(x):,}"


def _s(x: Any, digits: int = 2) -> str:
    return "n/a" if x is None else f"{float(x):,.{digits}f}"


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def load_inputs(artifacts: Path) -> dict[str, Any]:
    gnn = artifacts / "eval" / "gnn"
    return {
        "asos": {d: _load(gnn / f"us-202-asos-{d}.json") for d in DEVICES},
        "embeddings": {s: _load(gnn / f"us-202-embeddings-{s}.json") for s in ("main", "ood")},
        "risk": _load(artifacts / "eval" / "risk" / "metrics.json"),
    }


def _hardware(doc: dict[str, Any]) -> str:
    env = doc["environment"]
    if str(env["device"]).startswith("cuda"):
        what = f"{env.get('gpu_name')} ({env.get('gpu_arch')}, {env.get('gpu_total_gb')} GB)"
    else:
        what = f"{env['cpu']} ({env['cpu_count']} logical CPUs, {env['torch_threads']} threads)"
    versions = f"torch {env['torch']}" + (
        f", HIP {env['torch_hip']}" if env.get("torch_hip") else ""
    )
    if env.get("dgl"):
        versions += f", DGL {env['dgl']}"
    return f"{what}; {env['backend']} backend; {versions}"


def _measured_on(doc: dict[str, Any]) -> str:
    """The period of a run's timings: the UTC day it started (rule 11)."""
    at = (doc.get("timings") or {}).get("measured_at")
    return f"measured on {str(at)[:10]} (UTC)" if at else "date not recorded in the metrics file"


def _timing_nature(doc: dict[str, Any]) -> str:
    """Rule 11: a time measured on real hardware is real (files written before the jobs said so
    explicitly carry a free-text note instead)."""
    nature = str((doc.get("timings") or {}).get("nature", ""))
    if nature.startswith("real"):
        return nature
    return "real (measured on the machine named here; varies between runs and machines)"


def _run_line(doc: dict[str, Any]) -> str:
    cfg = doc["config"]
    sample = (
        "every event"
        if cfg["sample"] >= 1.0
        else f"**a seeded sample of {cfg['sample']:.0%} of the customers** (all their events, "
        "both splits; every model of the run on the same sample)"
    )
    return f"Device {doc['environment']['device']}: {_hardware(doc)}. Data: {sample}."


def _source(doc: dict[str, Any]) -> str:
    p = doc["provenance"]
    return (
        f"Origin: {p['origin']}. Nature: {p['nature']}. Test split "
        f"{doc['labels']['test']['period']}."
    )


def _headline(doc: dict[str, Any]) -> list[str]:
    rows: list[list[str]] = []
    for protocol, population in (("B", "decision_time"), ("A", "all")):
        entry = doc["protocols"].get(protocol)
        if entry is None:
            continue
        for kind in MODEL_ORDER:
            m = entry["models"].get(kind)
            if m is None or m["test"].get(population) is None:
                continue
            t = m["test"][population]
            rows.append(
                [
                    m["title"],
                    m["protocol_label"],
                    POPULATION_TITLES[population],
                    _n(t["n"]),
                    _f(t["f1"]),
                    _f(t["f1_at_half"]),
                    _f(t["cross_entropy"]),
                    _f(t["ece"]),
                    "this run",
                ]
            )
    paper = doc["paper"]
    for name, m in paper["models"].items():
        rows.append(
            [
                f"Paper: {name}",
                paper["protocol_label"],
                "Full test set (1.5M events)",
                "n/a",
                "n/a",
                _f(m["f1"]),
                _f(m["cross_entropy"]),
                "not reported",
                paper["source"],
            ]
        )
    return table(
        (
            "Model",
            "Protocol",
            "Population",
            "Events",
            "F1 (validation threshold)",
            "F1 at 0.5",
            "Cross-entropy",
            "ECE",
            "Source",
        ),
        rows,
    )


def _one_hot(name: str) -> bool:
    return name.startswith(("product_type_", "brand_"))


def _differences(doc: dict[str, Any]) -> list[str]:
    """GraphSAGE minus each other model of the run, on the headline population of a protocol:
    what the graph adds over the same head without message passing (MLP) and over XGBoost."""
    rows = []
    for protocol, entry in _protocols(doc):
        population = "decision_time" if protocol == "B" else "all"
        g = entry["models"].get("graphsage")
        if g is None or g["test"].get(population) is None:
            continue
        for kind in ("mlp", "xgboost"):
            other = entry["models"].get(kind)
            if other is None or other["test"].get(population) is None:
                continue
            a, b = g["test"][population], other["test"][population]
            rows.append(
                [
                    f"{g['title']} minus {other['title']}",
                    POPULATION_TITLES[population],
                    f"{a['f1'] - b['f1']:+.4f}",
                    f"{a['f1_at_half'] - b['f1_at_half']:+.4f}",
                    f"{a['cross_entropy'] - b['cross_entropy']:+.4f}",
                    f"{a['ece'] - b['ece']:+.4f}",
                ]
            )
    if not rows:
        return []
    return [
        "Differences on the same events (a positive F1 or a negative cross-entropy favours "
        "GraphSAGE); measured once, with no confidence interval:",
        "",
        *table(
            ("Comparison", "Population", "F1", "F1 at 0.5", "Cross-entropy", "ECE"),
            rows,
        ),
        "",
    ]


def _population_tables(doc: dict[str, Any], protocol: str) -> list[str]:
    entry = doc["protocols"][protocol]
    lines: list[str] = []
    for population, title in POPULATION_TITLES.items():
        rows = []
        n = None
        for kind in MODEL_ORDER:
            m = entry["models"].get(kind)
            if m is None:
                continue
            t = m["test"].get(population)
            if t is None:
                rows.append([m["title"], *(["n/a"] * 8)])
                continue
            n = t["n"]
            rows.append(
                [
                    m["title"],
                    _f(t["threshold"], 3),
                    _f(t["f1"]),
                    _f(t["precision"]),
                    _f(t["recall"]),
                    _f(t["f1_at_half"]),
                    _f(t["cross_entropy"]),
                    _f(t["ece"]),
                    _f(m["test_uncalibrated"][population]["cross_entropy"])
                    if m.get("test_uncalibrated", {}).get(population)
                    else "n/a",
                ]
            )
        lines += [
            f"#### {title} ({_n(n)} events)",
            "",
            *table(
                (
                    "Model",
                    "Threshold",
                    "F1",
                    "Precision",
                    "Recall",
                    "F1 at 0.5",
                    "Cross-entropy",
                    "ECE",
                    "Cross-entropy before calibration",
                ),
                rows,
            ),
            "",
        ]
    return lines


def _protocols(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(protocol, entry) in the order of the run (B first), whatever the JSON key order."""
    order = [p for p in doc.get("config", {}).get("protocols", ["B", "A"]) if p in doc["protocols"]]
    order += [p for p in sorted(doc["protocols"]) if p not in order]
    return [(p, doc["protocols"][p]) for p in order]


def _training_rows(doc: dict[str, Any]) -> list[list[str]]:
    rows = []
    for _, entry in _protocols(doc):
        for kind in MODEL_ORDER:
            m = entry["models"].get(kind)
            if m is None:
                continue
            tr = m["training"]
            if kind == "xgboost":
                rows.append(
                    [
                        m["title"],
                        f"cpu ({tr['threads']} threads)",
                        _n(tr["rows_fit"]),
                        f"{tr['trees_kept']} trees",
                        _s(tr["train_seconds"]),
                        "n/a",
                        "n/a",
                        f"{tr['load_average_before']} / {tr['load_average_after']}",
                        "n/a",
                    ]
                )
                continue
            inf = m["inference"]
            rows.append(
                [
                    m["title"],
                    doc["environment"]["device"],
                    _n(tr["rows_fit"]),
                    f"{tr['epochs_run']} epochs (best {tr['best_epoch']})",
                    _s(tr["train_seconds"]),
                    _s(tr["epoch_seconds_median"], 3),
                    f"{_s(inf['seconds'], 3)} ({_n(inf['events'])} events)",
                    f"{tr['load_average_before']} / {tr['load_average_after']}",
                    _s(tr.get("peak_allocated_gb"), 3) if tr.get("peak_allocated_gb") else "n/a",
                ]
            )
    return rows


def _timing_section(asos: dict[str, dict[str, Any] | None]) -> list[str]:
    lines = ["## Timings (benchmark B2; spec 12, 'Tempi distinti')", ""]
    found = {d: doc for d, doc in asos.items() if doc is not None}
    if not found:
        return [*lines, "No ASOS run found.", ""]
    for device, doc in found.items():
        t = doc["timings"]
        cold = t["cold_start"]
        lines += [
            f"### {device.upper()} run",
            "",
            _run_line(doc),
            f"Nature: {_timing_nature(doc)}. Period: {_measured_on(doc)}. Load: the machine's "
            "1-minute load average before and after training; a load above the number of CPUs "
            "means other jobs shared them, and the CPU times are an upper bound.",
            "",
            *table(
                ("Phase", "Seconds"),
                [
                    ["Cold start: import torch", _s(cold.get("import_torch_seconds"), 3)],
                    [
                        "Cold start: import DGL (0 with the PyTorch backend)",
                        _s(cold.get("import_backend_seconds"), 3),
                    ],
                    [
                        "Cold start: device context and first kernels",
                        _s(cold.get("device_init_and_first_kernels_seconds"), 3),
                    ],
                    ["Data loading: read raw/asos_* (contracts)", _s(t["read_raw_seconds"], 3)],
                    *[
                        [
                            f"Data loading, protocol {p}: join, graph, features, copy to device",
                            _s(e["data_loading_seconds"], 3),
                        ]
                        for p, e in _protocols(doc)
                    ],
                    ["Whole job after the cold start", _s(t["job_seconds_after_cold_start"], 1)],
                ],
            ),
            "",
            *table(
                (
                    "Model",
                    "Device",
                    "Training events",
                    "Epochs or trees",
                    "Training s",
                    "Median epoch s",
                    "Inference s (test split)",
                    "Load before / after",
                    "GPU peak GB",
                ),
                _training_rows(doc),
            ),
            "",
        ]
    cpu, gpu = found.get("cpu"), found.get("gpu")
    lines += ["### CPU against MI300X", ""]
    if cpu is None or gpu is None:
        missing = "GPU" if gpu is None else "CPU"
        lines += [
            f"Not measured yet: no {missing} run file (us-202-asos-{missing.lower()}.json). The "
            "speedup is computed only from two runs with the same data, configuration and "
            "seed (spec 12, 'Qualità comparabile'); the commands are in the last section.",
            "",
        ]
    elif cpu["comparable"] != gpu["comparable"]:
        lines += [
            "The CPU and GPU files were produced with different settings (data sample, epochs, "
            "protocols, model or seed): no speedup is computed (spec 12, 'Qualità comparabile').",
            "",
        ]
    else:
        rows = []
        for protocol, entry in _protocols(gpu):
            for kind in ("graphsage", "mlp"):
                g = entry["models"].get(kind)
                c = cpu["protocols"].get(protocol, {}).get("models", {}).get(kind)
                if g is None or c is None:
                    continue
                rows.append(
                    [
                        g["title"],
                        _s(c["training"]["epoch_seconds_median"], 3),
                        _s(g["training"]["epoch_seconds_median"], 3),
                        _s(
                            c["training"]["epoch_seconds_median"]
                            / g["training"]["epoch_seconds_median"]
                        )
                        + "x",
                        _s(c["inference"]["seconds"], 3),
                        _s(g["inference"]["seconds"], 3),
                        _s(c["inference"]["seconds"] / g["inference"]["seconds"]) + "x",
                        f"{_f(c['test']['all']['f1'])} / {_f(g['test']['all']['f1'])}",
                    ]
                )
        lines += [
            "Same data, configuration and seed on both devices; the median epoch compares one "
            "unit of work, the totals also depend on when early stopping fires.",
            "",
            *table(
                (
                    "Model",
                    "CPU median epoch s",
                    "GPU median epoch s",
                    "Speedup",
                    "CPU inference s",
                    "GPU inference s",
                    "Speedup",
                    "F1 full test, CPU / GPU",
                ),
                rows,
            ),
            "",
        ]
    return lines


def _asos_section(asos: dict[str, dict[str, Any] | None]) -> list[str]:
    doc = asos.get("gpu") or asos.get("cpu")
    if doc is None:
        return [
            "## ASOS: GraphSAGE against XGBoost and the paper",
            "",
            "Not measured yet: no `artifacts/eval/gnn/us-202-asos-*.json` (run "
            "`python -m ml.gnn asos --raw data/raw --out artifacts`).",
            "",
        ]
    which = "GPU" if asos.get("gpu") else "CPU"
    lines = [
        "## ASOS: GraphSAGE against XGBoost and the paper",
        "",
        f"Quality numbers of the {which} run. {_run_line(doc)} {_source(doc)} Every model of a "
        "protocol is trained on the same events, validated on the same seeded 10% of the train "
        "split (early stopping, isotonic calibration, F1 threshold) and evaluated on the same "
        "test events (US-201 splits and populations, ADR-10).",
        "",
        "### Headline: decision-time protocol (B) first, then the paper's protocol (A)",
        "",
        *_headline(doc),
        "",
        *_differences(doc),
        "Protocol B rows are measured on the decision-time population (ADR-10 amendment 1); "
        "protocol A rows and the paper's rows on the full test set. The paper's numbers use the "
        "paper's protocol (features over the labelled window) and its own training choices (see "
        "Method): they are a reference for protocol A, never a target for protocol B. The paper "
        "reports no calibration error.",
        "",
    ]
    mod02 = doc["mod02"]
    if mod02.get("measured"):
        lines += [
            "### MOD-02 (paper protocol only)",
            "",
            f"GraphSAGE, protocol A ({mod02['label']}), full test set: F1 "
            f"{_f(mod02['f1_at_half'])} "
            f"at 0.5 and {_f(mod02['f1_tuned'])} at the validation threshold, against the spec's "
            f"F1 >= {mod02['threshold_spec']} (paper 0.792): "
            f"**{'pass' if mod02['pass_tuned'] else 'FAIL'}** at the validation threshold, "
            f"**{'pass' if mod02['pass_at_half'] else 'FAIL'}** at 0.5. Protocol B is reported "
            "with no threshold (ADR-10). A protocol A number is never product performance "
            "(NFR-16).",
            "",
        ]
    for protocol in ("B", "A"):
        if protocol not in doc["protocols"]:
            continue
        entry = doc["protocols"][protocol]
        f = entry["features"]
        lines += [
            f"### Protocol {protocol}: {entry['protocol_label']}",
            "",
            f"{_source(doc)} Thresholds maximise F1 on the validation split; cross-entropy in "
            "nats and ECE over ten equal-width bins on calibrated probabilities.",
            "",
            f"Inputs. Customer nodes ({len(f['customer_node'])}): "
            + ", ".join(f"`{n}`" for n in f["customer_node"])
            + f". Variant nodes ({len(f['variant_node'])}): "
            + ", ".join(f"`{n}`" for n in f["variant_node"] if not _one_hot(n))
            + f" and {sum(_one_hot(n) for n in f['variant_node'])} product type and brand "
            "one-hot columns. Edge head: "
            + (
                f"the {len(f['edge'])} protocol B features of ml.propensity"
                if f["edge"]
                else "the two node representations only, as in the paper"
            )
            + ".",
            "",
            *_population_tables(doc, protocol),
        ]
    return lines


def _embedding_section(inputs: dict[str, Any]) -> list[str]:
    lines = ["## A4: GraphSAGE customer embeddings on the synthetic store", ""]
    main = inputs["embeddings"]["main"]
    if main is None:
        return [
            *lines,
            "Not measured yet: no `artifacts/eval/gnn/us-202-embeddings-main.json` (run "
            "`python -m ml.gnn embed --store data --out data --models artifacts`).",
            "",
        ]
    rows = []
    for store in ("main", "ood"):
        doc = inputs["embeddings"][store]
        if doc is None:
            rows.append([store, "not run", *(["n/a"] * 9)])
            continue
        enc = doc["encoder"]
        tr = (enc.get("training") or {}) if enc["trained_here"] else {}
        t = doc["timings"]
        rows.append(
            [
                store,
                enc["version"] + (" (trained here)" if enc["trained_here"] else " (applied)"),
                ", ".join(i[:10] for i in doc["output"]["instants"]),
                _n(doc["output"]["rows"]),
                f"{tr.get('epochs_run')} (best {tr.get('best_epoch')})" if tr else "-",
                _f(tr.get("validation_auc_at_best")) if tr else "-",
                _s(t.get("training_seconds"), 2) if t.get("training_seconds") else "-",
                _s(t.get("inference_seconds"), 3),
                f"{doc['environment']['device']}: {_hardware(doc)}",
                f"{tr.get('load_average_before')} / {tr.get('load_average_after')}" if tr else "-",
                _measured_on(doc),
            ]
        )
    enc = main["encoder"]
    cfg = enc["config"]
    graph = main["graphs"][-1]
    lines += [
        f"Origin: {main['output']['provenance']['origin']}. Period "
        f"{main['output']['provenance']['period']}. Nature: {main['nature']} (the generated "
        "Navigli Fashion store). The encoder never reads the ground truth (rule 12) and sees the "
        "store known at each instant only (rule 7).",
        "",
        f"Encoder: {cfg['layers']} heterogeneous SAGE layers of {cfg['hidden']} units, "
        f"aggregator {cfg['aggregator']}, dropout {cfg['dropout']}, trained by link prediction on "
        f"the (account, identifier value) uses known at {enc['fit_as_of']}: each epoch hides 20% "
        "of the uses and scores them against random values of the same kind; a fixed 10% is held "
        "out for early stopping. Inputs: "
        + ", ".join(f"`{n}`" for n in enc["inputs"]["customer"])
        + " (accounts); "
        + ", ".join(f"`{n}`" for n in enc["inputs"]["identifier"])
        + f" (identifier values). Graph at the last instant: {_n(graph['customers'])} accounts, "
        f"{_n(graph['identifier_values'])} values, {_n(graph['uses'])} uses, "
        f"{_n(graph['values_shared_by_two_or_more'])} values shared by two accounts or more.",
        "",
        *table(
            (
                "Store",
                "Encoder",
                "Instants",
                "Rows written",
                "Epochs",
                "Validation AUC (self-supervised task)",
                "Training s",
                "Inference s (all instants)",
                "Device: hardware, backend, versions",
                "Load before / after training",
                "Period",
            ),
            rows,
        ),
        "",
        f"Timings: nature {_timing_nature(main)}; a load above the number of CPUs means other "
        "jobs shared them, and the CPU times are an upper bound.",
        "",
        "The AUC measures the self-supervised task (a held-out use against a random value of the "
        "same kind), not abuse detection; what the embeddings add to abuse detection is the A4 "
        "row below.",
        "",
    ]
    lines += _a4_rows(inputs["risk"])
    return lines


def _metric(m: dict[str, Any], key: str) -> Any:
    if "." in key:
        outer, inner = key.split(".", 1)
        return m.get(outer, {}).get(inner)
    return m.get(key)


def _a4_rows(risk: dict[str, Any] | None) -> list[str]:
    head = "### A3 against A4 on the same split (MOD-04, from artifacts/eval/risk/metrics.json)"
    if risk is None:
        return [head, "", "Not measured yet: no risk metrics file.", ""]
    rows_by_set = {r["set"]: r for r in risk["ablation"]}
    a3, a4 = rows_by_set.get("A3"), rows_by_set.get("A4")
    if a4 is None or a4.get("status") != "run" or a3 is None:
        status = "missing" if a4 is None else a4.get("status")
        return [head, "", f"A4 not run in that file: {status}.", ""]
    lines = [
        head,
        "",
        f"Nature: {risk.get('nature', 'synthetic')}. Train end {risk['train_end']}, evaluation "
        f"at {risk['eval_end']}; held-out test groups of the grouped, time-ordered split, and the "
        "out-of-distribution store as a separate row (never trained on). A regression is "
        "reported as measured (rule 13). Full report: docs/results/us-204-risk.md.",
        "",
    ]
    rows = []
    for population, title in (("test", "held-out test customers"), ("ood", "OOD store")):
        for name, r in (("A3", a3), ("A4", a4)):
            m = r.get(population)
            if m is None:
                rows.append([name, title, *(["n/a"] * len(A4_METRICS))])
                continue
            rows.append([name, title, *[_f(_metric(m, key), 3) for _, key in A4_METRICS]])
    lines += [*table(("Set", "Rows", *(label for label, _ in A4_METRICS)), rows), ""]
    cover = a4.get("embedding_coverage", {})
    if cover:
        lines += [
            "Customer rows without an embedding: "
            + "; ".join(
                f"{k} {v['without_embedding']} of {_n(v['rows'])}" for k, v in cover.items()
            )
            + ".",
            "",
        ]
    return lines


def _method(asos: dict[str, dict[str, Any] | None]) -> list[str]:
    doc = asos.get("gpu") or asos.get("cpu")
    paper_notes = doc["paper"]["notes"] if doc else []
    return [
        "## Method and limitations",
        "",
        "- Scope: a benchmark of the ASOS return-prediction task (spec US-202, B2). ASOS has no "
        "identifiers and no abuse label: nothing here is evidence about rings. Abuse is measured "
        "on the synthetic store only (A4 row, and docs/results/us-204-risk.md).",
        "- Graph: one per ASOS split, customer and variant nodes, one undirected edge per event. "
        "GraphSAGE is inductive: trained on the train graph, applied to the test graph, as "
        "XGBoost uses the test split's node tables.",
        "- Model (spec 8.1): one heterogeneous SAGE layer of 16 units per relation, dropout 0.2, "
        "Adam 0.01, a 2-layer MLP head (64 units) on the two node representations and the edge's "
        "own features; binary cross-entropy, mini-batches of 131,072 events with the node "
        "representations recomputed on the whole graph each step, early stopping after 5 epochs "
        "without a better validation loss, at most 60 epochs; isotonic calibration and the F1 "
        "threshold on the validation split, as for XGBoost. The aggregator is the mean (sparse "
        "product); the paper and spec 8.1 use max-pool, available with `--aggregator pool` and "
        "not measured here.",
        "- Protocol B (decision time): no label travels along the graph. A node's return count "
        "holds the label of every edge of that node, so the nodes carry label-free features only "
        "(leave-one-out sales, degree, flags, product attributes), and the leave-one-out return "
        "counts of the event's own customer and variant enter the edge head per event (the 30 "
        "protocol B features of ml.propensity). Trained, calibrated and judged on the "
        "decision-time population (ADR-10 amendment 1).",
        "- Protocol A (paper): the paper's node features, return rates and reason shares "
        "included, on the nodes; nothing on the edge. Forbidden features (gender, country, age) "
        "are absent from both (rule 6, ADR-11): the paper's country virtual nodes are not built.",
        "- MLP row: the same head on the input node features, no message passing: what the graph "
        "adds over the same features. XGBoost row: ml.propensity's model of the protocol (US-201 "
        "code path) on the same rows.",
        *[f"- Paper: {n}." for n in paper_notes],
        "- Determinism: seeded Python, NumPy and torch, deterministic algorithms requested "
        "(warn only); the CPU run repeats exactly, a GPU run may differ in the last digits.",
        "- Timings are wall-clock on the machine named with each run, with its load average; a "
        "shared CPU makes them an upper bound.",
        "",
    ]


def _commands() -> list[str]:
    return [
        "## Reproduce",
        "",
        "CPU (any machine with PyTorch; DGL optional):",
        "",
        "```bash",
        "pip install -r requirements/dev.txt -r infra/requirements-gnn-cpu.txt",
        "make gnn-asos                # python -m ml.gnn asos --raw data/raw --out artifacts",
        "make embeddings              # A4 embeddings of the main and OOD stores into data/",
        "make risk                    # runs A4 when data/features/customer_embeddings exists",
        "make gnn-results             # this file",
        "```",
        "",
        "MI300X droplet (trainer-graphsage image: rocm/dgl plus this code; DGL backend), in the "
        "order of infra/spikes/README.md, 'The GPU session':",
        "",
        "```bash",
        "make gnn-image               # build the trainer-graphsage image (pulls rocm/dgl)",
        "make bench-b2 DEVICE=gpu     # B2: the asos job on the CPU and the MI300X, both",
        "                             # artifacts/eval/gnn/us-202-asos-{cpu,gpu}.json",
        "make embeddings-gpu          # encoder on the GPU, embeddings copied into data/",
        "```",
        "",
        "Then on the laptop, with the metrics files and the embeddings copied back: `make risk "
        "gnn-results` (the A4 row needs the ground truth, which stays on the laptop).",
        "",
        "Tests: `python -m pytest -m torch` with PyTorch installed (skipped without it); on the "
        "droplet `make gnn-test-gpu` also compares the DGL and PyTorch layers on the GPU.",
        "",
    ]


def render(inputs: dict[str, Any]) -> str:
    lines = [
        "# US-202: GraphSAGE on ASOS (benchmark B2) and the A4 customer embeddings",
        "",
        f"<!-- Generated by `{COMMAND}` from artifacts/eval/gnn/*.json and "
        "artifacts/eval/risk/metrics.json. Do not edit by hand: rerun the command. -->",
        "",
        "A benchmark of the ASOS return-prediction task, never evidence about rings (spec "
        "US-202). The ASOS numbers are real data (McGowan et al. 2023, arXiv:2302.14096, OSF "
        "c793h, CC BY 4.0); the A4 numbers are synthetic (the generated store); the paper's "
        "numbers are quoted from its Table 1; every timing is measured on the machine named with "
        "it.",
        "",
    ]
    lines += _asos_section(inputs["asos"])
    lines += _timing_section(inputs["asos"])
    lines += _embedding_section(inputs)
    lines += _method(inputs["asos"])
    lines += _commands()
    return "\n".join(lines).rstrip() + "\n"


def write_report(artifacts: Path, doc_path: Path) -> Path:
    text = render(load_inputs(artifacts))
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(text, encoding="utf-8")
    return doc_path
