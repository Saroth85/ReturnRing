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
                "Scoring of every fit row with the early-stopping model of the largest size "
                "(DMatrix build included):",
                "",
            ]
            lines += _table(
                ["Device", "Batch rows", "Batch s (median)", "Single row p50 ms", "p95 ms", "n"],
                [
                    [
                        s["device"],
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
    speedups = details.get("speedups", [])
    if speedups:
        lines += ["Speedup (CPU median over GPU median, same data and parameters):", ""]
        lines += _table(
            ["Protocol", "Size", "Variant", "CPU / GPU"],
            [
                [s["protocol"], f"{s['fraction'] * 100:g}%", s["variant"], _fmt(s["cpu_over_gpu"])]
                for s in speedups
            ],
        )
        lines.append("")
    return lines


def _b2(part: Mapping[str, Any]) -> list[str]:
    lines = _header(part)
    records = part.get("details", {}).get("records", [])
    if records:
        lines += _table(
            [
                "Protocol",
                "Device",
                "Nodes",
                "Edges",
                "Epochs",
                "Train s",
                "Epoch s (median)",
                "F1",
                "Population",
            ],
            [
                [
                    r["protocol"],
                    r["device_requested"],
                    _fmt(r["nodes"]),
                    _fmt(r["edges"]),
                    _fmt(r["epochs"]),
                    _fmt(r["train_seconds"], 2),
                    _fmt(sorted(r["epoch_seconds"])[len(r["epoch_seconds"]) // 2], 3),
                    _fmt(r["f1"], 4),
                    r["population"],
                ]
                for r in records
            ],
        )
        lines.append("")
    interface = part.get("details", {}).get("interface")
    if interface:
        lines += [f"Interface B2 calls: `{interface}`.", ""]
    return lines


def _load_rows(loads: Mapping[str, Mapping[str, Any]], batch: bool) -> list[list[str]]:
    rows = []
    for level, s in sorted(loads.items(), key=lambda kv: int(kv[0])):
        lat = s["latency_ms"]
        row = [
            level,
            _fmt(s["decisions"]),
            _fmt(s["failed"]),
            _fmt(lat.get("p50"), 1),
            _fmt(lat.get("p95"), 1),
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
                "generation. The MI300X numbers come from `make bench DEVICE=gpu` on the droplet "
                '(infra/spikes/README.md, "Benchmarks B1-B4").',
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
