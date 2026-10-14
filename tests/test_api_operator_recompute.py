"""POST /api/returns/{return_id}/regenerate (US-304, US-305), POST .../evidence and POST
/api/links/{link_id}/dispute (US-310, US-203, flow F7).

Regeneration runs llm.generate again on the latest version (503 when no model can answer: vLLM
down in live vLLM mode, or the cached mode). Evidence that explains links and a disputed link
take those links out of the cluster features (pipeline.graph.dispute_link); the case is scored
again by the request model on that graph and decided again by policy.engine: a new version
(trigger evidence_added or link_disputed) with its before and after, a regenerated draft, the
audit entries of the act and of the recompute, and the reversal of a confirmed coupon block of
the version superseded.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.api.serving import Serving
from pipeline.generator.golden import GIULIA, ROSSI_CARD_ACCOUNTS
from tests.api_harness import Api, golden_return, open_api, settings_for
from tests.test_api_operator_support import (
    audit,
    card,
    decide,
    error,
    latest,
    ok,
    ring_return,
    rows,
)

RING_LINK = "C-MARCO-3~C-MARCO-7"
RING_LAST_LINK = "C-MARCO-5~C-MARCO-7"
CARD_LINK = "C-ROSSI-CARD-1~C-ROSSI-CARD-2"
NOTE = "The card belongs to the parents: one household, confirmed by phone"


def _rossi_card(api: Api) -> str:
    scores = api.serving.frame("scores/request_scores")
    owned = scores.loc[scores["customer_id"].isin(ROSSI_CARD_ACCOUNTS), "return_id"]
    return str(sorted(owned)[0])


def _copy_app(
    serving: Serving, serving_dir: Path, seeded: Path, database: Path, **changes: Any
) -> Iterator[Api]:
    shutil.copyfile(seeded, database)
    yield from open_api(serving, settings_for(serving_dir, database, **changes), database)


@pytest.fixture
def cached_api(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, Any], tmp_path: Path
) -> Iterator[Api]:
    yield from _copy_app(
        serving, serving_dir, seeded_db[0], tmp_path / "cached.sqlite", demo_mode="cached"
    )


@pytest.fixture
def vllm_down_api(
    serving: Serving, serving_dir: Path, seeded_db: tuple[Path, Any], tmp_path: Path
) -> Iterator[Api]:
    """LLM_MODE=vllm with the local vLLM switched off (a closed port, rule 2)."""
    yield from _copy_app(
        serving, serving_dir, seeded_db[0], tmp_path / "vllm.sqlite", llm_mode="vllm",
        vllm_base_url="http://127.0.0.1:9", vllm_api_key="test-key",
    )  # fmt: skip


def _evidence(api: Api, return_id: str, **fields: Any) -> Any:
    body = {"decision_id": latest(api, return_id), "author": "sara", "note": NOTE, **fields}
    return api.client.post(f"/api/returns/{return_id}/evidence", json=body)


def _dispute(api: Api, link: str, **fields: Any) -> Any:
    body = {"reason": "Card used once for a gift; different households", "author": "sara"}
    return api.client.post(f"/api/links/{link}/dispute", json={**body, **fields})


def _from_case(api: Api, link: str, return_id: str) -> Any:
    return _dispute(api, link, return_id=return_id, decision_id=latest(api, return_id))


# ---- regenerate ----------------------------------------------------------------------------------


def test_regenerate_makes_a_new_draft_of_the_latest_version(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    before = card(api, return_id)
    body = {"decision_id": before["decision"]["decision_id"], "languages": ["en"]}
    response = api.client.post(
        f"/api/returns/{return_id}/regenerate", json={**body, "requested_by": "sara"}
    )
    result = ok(response, "regenerate_response")
    draft, d = result["draft"], result["decision"]
    assert result["return_id"] == return_id and d["decision_id"] == body["decision_id"]
    info = draft["generation"]
    assert info["generation_id"] != before["draft"]["generation"]["generation_id"]
    assert d["generation_id"] == info["generation_id"] and d["as_of"] == "2026-10-02T09:00:00Z"
    assert info["generation_path"] == "model" and info["llm_mode"] == "mock"
    assert [t["lang"] for t in draft["message"]["texts"]] == ["en"]
    last = audit(api, return_id)[-1]
    assert (last.action, last.actor, last.entity_id) == (
        "draft_regenerated",
        "sara",
        info["generation_id"],
    )
    after = card(api, return_id)
    assert after["draft"] == draft and after["decision"]["decision_id"] == d["decision_id"]


def test_regenerate_is_refused_without_a_model(cached_api: Api, vllm_down_api: Api) -> None:
    """503 llm_unavailable: in cached mode the model is off (US-901); in live vLLM mode with
    vLLM down no new draft can be made. The stored draft stays and nothing is written."""
    for api in (cached_api, vllm_down_api):
        return_id = golden_return(api.serving, GIULIA)
        before = card(api, return_id)
        body = {"decision_id": before["decision"]["decision_id"], "requested_by": "sara"}
        log = rows(api, "audit_log")
        error(
            api.client.post(f"/api/returns/{return_id}/regenerate", json=body),
            503,
            "llm_unavailable",
        )
        assert rows(api, "audit_log") == log
        assert card(api, return_id)["draft"] == before["draft"]


def test_regenerate_errors(api: Api) -> None:
    return_id = golden_return(api.serving, GIULIA)
    path = f"/api/returns/{return_id}/regenerate"
    body = {"decision_id": latest(api, return_id), "requested_by": "sara"}
    error(api.client.post("/api/returns/RET-NOPE/regenerate", json=body), 404, "not_found")
    error(api.client.post(path, json={**body, "requested_by": "ahmed"}), 403, "forbidden_role")
    error(api.client.post(path, json={**body, "languages": ["fr"]}), 422, "invalid_request")
    ring = ring_return(api)
    stale = latest(api, ring)
    ok(_from_case(api, RING_LINK, ring), "link_dispute_response")
    response = api.client.post(
        f"/api/returns/{ring}/regenerate", json={"decision_id": stale, "requested_by": "sara"}
    )
    body = error(response, 409, "stale_decision")
    assert stale in body["message"]


# ---- evidence ------------------------------------------------------------------------------------


def test_evidence_explaining_a_shared_card_recomputes_the_case(api: Api) -> None:
    """E2E-05: favourable evidence; the card link leaves the cluster features; before and after
    are visible; the explanation is regenerated; the log keeps both versions."""
    return_id = _rossi_card(api)
    before = card(api, return_id)
    response = _evidence(
        api, return_id, kind="shared_payment_explained", target_link_ids=[CARD_LINK]
    )
    result = ok(response, "recompute_response")
    b, a, d = result["before"], result["after"], result["decision"]
    assert result["trigger"] == "evidence_added" and result["dispute_id"] is None
    assert b["decision_id"] == before["decision"]["decision_id"] and b["version"] == 1
    assert (a["version"], d["version"], d["revision_trigger"]) == (2, 2, "evidence_added")
    assert d["supersedes"] == b["decision_id"] and d["state"] == "proposed"
    assert (b["active_links"], a["active_links"]) == (3, 2)
    assert b["cluster_size"] == a["cluster_size"] == 3  # still linked through the household
    assert a["quadrant"] not in ("Q3", "Q4") and not a["requires_approval"]  # MOD-11
    assert result["draft"]["generation"]["generation_path"] == "model"
    assert result["coupon_application"] is None
    log = audit(api, return_id)
    by_id = {e.entry_id: e for e in log}
    assert [by_id[i].action for i in result["audit_entry_ids"]] == [
        "evidence_added",
        "case_recomputed",
        "draft_generated",
    ]
    added = by_id[result["audit_entry_ids"][0]]
    assert added.actor == "sara" and added.reason == NOTE
    assert log[-1].action == "draft_generated" and log[-1].decision_id == d["decision_id"]
    (stored,) = rows(api, "evidence_additions")
    assert stored["evidence_id"] == result["evidence_id"]
    assert (stored["decision_before_id"], stored["decision_after_id"]) == (
        b["decision_id"],
        d["decision_id"],
    )
    assert stored["before"] == b and stored["after"] == a
    shown = card(api, return_id)
    assert shown["decision"]["decision_id"] == d["decision_id"]
    links = {x["link_id"]: x for x in shown["linked_accounts"]["links"]}
    assert links[CARD_LINK]["explained_by_evidence_id"] == result["evidence_id"]
    assert [r["version"] for r in shown["revisions"]] == [1, 2]
    old = ok(api.client.get(f"/api/decisions/{b['decision_id']}"), "decision_record")
    assert old["decision"]["superseded_by"] == d["decision_id"]


def test_evidence_without_links_records_a_recompute_with_the_same_figures(api: Api) -> None:
    return_id = ring_return(api)
    result = ok(
        _evidence(api, return_id, kind="customer_contact", target_link_ids=[]),
        "recompute_response",
    )
    b, a = result["before"], result["after"]
    for key in ("quadrant", "risk", "lever_ids", "cluster_size", "active_links", "top_factors"):
        if key == "risk":
            assert a[key]["value"] == b[key]["value"]
        else:
            assert a[key] == b[key], key
    assert result["decision"]["coupon_rule"]["application_id"].endswith("-2")


def test_evidence_errors(api: Api) -> None:
    return_id = _rossi_card(api)
    explained = {"kind": "household_members", "target_link_ids": [RING_LINK]}
    body = error(_evidence(api, return_id, **explained), 422, "invalid_request")
    assert RING_LINK in body["message"] + str(body["violations"])
    giulia = golden_return(api.serving, GIULIA)
    error(_evidence(api, giulia, kind="household_members", target_link_ids=[CARD_LINK]), 422,
          "invalid_request")  # fmt: skip
    stale = latest(api, return_id)
    ok(_evidence(api, return_id, kind="customer_contact"), "recompute_response")
    response = api.client.post(
        f"/api/returns/{return_id}/evidence",
        json={"decision_id": stale, "kind": "other", "note": NOTE, "author": "sara"},
    )
    error(response, 409, "stale_decision")
    error(
        api.client.post(
            "/api/returns/RET-NOPE/evidence",
            json={"decision_id": stale, "kind": "other", "note": NOTE, "author": "sara"},
        ),
        404,
        "not_found",
    )
    error(_evidence(api, return_id, kind="other", author="ahmed"), 403, "forbidden_role")
    ok(_evidence(api, return_id, kind="shared_payment_explained", target_link_ids=[CARD_LINK]),
       "recompute_response")  # fmt: skip
    again = _evidence(api, return_id, kind="household_members", target_link_ids=[CARD_LINK])
    error(again, 422, "invalid_request")  # already out of the cluster features


# ---- disputes ------------------------------------------------------------------------------------


def test_a_dispute_from_a_case_marks_the_link_and_recomputes_the_cluster(api: Api) -> None:
    return_id = ring_return(api)
    before = card(api, return_id)
    result = ok(_from_case(api, RING_LINK, return_id), "link_dispute_response")
    link = result["link"]
    assert link["link_id"] == RING_LINK and link["disputed"]
    assert link["dispute"]["author"] == "sara" and link["dispute"]["dispute_id"]
    assert result["affected_return_ids"] == [return_id]
    recompute = result["recompute"]
    assert recompute["trigger"] == "link_disputed" and recompute["evidence_id"] is None
    b, a = recompute["before"], recompute["after"]
    assert (b["cluster_size"], a["cluster_size"]) == (7, 7)  # still linked by other links
    assert a["active_links"] == b["active_links"] - 1
    d = recompute["decision"]
    assert d["quadrant"] == "Q4" and d["state"] == "proposed" and d["requires_approval"]
    assert d["coupon_rule"]["state"] == "proposed"
    assert d["coupon_rule"]["application_id"] != before["decision"]["coupon_rule"]["application_id"]
    assert recompute["coupon_application"] is None  # the version superseded was not approved
    log = {e.entry_id: e for e in audit(api, return_id)}
    assert [log[i].action for i in recompute["audit_entry_ids"]] == [
        "link_disputed",
        "case_recomputed",
        "draft_generated",
    ]
    (stored,) = rows(api, "link_disputes")
    assert stored["link_id"] == RING_LINK and stored["return_id"] == return_id
    shown = card(api, return_id)
    links = {x["link_id"]: x for x in shown["linked_accounts"]["links"]}
    assert links[RING_LINK]["disputed"] and links[RING_LINK]["dispute"] is not None
    text = str(result).lower()
    assert "same person" not in text and "stessa persona" not in text  # rule 11


def test_the_last_link_disputed_leaves_the_account_alone(api: Api) -> None:
    """After both card links of the account are disputed, it is in no cluster: the request is
    scored again on its own cluster features, and decided again (US-310)."""
    return_id = ring_return(api)
    ok(_from_case(api, RING_LINK, return_id), "link_dispute_response")
    api.clock.advance(timedelta(minutes=5))
    result = ok(_from_case(api, RING_LAST_LINK, return_id), "link_dispute_response")
    recompute = result["recompute"]
    b, a, d = recompute["before"], recompute["after"], recompute["decision"]
    assert b["version"] == 2 and a["version"] == 3
    assert b["cluster_size"] == 7 and a["cluster_size"] is None and a["active_links"] == 0
    assert a["risk"]["value"] < b["risk"]["value"]
    assert d["inputs"]["cluster_id"] is None and d["coupon_rule"] is None
    assert "account graph" in d["inputs"]["provenance"]["origin"]
    shown = card(api, return_id)
    assert shown["linked_accounts"] is None
    assert shown["risk"]["score"] == a["risk"] and shown["decision"]["quadrant"] == a["quadrant"]
    assert shown["risk"]["top_factors"] == a["top_factors"]


def test_a_dispute_reverses_the_confirmed_coupon_block_of_the_version_it_supersedes(
    api: Api,
) -> None:
    return_id = ring_return(api)
    approved = ok(decide(api, return_id, "approve"), "decision_result")
    application = approved["coupon_application"]["application_id"]
    api.clock.advance(timedelta(hours=2))
    result = ok(_from_case(api, RING_LINK, return_id), "link_dispute_response")
    app = result["recompute"]["coupon_application"]
    assert app["application_id"] == application and app["state"] == "reversed"
    assert app["reversed_by"] == "sara" and not app["in_force"]
    assert app["reversed_at"] == result["recompute"]["decision"]["created_at"]
    (row,) = rows(api, "coupon_rule_applications", application_id=application)
    assert row["state"] == "reversed"
    old = ok(api.client.get(f"/api/decisions/{approved['decision']['decision_id']}"),
             "decision_record")["decision"]  # fmt: skip
    assert not old["in_force"] and old["coupon_rule"]["state"] == "reversed"
    log = {e.entry_id: e for e in audit(api, return_id)}
    assert [log[i].action for i in result["recompute"]["audit_entry_ids"]] == [
        "link_disputed",
        "case_recomputed",
        "coupon_rule_application_reversed",
        "draft_generated",
    ]


def test_a_dispute_from_the_graph_recomputes_every_open_case_of_the_cluster(api: Api) -> None:
    return_id = ring_return(api)
    result = ok(_dispute(api, RING_LINK), "link_dispute_response")
    assert result["recompute"] is None and result["affected_return_ids"] == [return_id]
    shown = card(api, return_id)
    assert shown["decision"]["version"] == 2
    assert shown["decision"]["revision_trigger"] == "link_disputed"


def test_dispute_errors(api: Api) -> None:
    return_id = ring_return(api)
    error(_dispute(api, "C-NOPE-1~C-NOPE-2"), 404, "not_found")
    error(_dispute(api, "not-a-link"), 422, "invalid_request")
    error(_dispute(api, RING_LINK, author="ahmed"), 403, "forbidden_role")
    giulia = golden_return(api.serving, GIULIA)
    error(_from_case(api, RING_LINK, giulia), 422, "invalid_request")  # not the case's cluster
    stale = latest(api, return_id)
    ok(_from_case(api, RING_LINK, return_id), "link_dispute_response")
    body = error(
        _dispute(api, RING_LAST_LINK, return_id=return_id, decision_id=stale),
        409,
        "stale_decision",
    )
    assert stale in body["message"]
    error(_dispute(api, RING_LINK), 409, "conflict")  # a link is disputed once
    assert len(rows(api, "link_disputes")) == 1
