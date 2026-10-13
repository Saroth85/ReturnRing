"""The serving snapshot in memory: read-only repositories of the API (ADR-16; rules 7 and 12).

The API never reads data/: it loads SERVING_DIR, written by python -m pipeline.serving, once at
startup, after checking every file against the manifest. 20,000 customers and 200,000 order
lines fit in memory as pandas frames. Everything the API writes goes to PostgreSQL.

Three reads matter for a decision (flow F1):

- request_score: the risk of a return request as scored when it arrived, from
  scores/request_scores (the batch, ADR-03), or scored here with the request model of the
  snapshot when the batch does not hold it (a request of the shop adapter): request features at
  the request instant (pipeline.features.request_features), the customer snapshot before it;
- customer_context: the customer snapshot strictly before the request (MOD-08): evidence quality,
  value and its threshold, the residual and the cluster; computed at the request instant from
  the store when no snapshot precedes it (a new account);
- the store rows of the return, its order, line, delivery and terms snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from contracts.io import Provenance, read_table
from ml.reference.model import LineModel
from ml.reference.persist import load_line_model
from ml.risk.data import latest_before
from ml.scoring.batch import Inputs, Models, read_inputs, score_requests, value_threshold
from pipeline.anomalies.detect import sales_frame
from pipeline.features.evidence import EVIDENCE_THRESHOLDS, evidence_quality
from pipeline.features.lines import line_frame
from pipeline.features.snapshot import StoreSnapshot, load_snapshot, utc
from pipeline.features.value import customer_value
from pipeline.generator.golden import GOLDEN_CUSTOMERS
from pipeline.serving.export import SERVED_TABLES, check_source
from pipeline.serving.manifest import Manifest, verify
from policy.engine import NAVIGLI_BASE_TERMS, Thresholds, choose_path
from policy.types import EvidenceQuality, Quadrant, ReturnReason, ReturnTerms
from policy.types import Path as ReturnPath

ONE_MICROSECOND: Final = timedelta(microseconds=1)
STORE_PROVENANCE: Final = "store/returns"
BASE_TERMS: Final = NAVIGLI_BASE_TERMS  # the merchant base policy (spec 4)


class ServingError(RuntimeError):
    """The serving snapshot is missing, changed since the export, or unreadable."""


def _ts(value: Any) -> datetime:
    return utc(pd.Timestamp(value).to_pydatetime())


def _opt_ts(value: Any) -> datetime | None:
    return None if value is None or pd.isna(value) else _ts(value)


def _row(frame: pd.DataFrame, mask: Any) -> dict[str, Any] | None:
    rows = frame.loc[mask]
    if rows.empty:
        return None
    if len(rows) > 1:
        raise ServingError(f"expected one row, found {len(rows)}")
    return {k: (None if _missing(v) else v) for k, v in rows.iloc[0].to_dict().items()}


def _missing(value: Any) -> bool:
    if isinstance(value, list | tuple | np.ndarray):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class ReturnRecord:
    """A return request as the store (or the shop adapter) knows it."""

    return_id: str
    line_id: str
    order_id: str
    customer_id: str
    variant_id: str
    reason: ReturnReason
    requested_at: datetime  # when the customer asked (the notice)
    known_at: datetime  # when the request became known to the shop: its decision time
    delivered_at: datetime
    terms_snapshot_id: str
    order_terms: ReturnTerms
    customer_text: str | None = None

    @property
    def notice_on(self) -> Any:
        return self.requested_at.date()

    @property
    def delivered_on(self) -> Any:
        return self.delivered_at.date()

    @property
    def path(self) -> ReturnPath:
        return choose_path(self.reason)  # rule 4: the path comes first


@dataclass(frozen=True)
class RequestScore:
    """The risk of one return request as known when it arrived (spec 8.4)."""

    return_id: str
    customer_id: str
    decided_at: datetime
    path: ReturnPath
    risk: float | None  # None on the guarantee path: never scored (rule 4)
    factors: tuple[Mapping[str, Any], ...]  # shap_top3: contributions, not causes
    model_version: str
    quadrant: Quadrant | None  # the batch's quadrant, for checks
    source: Literal["batch", "live"]


@dataclass(frozen=True)
class CustomerContext:
    """The customer snapshot a decision reads: the last one strictly before the request."""

    customer_id: str
    as_of: datetime
    live: bool  # computed at the request instant: no snapshot preceded it
    evidence: Mapping[str, Any]
    value: Mapping[str, Any]
    value_threshold: Decimal
    features: Mapping[str, Any] | None  # features/customer_features (residual), None when live
    cluster: Mapping[str, Any] | None  # features/clusters row of a cluster of 2 accounts or more

    @property
    def evidence_quality(self) -> EvidenceQuality:
        return EvidenceQuality(str(self.evidence["evidence_quality"]))

    @property
    def missing_evidence(self) -> list[str]:
        return [str(x) for x in self.evidence["missing_evidence"]]

    @property
    def value_eur(self) -> Decimal:
        return Decimal(str(self.value["value_eur"]))

    @property
    def cluster_id(self) -> str | None:
        return None if self.cluster is None else str(self.cluster["cluster_id"])


def order_golden_first(return_ids: Iterable[str], customers: Mapping[str, str]) -> list[str]:
    """Golden cases first (US-104, US-903), in the order of GOLDEN_CUSTOMERS; then by id."""
    rank = {c: i for i, c in enumerate(GOLDEN_CUSTOMERS)}
    late = len(rank)
    return sorted(return_ids, key=lambda r: (rank.get(customers.get(r, ""), late), r))


@dataclass
class Serving:
    """The serving snapshot loaded in memory (read only)."""

    root: Path
    manifest: Manifest
    frames: dict[str, pd.DataFrame]
    provenance: dict[str, Provenance]
    store: StoreSnapshot
    models: Models
    checkout_model: LineModel
    _live: dict[str, RequestScore] = field(default_factory=dict)
    _at_cache: dict[str, dict[pd.Timestamp, pd.DataFrame]] = field(default_factory=dict)
    _thresholds: dict[pd.Timestamp, Decimal] = field(default_factory=dict)

    # ---- loading -------------------------------------------------------------------------------

    @classmethod
    def load(cls, root: Path, *, check: bool = True) -> Serving:
        root = Path(root)
        try:
            check_source(root)  # rule 12: never a folder of the offline labels
        except ValueError as exc:
            raise ServingError(str(exc)) from None
        if check:
            problems = verify(root)
            if problems:
                raise ServingError(f"{root}: " + "; ".join(problems[:5]))
        manifest = Manifest.read(root)
        frames: dict[str, pd.DataFrame] = {}
        provenance: dict[str, Provenance] = {}
        for name in SERVED_TABLES:
            table, prov = read_table(name, root)
            frames[name] = table.to_pandas()
            provenance[name] = prov
        as_of = utc(manifest.as_of)
        return cls(
            root=root,
            manifest=manifest,
            frames=frames,
            provenance=provenance,
            store=load_snapshot(root, as_of),
            models=Models.load(root / "models" / "risk"),
            checkout_model=load_line_model(root / "models" / "checkout", "personalized"),
        )

    def warm(self) -> None:
        """Compute the derived frames the case card reads (the first card is then as fast as
        the others): the line frame, the lines by customer and the sales frame."""
        _ = self._lines_by_customer, self.sales, self._keys

    @property
    def as_of(self) -> datetime:
        return utc(self.manifest.as_of)

    def model_versions(self) -> dict[str, str]:
        return self.manifest.model_versions()

    def frame(self, name: str) -> pd.DataFrame:
        return self.frames[name]

    @cached_property
    def _keys(self) -> dict[str, pd.DataFrame]:
        """Store tables indexed by their key, for lookups by id."""
        keys = {
            "store/returns": "return_id",
            "store/order_lines": "line_id",
            "store/orders": "order_id",
            "store/deliveries": "order_id",
            "store/terms_snapshots": "terms_snapshot_id",
            "store/variants": "variant_id",
            "scores/request_scores": "return_id",
        }
        return {name: self.frames[name].set_index(key, drop=False) for name, key in keys.items()}

    def by_key(self, name: str, key: str) -> dict[str, Any] | None:
        """The row of a store table (or of scores/request_scores) by its key, if any."""
        frame = self._keys[name]
        if key not in frame.index:
            return None
        row = frame.loc[key]
        if isinstance(row, pd.DataFrame):
            raise ServingError(f"{name}: key {key} is not unique")
        return {k: (None if _missing(v) else v) for k, v in row.to_dict().items()}

    def at(self, name: str, as_of: datetime) -> pd.DataFrame:
        """The rows of an as-of table computed at `as_of`, indexed by customer_id."""
        cache = self._at_cache.setdefault(name, {})
        stamp = pd.Timestamp(as_of)
        if stamp not in cache:
            frame = self.frames[name]
            cache[stamp] = frame.loc[frame["as_of"] == stamp].set_index("customer_id", drop=False)
        return cache[stamp]

    @cached_property
    def lines(self) -> pd.DataFrame:
        """pipeline.features.lines.line_frame of the store at the snapshot instant."""
        return line_frame(self.store)

    @cached_property
    def _lines_by_customer(self) -> dict[str, pd.DataFrame]:
        return {str(c): g for c, g in self.lines.groupby("customer_id", sort=False)}

    def customer_lines(self, customer_id: str) -> pd.DataFrame:
        """The rows of `lines` of one customer (an empty frame for a customer without any)."""
        return self._lines_by_customer.get(customer_id, self.lines.iloc[0:0])

    @cached_property
    def sales(self) -> pd.DataFrame:
        """pipeline.anomalies.detect.sales_frame of the store at the snapshot instant."""
        return sales_frame(self.store)

    @cached_property
    def _inputs(self) -> Inputs:
        return read_inputs(self.root, self.as_of)

    @cached_property
    def feature_instants(self) -> list[datetime]:
        frame = self.frames["features/customer_features"]
        return sorted(_ts(t) for t in pd.unique(frame["as_of"]))

    @cached_property
    def display_names(self) -> dict[str, str | None]:
        customers = self.frames["store/customers"]
        return {
            str(c): (None if _missing(n) else str(n))
            for c, n in zip(customers["customer_id"], customers["display_name"], strict=True)
        }

    # ---- returns ------------------------------------------------------------------------------

    def open_return_ids(self) -> list[str]:
        """The open returns of the batch scoring (scores/request_scores), golden cases first."""
        req = self.frames["scores/request_scores"]
        owners = dict(zip(req["return_id"], req["customer_id"], strict=True))
        return order_golden_first(owners, owners)

    def store_return(self, return_id: str) -> ReturnRecord | None:
        """The store's return request `return_id`, with its line, order, delivery and terms."""
        ret = self.by_key("store/returns", return_id)
        if ret is None:
            return None
        line = self.by_key("store/order_lines", str(ret["line_id"]))
        if line is None:
            raise ServingError(f"{return_id}: line {ret['line_id']} not in the store")
        return self._record(
            return_id,
            line,
            ReturnReason(str(ret["reason"])),
            _ts(ret["event_at"]),
            _ts(ret["available_at"]),
        )

    def adapter_return(
        self,
        return_id: str,
        line_id: str,
        reason: ReturnReason,
        requested_at: datetime,
        customer_text: str | None = None,
    ) -> ReturnRecord:
        """A return request of the simulated shop adapter on a store line (flow F1, step 1)."""
        line = self.by_key("store/order_lines", line_id)
        if line is None:
            raise KeyError(f"order line {line_id} is not in the store")
        at = utc(requested_at)
        return self._record(return_id, line, reason, at, at, customer_text)

    def _record(
        self,
        return_id: str,
        line: Mapping[str, Any],
        reason: ReturnReason,
        requested_at: datetime,
        known_at: datetime,
        customer_text: str | None = None,
    ) -> ReturnRecord:
        order = self.by_key("store/orders", str(line["order_id"]))
        delivery = self.by_key("store/deliveries", str(line["order_id"]))
        if order is None or delivery is None:
            raise ServingError(f"{return_id}: order or delivery of {line['line_id']} unknown")
        if _ts(delivery["available_at"]) > known_at:
            raise ValueError(f"{return_id}: the order was not delivered when the return arrived")
        terms = self.by_key("store/terms_snapshots", str(order["terms_snapshot_id"]))
        if terms is None:
            raise ServingError(f"{return_id}: terms snapshot of {order['order_id']} unknown")
        return ReturnRecord(
            return_id=return_id,
            line_id=str(line["line_id"]),
            order_id=str(order["order_id"]),
            customer_id=str(order["customer_id"]),
            variant_id=str(line["variant_id"]),
            reason=reason,
            requested_at=requested_at,
            known_at=known_at,
            delivered_at=_ts(delivery["event_at"]),
            terms_snapshot_id=str(terms["terms_snapshot_id"]),
            order_terms=ReturnTerms(
                return_window_days=int(terms["return_window_days"]),
                free_return_shipping=bool(terms["free_return_shipping"]),
                instant_refund=bool(terms["instant_refund"]),
                free_size_exchange=bool(terms["free_size_exchange"]),
                coupons_enabled=bool(terms["coupons_enabled"]),
            ),
            customer_text=customer_text,
        )

    # ---- scores -------------------------------------------------------------------------------

    def batch_score(self, return_id: str) -> RequestScore | None:
        row = self.by_key("scores/request_scores", return_id)
        if row is None:
            return None
        path = ReturnPath(str(row["path"]))
        factors = tuple(json.loads(row["shap_top3"])) if row["shap_top3"] else ()
        quadrant = Quadrant(str(row["quadrant"])) if row["quadrant"] else None
        return RequestScore(
            return_id=return_id,
            customer_id=str(row["customer_id"]),
            decided_at=_ts(row["decided_at"]),
            path=path,
            risk=None if row["risk"] is None else float(row["risk"]),
            factors=factors,
            model_version=str(row["model_version"]),
            quadrant=quadrant,
            source="batch",
        )

    def request_score(self, record: ReturnRecord) -> RequestScore:
        """The score of a return request: the batch's, else scored here with the request model
        of the snapshot, as known when the request arrived (the same code as the batch)."""
        found = self.batch_score(record.return_id)
        if found is not None:
            return found
        if record.return_id not in self._live:
            self._live[record.return_id] = self._score_live(record)
        return self._live[record.return_id]

    def _snapshot_with(self, record: ReturnRecord) -> StoreSnapshot:
        """The store snapshot that knows `record` (added when the store does not hold it)."""
        returns = self.store["store/returns"]
        if (returns["return_id"] == record.return_id).any():
            return self.store
        dtype = returns["available_at"].dtype
        row = pd.DataFrame(
            {
                "return_id": [record.return_id],
                "line_id": [record.line_id],
                "reason": [record.reason.value],
                "days_since_delivery": [(record.notice_on - record.delivered_on).days],
                "event_at": pd.Series([pd.Timestamp(record.requested_at)]).astype(dtype),
                "available_at": pd.Series([pd.Timestamp(record.known_at)]).astype(dtype),
            }
        ).astype({"days_since_delivery": returns["days_since_delivery"].dtype})
        frames = dict(self.store.frames)
        frames["store/returns"] = pd.concat([returns, row], ignore_index=True)
        at = max(self.store.as_of, record.known_at)
        return StoreSnapshot(at, frames, self.store.provenance)

    def _score_live(self, record: ReturnRecord) -> RequestScore:
        snap = self._snapshot_with(record)
        rows = score_requests(self.models, self._inputs, snap, [record.return_id], snap.as_of)
        row = rows.iloc[0].to_dict()
        path = ReturnPath(str(row["path"]))
        risk = row["risk"]
        shap = row["shap_top3"]
        quadrant = row["quadrant"]
        return RequestScore(
            return_id=record.return_id,
            customer_id=str(row["customer_id"]),
            decided_at=_ts(row["decided_at"]),
            path=path,
            risk=None if _missing(risk) else float(risk),
            factors=tuple(json.loads(shap)) if isinstance(shap, str) else (),
            model_version=str(row["model_version"]),
            quadrant=None if _missing(quadrant) else Quadrant(str(quadrant)),
            source="live",
        )

    # ---- customer snapshot before a request ----------------------------------------------------

    def context_instant(self, before: datetime) -> datetime | None:
        """The last customer snapshot strictly before `before` (MOD-08), if any."""
        found = latest_before(pd.Series([pd.Timestamp(before)]), self.feature_instants).iloc[0]
        return None if pd.isna(found) else _ts(found)

    def _at(self, name: str, customer_id: str, as_of: datetime) -> dict[str, Any] | None:
        frame = self.at(name, as_of)
        if customer_id not in frame.index:
            return None
        return {
            k: (None if _missing(v) else v) for k, v in frame.loc[customer_id].to_dict().items()
        }

    def value_threshold(self, as_of: datetime) -> Decimal:
        """The 70th percentile of value at a customer snapshot (ml.scoring.batch)."""
        stamp = pd.Timestamp(as_of)
        if stamp not in self._thresholds:
            self._thresholds[stamp] = value_threshold(self.frames["features/customer_value"], as_of)
        return self._thresholds[stamp]

    def customer_context(self, customer_id: str, before: datetime) -> CustomerContext:
        instant = self.context_instant(before)
        if instant is None:
            return self._live_context(customer_id, utc(before) - ONE_MICROSECOND)
        evidence = self._at("features/evidence_quality", customer_id, instant)
        value = self._at("features/customer_value", customer_id, instant)
        if evidence is None or value is None:  # an account opened after the snapshot
            return self._live_context(customer_id, utc(before) - ONE_MICROSECOND)
        cluster = self._at("features/clusters", customer_id, instant)
        if cluster is not None and (cluster["cluster_id"] is None or cluster["cluster_size"] < 2):
            cluster = None
        return CustomerContext(
            customer_id=customer_id,
            as_of=instant,
            live=False,
            evidence=evidence,
            value=value,
            value_threshold=self.value_threshold(instant),
            features=self._at("features/customer_features", customer_id, instant),
            cluster=cluster,
        )

    def _customer_store(self, customer_id: str, at: datetime) -> StoreSnapshot:
        """The store rows of one customer known at `at` (and the whole catalogue)."""
        frames = self.store.frames
        orders = frames["store/orders"]
        mine = orders.loc[orders["customer_id"] == customer_id]
        order_ids = set(mine["order_id"])
        lines = frames["store/order_lines"]
        mine_lines = lines.loc[lines["order_id"].isin(order_ids)]
        returns = frames["store/returns"]
        snapshots = frames["store/terms_snapshots"]
        own = {
            "store/customers": frames["store/customers"].loc[
                frames["store/customers"]["customer_id"] == customer_id
            ],
            "store/identifiers": frames["store/identifiers"].loc[
                frames["store/identifiers"]["customer_id"] == customer_id
            ],
            "store/variants": frames["store/variants"],
            "store/terms_snapshots": snapshots.loc[snapshots["order_id"].isin(order_ids)],
            "store/orders": mine,
            "store/order_lines": mine_lines,
            "store/deliveries": frames["store/deliveries"].loc[
                frames["store/deliveries"]["order_id"].isin(order_ids)
            ],
            "store/returns": returns.loc[returns["line_id"].isin(set(mine_lines["line_id"]))],
            "store/coupons": frames["store/coupons"].loc[
                frames["store/coupons"]["customer_id"] == customer_id
            ],
        }
        snap = StoreSnapshot(self.store.as_of, own, self.store.provenance)
        return snap.restrict(at)

    def _live_context(self, customer_id: str, at: datetime) -> CustomerContext:
        """Evidence and value of one customer computed from the store rows known at `at`
        (pipeline.features), for an account with no snapshot before the request; the value
        threshold of the last snapshot before it (or of the first one), and no cluster (the
        request model saw none either)."""
        snap = self._customer_store(customer_id, at)
        evidence = evidence_quality(snap, line_frame(snap), EVIDENCE_THRESHOLDS)
        values = customer_value(snap)
        ev = _row(evidence, evidence["customer_id"] == customer_id)
        val = _row(values, values["customer_id"] == customer_id)
        if ev is None or val is None:
            raise ServingError(f"{customer_id} is not a customer at {at.isoformat()}")
        instant = self.context_instant(at) or self.feature_instants[0]
        stamp = pd.Timestamp(at)
        return CustomerContext(
            customer_id=customer_id,
            as_of=at,
            live=True,
            evidence={**ev, "as_of": stamp},
            value={**val, "as_of": stamp},
            value_threshold=self.value_threshold(instant),
            features=None,
            cluster=None,
        )

    def thresholds(self, context: CustomerContext) -> Thresholds:
        """The thresholds of spec 8.6 for a request: the request model's risk threshold
        (precision >= 0.8 on validation; none reached: 1.0 flags no one), the 70th percentile
        of value at the customer snapshot (ml.scoring.batch)."""
        risk = self.models.request.threshold
        return Thresholds(risk=risk if risk is not None else 1.0, value_eur=context.value_threshold)

    # ---- graph ---------------------------------------------------------------------------------

    def cluster_members(self, cluster_id: str, as_of: datetime) -> list[str]:
        frame = self.frames["features/clusters"]
        rows = frame.loc[
            (frame["as_of"] == pd.Timestamp(as_of)) & (frame["cluster_id"] == cluster_id)
        ]
        return sorted(str(c) for c in rows["customer_id"])

    def cluster_links(self, members: Sequence[str], as_of: datetime) -> pd.DataFrame:
        frame = self.frames["features/links"]
        inside = frame["customer_a"].isin(members) & frame["customer_b"].isin(members)
        rows = frame.loc[(frame["as_of"] == pd.Timestamp(as_of)) & inside]
        return rows.sort_values(["customer_a", "customer_b"]).reset_index(drop=True)
