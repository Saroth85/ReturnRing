"""The account graph after the operators' disputes and explanations (US-310, US-203; ADR-16).

apps.api.services.operator.graph.GraphView is the serving snapshot with the links people
disputed or explained left out of the cluster features: its clusters equal a full rebuild of
pipeline.graph with those pairs disputed, the customer snapshot and the cluster members follow,
and a request whose cluster changed is scored again by the batch's code on that graph (rule 1:
the scores still come from ml/, the decisions from policy/). The snapshot itself never changes.
"""

from __future__ import annotations

import pandas as pd

from apps.api.services.operator.graph import GraphView
from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA, MARCO_ACCOUNTS
from pipeline.graph.build import build_graph
from pipeline.graph.clusters import clusters_table
from pipeline.graph.snapshot import load_snapshot
from pipeline.graph.weights import GraphParams
from tests.api_harness import golden_return

PAIRS = [("C-MARCO-3", "C-MARCO-7"), ("C-MARCO-5", "C-MARCO-7")]


def _ring(serving: Serving) -> str:
    scores = serving.frame("scores/request_scores")
    customer = next(c for c in MARCO_ACCOUNTS if (scores["customer_id"] == c).any())
    return golden_return(serving, customer)


def test_without_exclusions_the_view_is_the_snapshot(serving: Serving) -> None:
    assert GraphView.of(serving, []) is serving


def test_clusters_equal_a_full_rebuild_with_the_pairs_disputed(serving: Serving) -> None:
    record = serving.store_return(_ring(serving))
    assert record is not None
    instant = serving.context_instant(record.known_at)
    assert instant is not None
    view = GraphView.of(serving, PAIRS)
    snapshot = load_snapshot(serving.root, instant)
    rebuilt = clusters_table(build_graph(snapshot, GraphParams(), PAIRS).clusters).to_pandas()
    adjusted = view.clusters(instant)
    key = ["customer_id"]
    pd.testing.assert_frame_equal(
        adjusted.sort_values(key).reset_index(drop=True),
        rebuilt.sort_values(key).reset_index(drop=True),
    )
    served = serving.frame("features/clusters")
    assert (served["as_of"] == pd.Timestamp(instant)).sum() == len(adjusted)  # unchanged


def test_the_customer_snapshot_and_the_scores_follow_the_graph(serving: Serving) -> None:
    record = serving.store_return(_ring(serving))
    assert record is not None
    base = serving.customer_context(record.customer_id, record.known_at)
    assert base.cluster_id is not None
    view = GraphView.of(serving, PAIRS)
    alone = view.customer_context(record.customer_id, record.known_at)
    assert alone.cluster is None and alone.evidence == base.evidence and alone.value == base.value
    others = sorted(set(MARCO_ACCOUNTS) - {record.customer_id})
    cluster = view.customer_context(others[0], record.known_at).cluster_id
    assert cluster is not None and cluster != base.cluster_id
    assert view.cluster_members(cluster, base.as_of) == others
    batch = serving.request_score(record)
    rescored = view.request_score(record)
    assert batch.source == "batch" and rescored.source == "live"
    assert rescored.risk is not None and batch.risk is not None and rescored.risk < batch.risk
    assert len(rescored.factors) == 3
    assert serving.request_score(record) == batch  # the snapshot never changes
    assert serving.customer_context(record.customer_id, record.known_at).cluster_id == (
        base.cluster_id
    )


def test_a_request_outside_the_changed_clusters_keeps_the_batch_score(serving: Serving) -> None:
    record = serving.store_return(golden_return(serving, GIULIA))
    assert record is not None
    assert GraphView.of(serving, PAIRS).request_score(record) == serving.request_score(record)


def test_a_disputed_pair_that_is_no_link_changes_nothing(serving: Serving) -> None:
    record = serving.store_return(_ring(serving))
    assert record is not None
    instant = serving.context_instant(record.known_at)
    assert instant is not None
    view = GraphView.of(serving, [("C-MARCO-1", "C-GIULIA")])
    served = serving.frame("features/clusters")
    rows = served.loc[served["as_of"] == pd.Timestamp(instant)]
    pd.testing.assert_frame_equal(
        view.clusters(instant).reset_index(drop=True), rows.reset_index(drop=True)
    )
