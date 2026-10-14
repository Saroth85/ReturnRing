"""Evidence quality, measured apart from risk (US-209; US-307; spec 8.6 "Dati insufficienti").

Three components, each computed from what is known at the decision time T:

- observations: the account's own order lines with a settled withdrawal outcome
  (pipeline.features.lines), the sample behind the return rate and the residual. With the account
  graph (features/clusters at the same as_of), n_observations_linked reports the observations of
  the linked accounts too; it is never counted towards the threshold (review F14: counted, it let
  an account with no settled line of its own into Q4, and the FAIR-03 counterfactual, which keeps
  the evidence fixed, could not see it);
- identifier completeness: the share of the identifier weight of spec 8.3 (card 1.0, e-mail 0.9,
  device 0.8, address 0.6, phone 0.6, IP prefix 0.2) recorded for the customer. Without
  identifiers the graph cannot see links, so the absence of a link is not evidence;
- reason coherence: the share of the withdrawal returns whose stated reason is specific and fits
  the item (size, changed mind, not as expected; a size reason only on a sized item). Vague
  reasons make the reason shares uninformative. Non-conformity returns are not judged here: they
  follow the guarantee path (rule 4).

The outcome is insufficient when any component is below its threshold, with one plain sentence
per missing piece of evidence (US-307). An insufficient case gets standard terms: it is never
treated as safe or as suspicious. The thresholds are design values of D-15, versioned in
EVIDENCE_THRESHOLDS; docs/results/customer-quantities.md measures them on the generated store.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import numpy as np
import pandas as pd
import pyarrow as pa

from contracts.parquet import IDENTIFIER_KINDS, SCHEMAS
from pipeline.features.snapshot import StoreSnapshot
from policy.types import EvidenceQuality, ReturnReason

# Spec 8.3 identifier weights w_k (the account graph uses the same values).
IDENTIFIER_WEIGHTS: Final[Mapping[str, float]] = {
    "card": 1.0,
    "email": 0.9,
    "device": 0.8,
    "address": 0.6,
    "phone": 0.6,
    "ip_prefix": 0.2,
}
SPECIFIC_REASONS: Final = frozenset(
    {ReturnReason.SIZE.value, ReturnReason.CHANGED_MIND.value, ReturnReason.NOT_AS_EXPECTED.value}
)


@dataclass(frozen=True)
class EvidenceThresholds:
    """Design thresholds of D-15, versioned: a change of value is a change of version."""

    version: str
    min_observations: int
    min_identifier_completeness: float
    min_reason_coherence: float
    min_reasons: int  # the reasons are judged from this many withdrawal returns on

    def __post_init__(self) -> None:
        if self.min_observations < 1 or self.min_reasons < 1:
            raise ValueError("minimum counts must be at least 1")
        for name in ("min_identifier_completeness", "min_reason_coherence"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must lie in (0, 1]")


EVIDENCE_THRESHOLDS: Final = EvidenceThresholds(
    version="evidence-v1-2026-10-13",
    min_observations=4,
    min_identifier_completeness=0.6,
    min_reason_coherence=0.5,
    min_reasons=2,
)


def evidence_quality(
    snap: StoreSnapshot,
    lines: pd.DataFrame,
    thresholds: EvidenceThresholds = EVIDENCE_THRESHOLDS,
    clusters: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per customer known at snap.as_of. `lines` is line_frame(snap); `clusters`
    (customer_id, cluster_id) is the account graph at the same instant, if known."""
    th = thresholds
    customers = pd.Index(sorted(set(snap["store/customers"]["customer_id"])), name="customer_id")
    observed = lines.loc[lines["observed"]].groupby("customer_id").size()
    n_obs = observed.reindex(customers, fill_value=0).astype(np.int64)

    linked: pd.Series | None = None
    if clusters is not None:
        cl = clusters.set_index("customer_id")["cluster_id"].reindex(customers)
        frame = pd.DataFrame({"cluster_id": cl, "n": n_obs})
        sums = frame.dropna(subset=["cluster_id"]).groupby("cluster_id")["n"].sum()
        linked = cl.map(sums).fillna(n_obs).astype(np.int64)

    ids = snap["store/identifiers"]
    total = sum(IDENTIFIER_WEIGHTS.values())
    kinds: dict[str, set[str]] = {}
    for cid, kind in zip(ids["customer_id"], ids["kind"], strict=True):
        kinds.setdefault(cid, set()).add(kind)

    withdrawal = lines.loc[lines["withdrawal_return"]]
    specific = withdrawal["reason"].isin(SPECIFIC_REASONS)
    fits = (withdrawal["reason"] != ReturnReason.SIZE.value) | withdrawal["size"].notna()
    coherent = (specific & fits).groupby(withdrawal["customer_id"]).agg(["size", "sum"])
    coherent = coherent.reindex(customers, fill_value=0)
    reasons = dict(zip(customers, coherent["size"].astype(int), strict=True))
    coherent_reasons = dict(zip(customers, coherent["sum"].astype(int), strict=True))
    observations = dict(zip(customers, n_obs.astype(int), strict=True))
    with_links = None if linked is None else dict(zip(customers, linked.astype(int), strict=True))

    rows = []
    for cid in customers:
        seen = kinds.get(cid, set())
        missing_kinds = [k for k in IDENTIFIER_KINDS if k not in seen]
        completeness = sum(IDENTIFIER_WEIGHTS[k] for k in IDENTIFIER_KINDS if k in seen) / total
        n_reasons = reasons[cid]
        n_coherent = coherent_reasons[cid]
        coherence = n_coherent / n_reasons if n_reasons else None
        n = observations[cid]
        n_linked = None if with_links is None else with_links[cid]

        missing: list[str] = []
        # The account's own settled lines only (review F14): the lines of linked accounts are
        # reported, never counted, so a new account in a cluster is never judged on others.
        # The sentences hold no number and no date: an LLM case quotes them (llm.case requires
        # number-free items, ADR-14 decision 11); the counts, thresholds and the decision time
        # are the table's own columns and its provenance, shown next to the sentence.
        if n < th.min_observations:
            more = (
                " (the linked accounts have more)"
                if n_linked is not None and n_linked >= th.min_observations
                else ""
            )
            missing.append(
                f"observations: too few order lines with a settled outcome for this account{more}"
            )
        if completeness < th.min_identifier_completeness:
            missing.append(f"identifiers: no {', '.join(missing_kinds)} recorded")
        if (
            coherence is not None
            and n_reasons >= th.min_reasons
            and coherence < th.min_reason_coherence
        ):
            missing.append(
                "return reasons: most returns give no specific reason that fits the item"
            )
        quality = EvidenceQuality.INSUFFICIENT if missing else EvidenceQuality.SUFFICIENT
        rows.append(
            {
                "customer_id": cid,
                "evidence_quality": quality.value,
                "n_observations": n,
                "n_observations_linked": n_linked,
                "identifier_completeness": completeness,
                "missing_identifier_kinds": missing_kinds,
                "n_reasons": n_reasons,
                "reason_coherence": coherence,
                "missing_evidence": missing,
                "thresholds_version": th.version,
            }
        )
    columns = [n for n in SCHEMAS["features/evidence_quality"].names if n != "as_of"]
    out = pd.DataFrame(rows, columns=columns)
    out["n_observations_linked"] = out["n_observations_linked"].astype("Int64")
    out["reason_coherence"] = out["reason_coherence"].astype("Float64")
    return out


def evidence_table(evidence: pd.DataFrame, as_of: datetime) -> pa.Table:
    """The rows of features/evidence_quality at `as_of`."""
    schema = SCHEMAS["features/evidence_quality"]
    data = evidence.assign(as_of=pd.Timestamp(as_of))
    return pa.Table.from_pandas(data[schema.names], schema=schema, preserve_index=False)
