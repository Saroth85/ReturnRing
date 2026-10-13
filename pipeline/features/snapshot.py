"""Store snapshots at a decision time (rule 7, NFR-15, MOD-08; ADR-13).

A snapshot holds the store rows available at `as_of`, read with
contracts.io.read_table(name, root, as_of=as_of): every feature of pipeline/features is computed
from a snapshot, so no feature can use an event whose available_at is after the decision time.
`restrict` narrows a snapshot to an earlier instant with the same filter, so one read serves several
decision times.

Columns that may never feed a feature are not loaded (rule 6): display names and identifier hashes.
Amounts are integer cents (`<column>_cents`), so sums reconcile exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, read_table
from contracts.parquet import EUR, is_forbidden_feature

_TIMES = ("event_at", "available_at")

# The columns a feature may read, per store table. value_hash and display_name are absent on
# purpose: they are forbidden features (contracts.parquet.is_forbidden_feature).
COLUMNS: Mapping[str, tuple[str, ...]] = {
    "store/customers": ("customer_id",),
    "store/identifiers": ("customer_id", "kind"),
    "store/variants": ("variant_id", "product_id", "category", "price_eur", "size_range"),
    "store/terms_snapshots": ("terms_snapshot_id", "order_id", "return_window_days"),
    "store/orders": ("order_id", "customer_id", "terms_snapshot_id", "coupon_code", "total_eur"),
    "store/order_lines": ("line_id", "order_id", "variant_id", "size", "price_eur"),
    "store/deliveries": ("order_id",),
    "store/returns": ("return_id", "line_id", "reason", "days_since_delivery"),
    "store/coupons": ("coupon_code", "customer_id", "kind", "discount_eur", "order_id"),
}
TABLES = tuple(COLUMNS)
_READ_FORBIDDEN = [c for cols in COLUMNS.values() for c in cols if is_forbidden_feature(c)]
if _READ_FORBIDDEN:  # pragma: no cover - a guard on the constant above
    raise RuntimeError(f"forbidden columns in the feature reads: {_READ_FORBIDDEN}")


def utc(moment: datetime) -> datetime:
    """The same instant in UTC; refuses a naive datetime (ambiguous decision time)."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"decision times must be timezone-aware, got {moment!r}")
    return moment.astimezone(UTC)


def _cents(column: pa.ChunkedArray) -> pa.ChunkedArray:
    scaled = pc.multiply(column, pa.scalar(Decimal(100), pa.decimal128(3, 0)))
    return pc.cast(scaled, pa.int64())


def to_frame(name: str, table: pa.Table) -> pd.DataFrame:
    """The readable columns of a store table as a DataFrame; EUR amounts become int64 cents."""
    table = table.select([*COLUMNS[name], *_TIMES])
    for field in table.schema:
        if field.type.equals(EUR):
            i = table.schema.get_field_index(field.name)
            table = table.set_column(i, f"{field.name[:-4]}_cents", _cents(table.column(i)))
    return table.to_pandas()


@dataclass(frozen=True)
class StoreSnapshot:
    """The store as known at `as_of`: rows with available_at <= as_of only."""

    as_of: datetime
    frames: Mapping[str, pd.DataFrame]
    provenance: Mapping[str, Provenance]

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.frames[name]

    def restrict(self, as_of: datetime) -> StoreSnapshot:
        """The snapshot at an earlier instant: the same filter read_table(as_of=...) applies."""
        as_of = utc(as_of)
        if as_of > self.as_of:
            raise ValueError(
                f"cannot restrict a snapshot at {self.as_of.isoformat()} to the later instant "
                f"{as_of.isoformat()}: the rows after it were never read"
            )
        limit = pd.Timestamp(as_of)
        frames = {
            name: frame.loc[frame["available_at"] <= limit].reset_index(drop=True)
            for name, frame in self.frames.items()
        }
        return StoreSnapshot(as_of, frames, self.provenance)


def load_snapshot(root: Path | str, as_of: datetime) -> StoreSnapshot:
    """Read every store table under <root>/store available at `as_of` (rule 7)."""
    as_of = utc(as_of)
    frames: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Provenance] = {}
    for name in TABLES:
        table, prov = read_table(name, root, as_of=as_of)
        frames[name] = to_frame(name, table)
        provenance[name] = prov
    return StoreSnapshot(as_of, frames, provenance)


def month_ends(first: datetime, last: datetime) -> list[datetime]:
    """The instants 00:00 UTC of the first day of each month in (first, last]: the end of each
    month whose last day lies in the period, for example 2026-07-01T00:00Z for June 2026."""
    first, last = utc(first), utc(last)
    out: list[datetime] = []
    year, month = first.year, first.month
    while True:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        instant = datetime(year, month, 1, tzinfo=UTC)
        if instant > last:
            return out
        out.append(instant)
