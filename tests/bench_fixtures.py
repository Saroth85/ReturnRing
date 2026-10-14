"""Fixtures of the benchmark tests (US-902): a hand-built scored store and a fake amd-smi.

The store holds six open returns, one per kind of decision (Q1 to Q4, insufficient evidence,
guarantee), on golden customer ids, written through contracts.io.write_table so every file is a
valid contract file. Every value is made up (origin: bench test fixture; nature: synthetic).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa

from contracts.io import Provenance, write_table
from contracts.parquet import SCHEMAS

FIXTURE = Provenance(
    origin="bench test fixture (hand-built)",
    period_start=date(2026, 1, 1),
    period_end=date(2026, 12, 31),
    nature="synthetic",
)
AS_OF = datetime(2026, 10, 1, tzinfo=UTC)
CONTEXT = datetime(2026, 9, 1, tzinfo=UTC)
RISK_THRESHOLD = 0.3
VALUE_THRESHOLD = Decimal("100.00")
# return id, customer, reason, path, risk, value, evidence, quadrant
RETURNS: tuple[tuple[str, str, str, str, float | None, str, str | None, str | None], ...] = (
    ("RET-1", "C-GIULIA", "size", "withdrawal", 0.1, "200.00", "sufficient", "Q1"),
    ("RET-2", "C-ROSSI-1", "size", "withdrawal", 0.1, "50.00", "sufficient", "Q2"),
    ("RET-3", "C-ROSSI-2", "changed_mind", "withdrawal", 0.8, "200.00", "sufficient", "Q3"),
    ("RET-4", "C-MARCO-1", "changed_mind", "withdrawal", 0.9, "20.00", "sufficient", "Q4"),
    ("RET-5", "C-NEW-1", "size", "withdrawal", 0.4, "10.00", "insufficient", None),
    ("RET-6", "C-ELENA", "not_as_described", "guarantee", None, "90.00", None, None),
)
CLUSTER = ("C-MARCO-1", "C-MARCO-2")


def eur(text: str) -> Decimal:
    return Decimal(text).quantize(Decimal("0.01"))


def _write(name: str, rows: list[dict[str, Any]], root: Path) -> None:
    write_table(name, pa.Table.from_pylist(rows, schema=SCHEMAS[name]), root, FIXTURE)


def _factors(cluster: bool) -> str:
    items = [
        {
            "feature": "returns_90d",
            "value": 5.0,
            "contribution": 0.8,
            "direction": "raises",
            "phrase_en": "Returns in the last 90 days: raises the score (not a cause)",
            "phrase_it": "Resi negli ultimi 90 giorni: alza il punteggio (non una causa)",
        },
        {
            "feature": "mean_line_price_eur",
            "value": 26.5,
            "contribution": -0.3,
            "direction": "lowers",
            "phrase_en": "Mean price of the items ordered: lowers the score (not a cause)",
            "phrase_it": "Prezzo medio: abbassa il punteggio (non una causa)",
        },
    ]
    if cluster:
        items.append(
            {
                "feature": "welcome_coupons_per_account",
                "value": 1.0,
                "contribution": 0.5,
                "direction": "raises",
                "phrase_en": "Welcome coupons per linked account: raises the score (not a cause)",
                "phrase_it": "Coupon di benvenuto per account collegato: alza il punteggio",
            }
        )
    return json.dumps(items, sort_keys=True)


def write_store(root: Path) -> Path:
    """The six open returns with every table bench.cases reads; returns root."""
    store: dict[str, list[dict[str, Any]]] = {name: [] for name in SCHEMAS}
    for i, (rid, customer, reason, path, risk, value, evidence, quadrant) in enumerate(RETURNS):
        ordered = datetime(2026, 9, 10, 12, tzinfo=UTC) + timedelta(hours=i)
        delivered = ordered + timedelta(days=4)
        decided = datetime(2026, 9, 27, 9, tzinfo=UTC) + timedelta(hours=i)
        order, line, terms = f"O-{i}", f"L-{i}", f"TS-{i}"
        store["store/terms_snapshots"].append(
            {
                "terms_snapshot_id": terms,
                "order_id": order,
                "source": "base",
                "quadrant_at_purchase": None,
                "return_window_days": 30,
                "free_return_shipping": True,
                "instant_refund": True,
                "free_size_exchange": True,
                "coupons_enabled": True,
                "event_at": ordered,
                "available_at": ordered,
            }
        )
        store["store/orders"].append(
            {
                "order_id": order,
                "customer_id": customer,
                "terms_snapshot_id": terms,
                "coupon_code": None,
                "total_eur": eur("59.90"),
                "event_at": ordered,
                "available_at": ordered,
            }
        )
        store["store/order_lines"].append(
            {
                "line_id": line,
                "order_id": order,
                "variant_id": "V-1",
                "size": "M",
                "price_eur": eur("59.90"),
                "event_at": ordered,
                "available_at": ordered,
            }
        )
        store["store/deliveries"].append(
            {"order_id": order, "event_at": delivered, "available_at": delivered}
        )
        store["store/returns"].append(
            {
                "return_id": rid,
                "line_id": line,
                "reason": reason,
                "days_since_delivery": (decided - delivered).days,
                "event_at": decided,
                "available_at": decided,
            }
        )
        missing = ["fewer than 3 orders observed"] if evidence == "insufficient" else []
        store["features/evidence_quality"].append(
            {
                "customer_id": customer,
                "evidence_quality": evidence or "sufficient",
                "n_observations": 1 if missing else 9,
                "n_observations_linked": None,
                "identifier_completeness": 0.8,
                "missing_identifier_kinds": [],
                "n_reasons": 2,
                "reason_coherence": 0.9,
                "missing_evidence": missing,
                "thresholds_version": "evidence-test",
                "as_of": CONTEXT,
            }
        )
        store["features/customer_value"].append(
            {
                "customer_id": customer,
                "value_eur": eur(value),
                "kept_eur": eur(value),
                "returned_eur": eur("0"),
                "discount_eur": eur("0"),
                "orders_total_eur": eur(value),
                "n_orders": 3,
                "n_lines": 4,
                "n_returns": 1,
                "window_start": CONTEXT - timedelta(days=365),
                "assumptions_version": "value-test",
                "as_of": CONTEXT,
            }
        )
        withdrawal = path == "withdrawal"
        store["scores/request_scores"].append(
            {
                "return_id": rid,
                "customer_id": customer,
                "decided_at": decided,
                "path": path,
                "context_as_of": CONTEXT if withdrawal else None,
                "risk": risk,
                "risk_threshold": RISK_THRESHOLD if withdrawal else None,
                "evidence_quality": evidence,
                "value_eur": eur(value) if withdrawal else None,
                "value_threshold_eur": VALUE_THRESHOLD if withdrawal else None,
                "quadrant": quadrant,
                "shap_top3": _factors(customer in CLUSTER) if withdrawal else None,
                "model_version": "risk-request-test" if withdrawal else None,
                "as_of": AS_OF,
            }
        )
    customers = sorted({r[1] for r in RETURNS} | set(CLUSTER))
    for customer in customers:
        clustered = customer in CLUSTER
        store["features/clusters"].append(
            {
                "customer_id": customer,
                "cluster_id": "CL-TEST" if clustered else None,
                "cluster_size": 2 if clustered else 1,
                "shared_strong_ids": 2 if clustered else 0,
                "welcome_coupons": 2 if clustered else 0,
                "n_distinct_cards": 1,
                "creation_spread_days": 3.0,
                "welcome_coupons_per_account": 1.0,
                "cluster_return_rate": 0.9,
                "strong_id_share": 1.0,
                "as_of": AS_OF,
            }
        )
    store["features/links"].append(
        {
            "customer_a": CLUSTER[0],
            "customer_b": CLUSTER[1],
            "kinds": ["device", "card"],
            "weight": 1.0,
            "weight_decayed": 0.9,
            "first_shared_at": CONTEXT,
            "last_shared_at": CONTEXT,
            "n_shared_events": 2,
            "max_sharers": 2,
            "disputed": False,
            "as_of": AS_OF,
        }
    )
    for name, rows in store.items():
        if rows:
            _write(name, rows, root)
    return root


FAKE_AMD_SMI = """\
import json, sys
if "--fail" in sys.argv:
    print("amd-smi: no GPU", file=sys.stderr)
    raise SystemExit(3)
print("WARNING: user is not in the render group")
print(json.dumps({"gpu_data": [{
    "gpu": 0,
    "usage": {"gfx_activity": {"value": 42, "unit": "%"}, "umc_activity": "N/A"},
    "power": {"socket_power": {"value": 350, "unit": "W"}},
    "mem_usage": {"total_vram": {"value": 196592, "unit": "MB"},
                  "used_vram": {"value": 108000, "unit": "MB"}},
}]}))
"""


def fake_amd_smi(tmp: Path, fail: bool = False) -> list[str]:
    """A command that prints what `amd-smi metric --json` prints (or fails like it)."""
    script = tmp / "fake_amd_smi.py"
    script.write_text(FAKE_AMD_SMI, encoding="utf-8")
    return [sys.executable, str(script), "metric", "--json", *(["--fail"] if fail else [])]
