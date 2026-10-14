"""The contract examples show what the live service produces (review backlog of 14 Oct 2026,
"Examples vs live"; ADR-15 section 18).

The examples are the UI's mock data: an example the live service can never produce makes the
UI build a screen the demo cannot show. These tests tie each example to the live code path that
answers the same request: the size advice and the line probabilities of the checkout, the mock
vision checklist of an inspection photo, the returns a catalogue task can name from a case card,
the decision change of the demo dispute, and the model versions of the serving manifest.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from apps.api import schemas as s
from apps.api.services import demo
from apps.api.services.other import checkout, inspections
from apps.api.serving import ReturnRecord, Serving
from ml.reference.features import QUERY_COLUMNS
from pipeline.generator.golden import ELENA
from pipeline.serving.manifest import MODEL_VERSION_KEYS
from tests.api_harness import Api, golden_return, validate_body

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def mutated(document: Any, path: Sequence[str | int], value: Any) -> Any:
    out = copy.deepcopy(document)
    node = out
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return out


def refused(model: type[BaseModel], document: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(document)


# ---- Checkout: size advice and line probabilities (US-401) --------------------------------------


def _live_advice_kinds() -> set[str]:
    """Every kind of advice the live checkout can give: the store holds no measurements or
    exchange outcomes, so it never suggests a size (apps/api/services/other/checkout.py)."""
    reasons = (None, "defective", "changed_mind", *sorted(checkout.GUIDE_DOUBTS))
    return {
        checkout.advice_for(size_range, reason).kind
        for size_range in (None, "", "XS-XL")
        for reason in reasons
    }


def test_the_live_checkout_never_suggests_a_size() -> None:
    assert _live_advice_kinds() == {"measurement_guide", "no_reliable_recommendation"}
    assert checkout.advice_for("XS-XL", None) == s.SizeAdvice.of("measurement_guide")
    for reason in checkout.GUIDE_DOUBTS:  # the size guide may be what is wrong (spec 8.9)
        assert checkout.advice_for("XS-XL", reason).kind == "no_reliable_recommendation"
    assert checkout.advice_for(None, None).kind == "no_reliable_recommendation"


def test_checkout_examples_show_the_live_size_advice_and_probabilities() -> None:
    """The Giulia example has two sizes of one dress: the personalized predictor has no size
    feature, so both lines get one probability, and the advice is the size guide (no "switch to
    M"); the threshold is the live one."""
    assert "size" not in QUERY_COLUMNS
    live = _live_advice_kinds()
    for name in ("giulia", "marco"):
        body = s.CheckoutEvaluateResponse.model_validate(
            load(f"checkout_evaluate_response.{name}.json")
        )
        assert body.p_return_threshold == checkout.P_RETURN_THRESHOLD
        by_variant: dict[str, set[float]] = {}
        for line in body.lines:
            by_variant.setdefault(line.variant_id, set()).add(line.p_return)
            if line.size_advice is not None:
                assert line.size_advice.kind in live, (name, line.size_advice.kind)
        assert all(len(found) == 1 for found in by_variant.values()), (name, by_variant)
    giulia = s.CheckoutEvaluateResponse.model_validate(
        load("checkout_evaluate_response.giulia.json")
    )
    sizes = {(line.variant_id, line.size) for line in giulia.lines}
    assert len(sizes) == 2 and len({v for v, _ in sizes}) == 1  # one dress, two sizes
    advice = [line.size_advice for line in giulia.lines if line.size_advice is not None]
    assert advice and all(a == s.SizeAdvice.of("measurement_guide") for a in advice)


# ---- Inspections: the mock vision's answer for the photo of the request (US-501..503) ---------


def _line_price(return_id: str) -> Decimal:
    """The price of the returned line, as the case card of the return shows it."""
    for path in sorted(EXAMPLES.glob("case_card.*.json")):
        card = json.loads(path.read_text(encoding="utf-8"))
        if card["return_id"] == return_id:
            return Decimal(str(card["item"]["price_eur"]))
    raise AssertionError(f"no case card of {return_id}")


@pytest.mark.parametrize(
    ("request_name", "inspection_name", "outcome"),
    [
        ("inspection_create_request", "inspection.awaiting", "damaged"),
        ("inspection_create_request.manual_review", "inspection.manual_review", "unclear"),
    ],
)
def test_inspection_examples_are_the_mock_vision_answer_for_their_photo(
    request_name: str, inspection_name: str, outcome: str
) -> None:
    """LLM_MODE=mock answers the photo of each example request with the checklist, the state,
    the manual review reason, the proposal and the generation record of its inspection example:
    a damaged photo for the claim of the awaiting case, an unclear one for the manual review."""
    request = s.InspectionCreateRequest.model_validate(load(f"{request_name}.json"))
    view = s.InspectionView.model_validate(load(f"{inspection_name}.json"))
    assert view.return_id == request.return_id
    photo = inspections.decode(request.photo)
    assert inspections.mock_outcome(photo.data, photo.media_type) == outcome
    result = inspections.MockVision().checklist(photo, request.return_id)
    assert view.checklist == result.items
    generation = view.generation
    assert generation == inspections.generation_of(
        result, generation.generation_id, generation.created_at
    )
    claim = view.proposal.amount.provenance if view.proposal is not None else view.provenance
    verdict = inspections.assess(result, view.path, _line_price(view.return_id), claim)
    assert (verdict.state, verdict.manual_review_reason, verdict.proposal) == (
        view.state,
        view.manual_review_reason,
        view.proposal,
    )
    assert (view.proposal is not None) == (outcome == "damaged")


# ---- Catalogue tasks: the case's own return on its card (US-606, ADR-15 H14) -------------------


def test_a_card_anomaly_lists_the_case_return_among_the_returns_a_task_can_name() -> None:
    """A catalogue task may name any queued return of the anomaly's variant (the case that led
    to it). The anomaly of a case card lists the case's own return among queued_return_ids even
    when it is not among the anomaly's returns: Elena's return is requested after the anomaly's
    snapshot, so live it never is (ADR-15 section 18). Nothing else outside the anomaly's
    returns. The manager's list keeps the returns of the anomaly's week only, so its item for
    the same anomaly does not offer her return; the rest of the item is the card's anomaly."""
    card = load("case_card.elena.json")
    own = card["return_id"]
    anomaly = card["product_anomaly"]
    assert own in anomaly["queued_return_ids"]
    assert own not in anomaly["return_ids"]
    s.CaseCard.model_validate(card)
    refused(s.CaseCard, mutated(card, ("product_anomaly", "queued_return_ids"), []))
    stranger = sorted([own, "RET-999999"])
    refused(s.CaseCard, mutated(card, ("product_anomaly", "queued_return_ids"), stranger))
    listing = load("product_anomalies.json")
    items = listing["items"]
    i = next(i for i, x in enumerate(items) if x["anomaly_id"] == anomaly["anomaly_id"])
    item = items[i]
    assert own not in item["queued_return_ids"]
    assert {**anomaly, "queued_return_ids": item["queued_return_ids"]} == item
    offered = sorted([*item["queued_return_ids"], own])
    listing = mutated(listing, ("items", i, "queued_return_ids"), offered)
    refused(s.ProductAnomaliesResponse, listing)


def test_the_live_elena_card_offers_her_return_to_the_catalogue_task(api: Api) -> None:
    """The Elena beat: from her card, Luca opens the task of the anomaly naming her return."""
    elena = golden_return(api.serving, ELENA)
    response = api.client.get(f"/api/returns/{elena}")
    assert response.status_code == 200, response.text
    card = response.json()
    validate_body("case_card", card)
    anomaly = card["product_anomaly"]
    if anomaly is None:
        pytest.skip("no anomaly on the defective jacket in the tiny store")
    assert elena in anomaly["queued_return_ids"]
    assert elena not in anomaly["return_ids"]  # requested after the snapshot, as in the example
    assert set(anomaly["queued_return_ids"]) - {elena} <= set(anomaly["return_ids"])
    task = {
        "anomaly_id": anomaly["anomaly_id"],
        "kind": "batch_check",
        "note": "Check the seams of the batch the customer received",
        "author": "luca",
        "return_id": elena,
    }
    created = api.client.post("/api/catalog-tasks", json=task)
    assert created.status_code == 201, created.text
    validate_body("catalog_task", created.json())
    assert created.json()["return_id"] == elena


# ---- The demo dispute: a link that changes the decision, or says it does not (US-310) ---------


def _changed(recompute: dict[str, Any]) -> bool:
    before, after = recompute["before"], recompute["after"]
    return (before["quadrant"], before["lever_ids"]) != (after["quadrant"], after["lever_ids"])


def test_the_demo_dispute_says_whether_its_recompute_changes_the_decision(api: Api) -> None:
    """changes_decision is what the dispute then does to the engine's quadrant and levers: the
    UI says "decision unchanged" when it is false."""
    response = api.client.get("/api/demo/cases")
    assert response.status_code == 200, response.text
    validate_body("demo_cases", response.json())
    dispute = response.json()["dispute"]
    if dispute is None:
        pytest.skip("no link to dispute in the tiny store")
    request = {
        "reason": "Two different households, confirmed by phone",
        "author": "sara",
        "return_id": dispute["return_id"],
        "decision_id": dispute["decision_id"],
    }
    result = api.client.post(f"/api/links/{dispute['link_id']}/dispute", json=request)
    assert result.status_code == 200, result.text
    validate_body("link_dispute_response", result.json())
    recompute = result.json()["recompute"]
    assert recompute["return_id"] == dispute["return_id"]
    assert dispute["changes_decision"] is _changed(recompute)


def test_the_demo_dispute_prefers_a_link_whose_recompute_changes_the_decision(api: Api) -> None:
    """find_dispute dry-runs the candidate links in their order (at most MAX_DISPUTE_DRY_RUNS)
    and takes the first whose recompute changes the decision, else the first candidate."""
    rows = api.run(demo._cases)
    tried: list[tuple[str, str]] = []

    def never(graph: Serving, record: ReturnRecord, row: Mapping[str, Any]) -> bool:
        tried.append((record.return_id, str(row["decision_id"])))
        return False

    first = demo.find_dispute(api.serving, rows, never)
    if first is None:
        pytest.skip("no link to dispute in the tiny store")
    assert not first.changes_decision
    assert 1 <= len(tried) <= demo.MAX_DISPUTE_DRY_RUNS
    assert tried[0] == (first.return_id, first.decision_id)
    if len(tried) < 2:
        pytest.skip("one candidate link in the tiny store")
    calls: list[int] = []

    def second(graph: Serving, record: ReturnRecord, row: Mapping[str, Any]) -> bool:
        calls.append(1)
        return len(calls) == 2

    chosen = demo.find_dispute(api.serving, rows, second)
    assert chosen is not None and chosen.changes_decision and len(calls) == 2
    assert (chosen.return_id, chosen.link_id) != (first.return_id, first.link_id)


# ---- Model versions: the keys of the serving manifest ------------------------------------------


def _model_versions(node: Any) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "model_versions" and isinstance(value, dict):
                found.append(value)
            else:
                found += _model_versions(value)
    elif isinstance(node, list):
        for value in node:
            found += _model_versions(value)
    return found


def test_example_model_versions_have_the_keys_of_the_serving_manifest(serving: Serving) -> None:
    """Every model_versions of the examples (decisions, audit entries, /readyz) has the keys the
    live API records: those of the serving manifest (pipeline.serving.export)."""
    keys = set(MODEL_VERSION_KEYS)
    assert set(serving.model_versions()) == keys
    found = 0
    for path in sorted(EXAMPLES.glob("*.json")):
        for versions in _model_versions(json.loads(path.read_text(encoding="utf-8"))):
            assert set(versions) == keys, path.name
            found += 1
    assert found > 20
