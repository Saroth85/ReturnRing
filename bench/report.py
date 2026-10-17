"""Markdown report of a run document (docs/results/benchmarks-*.md). Standard library only.

Every table says where its numbers come from (origin, period, nature: rule 11), and a dry run or
a CPU-only run says so in its title and in each section.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PAPER_XGB_F1 = 0.774


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def _provenance(part: Mapping[str, Any]) -> str:
    measurements = part.get("measurements", [])
    if not measurements:
        return ""
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for m in measurements:
        p = m["provenance"]
        seen.setdefault((p["origin"], p["nature"]), (p["period_start"], p["period_end"]))
    lines = []
    for (origin, nature), (start, end) in seen.items():
        period = start if start == end else f"{start}..{end}"
        lines.append(f"- Origin: {origin}. Period: {period}. Nature: {nature}.")
    return "\n".join(lines)


def _header(part: Mapping[str, Any]) -> list[str]:
    lines = [
        f"Status: **{part['status']}**. Data: {part['dataset']}.",
        "",
    ]
    prov = _provenance(part)
    if prov:
        lines += [prov, ""]
    config = part.get("config") or {}
    if config:
        lines += ["Configuration: " + "; ".join(f"{k} {v}" for k, v in config.items()) + ".", ""]
    return lines


def _b1(part: Mapping[str, Any]) -> list[str]:
    lines = _header(part)
    details = part.get("details", {})
    cold = [m for m in part["measurements"] if m["phase"] == "cold_start"]
    if cold:
        cold_loads = {
            "mi300x" if c["device"] == "cuda" else "cpu": c.get("load_average_before_after")
            for c in details.get("cold_start", [])
        }
        lines += ["Cold start (first training call on the device in the process):", ""]
        lines += _table(
            ["Device", "Seconds", "Rows", "Load avg before/after", "Label"],
            [
                [
                    m["device"],
                    _fmt(m["value"], 3),
                    _fmt(m["rows"]),
                    "/".join(map(str, cold_loads.get(m["device"]) or ("n/a",))),
                    m["label"],
                ]
                for m in cold
            ],
        )
        lines.append("")
    for protocol, d in details.get("protocols", {}).items():
        lines += [
            f"### Protocol {protocol}: {d['label']}",
            "",
            f"{d['fit_rows']:,} fit rows and {d['validation_rows']:,} validation rows at 100%, "
            f"{d['features']} features; matrices prepared in {d['prepare_seconds']:.1f} s.",
            "",
        ]
        rows = []
        for r in d["runs"]:
            loads = ", ".join(f"{a}/{b}" for a, b in r["load_average_before_after"])
            rows.append(
                [
                    r["device"],
                    f"{r['fraction'] * 100:g}%",
                    _fmt(r["rows_fit"]),
                    "early stopping" if r["variant"] == "es" else r["variant"],
                    _fmt(r["train_seconds_median"], 2),
                    f"{r['train_seconds']['n']}",
                    _fmt(r["rounds_run"]),
                    _fmt(r["seconds_per_100_rounds_median"], 3),
                    _fmt(r["dmatrix_seconds_median"], 2),
                    _fmt(r["validation_logloss_median"], 4),
                    _fmt(r["validation_auc_median"], 4),
                    loads,
                ]
            )
        lines += _table(
            [
                "Device",
                "Size",
                "Fit rows",
                "Variant",
                "Train s (median)",
                "Runs",
                "Rounds",
                "s per 100 rounds",
                "DMatrix s",
                "Val. log loss",
                "Val. AUC",
                "Load avg before/after",
            ],
            rows,
        )
        lines.append("")
        scoring = d.get("scoring", [])
        if scoring:
            lines += [
                "Scoring of every fit row with the model of the largest size (the fixed-rounds "
                "model when the run has one: the same trees on every device; DMatrix build "
                "included):",
                "",
            ]
            lines += _table(
                [
                    "Device",
                    "Model",
                    "Trees",
                    "Batch rows",
                    "Batch s (median)",
                    "Single row p50 ms",
                    "p95 ms",
                    "n",
                ],
                [
                    [
                        s["device"],
                        s.get("model", "es"),
                        _fmt(s.get("trees")),
                        _fmt(s["rows"]),
                        _fmt(s["batch_seconds"]["p50"], 3),
                        _fmt(s["single_row_ms"]["p50"], 3),
                        _fmt(s["single_row_ms"]["p95"], 3),
                        _fmt(s["single_row_ms"]["n"]),
                    ]
                    for s in scoring
                ],
            )
            lines.append("")
    lines += _b1_comparisons(details)
    return lines


def _rounds(pair: Mapping[str, Sequence[int]]) -> str:
    return " / ".join(",".join(str(r) for r in pair.get(k, [])) for k in ("cpu", "mi300x"))


def _b1_comparisons(details: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    speedups = details.get("speedups", [])
    if speedups:
        lines += [
            "Speedup on identical work (fixed rounds, no early stopping: the same trees on both "
            "devices): CPU median over MI300X median.",
            "",
        ]
        lines += _table(
            ["Protocol", "Size", "Variant", "Rounds CPU / MI300X", "CPU / MI300X"],
            [
                [
                    s["protocol"],
                    f"{s['fraction'] * 100:g}%",
                    s["variant"],
                    _rounds(s["rounds"]) if "rounds" in s else "n/a",
                    _fmt(s["cpu_over_gpu"]),
                ]
                for s in speedups
            ],
        )
        lines.append("")
    early = details.get("early_stopping", [])
    if early:
        lines += [
            "Early stopping, side by side: each device stops at its own round, so the times are "
            "different work and their ratio is not a speedup; seconds per 100 rounds are "
            "compared instead, and a speedup only when every run stopped at the same round.",
            "",
        ]
        lines += _table(
            [
                "Protocol",
                "Size",
                "Rounds CPU / MI300X",
                "Train s CPU / MI300X",
                "s per 100 rounds, CPU / MI300X",
                "Speedup (same rounds only)",
            ],
            [
                [
                    e["protocol"],
                    f"{e['fraction'] * 100:g}%",
                    _rounds(e["rounds"]),
                    f"{_fmt(e['train_seconds_median']['cpu'])} / "
                    f"{_fmt(e['train_seconds_median']['mi300x'])}",
                    _fmt(e.get("per_100_rounds_cpu_over_gpu")),
                    _fmt(e.get("cpu_over_gpu")) if e.get("same_rounds") else "not computed",
                ]
                for e in early
            ],
        )
        lines.append("")
    scoring = details.get("scoring_speedups", [])
    if scoring:
        lines += ["Scoring, CPU over MI300X (only between models with the same trees):", ""]
        lines += _table(
            ["Protocol", "Trees CPU / MI300X", "Batch", "Single row p50"],
            [
                [
                    r["protocol"],
                    f"{r['trees']['cpu']} / {r['trees']['mi300x']}",
                    _fmt(r.get("batch_cpu_over_gpu")) if "not_computed" not in r else "n/a",
                    _fmt(r.get("single_row_p50_cpu_over_gpu"))
                    if "not_computed" not in r
                    else r["not_computed"],
                ]
                for r in scoring
            ],
        )
        lines.append("")
    return lines


def _b2(part: Mapping[str, Any]) -> list[str]:
    lines = _header(part)
    details = part.get("details", {})
    records = details.get("records", [])
    if records:
        lines += [
            "Each row is one US-202 job (`python -m ml.gnn asos`, a fresh process per device and "
            "data size). F1 as US-202 states it: protocol B on the decision-time population, "
            "protocol A on the full test set.",
            "",
        ]
        lines += _table(
            [
                "Size",
                "Protocol",
                "Device",
                "Nodes",
                "Training events",
                "Epochs",
                "Median epoch s",
                "Training loop s",
                "Inference s",
                "F1",
                "F1 at 0.5",
                "Population",
            ],
            [
                [
                    f"{r.get('sample', 1.0):.0%}",
                    r["protocol"],
                    "mi300x" if r["device_requested"] == "cuda" else "cpu",
                    _fmt(r["nodes"]),
                    _fmt(r["edges"]),
                    _fmt(r["epochs"]),
                    _fmt(r.get("epoch_seconds_median"), 3),
                    _fmt(r["train_seconds"], 1),
                    _fmt(r["inference_seconds"], 3),
                    _fmt(r["f1"], 4),
                    _fmt(r.get("f1_at_half"), 4),
                    r["population"],
                ]
                for r in records
            ],
        )
        lines.append("")
    speedups = details.get("speedups", [])
    if speedups:
        lines += [
            "CPU over MI300X: the median epoch (the same work on both devices) and the inference; "
            "the training loops are not divided (early stopping ends at another epoch on each).",
            "",
        ]
        lines += _table(
            [
                "Size",
                "Protocol",
                "Epochs CPU / MI300X",
                "Median epoch s CPU / MI300X",
                "Epoch speedup",
                "Inference speedup",
            ],
            [
                [
                    f"{s['sample']:.0%}",
                    s["protocol"],
                    f"{s['epochs']['cpu']} / {s['epochs']['mi300x']}",
                    f"{_fmt(s['epoch_seconds_median']['cpu'], 3)} / "
                    f"{_fmt(s['epoch_seconds_median']['mi300x'], 3)}",
                    _fmt(s.get("epoch_cpu_over_gpu")) if "not_computed" not in s else "n/a",
                    _fmt(s.get("inference_cpu_over_gpu"))
                    if "not_computed" not in s
                    else s["not_computed"],
                ]
                for s in speedups
            ],
        )
        lines.append("")
    interface = details.get("interface")
    if interface:
        lines += [f"Interface B2 calls: `{interface}`.", ""]
    return lines


def _load_rows(loads: Mapping[str, Mapping[str, Any]], batch: bool) -> list[list[str]]:
    rows = []
    for level, s in sorted(loads.items(), key=lambda kv: int(kv[0])):
        lat = s["latency_ms"]
        p95 = lat.get("p95")
        row = [
            level,
            _fmt(s["decisions"]),
            _fmt(s["failed"]),
            _fmt(lat.get("p50"), 1),
            _fmt(p95, 1) if p95 is not None or not lat.get("n") else "n/a (n < 20)",
        ]
        if batch:
            row.append(_fmt(s["wall_seconds"].get("p50"), 3))
        else:
            row.append(_fmt(s["decisions_per_second"], 3))
        row += [
            _fmt(s["output_tokens_per_second"], 1),
            _fmt(s["per_request_tokens_per_second"].get("p50"), 1),
            _fmt(s["output_tokens_per_decision"].get("p50"), 0),
            ", ".join(f"{k} {v}" for k, v in sorted(s["paths"].items())),
        ]
        rows.append(row)
    return rows


def _b3(part: Mapping[str, Any]) -> list[str]:
    lines = _header(part)
    d = part.get("details", {})
    cases = d.get("cases", {})
    if cases:
        lines += [
            f"Inputs: {cases['cases']} llm_case inputs as of {cases['as_of']} ({cases['nature']}),"
            f" golden first ({', '.join(cases['golden'])}); by decision: "
            + ", ".join(f"{k} {v}" for k, v in cases["by_decision"].items())
            + f"; skipped: {len(cases['skipped'])}.",
            "",
        ]
    case_list = d.get("case_list")
    if case_list:
        lines += [
            f"Every load ran the same {case_list['decisions_per_load']} decisions (seed "
            f"{case_list['seed']}, stratified by decision type: "
            + ", ".join(f"{k} {v}" for k, v in case_list["by_decision"].items())
            + f"; {case_list['golden']} of them golden cases).",
            "",
        ]
    prefix = d.get("prefix_cache_pass")
    if prefix:
        lines += [
            f"Before the loads, one untimed pass over the {prefix['decisions']} distinct cases "
            f"({prefix['wall_seconds']} s at concurrency {prefix['concurrency']}) filled vLLM's "
            "prefix cache, so every load sees the same cache.",
            "",
        ]
    server = d.get("server", {})
    if server:
        lines += [
            f"Server: vLLM {server.get('vllm_version')}, model {server.get('served_model')}, "
            f"max_model_len {server.get('max_model_len')}.",
            "",
        ]
    common = ["Decisions", "Failed", "Latency p50 ms", "Latency p95 ms"]
    tail = [
        "Output tok/s (aggregate)",
        "Tok/s per request (median)",
        "Output tokens (median)",
        "Paths",
    ]
    if d.get("batches"):
        lines += ["Batches sent at once:", ""]
        lines += _table(
            ["Batch", *common, "Batch wall s (median)", *tail], _load_rows(d["batches"], True)
        )
        lines.append("")
    if d.get("concurrency"):
        lines += ["Closed-loop concurrency:", ""]
        lines += _table(
            ["Concurrency", *common, "Decisions/s", *tail], _load_rows(d["concurrency"], False)
        )
        lines.append("")
    cold = [m for m in part["measurements"] if m["phase"] == "cold_start"]
    if cold:
        lines += _table(
            ["Cold start", "Value", "Unit"],
            [[m["label"], _fmt(m["value"], 1), m["unit"]] for m in cold],
        )
        lines.append("")
    cost = d.get("cost")
    if cost:
        lines += [
            f"### Cost per decision ({cost['assumptions']['version']}, nature simulated)",
            "",
            f"Declared operating scenario: {cost['declared_scenario']}. Inputs: "
            + "; ".join(f"{k} {v}" for k, v in cost["inputs"].items() if v is not None)
            + ".",
            "",
        ]
        rows = []
        for name, r in cost["scenarios"].items():
            if "unavailable" in r:
                rows.append([name, "not computed", "", "", "", "", r["unavailable"]])
                continue
            rows.append(
                [
                    name,
                    r["eur_per_decision"],
                    r["eur_per_1000_decisions"],
                    r["eur_per_day"],
                    _fmt(float(r["droplet_seconds_per_day"]) / 3600, 2),
                    "yes" if r["meets_target"] else "no",
                    _fmt(r["break_even_decisions_per_day"]),
                ]
            )
        lines += _table(
            [
                "Scenario",
                "EUR per decision",
                "EUR per 1,000",
                "EUR per day",
                "Droplet h/day",
                "<= 0.01 EUR",
                "Break-even decisions/day",
            ],
            rows,
        )
        lines += [
            "",
            "Assumptions: " + "; ".join(f"{k} {v}" for k, v in cost["assumptions"].items()) + ".",
            "",
        ]
    return lines


def _b4(part: Mapping[str, Any]) -> list[str]:
    lines = _header(part)
    steps = part.get("details", {}).get("steps", [])
    rows = []
    for s in steps:
        g = s.get("gpu") or {}
        rows.append(
            [
                s["name"],
                # A part written before steps named their device: on a CPU run, the CPU.
                s.get("executes_on")
                or ("cpu" if (part.get("config") or {}).get("device") == "cpu" else "n/a"),
                "ok" if s["ok"] else "failed",
                _fmt(s["seconds"], 1),
                ", ".join(_fmt(c["seconds"], 1) for c in s["commands"]),
                _fmt(g.get("n")),
                _fmt(g.get("gfx_activity_mean_percent"), 1),
                _fmt(g.get("gfx_activity_max_percent"), 1),
                _fmt(g.get("vram_used_max_gib"), 1),
                _fmt(g.get("power_mean_w"), 0),
            ]
        )
    if rows:
        total = sum(s["seconds"] for s in steps)
        lines += _table(
            [
                "Step",
                "Ran on",
                "Exit",
                "Seconds",
                "Commands (s)",
                "GPU samples",
                "GPU mean %",
                "GPU max %",
                "VRAM max GiB",
                "Power W",
            ],
            rows,
        )
        lines += ["", f"Total: {total:,.1f} s ({total / 60:.1f} min).", ""]
    nightly = part.get("details", {}).get("nightly")
    if nightly:
        lines += [
            f"Nightly seconds (the cost's input): {nightly['seconds']:,.1f} s, "
            + ", ".join(nightly["included"])
            + "; timed but left out: "
            + ", ".join(nightly["excluded"])
            + ".",
            "",
        ]
    return lines


RENDER = {"B1": _b1, "B2": _b2, "B3": _b3, "B4": _b4}


def render(doc: Mapping[str, Any], command: str) -> str:
    where = "MI300X droplet" if doc["device"] == "gpu" else "CPU only, no GPU"
    statuses = {b: p["status"] for b, p in doc["benchmarks"].items()}
    lines = [
        f"# Benchmarks B1-B4: run {doc['run_id']} ({where})",
        "",
        f"<!-- Generated by `{command}` from bench/results/{doc['run_id']}.json. Do not edit by "
        "hand: rerun the command. -->",
        "",
        f"US-902. Method and the four rules of spec 12: [docs/benchmarks.md](../benchmarks.md). "
        f"Run {doc['run_id']}, created {doc['created_at']}, commit "
        f"{(doc.get('git') or {}).get('commit') or 'unknown'}"
        + (" (uncommitted changes)" if (doc.get("git") or {}).get("dirty") else "")
        + ". Statuses: "
        + ", ".join(f"{b} {s}" for b, s in statuses.items())
        + ".",
        "",
        "Natures (rule 11): a time measured on this hardware is real, also when the data it ran "
        "on is synthetic (B4, B3 inputs); dry-run and cost figures are simulated. Measurements "
        "vary between runs and machines; each is one run unless a median says otherwise.",
        "",
        *(
            [
                "This run had no GPU: B1 timed the CPU only, B3 was a dry run against a local "
                "fake server (no Gemma), B4 had no GPU sampling and a mock model in its batch "
                "generation. No MI300X run was made: CPU against MI300X benchmarks were retired "
                "by ADR-18 (16 Oct 2026). A CPU run checks the harness under the load recorded "
                "next to each time; GET /api/benchmarks does not serve it.",
                "",
            ]
            if doc["device"] == "cpu"
            else []
        ),
        "## Hardware and software",
        "",
    ]
    rows = []
    for b, hw in doc.get("hardware", {}).items():
        gpu = hw.get("gpu") or {}
        names = ", ".join(str(g.get("market_name")) for g in gpu.get("gpus", [])) or "none"
        rows.append(
            [
                b,
                hw.get("cpu"),
                _fmt(hw.get("logical_cpus")),
                _fmt(hw.get("memory_gib"), 1),
                names,
                hw.get("kernel"),
            ]
        )
    lines += _table(["Part", "CPU", "Logical CPUs", "RAM GiB", "GPU", "Kernel"], rows)
    lines.append("")
    rows = [
        [b, ", ".join(f"{k} {v}" for k, v in sorted(v.items()))]
        for b, v in doc.get("software", {}).items()
    ]
    lines += _table(["Part", "Versions"], rows)
    lines.append("")
    for b, part in doc["benchmarks"].items():
        lines += [f"## {b}: {part['title']}", ""]
        lines += RENDER[b](part)
        if part.get("notes"):
            lines += ["Notes:", ""] + [f"- {n}" for n in part["notes"]] + [""]
    return "\n".join(lines).rstrip() + "\n"
