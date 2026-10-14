"""GET /api/returns (US-301, US-607) and GET /api/decisions/{decision_id} (US-604).

The queue lists every queued return with the figures of its case card: the decision of its
latest version, the risk and value given to the engine, the expected net benefit of the
recommended alternative, the linked accounts of the cluster the engine was given, the anomaly of
the returned variant and the draft status. It sorts by expected net benefit (ties by return id)
by default, filters exactly as QueueQuery.matches (the reference meaning of ADR-15), pages from
1, marks the restrictions due for review, and answers in under 2 seconds (US-301).
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api import schemas as s
from apps.api.services.cases import intake
from pipeline.generator.golden import ELENA, GIULIA
from tests.api_harness import Api, golden_return
from tests.test_api_operator_support import (
    by_id,
    card,
    cards_the_contract_refuses,
    decide,
    error,
    latest,
    ok,
    ring_return,
)

ALL = {"page_size": 100}


def _queue(api: Api, **params: Any) -> dict[str, Any]:
    return ok(api.client.get("/api/returns", params=params), "queue_response")


def _items(api: Api) -> list[s.QueueItem]:
    return [s.QueueItem.model_validate(i) for i in _queue(api, **ALL)["items"]]


def test_the_queue_lists_every_return_sorted_by_expected_net_benefit(api: Api) -> None:
    body = _queue(api)
    total = len(api.serving.open_return_ids())
    assert body["total"] == total and body["page"] == 1 and body["page_size"] == 25
    assert body["sort"] == "-expected_net_benefit" and len(body["items"]) == min(25, total)
    assert body["as_of"] == "2026-10-02T09:00:00Z"
    whole = _queue(api, **ALL)["items"]
    assert {i["return_id"] for i in whole} == set(api.serving.open_return_ids())
    keys = [(-i["expected_net_benefit"]["value"], i["return_id"]) for i in whole]
    assert keys == sorted(keys)
    assert body["items"] == whole[:25]
    assert body["provenance"]["nature"] == "synthetic"


def test_every_item_shows_the_figures_of_its_case_card(api: Api) -> None:
    """One source for a figure (rule 11): the queue shows what the card shows."""
    refused = cards_the_contract_refuses(api)
    for item in _queue(api, **ALL)["items"]:
        if item["return_id"] in refused:
            continue
        c = card(api, item["return_id"])
        d = c["decision"]
        recommended = next(a for a in c["alternatives"] if a["recommended"])
        linked = c["linked_accounts"]
        assert (item["order_id"], item["customer_id"], item["display_name"]) == (
            c["order_id"],
            c["customer_id"],
            c["display_name"],
        )
        assert (item["requested_at"], item["reason"]) == (
            c["request"]["requested_at"],
            c["request"]["reason"],
        )
        for key in ("path", "evidence_quality", "quadrant", "decision_id", "state"):
            assert item[key] == d[key], key
        for key in ("requires_approval", "lever_ids", "review_at"):
            assert item[key] == d[key], key
        assert item["review_due"] is False
        assert item["risk"] == (c["risk"]["score"] if c["risk"] else None)
        assert item["value"] == c["value"]["value_eur"]
        assert item["expected_net_benefit"] == recommended["net_benefit"]["total"]
        assert item["linked_account_count"] == (linked["cluster_size"] - 1 if linked else 0)
        assert item["variant_anomaly"] == (c["product_anomaly"] is not None)
        assert item["draft_status"] == c["draft_status"] == "ready"


def test_golden_items(api: Api) -> None:
    items = by_id(_queue(api, **ALL)["items"])
    giulia = items[golden_return(api.serving, GIULIA)]
    assert giulia["quadrant"] == "Q1" and giulia["display_name"] == "Giulia Bianchi"
    assert not giulia["requires_approval"] and giulia["linked_account_count"] == 0
    marco = items[ring_return(api)]
    assert marco["quadrant"] == "Q4" and marco["requires_approval"]
    assert (
        marco["linked_account_count"] == 6
        and "cluster_coupons_disabled:true" in (marco["lever_ids"])
    )
    elena = items[golden_return(api.serving, ELENA)]
    assert elena["path"] == "guarantee" and elena["quadrant"] is None and elena["risk"] is None
    assert elena["variant_anomaly"] and not elena["requires_approval"]
    insufficient = [i for i in items.values() if i["evidence_quality"] == "insufficient"]
    assert insufficient and all(i["quadrant"] is None and i["risk"] is None for i in insufficient)


QUERIES: list[dict[str, Any]] = [
    {"state": ["proposed"]},
    {"state": ["approved", "overridden"]},
    {"quadrant": ["Q3", "Q4"]},
    {"quadrant": ["none"]},
    {"quadrant": ["Q1", "none"]},
    {"path": ["guarantee"]},
    {"evidence_quality": "insufficient"},
    {"requires_approval": True},
    {"requires_approval": False, "path": ["withdrawal"]},
    {"review_due": True},
    {"review_due": False},
    {"q": "giulia"},
    {"q": "ret-00030"},
    {"q": "C-MARCO"},
    {"state": ["proposed"], "quadrant": ["Q3", "Q4"], "requires_approval": True},
]


@pytest.mark.parametrize("query", QUERIES, ids=[str(q) for q in QUERIES])
def test_filters_mean_what_queue_query_matches_says(api: Api, query: dict[str, Any]) -> None:
    everything = _items(api)
    reference = s.QueueQuery.model_validate(query)
    expected = [i.return_id for i in everything if reference.matches(i)]
    body = _queue(api, **query, **ALL)
    assert [i["return_id"] for i in body["items"]] == expected
    assert body["total"] == len(expected)


SORTS = ["-expected_net_benefit", "expected_net_benefit", "-requested_at", "requested_at", "-risk"]


def _sort_key(sort: str, item: dict[str, Any]) -> tuple[Any, ...]:
    if sort.endswith("expected_net_benefit"):
        value = item["expected_net_benefit"]["value"]
        return (-value if sort.startswith("-") else value, item["return_id"])
    if sort.endswith("requested_at"):
        stamp = s.QueueItem.model_validate(item).requested_at.timestamp()
        return (-stamp if sort.startswith("-") else stamp, item["return_id"])
    risk = item["risk"]
    return (risk is None, -(risk["value"] if risk else 0.0), item["return_id"])


@pytest.mark.parametrize("sort", SORTS)
def test_every_sort_orders_with_ties_by_return_id(api: Api, sort: str) -> None:
    body = _queue(api, sort=sort, **ALL)
    assert body["sort"] == sort
    keys = [_sort_key(sort, i) for i in body["items"]]
    assert keys == sorted(keys)


def test_pages_split_the_filtered_list(api: Api) -> None:
    whole = _queue(api, **ALL)["items"]
    first = _queue(api, page=1, page_size=10)
    second = _queue(api, page=2, page_size=10)
    assert first["items"] == whole[:10] and second["items"] == whole[10:20]
    assert first["total"] == second["total"] == len(whole)
    beyond = _queue(api, page=99, page_size=10)
    assert beyond["items"] == [] and beyond["total"] == len(whole)


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page_size": 101}, {"sort": "risk"}, {"quadrant": "Q5"}, {"state": "done"}],
)
def test_an_invalid_query_is_an_invalid_request(api: Api, params: dict[str, Any]) -> None:
    error(api.client.get("/api/returns", params=params), 422, "invalid_request")


def test_a_restriction_past_its_review_date_is_due_for_review(api: Api) -> None:
    """US-607: an approved restriction is due for review from its review date on (UTC days);
    review_due=true lists it until a person renews or lifts it."""
    return_id = ring_return(api)
    ok(decide(api, return_id, "approve"), "decision_result")
    item = by_id(_queue(api, **ALL)["items"])[return_id]
    assert item["state"] == "approved" and not item["review_due"]
    assert _queue(api, review_due=True)["items"] == []
    review_at = item["review_at"]
    api.clock.set(api.clock.now() + timedelta(days=200))
    due = _queue(api, review_due=True)
    assert [i["return_id"] for i in due["items"]] == [return_id]
    assert due["items"][0]["review_at"] == review_at and due["items"][0]["review_due"]


def test_the_queue_answers_under_two_seconds(api: Api) -> None:
    """US-301 (NFR: queue under 2 s), measured on the tiny store after a warm-up call."""
    _queue(api)
    started = time.perf_counter()
    _queue(api, **ALL)
    assert time.perf_counter() - started < 2.0


def test_an_empty_queue(empty_api: Api) -> None:
    body = _queue(empty_api)
    assert body["items"] == [] and body["total"] == 0


def test_a_return_of_the_shop_adapter_is_queued_with_the_figures_of_its_card(
    empty_api: Api,
) -> None:
    """A request of the simulated shop (flow F1), known when it arrives, by an account first
    seen after the last customer snapshot: its snapshot is computed at the request."""
    serving = empty_api.serving
    customers = serving.frame("store/customers")
    newest = customers.sort_values("event_at").iloc[-1]["customer_id"]
    orders = serving.frame("store/orders")
    lines = serving.frame("store/order_lines")
    deliveries = serving.frame("store/deliveries").set_index("order_id")
    mine = orders.loc[orders["customer_id"] == newest, "order_id"]
    candidates = lines.loc[lines["order_id"].isin(set(mine) & set(deliveries.index))]
    if candidates.empty:
        pytest.skip("the newest account of the tiny store has no delivered line")
    line = candidates.iloc[0]
    delivered = deliveries.loc[line["order_id"], "available_at"].to_pydatetime()
    at = max(serving.as_of, delivered) + timedelta(hours=2)
    record = serving.adapter_return(
        "RET-ADAPTER-9", str(line["line_id"]), s.ReturnReason.CHANGED_MIND, at, None
    )

    async def run(conn: AsyncConnection) -> None:
        await intake(conn, serving, None, record, at=at, source="adapter")

    empty_api.run(run)
    empty_api.clock.set(at + timedelta(minutes=5))
    (item,) = _queue(empty_api)["items"]
    c = card(empty_api, "RET-ADAPTER-9")
    assert item["return_id"] == "RET-ADAPTER-9" and item["draft_status"] == "pending"
    assert item["value"] == c["value"]["value_eur"]
    assert item["risk"] == (c["risk"]["score"] if c["risk"] else None)
    assert item["requested_at"] == c["request"]["requested_at"]
    assert item["linked_account_count"] == 0


# ---- Decision history (US-604) ----------------------------------------------------------------


def test_every_version_of_a_card_is_served_as_a_record(api: Api) -> None:
    refused = cards_the_contract_refuses(api)
    for return_id in api.serving.open_return_ids():
        if return_id in refused:
            continue
        c = card(api, return_id)
        body = ok(
            api.client.get(f"/api/decisions/{c['decision']['decision_id']}"), "decision_record"
        )
        assert body["decision"] == c["decision"]
        assert body["alternatives"] == c["alternatives"]


def test_an_earlier_version_is_served_as_it_was(api: Api) -> None:
    """After a lift, version 1 is history: superseded, no longer in force, still served whole."""
    return_id = ring_return(api)
    first = latest(api, return_id)
    ok(decide(api, return_id, "approve"), "decision_result")
    api.clock.advance(timedelta(hours=1))
    lifted = ok(
        decide(
            api, return_id, "lift", override_reason="The customer asked for a rectification",
            override_category="customer_context",
        ),
        "decision_result",
    )  # fmt: skip
    second = lifted["decision"]["decision_id"]
    old = ok(api.client.get(f"/api/decisions/{first}"), "decision_record")["decision"]
    assert old["superseded_by"] == second and not old["in_force"] and old["state"] == "approved"
    assert old["coupon_rule"]["state"] == "reversed"
    new = ok(api.client.get(f"/api/decisions/{second}"), "decision_record")["decision"]
    assert new["supersedes"] == first and new["version"] == 2 and new["in_force"]


def test_an_unknown_decision_is_not_found(api: Api) -> None:
    error(api.client.get("/api/decisions/DEC-9999999-1"), 404, "not_found")
