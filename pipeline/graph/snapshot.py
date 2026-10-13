"""The store as known at a decision time: the only input of the account graph (rule 7, MOD-08).

load_snapshot reads every store table with contracts.io.read_table(..., as_of=T), which keeps the
rows with available_at <= T. Nothing else is read: no ground truth (rule 12), no price or amount
(customer value never feeds risk, rule 6). The frames are small projections, in polars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc

from contracts.io import Provenance, read_table
from contracts.parquet import SCHEMAS
from policy.types import NON_CONFORMITY_REASONS

SNAPSHOT_TABLES = (
    "store/customers",
    "store/identifiers",
    "store/coupons",
    "store/orders",
    "store/order_lines",
    "store/deliveries",
    "store/returns",
)
_GUARANTEE = sorted(r.value for r in NON_CONFORMITY_REASONS)
US_PER_DAY = 86_400_000_000


def check_as_of(as_of: datetime) -> datetime:
    """A decision time is timezone-aware; it is handled in UTC."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    return as_of.astimezone(UTC)


@dataclass(frozen=True)
class StoreSnapshot:
    """What the store knew at `as_of`.

    customers: customer_id, created_at.
    identifiers: customer_id, kind, value_hash, used_at (first use by the account).
    welcome_redemptions: customer_id, coupon_code: welcome coupons redeemed.
    lines: customer_id, line_id, withdrawal_return, guarantee_return: delivered order lines, with
        a return on the withdrawal path or on the guarantee path (non-conformity, rule 4).
    provenance: of every table read, by contract name.
    """

    as_of: datetime
    customers: pl.DataFrame
    identifiers: pl.DataFrame
    welcome_redemptions: pl.DataFrame
    lines: pl.DataFrame
    provenance: dict[str, Provenance]

    def period(self) -> tuple[date, date]:
        """The period the snapshot covers: from the oldest input to the decision day, at most
        the end of the store period."""
        end = min(max(p.period_end for p in self.provenance.values()), self.as_of.date())
        start = min(min(p.period_start for p in self.provenance.values()), end)
        return start, end


def _frame(table: pa.Table, columns: list[str]) -> pl.DataFrame:
    out = pl.from_arrow(table.select(columns))
    assert isinstance(out, pl.DataFrame)
    return out


def load_snapshot(root: Path | str, as_of: datetime) -> StoreSnapshot:
    """Read <root>/store/*.parquet as known at as_of (available_at <= as_of)."""
    as_of = check_as_of(as_of)
    tables: dict[str, pa.Table] = {}
    provenance: dict[str, Provenance] = {}
    for name in SNAPSHOT_TABLES:
        tables[name], provenance[name] = read_table(name, root, as_of=as_of)
    return snapshot_from_tables(tables, as_of, provenance)


def snapshot_from_tables(
    tables: dict[str, pa.Table], as_of: datetime, provenance: dict[str, Provenance]
) -> StoreSnapshot:
    """Build the snapshot from store tables already filtered at as_of by read_table."""
    as_of = check_as_of(as_of)
    for name in SNAPSHOT_TABLES:
        if not tables[name].schema.remove_metadata().equals(SCHEMAS[name]):
            raise ValueError(f"{name}: not the contract schema")
        late = tables[name].filter(
            pc.greater(tables[name].column("available_at"), pa.scalar(as_of))
        )
        if late.num_rows:
            raise ValueError(f"{name}: {late.num_rows} rows available after as_of (rule 7)")
    customers = _frame(tables["store/customers"], ["customer_id", "event_at"]).rename(
        {"event_at": "created_at"}
    )
    identifiers = (
        _frame(tables["store/identifiers"], ["customer_id", "kind", "value_hash", "event_at"])
        .rename({"event_at": "used_at"})
        .group_by("customer_id", "kind", "value_hash")
        .agg(pl.col("used_at").min())
        .sort("customer_id", "kind", "value_hash")
    )
    unknown = identifiers.join(customers, on="customer_id", how="anti")
    if unknown.height:
        raise ValueError(
            f"store/identifiers: {unknown.height} rows of accounts not in store/customers at as_of"
        )
    coupons = _frame(tables["store/coupons"], ["coupon_code", "customer_id", "kind", "order_id"])
    welcome = (
        coupons.filter((pl.col("kind") == "welcome") & pl.col("order_id").is_not_null())
        .select("customer_id", "coupon_code")
        .unique()
        .sort("customer_id", "coupon_code")
    )
    orders = _frame(tables["store/orders"], ["order_id", "customer_id"])
    delivered = _frame(tables["store/deliveries"], ["order_id"]).unique()
    order_lines = _frame(tables["store/order_lines"], ["line_id", "order_id"])
    returns = (
        _frame(tables["store/returns"], ["line_id", "reason"])
        .group_by("line_id")
        .agg(
            pl.col("reason").is_in(_GUARANTEE).any().alias("guarantee_return"),
            (~pl.col("reason").is_in(_GUARANTEE)).any().alias("any_withdrawal"),
        )
    )
    lines = (
        order_lines.join(orders, on="order_id", how="inner")
        .join(delivered, on="order_id", how="semi")
        .join(returns, on="line_id", how="left")
        .with_columns(
            pl.col("guarantee_return").fill_null(False),
            # A line returned on the guarantee path is not a withdrawal return (rule 4).
            (
                pl.col("any_withdrawal").fill_null(False)
                & ~pl.col("guarantee_return").fill_null(False)
            ).alias("withdrawal_return"),
        )
        .select("customer_id", "line_id", "withdrawal_return", "guarantee_return")
        .sort("line_id")
    )
    return StoreSnapshot(
        as_of=as_of,
        customers=customers.sort("customer_id"),
        identifiers=identifiers,
        welcome_redemptions=welcome,
        lines=lines,
        provenance=provenance,
    )
