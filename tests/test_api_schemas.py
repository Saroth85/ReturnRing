"""HTTP API contract (ADR-15): models, exported JSON Schemas, examples and the endpoint table."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from apps.api import schemas as s
from contracts.io import NATURES
from contracts.levers import lever_json_schema
from contracts.parquet import ANOMALY_STATUSES, COUPON_KINDS, IDENTIFIER_KINDS
from contracts.vocab import ASSUMPTION_BASES, CLAUSE_KINDS, DECISION_STATUSES, LANGS
from llm.legal_clause import CLAUSES
from policy.engine import NAVIGLI_BASE_TERMS, levers_for
from policy.simulator import PROPOSAL_EXAMPLE, net_benefit, sensitivity
from policy.types import DecisionState, Quadrant
from scripts import build_api_examples, export_api_schemas

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples" / "api"
SCHEMAS = ROOT / "contracts" / "json" / "api"
EXAMPLE_FILES = sorted(p for p in EXAMPLES.glob("*.json") if p.name != "index.json")


def model_name(path: Path) -> str:
    return path.name.split(".")[0]


def load(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    return data


# ---- Examples, schemas and freshness -----------------------------------------------------------


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_passes_its_model_and_its_json_schema(path: Path) -> None:
    name = model_name(path)
    s.API_MODELS[name].model_validate_json(path.read_text(encoding="utf-8"))
    document = schema(name)
    Draft202012Validator.check_schema(document)
    Draft202012Validator(document).validate(json.loads(path.read_text(encoding="utf-8")))


def test_every_model_has_a_schema_and_an_example() -> None:
    with_examples = {model_name(p) for p in EXAMPLE_FILES}
    assert set(s.API_MODELS) <= with_examples
    assert {p.name for p in SCHEMAS.glob("*.schema.json")} == {
        f"{name}.schema.json" for name in s.API_MODELS
    }


def test_exported_schemas_are_fresh() -> None:
    assert export_api_schemas.check() == [], "run: python -m scripts.export_api_schemas"


def test_examples_are_fresh() -> None:
    assert build_api_examples.check_files() == [], "run: python -m scripts.build_api_examples"


def test_export_check_reports_changed_missing_and_stale_files(tmp_path: Path) -> None:
    export_api_schemas.write(tmp_path)
    assert export_api_schemas.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "case_card.schema.json").write_text("{}")
    (tmp_path / "kpi.schema.json").unlink()
    (tmp_path / "old.schema.json").write_text("{}")
    assert set(export_api_schemas.check(tmp_path)) == {
        "differs: case_card.schema.json",
        "missing: kpi.schema.json",
        "stale: old.schema.json",
    }
    assert export_api_schemas.main(["--check", "--out", str(tmp_path)]) == 1
    assert export_api_schemas.main(["--out", str(tmp_path)]) == 0
    assert export_api_schemas.check(tmp_path) == []


def test_examples_check_reports_changed_files(tmp_path: Path) -> None:
    build_api_examples.write(tmp_path)
    assert build_api_examples.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "kpi.json").write_text("{}")
    (tmp_path / "stale.json").write_text("{}")
    problems = set(build_api_examples.check_files(tmp_path))
    assert problems == {"differs: kpi.json", "stale: stale.json"}
    assert build_api_examples.main(["--check", "--out", str(tmp_path)]) == 1


def test_lever_schema_is_the_catalogue_schema() -> None:
    """ADR-13: one lever schema everywhere, generated from contracts/levers.py."""
    for path in SCHEMAS.glob("*.schema.json"):
        defs = json.loads(path.read_text()).get("$defs", {})
        if "Lever" in defs:
            assert defs["Lever"] == lever_json_schema(), path.name
    assert schema("decision_request")["$defs"]["Lever"] == lever_json_schema()


def test_responses_list_every_field_as_required() -> None:
    """The API always sends every field of a response (null included)."""
    card = schema("case_card")
    assert set(card["required"]) == set(card["properties"])
    query = schema("queue_query")  # inputs keep their defaults optional
    assert "required" not in query


def test_metric_requires_its_provenance() -> None:
    metric = schema("kpi")["$defs"]["Metric"]
    assert set(metric["required"]) == {"value", "unit", "provenance"}
    provenance = schema("kpi")["$defs"]["Provenance"]
    assert provenance["properties"]["nature"]["enum"] == list(NATURES)


# ---- Endpoints ----------------------------------------------------------------------------------

SPEC_SECTION_9 = {
    ("GET", "/api/returns"),
    ("GET", "/api/returns/{return_id}"),
    ("POST", "/api/returns/{return_id}/decision"),
    ("POST", "/api/returns/{return_id}/regenerate"),
    ("POST", "/api/checkout/evaluate"),
    ("POST", "/api/inspections"),
    ("POST", "/api/inspections/{inspection_id}/confirm"),
    ("GET", "/api/portfolio/matrix"),
    ("GET", "/api/kpi"),
    ("GET", "/api/fairness"),
    ("GET", "/api/audit"),
    ("POST", "/api/agent/ask"),
    ("GET", "/api/benchmarks"),
    ("GET", "/api/telemetry"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/api/products/anomalies"),
    ("POST", "/api/catalog-tasks"),
    ("POST", "/api/returns/{return_id}/evidence"),
    ("POST", "/api/links/{link_id}/dispute"),
    ("GET", "/api/simulator/assumptions"),
    ("PUT", "/api/simulator/assumptions"),
    ("POST", "/api/checkout/confirm"),  # US-403: the snapshot shown is stored
}


def test_every_endpoint_of_spec_section_9_is_in_the_contract() -> None:
    keys = [(ep.method, ep.path) for ep in s.ENDPOINTS]
    assert len(keys) == len(set(keys))
    assert set(keys) >= SPEC_SECTION_9


def test_endpoint_models_are_registered() -> None:
    for ep in s.ENDPOINTS:
        for name in (ep.request, ep.query, ep.response):
            assert name is None or name in s.API_MODELS, (ep.path, name)
        assert ep.stories and all(x.startswith("US-") for x in ep.stories)
        assert ep.method in ("GET", "POST", "PUT", "PATCH")
        assert (ep.method == "GET") == (ep.request is None), ep.path


def test_endpoints_json_and_openapi_list_every_endpoint() -> None:
    document = json.loads((SCHEMAS / "endpoints.json").read_text())
    assert document["endpoints"] == [ep.as_dict() for ep in s.ENDPOINTS]
    assert document["contract_version"] == s.API_CONTRACT_VERSION
    openapi = json.loads((SCHEMAS / "openapi.json").read_text())
    found = {(m.upper(), p) for p, ops in openapi["paths"].items() for m in ops}
    assert found == {(ep.method, ep.path) for ep in s.ENDPOINTS}


def test_mock_routes_cover_every_endpoint_with_valid_examples() -> None:
    index = load("index.json")
    endpoints = {(ep.method, ep.path): ep for ep in s.ENDPOINTS}
    covered = set()
    for route in index["routes"]:
        ep = endpoints[(route["method"], route["endpoint"])]
        covered.add((ep.method, ep.path))
        response_model = "error" if route["status"] >= 400 else ep.response
        assert model_name(Path(route["response"])) == response_model, route
        if route["status"] < 400:
            assert route["status"] == ep.status, route
        assert route["request"] is None or model_name(Path(route["request"])) == ep.request
        assert route["query"] is None or model_name(Path(route["query"])) == ep.query
        for key in ("response", "request", "query"):
            assert route[key] is None or (EXAMPLES / route[key]).exists(), route[key]
    assert covered == set(endpoints)


def test_sse_example_is_the_wire_format_of_the_json_stream() -> None:
    events = s.AgentStream.model_validate(load("agent_stream.marco_link.json")).root
    text = (EXAMPLES / "agent_stream.marco_link.sse").read_text(encoding="utf-8")
    assert text == "".join(s.sse_frame(e) for e in events)
    first = text.split("\n\n")[0].splitlines()
    assert first[0] == "event: tool_call" and first[1] == "id: 0"
    assert json.loads(first[2].removeprefix("data: "))["tool"] == "get_cluster"


# ---- Vocabularies --------------------------------------------------------------------------------


def test_vocabularies_match_their_sources() -> None:
    assert get_args(s.IdentifierKind) == IDENTIFIER_KINDS
    assert get_args(s.CouponKind) == COUPON_KINDS
    assert get_args(s.AnomalyStatus) == ANOMALY_STATUSES
    assert set(LANGS) == {"it", "en"} and set(CLAUSE_KINDS) == set(CLAUSES)
    assert {x.value for x in DecisionState} <= set(DECISION_STATUSES)
    assert tuple(net_benefit(PROPOSAL_EXAMPLE).components) == s.NET_BENEFIT_COMPONENTS
    assert tuple(sensitivity(PROPOSAL_EXAMPLE)) == s.SENSITIVITY_IDS
    assert set(ASSUMPTION_BASES) == {"simulated", "realized"}
    assert len(get_args(s.AgentTool)) == s.MAX_AGENT_STEPS == 5  # spec 8.8: five read-only tools


# ---- Provenance and wording (rule 11, US-906) ---------------------------------------------------


def _walk(node: Any) -> Iterator[Any]:
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_numbers_are_labelled_contract_examples(path: Path) -> None:
    """Mock data is synthetic or simulated and says it is an example, never a result."""
    for node in _walk(json.loads(path.read_text(encoding="utf-8"))):
        if isinstance(node, dict) and {"origin", "nature", "period_start"} <= set(node):
            assert node["nature"] in {"synthetic", "simulated"}, path.name
            assert node["origin"].startswith("Contract example: "), node["origin"]


def test_linked_accounts_never_same_person() -> None:
    texts = [p.read_text(encoding="utf-8").lower() for p in EXAMPLES.iterdir()]
    texts += [p.read_text(encoding="utf-8").lower() for p in SCHEMAS.iterdir()]
    assert not any("same person" in t or "stessa persona" in t or "same_person" in t for t in texts)
    assert any("linked accounts" in t for t in texts)


# ---- Helpers -------------------------------------------------------------------------------------


def test_link_ids() -> None:
    assert s.link_id("C-2", "C-1") == "C-1~C-2"
    assert s.split_link_id("C-1~C-2") == ("C-1", "C-2")
    for bad in ("C-2~C-1", "C-1", "C-1~C-1", "a~b~c"):
        with pytest.raises(ValueError):
            s.split_link_id(bad)
    with pytest.raises(ValueError):
        s.link_id("C-1", "C-1")


def test_anomaly_id() -> None:
    from policy.types import ReturnReason

    week = date(2026, 9, 21)
    assert s.anomaly_id("V-1", ReturnReason.DEFECTIVE, week) == "V-1~defective~2026-09-21"


@pytest.mark.parametrize("quadrant", list(Quadrant))
@pytest.mark.parametrize("cluster", [True, False])
def test_levers_round_trip_between_policy_and_contract(quadrant: Quadrant, cluster: bool) -> None:
    levers = levers_for(quadrant, NAVIGLI_BASE_TERMS, has_cluster=cluster)
    objects = s.levers_from_policy(levers, NAVIGLI_BASE_TERMS)
    assert s.to_policy_levers(objects, NAVIGLI_BASE_TERMS) == levers
    assert [x.lever_id() for x in objects] == levers.lever_ids(NAVIGLI_BASE_TERMS)


def test_to_policy_levers_rejects_duplicates_and_no_op_levers() -> None:
    window = s.Lever(id="return_window_days", value=14, scope="future_orders")
    with pytest.raises(ValueError, match="twice"):
        s.to_policy_levers([window, window], NAVIGLI_BASE_TERMS)
    no_op = s.Lever(id="instant_refund", value=True, scope="future_orders")
    with pytest.raises(ValueError, match="standard"):
        s.to_policy_levers([no_op], NAVIGLI_BASE_TERMS)


def test_terms_and_assumptions_round_trip() -> None:
    assert s.Terms.from_policy(NAVIGLI_BASE_TERMS).to_policy() == NAVIGLI_BASE_TERMS
    assert s.Assumptions.from_policy(PROPOSAL_EXAMPLE).as_policy() == PROPOSAL_EXAMPLE


def test_metric_takes_decimals_from_code() -> None:
    provenance = s.Provenance.model_validate(load("kpi.json")["decision_time_p50"]["provenance"])
    eur = s.Metric.model_validate(
        {"value": Decimal("368.00"), "unit": "eur", "provenance": provenance}
    )
    assert eur.value == 368.0
    count = s.Metric.model_validate(
        {"value": Decimal("7"), "unit": "count", "provenance": provenance}
    )
    assert count.value == 7 and isinstance(count.value, int)


def test_human_actors_exclude_the_system() -> None:
    body = load("decision_request.marco_approve.json")
    s.DecisionRequest.model_validate(body)
    for actor in ("system", "Sara", "", "s"):
        with pytest.raises(ValidationError):
            s.DecisionRequest.model_validate({**body, "approver": actor})


# ---- Semantic rules: each mutation must be rejected by the model -------------------------------

Path_ = tuple[str | int, ...]


def mutated(doc: Any, path: Path_, value: Any) -> Any:
    out = copy.deepcopy(doc)
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


DELETE = object()
GUARANTEE_LEVER = {"id": "free_return_shipping", "value": False, "scope": "future_orders"}

REJECTED: list[tuple[str, str, Path_, Any]] = [
    # Metric and provenance
    ("kpi.json", "kpi", ("abstention_rate", "value"), 1.5),
    ("kpi.json", "kpi", ("abstentions_insufficient_evidence", "value"), 9.5),
    ("kpi.json", "kpi", ("abstentions_insufficient_evidence", "value"), True),
    ("case_card.giulia.json", "case_card", ("value", "value_eur", "value"), 211.855),
    ("kpi.json", "kpi", ("decision_time_p50", "provenance", "nature"), "estimated"),
    ("kpi.json", "kpi", ("decision_time_p50", "provenance", "period_start"), "2027-01-01"),
    ("kpi.json", "kpi", ("decision_time_p50", "provenance", "origin"), ""),
    # levers
    (
        "decision_request.marco_approve.json",
        "decision_request",
        ("levers", 0, "scope"),
        "current_order",
    ),  # noqa: E501
    ("decision_request.marco_approve.json", "decision_request", ("levers", 0, "value"), 7),
    (
        "decision_request.marco_approve.json",
        "decision_request",
        ("levers", 0, "id"),
        "welcome_coupon",
    ),  # noqa: E501
    ("decision_request.marco_approve.json", "decision_request", ("levers", 1, "value"), 0),
    # decision request (US-306)
    ("decision_request.override.json", "decision_request", ("override_reason",), None),
    ("decision_request.override.json", "decision_request", ("override_reason",), "   "),
    ("decision_request.override.json", "decision_request", ("override_category",), None),
    ("decision_request.override.json", "decision_request", ("levers",), None),
    ("decision_request.reject.json", "decision_request", ("levers",), []),
    ("decision_request.reject.json", "decision_request", ("override_reason",), None),
    ("decision_request.marco_approve.json", "decision_request", ("override_reason",), "why not"),
    ("decision_request.marco_approve.json", "decision_request", ("message",), DELETE),
    (
        "decision_request.override.json",
        "decision_request",
        ("message", "text"),
        "Rimborso di 12 euro.",
    ),  # noqa: E501
    ("decision_request.override.json", "decision_request", ("message", "text"), "Entro il {date}."),  # noqa: E501
    ("decision_request.override.json", "decision_request", ("message", "text"), "Sospetta frode."),  # noqa: E501
    # decision invariants (spec 8.6, rules 3 to 5, US-307, US-607)
    ("case_card.marco.json", "case_card", ("decision", "requires_approval"), False),
    ("case_card.marco.json", "case_card", ("decision", "review_at"), None),
    ("case_card.marco.json", "case_card", ("decision", "review_at"), "2027-09-30"),
    ("case_card.marco.json", "case_card", ("decision", "approver"), "sara"),
    ("case_card.marco.json", "case_card", ("decision", "refund_due_by"), "2026-10-30"),
    ("case_card.marco.json", "case_card", ("decision", "lever_ids", 0), "return_window_days:15"),
    ("case_card.marco.json", "case_card", ("decision", "future_terms", "instant_refund"), True),
    ("case_card.marco.json", "case_card", ("decision", "coupon_rule"), None),
    ("case_card.marco.json", "case_card", ("decision", "in_force"), True),
    (
        "case_card.marco.json",
        "case_card",
        ("decision", "current_return_terms", "return_window_days"),
        14,
    ),  # noqa: E501
    ("case_card.marco.json", "case_card", ("decision", "state"), "rejected"),
    ("case_card.elena.json", "case_card", ("decision", "quadrant"), "Q4"),
    ("case_card.elena.json", "case_card", ("decision", "proposed_levers"), [GUARANTEE_LEVER]),
    ("case_card.elena.json", "case_card", ("decision", "inputs", "risk"), 0.2),
    ("case_card.elena.json", "case_card", ("decision", "missing_evidence"), ["no data"]),
    ("decision_result.marco_approved.json", "decision_result", ("decision", "approver"), None),
    (
        "decision_result.marco_approved.json",
        "decision_result",
        ("decision", "coupon_rule", "confirmed_by"),
        None,
    ),  # noqa: E501
    ("decision_result.override.json", "decision_result", ("decision", "override_reason"), None),
    (
        "decision_result.override.json",
        "decision_result",
        ("outbox_message", "legal_clause_kind"),
        "withdrawal",
    ),  # noqa: E501
    # case card (rules 1, 4 and 10; US-302, US-308)
    (
        "case_card.marco.json",
        "case_card",
        ("draft", "decision_echo", "lever_ids"),
        ["instant_refund:false"],
    ),  # noqa: E501
    ("case_card.marco.json", "case_card", ("draft", "messages", 0, "legal_clause_kind"), None),
    ("case_card.marco.json", "case_card", ("draft", "messages", 0, "rendered_text"), "Ciao."),
    (
        "case_card.marco.json",
        "case_card",
        ("draft", "messages", 0, "connective_text"),
        "Rimborso entro 14 giorni.",
    ),  # noqa: E501
    (
        "case_card.marco.json",
        "case_card",
        ("draft", "messages", 1, "connective_text"),
        "We noticed abuse: {terms}.",
    ),  # noqa: E501
    (
        "case_card.marco.json",
        "case_card",
        ("draft", "explanation", 2, "text"),
        "It is the same person.",
    ),  # noqa: E501
    ("case_card.marco.json", "case_card", ("alternatives", 0, "recommended"), False),
    ("case_card.marco.json", "case_card", ("alternatives", 1, "recommended"), True),
    ("case_card.marco.json", "case_card", ("alternatives", 0, "levers"), []),
    (
        "case_card.marco.json",
        "case_card",
        ("alternatives", 0, "net_benefit", "total", "value"),
        999.0,
    ),  # noqa: E501
    (
        "case_card.marco.json",
        "case_card",
        ("alternatives", 0, "net_benefit", "components", 0, "amount", "value"),
        1.0,
    ),  # noqa: E501
    (
        "case_card.marco.json",
        "case_card",
        ("alternatives", 0, "net_benefit", "components", 0, "id"),
        "refund_saved",
    ),  # noqa: E501
    ("case_card.marco.json", "case_card", ("risk", "score", "value"), 0.5),
    ("case_card.marco.json", "case_card", ("risk", "band"), "low"),
    ("case_card.marco.json", "case_card", ("risk", "top_factors", 0, "feature"), "is_male"),
    ("case_card.marco.json", "case_card", ("risk", "top_factors", 0, "feature"), "value_eur"),
    ("case_card.marco.json", "case_card", ("risk", "top_factors", 0, "direction"), "lowers"),
    ("case_card.marco.json", "case_card", ("risk", "top_factors"), []),
    ("case_card.marco.json", "case_card", ("linked_accounts", "cluster_id"), "R-MARCO"),
    (
        "case_card.marco.json",
        "case_card",
        ("linked_accounts", "links", 0, "link_id"),
        "C-MARCO-2~C-MARCO-1",
    ),  # noqa: E501
    ("case_card.marco.json", "case_card", ("linked_accounts", "links", 0, "weight_decayed"), 0.99),
    ("case_card.marco.json", "case_card", ("linked_accounts", "links", 0, "disputed"), True),
    ("case_card.marco.json", "case_card", ("linked_accounts", "cluster_size"), 6),
    ("case_card.marco.json", "case_card", ("value", "kept_margin_eur", "value"), 100.0),
    ("case_card.marco.json", "case_card", ("orders", "return_rate", "value"), 0.2),
    ("case_card.marco.json", "case_card", ("order_terms", "confirmed_at"), None),
    ("case_card.marco.json", "case_card", ("request", "days_since_delivery"), 2),
    ("case_card.marco.json", "case_card", ("revisions", 0, "version"), 2),
    (
        "case_card.elena.json",
        "case_card",
        ("draft", "messages", 0, "legal_clause_kind"),
        "withdrawal",
    ),  # noqa: E501
    ("case_card.elena.json", "case_card", ("draft", "explanation", 1, "label"), "SCORE"),
    ("case_card.elena.json", "case_card", ("product_anomaly", "z", "value"), 3.5),
    ("case_card.elena.json", "case_card", ("product_anomaly", "anomaly_id"), "V-JKT-DEF"),
    ("case_card.giulia.json", "case_card", ("evidence", "components", 0, "passed"), False),
    ("case_card.giulia.json", "case_card", ("evidence", "quality"), "insufficient"),
    ("case_card.giulia.json", "case_card", ("draft_status",), "pending"),
    # queue (US-301, US-307)
    (
        "queue_response.json",
        "queue_response",
        ("items", 1, "risk"),
        {
            "value": 0.1,
            "unit": "probability",
            "provenance": {
                "origin": "x",
                "period_start": "2026-01-01",
                "period_end": "2026-01-02",
                "nature": "synthetic",
            },
        },
    ),  # noqa: E501
    ("queue_response.json", "queue_response", ("items", 0, "requires_approval"), False),
    ("queue_response.json", "queue_response", ("page_size",), 3),
    # checkout (US-401, US-402, LEG-01)
    (
        "checkout_evaluate_response.marco.json",
        "checkout_evaluate_response",
        ("coupon_eligible",),
        True,
    ),  # noqa: E501
    (
        "checkout_evaluate_response.marco.json",
        "checkout_evaluate_response",
        ("terms", "return_window_days"),
        7,
    ),  # noqa: E501
    (
        "checkout_evaluate_response.giulia.json",
        "checkout_evaluate_response",
        ("lines", 0, "p_return"),
        0.5,
    ),  # noqa: E501
    (
        "checkout_evaluate_response.giulia.json",
        "checkout_evaluate_response",
        ("terms_source",),
        "base",
    ),  # noqa: E501
    ("checkout_confirm_response.giulia.json", "checkout_confirm_response", ("order_id",), "ORD-X"),
    # inspection (US-501..503, LEG-05)
    (
        "inspection.manual_review.json",
        "inspection",
        ("proposal",),
        load("inspection.awaiting.json")["proposal"],
    ),  # noqa: E501
    ("inspection.awaiting.json", "inspection", ("path",), "guarantee"),
    ("inspection.awaiting.json", "inspection", ("confidence_is_calibrated",), True),
    ("inspection.awaiting.json", "inspection", ("checklist", 0, "item"), "visible_damage"),
    (
        "inspection_confirm_response.json",
        "inspection_confirm_response",
        ("inspection", "confirmed_observations", 1, "observation"),
        "not_observable",
    ),  # noqa: E501
    (
        "inspection_confirm_response.json",
        "inspection_confirm_response",
        ("claim", "confirmed_by"),
        "sara",
    ),  # noqa: E501
    # matrix (US-601, ADR-04)
    ("portfolio_matrix.json", "portfolio_matrix", ("points", 0, "quadrant"), "Q4"),
    ("portfolio_matrix.json", "portfolio_matrix", ("quadrants", 0, "customers", "value"), 1),
    ("portfolio_matrix.json", "portfolio_matrix", ("insufficient_evidence", "value"), 0),
    # simulator, KPI, fairness (US-602, US-603, US-605)
    (
        "simulator_assumptions.v1.json",
        "simulator_assumptions",
        ("current", "sensitivity", 3, "net_benefit", "total", "value"),
        -191.0,
    ),  # noqa: E501
    (
        "simulator_assumptions.v1.json",
        "simulator_assumptions",
        ("current", "net_benefit", "assumptions", "incentives_eur"),
        -5.0,
    ),  # noqa: E501
    ("kpi.json", "kpi", ("period_end",), "2026-09-28"),
    ("kpi.json", "kpi", ("decisions", "total", "value"), 127),
    ("fairness.json", "fairness", ("dimensions", 2, "warning"), False),
    ("fairness.json", "fairness", ("dimensions", 0, "max_gap", "value"), 3.0),
    # audit and outbox (rule 5, US-604)
    ("audit_page.json", "audit_page", ("items", 0, "actor"), "system"),
    ("decision_result.override.json", "decision_result", ("audit_entry", "reason"), None),
    ("decision_result.marco_approved.json", "decision_result", ("outbox_message", "body"), "Ciao."),
    # agent stream (US-701)
    ("agent_stream.marco_link.json", "agent_stream", (1, "seq"), 7),
    ("agent_stream.marco_link.json", "agent_stream", (1, "tool"), "get_orders"),
    ("agent_stream.marco_link.json", "agent_stream", (-1, "steps_used"), 1),
    ("agent_stream.marco_link.json", "agent_stream", (-2, "text"), "Another answer."),
    ("agent_stream.write_refused.json", "agent_stream", (0, "type"), "token"),
    # catalogue, coupons, ops
    ("catalog_tasks.json", "catalog_tasks", ("items", 1, "done_at"), None),
    ("catalog_tasks.json", "catalog_tasks", ("items", 1, "follow_up", "caveat"), "It worked."),
    ("coupon_rules.json", "coupon_rules", ("applications", 0, "confirmed_by"), None),
    ("coupon_rules.json", "coupon_rules", ("applications", 1, "reversal_reason"), None),
    ("coupon_rules.json", "coupon_rules", ("applications", 0, "reversible"), False),
    ("coupon_rules.after_create.json", "coupon_rules", ("history", 1, "active"), True),
    ("benchmarks.json", "benchmarks", ("items", 0, "status"), "measured"),
    ("benchmarks.json", "benchmarks", ("items", 1, "measured_at"), "2026-10-13T21:00:00Z"),
    ("readyz_response.cached.json", "readyz_response", ("label",), None),
    ("telemetry.cached.json", "telemetry", ("note",), None),
    ("recompute_response.rossi_card.json", "recompute_response", ("after", "version"), 3),
    ("link_dispute_response.json", "link_dispute_response", ("link", "disputed"), False),
    ("evidence_request.rossi_card.json", "evidence_request", ("target_link_ids",), []),
    ("evidence_request.rossi_card.json", "evidence_request", ("target_link_ids", 0), "C-2~C-1"),
]


@pytest.mark.parametrize(("file", "model", "path", "value"), REJECTED, ids=lambda x: str(x)[:40])
def test_semantic_rule_rejects_the_mutation(file: str, model: str, path: Path_, value: Any) -> None:
    doc = load(file)
    s.API_MODELS[model].model_validate(doc)  # the example itself is valid
    with pytest.raises(ValidationError):
        s.API_MODELS[model].model_validate(mutated(doc, path, value))


def test_every_api_model_forbids_unknown_fields() -> None:
    def models(root: type[BaseModel]) -> Iterator[type[BaseModel]]:
        yield root
        for sub in root.__subclasses__():
            yield from models(sub)

    for model in models(s.ApiModel):
        assert model.model_config.get("extra") == "forbid", model.__name__
    with pytest.raises(ValidationError):
        s.KpiResponse.model_validate({**load("kpi.json"), "same_person": True})
