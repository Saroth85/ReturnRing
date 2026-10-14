"""GET /api/returns/{return_id}: the case card, reference implementation of the route builders
(US-302..US-309; ADR-15 section 4 "Queue and case card").

Every open return of the tiny store gets a card that passes its JSON Schema and its model (the
engine invariants, the legal guard, the draft rebuilt by llm.case.build_case and validated by
llm.validator, the alternatives priced by policy.simulator), with the shape of the contract
examples. The golden cases show what the demo shows.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.api.schemas import MODE_HEADER, REQUEST_ID_HEADER
from pipeline.generator.golden import ELENA, GIULIA, MARCO_ACCOUNTS, ROSSI_ACCOUNTS
from tests.api_harness import Api, golden_return, validate_body

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"


def _card(api: Api, return_id: str) -> dict[str, Any]:
    response = api.client.get(f"/api/returns/{return_id}")
    assert response.status_code == 200, response.text
    assert response.headers[MODE_HEADER] == "live" and response.headers[REQUEST_ID_HEADER]
    body: dict[str, Any] = response.json()
    return body


FREE_MAPS = ("model_versions",)  # dict[str, Version]: the keys are data, not shape


def _keys(node: Any, path: str = "") -> set[str]:
    """Every key path of a JSON document (list items share the path of their list)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            found.add(here)
            if key not in FREE_MAPS:
                found |= _keys(value, here)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item, f"{path}[]")
    return found


def test_every_open_return_has_a_valid_card(api: Api) -> None:
    for return_id in api.serving.open_return_ids():
        body = _card(api, return_id)
        validate_body("case_card", body)
        assert body["return_id"] == return_id and body["decision"]["return_id"] == return_id
        assert body["draft_status"] == "ready"
        assert body["as_of"] == body["decision"]["as_of"]


def test_cards_have_the_shape_of_the_contract_examples(api: Api) -> None:
    """Every top-level section and every key of a filled section is a key of the examples."""
    examples = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(EXAMPLES.glob("case_card.*.json"))
    ]
    expected = set().union(*(_keys(e) for e in examples))
    tops = set(examples[0])
    for return_id in api.serving.open_return_ids():
        body = _card(api, return_id)
        assert set(body) == tops
        assert _keys(body) <= expected, sorted(_keys(body) - expected)


def test_giulia_card(api: Api) -> None:
    """Q1 on the withdrawal path: risk with 3 contributions (not causes), value never a risk
    signal, the upgrade in force without approval, a size exchange among the alternatives."""
    body = _card(api, golden_return(api.serving, GIULIA))
    decision, risk = body["decision"], body["risk"]
    assert body["path"] == "withdrawal" and decision["quadrant"] == "Q1"
    assert decision["in_force"] and not decision["requires_approval"]
    assert len(risk["top_factors"]) == 3
    assert all("not a cause" in f["phrase"] for f in risk["top_factors"])
    assert risk["score"]["value"] == decision["inputs"]["risk"]
    assert body["value"]["legend"].startswith("Customer value orients benefits")
    assert body["linked_accounts"] is None
    kinds = [a["kind"] for a in body["alternatives"]]
    assert kinds[0] == "proposal" and "current_terms" in kinds
    assert body["display_name"] == "Giulia Bianchi"


def test_marco_card(api: Api) -> None:
    """Q4 of a ring account: the six levers, approval required, a review date, the cluster of 7
    linked accounts with its links, and the coupon block proposed for exactly those accounts."""
    customer = next(
        c for c in MARCO_ACCOUNTS
        if (api.serving.frame("scores/request_scores")["customer_id"] == c).any()
    )  # fmt: skip
    body = _card(api, golden_return(api.serving, customer))
    decision, linked = body["decision"], body["linked_accounts"]
    assert decision["quadrant"] == "Q4" and decision["requires_approval"]
    assert decision["review_at"] is not None and not decision["in_force"]
    assert (
        len(decision["lever_ids"]) == 6 and "cluster_coupons_disabled:true" in decision["lever_ids"]
    )
    assert linked["cluster_size"] == 7 and linked["cluster_id"] == decision["inputs"]["cluster_id"]
    assert {a["customer_id"] for a in linked["accounts"]} == set(MARCO_ACCOUNTS)
    assert sum(a["is_case_customer"] for a in linked["accounts"]) == 1
    assert linked["links"] and all(link["kinds"] for link in linked["links"])
    rule = decision["coupon_rule"]
    assert rule["state"] == "proposed" and sorted(rule["affected_customer_ids"]) == list(
        MARCO_ACCOUNTS
    )
    text = json.dumps(body).lower()
    assert "same person" not in text and "stessa persona" not in text  # rule 11


def test_elena_card(api: Api) -> None:
    """The guarantee path: no risk, no quadrant, no restriction, never scored (rule 4)."""
    body = _card(api, golden_return(api.serving, ELENA))
    decision = body["decision"]
    assert body["path"] == "guarantee" and body["risk"] is None
    assert decision["quadrant"] is None and decision["proposed_levers"] == []
    assert decision["inputs"]["risk"] is None and decision["inputs"]["value_eur"] is None
    assert not any(s["label"] == "SCORE" for s in body["draft"]["explanation"])
    assert any(a["kind"] == "product_report" for a in body["alternatives"])
    assert body["draft"]["message"]["legal_clause_kind"] == "guarantee"


def test_rossi_cards_are_never_restricted(api: Api) -> None:
    req = api.serving.frame("scores/request_scores")
    found = 0
    for customer in ROSSI_ACCOUNTS:
        for return_id in req.loc[req["customer_id"] == customer, "return_id"]:
            body = _card(api, str(return_id))
            assert body["decision"]["quadrant"] not in ("Q3", "Q4")
            assert not body["decision"]["requires_approval"]
            found += 1
    if not found:
        pytest.skip("no open Rossi return in the tiny store")


def test_an_unknown_return_is_not_found(api: Api) -> None:
    response = api.client.get("/api/returns/RET-9999999")
    assert response.status_code == 404
    validate_body("error", response.json())
    assert response.json()["code"] == "not_found"


def test_a_restriction_lapses_at_its_review_date(api: Api) -> None:
    """US-607: in_force is computed when served; a restriction never applies on or after its
    review date (here a proposal, never in force: the card still shows it with its date)."""
    customer = next(
        c for c in MARCO_ACCOUNTS
        if (api.serving.frame("scores/request_scores")["customer_id"] == c).any()
    )  # fmt: skip
    return_id = golden_return(api.serving, customer)
    before = _card(api, return_id)
    review = before["decision"]["review_at"]
    api.clock.advance(timedelta(days=120))
    after = _card(api, return_id)
    assert after["decision"]["review_at"] == review and not after["decision"]["in_force"]
    assert after["as_of"] > before["as_of"]
    validate_body("case_card", after)
