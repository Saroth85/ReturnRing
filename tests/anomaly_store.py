"""Hand-built sales for the product anomaly tests (US-208): frames shaped like
pipeline.anomalies.detect.sales_frame, and the same rows written as a store through
contracts.io.write_table.

Every value here is made up (origin: test fixture; nature: synthetic).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa

from contracts.io import Provenance, write_table
from contracts.parquet import SCHEMAS
from pipeline.anomalies.detect import week_start

FIXTURE = Provenance(
    origin="product anomaly test fixture (hand-built)",
    period_start=date(2025, 1, 1),
    period_end=date(2026, 12, 31),
    nature="synthetic",
)
W0 = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday


def at(days: float, hours: float = 0.0) -> datetime:
    return W0 + timedelta(days=days, hours=hours)


@dataclass(frozen=True)
class Sale:
    line_id: str
    variant_id: str
    category: str
    sold_at: datetime
    reason: str | None = None
    returned_after: timedelta | None = None  # from the sale to the instant the return is known
    recorded_after: timedelta = timedelta(0)  # from the sale to the instant the line is known

    @property
    def return_known_at(self) -> datetime | None:
        if self.returned_after is None:
            return None
        return self.sold_at + self.returned_after


def batch(
    prefix: str,
    variant_id: str,
    category: str,
    sold_at: datetime,
    n: int,
    returns: Iterable[tuple[str, float]] = (),
) -> list[Sale]:
    """n lines of one variant sold at one instant; the first ones returned, one per (reason,
    days from the sale to the instant the return is known)."""
    out = [Sale(f"{prefix}-{i:03d}", variant_id, category, sold_at) for i in range(n)]
    for i, (reason, days) in enumerate(returns):
        s = out[i]
        out[i] = Sale(s.line_id, s.variant_id, s.category, s.sold_at, reason, timedelta(days=days))
    return out


def frame(sales: Iterable[Sale]) -> pd.DataFrame:
    """The rows as detect.sales_frame returns them (returns known whenever they are known)."""
    rows = [
        {
            "line_id": s.line_id,
            "variant_id": s.variant_id,
            "category": s.category,
            "sold_at": s.sold_at,
            "line_known_at": s.sold_at + s.recorded_after,
            "reason": s.reason,
            "return_known_at": s.return_known_at,
        }
        for s in sales
    ]
    out = pd.DataFrame(rows)
    for column in ("sold_at", "line_known_at", "return_known_at"):
        out[column] = pd.to_datetime(out[column], utc=True).dt.as_unit("us")
    out["reason"] = out["reason"].astype(object)
    out["week"] = week_start(out["sold_at"])
    return out.sort_values("line_id", kind="stable").reset_index(drop=True)


def write_store(root: Path, sales: Iterable[Sale], provenance: Provenance = FIXTURE) -> Path:
    """store/variants, store/order_lines and store/returns holding `sales` (one order per line)."""
    sales = list(sales)
    listed = datetime.combine(provenance.period_start, time(), tzinfo=UTC)  # listed first
    variants = sorted({(s.variant_id, s.category) for s in sales})
    variant_rows = [
        {
            "variant_id": v,
            "product_id": f"P-{v}",
            "category": c,
            "brand": "Brand",
            "price_eur": Decimal("50.00"),
            "size_range": "XS-XL",
            "event_at": listed,
            "available_at": listed,
        }
        for v, c in variants
    ]
    line_rows = [
        {
            "line_id": s.line_id,
            "order_id": f"O-{s.line_id}",
            "variant_id": s.variant_id,
            "size": "M",
            "price_eur": Decimal("50.00"),
            "event_at": s.sold_at,
            "available_at": s.sold_at + s.recorded_after,
        }
        for s in sales
    ]
    return_rows = [
        {
            "return_id": f"R-{s.line_id}",
            "line_id": s.line_id,
            "reason": s.reason,
            "days_since_delivery": 1,
            "event_at": s.return_known_at - timedelta(minutes=2),
            "available_at": s.return_known_at,
        }
        for s in sales
        if s.return_known_at is not None
    ]
    for name, rows in (
        ("store/variants", variant_rows),
        ("store/order_lines", line_rows),
        ("store/returns", return_rows),
    ):
        write_table(name, pa.Table.from_pylist(rows, schema=SCHEMAS[name]), root, provenance)
    return root
