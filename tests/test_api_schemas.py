"""HTTP API contract (ADR-15): models, exported JSON Schemas, examples and the endpoint table."""

from __future__ import annotations

import copy
import functools
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from apps.api import schemas as s
from contracts.io import NATURES
from contracts.levers import lever_json_schema, lever_list_json_schema
from contracts.parquet import ANOMALY_STATUSES, COUPON_KINDS, IDENTIFIER_KINDS
from contracts.vocab import (
    ASSUMPTION_BASES,
    CLAUSE_KINDS,
    DECISION_AUDIT_ACTIONS,
    DECISION_STATUSES,
    FALLBACK_REASONS,
    GENERATION_PATHS,
    HUMAN_ONLY_ACTIONS,
    LANGS,
    REASON_REQUIRED_ACTIONS,
    VALIDATOR_CODES,
)
from llm import messages as llm_messages
from llm.client import LlmUnavailable, MockClient
from llm.legal_clause import CLAUSES
from llm.phrasebook import render_selection
from policy.engine import NAVIGLI_BASE_TERMS, levers_for
from policy.legal_guard import check
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
    """ADR-13, ADR-14: one lever schema everywhere, generated from contracts/levers.py: every
    lever array is lever_list_json_schema (each lever at most once), with its items the catalogue
    schema."""

    def arrays(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if node.get("type") == "array" and node.get("items") == lever_json_schema():
                yield node
            for child in node.values():
                yield from arrays(child)
        elif isinstance(node, list):
            for child in node:
                yield from arrays(child)

    found = 0
    for path in SCHEMAS.glob("*.json"):
        doc = json.loads(path.read_text())
        assert "Lever" not in doc.get("$defs", {}), path.name
        for array in arrays(doc):
            assert {k: v for k, v in array.items() if k != "title"} == lever_list_json_schema()
            found += 1
    assert found >= 10
    levers = schema("decision_request")["properties"]["levers"]["anyOf"][0]
    assert {k: v for k, v in levers.items() if k != "title"} == lever_list_json_schema()


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


def test_llm_vocabularies_are_the_llm_layer_ones() -> None:
    """The generation records use the paths of llm.generate, the codes of llm.validator and its
    fallback reasons; sentence ids and fact entities are those of the phrasebook and the case."""
    from llm.case import ENTITIES
    from llm.generate import GenerationBlocked, GenerationPath, generate
    from llm.phrasebook import SENTENCE_IDS
    from llm.validator import Code
    from tests.llm_factories import make

    assert tuple(p.value for p in GenerationPath) == GENERATION_PATHS
    assert tuple(c.value for c in Code) == VALIDATOR_CODES
    assert get_args(s.FactEntity) == ENTITIES
    schema_ids = schema("case_card")["$defs"]["CustomerMessageView"]["properties"]["sentence_ids"]
    assert schema_ids["items"]["enum"] == list(SENTENCE_IDS)
    built = make("Q4")
    bad = {"decision_echo": {"path": "withdrawal", "quadrant": "Q1", "lever_ids": []}}
    reasons = {
        generate(built.case, built.expected, MockClient([LlmUnavailable("down")])).fallback_reason,
        generate(built.case, built.expected, MockClient(["{}", "{}"])).fallback_reason,
        generate(
            built.case,
            built.expected,
            MockClient([{**load_llm_output(), **bad}]),
        ).fallback_reason,
    }
    assert reasons == set(FALLBACK_REASONS)
    assert GenerationBlocked  # a blocked generation has no record: it is a code bug


def load_llm_output() -> dict[str, Any]:
    out: dict[str, Any] = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts/examples/llm_output.json").read_text()
    )
    return out


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
    # the operator picks phrasebook ids, never free text (ADR-14 decision 8)
    ("decision_request.override.json", "decision_request", ("message", "text"),
     "Rimborso di 12 euro."),
    ("decision_request.override.json", "decision_request", ("message", "sentence_ids"),
     ["CLOSE_DAY", "OPEN_THANKS"]),
    ("decision_request.override.json", "decision_request", ("message", "sentence_ids"),
     ["OPEN_THANKS", "CONTACT_TEAM", "CONTACT_TEAM", "CLOSE_DAY"]),
    ("decision_request.override.json", "decision_request", ("message", "sentence_ids"),
     ["OPEN_THANKS", "Sospetta frode.", "CLOSE_DAY"]),
    ("decision_request.lift.json", "decision_request", ("message",), {"lang": "it"}),
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
    ("case_card.marco.json", "case_card", ("draft", "message", "legal_clause_kind"), None),
    ("case_card.marco.json", "case_card", ("draft", "message", "texts", 0, "text"), "Ciao."),
    ("case_card.marco.json", "case_card", ("draft", "message", "sentence_ids"),
     ["OPEN_THANKS", "GUARANTEE_CARE", "CLOSE_REGARDS"]),  # not allowed for Q4
    ("case_card.marco.json", "case_card", ("draft", "message", "sentence_ids"),
     ["OPEN_THANKS", "CLOSE_REGARDS"]),  # the texts are of other ids
    ("case_card.marco.json", "case_card", ("draft", "explanation", 2, "text"),
     "It is the same person."),
    ("case_card.marco.json", "case_card", ("draft", "provenance", "nature"), "real"),
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
    ("case_card.elena.json", "case_card", ("draft", "message", "legal_clause_kind"),
     "withdrawal"),
    ("case_card.elena.json", "case_card", ("draft", "explanation", 0, "label"), "SCORE"),
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
    # review and lift of restrictions (US-607)
    ("decision_request.lift.json", "decision_request", ("override_reason",), None),
    ("decision_request.lift.json", "decision_request", ("levers",), []),
    ("decision_result.lift.json", "decision_result", ("decision", "revision_trigger"), "initial"),
    ("decision_result.lift.json", "decision_result", ("decision", "supersedes"), None),
    ("decision_result.lift.json", "decision_result", ("audit_entry", "action"),
     "decision_overridden"),
    ("case_card.lorenzo.json", "case_card", ("decision", "in_force"), True),
    ("case_card.lorenzo.json", "case_card", ("decision", "superseded_by"), "DEC-000088-2"),
    ("queue_response.review_due.json", "queue_response", ("items", 0, "review_due"), False),
    ("queue_response.json", "queue_response", ("items", 0, "review_due"), True),
    ("queue_response.json", "queue_response", ("items", 0, "review_at"), None),
    # decision versions and history (US-604)
    ("decision_record.chiara_v1.json", "decision_record", ("decision", "in_force"), True),
    ("decision_record.chiara_v1.json", "decision_record", ("alternatives", 0, "recommended"),
     False),
    ("recompute_response.rossi_card.json", "recompute_response", ("decision", "supersedes"), None),
    ("case_card.marco.json", "case_card", ("decision", "inputs", "order_id"), "ORD-000001"),
    ("case_card.marco.json", "case_card", ("decision", "as_of"), "2026-09-30T19:00:00Z"),
    ("decision_result.marco_approved.json", "decision_result", ("audit_entry", "after"),
     {"state": "approved"}),
    ("decision_result.marco_approved.json", "decision_result", ("audit_entry", "after", "state"),
     "rejected"),
    ("audit_page.json", "audit_page", ("items", 0, "entity_type"), "outbox_message"),
    # template fields and outbox (rule 10)
    ("decision_result.marco_approved.json", "decision_result", ("outbox_message", "body"),
     "Ciao."),
    ("decision_result.override.json", "decision_result", ("outbox_message", "sentence_ids"),
     ["OPEN_THANKS", "GUARANTEE_CARE", "CLOSE_DAY"]),
    ("decision_result.override.json", "decision_result", ("outbox_message", "fields", "deadline"),
     "1 gennaio 2027"),
    ("decision_result.lift.json", "decision_result", ("outbox_message",),
     load("decision_result.override.json")["outbox_message"]),
    # explanation statements (US-305, ADR-14): facts are code-built and cited, numbers grounded
    ("case_card.marco.json", "case_card", ("draft", "explanation", 0, "fact_ids"), ["F2", "F3"]),
    ("case_card.marco.json", "case_card", ("draft", "explanation", 0, "facts", 0, "value"), 0.5),
    ("case_card.marco.json", "case_card", ("draft", "explanation", 0, "facts", 0, "nature"),
     "simulated"),
    ("case_card.marco.json", "case_card", ("draft", "explanation", 0, "label"), "OBSERVED"),
    ("case_card.marco.json", "case_card", ("draft", "explanation", 1, "text"),
     "The cluster has 8 linked accounts, created within 9 days."),
    ("case_card.marco.json", "case_card", ("draft", "explanation", 1, "facts", 0, "value"), 8),
    # assumption sets (US-602)
    ("case_card.marco.json", "case_card",
     ("alternatives", 0, "net_benefit", "assumptions", "based_on"), "assumptions-v9"),
    ("case_card.marco.json", "case_card",
     ("alternatives", 1, "net_benefit", "assumptions", "version"), "DEC-000102-1/proposal"),
    ("simulator_assumptions.v1.json", "simulator_assumptions",
     ("current", "sensitivity", 1, "net_benefit", "assumptions", "version"), "assumptions-v1"),
    ("kpi.json", "kpi", ("net_benefit_simulated", "assumptions", "based_on"), None),
    # provenance, terms source, disputes (rule 11, US-403, US-310)
    ("inspection.awaiting.json", "inspection", ("provenance",), DELETE),
    ("queue_response.json", "queue_response", ("provenance",), DELETE),
    ("checkout_evaluate_response.marco.json", "checkout_evaluate_response", ("review_at",), None),
    ("checkout_confirm_response.giulia.json", "checkout_confirm_response",
     ("terms_snapshot", "source"), "base"),
    ("link_dispute_request.json", "link_dispute_request", ("decision_id",), DELETE),
    ("portfolio_matrix.json", "portfolio_matrix", ("points", 6, "risk"), 0.4),
    # restrictions lapse where they reach customers: checkout terms and coupon blocks (US-607)
    ("checkout_evaluate_response.marco.json", "checkout_evaluate_response", ("review_at",),
     "2026-09-01"),
    ("checkout_evaluate_response.marco.json", "checkout_evaluate_response", ("shown_at",),
     "2027-01-10T10:00:00Z"),
    ("checkout_evaluate_response.marco.json", "checkout_evaluate_response",
     ("coupon_block", "review_at"), "2026-09-30"),
    ("coupon_rules.json", "coupon_rules", ("applications", 0, "in_force"), False),
    ("coupon_rules.json", "coupon_rules", ("as_of",), "2026-12-27T08:00:00Z"),
    ("decision_result.marco_approved.json", "decision_result", ("coupon_application",), None),
    ("decision_result.marco_approved.json", "decision_result",
     ("coupon_application", "confirmed_by"), "luca"),
    # a decision result, its audit entry and its message are about one decision (US-604)
    ("decision_result.marco_approved.json", "decision_result", ("outbox_message", "return_id"),
     "RET-000999"),
    ("decision_result.marco_approved.json", "decision_result", ("outbox_message", "customer_id"),
     "C-SOMEONE-ELSE"),
    ("decision_result.marco_approved.json", "decision_result", ("audit_entry", "return_id"),
     "RET-000999"),
    ("decision_result.override.json", "decision_result", ("audit_entry", "reason"),
     "something else entirely"),
    ("decision_result.lift.json", "decision_result", ("audit_entry", "before", "decision_id"),
     "DEC-000999-1"),
    ("decision_result.lift.json", "decision_result", ("audit_entry", "before", "decision_id"),
     "DEC-000088-2"),
    ("inspection_confirm_response.json", "inspection_confirm_response",
     ("outbox_message", "customer_id"), "C-SOMEONE-ELSE"),
    ("inspection_confirm_response.json", "inspection_confirm_response",
     ("inspection", "return_id"), "RET-000999"),
    ("recompute_response.rossi_card.json", "recompute_response", ("return_id",), "RET-000999"),
    # a regenerated draft is checked against the decision it explains (rules 1 and 10)
    ("regenerate_response.json", "regenerate_response", ("draft", "decision_echo", "lever_ids"),
     ["instant_refund:false"]),
    ("regenerate_response.json", "regenerate_response", ("draft", "decision_echo", "quadrant"),
     "Q4"),
    # one label, one set of values: derived labels are checked by the models (US-602)
    ("case_card.marco.json", "case_card",
     ("alternatives", 0, "net_benefit", "assumptions", "version"), "whatever/proposal"),
    ("decision_record.chiara_v1.json", "decision_record",
     ("alternatives", 1, "net_benefit", "assumptions", "version"), "DEC-000106-2/verification"),
    ("kpi.json", "kpi", ("net_benefit_simulated", "assumptions", "version"), "kpi/2026-09-28"),
]  # fmt: skip


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


# ---- Decisions and the legal guard (rules 3 to 5, US-306, US-607) ------------------------------


def test_overridden_view_maps_to_an_approved_policy_decision_without_quadrant() -> None:
    """ADR-15 section 8: the legal guard sees an override as a person's approved decision whose
    levers are the operator's, judged without the engine's quadrant."""
    view = s.DecisionResult.model_validate(load("decision_result.override.json")).decision
    assert view.state == "overridden" and view.quadrant is Quadrant.Q3 and view.levers == []
    decision, request = s.to_policy_decision(view)
    assert decision.state is DecisionState.APPROVED and decision.quadrant is None
    assert decision.approver == view.approver and request.return_id == view.return_id
    assert check(decision, request, NAVIGLI_BASE_TERMS) == []


def test_decision_view_runs_the_legal_guard() -> None:
    """An override the field checks accept but policy.legal_guard refuses is not a valid view:
    an upgrade on a case with insufficient evidence (US-307)."""
    doc = load("case_card.insufficient.json")["decision"]
    upgrade = [{"id": "return_window_days", "value": 60, "scope": "future_orders"}]
    doc.update(
        state="overridden",
        levers=upgrade,
        lever_ids=["return_window_days:60"],
        future_terms={**doc["future_terms"], "return_window_days": 60},
        approver="sara",
        approved_at=doc["created_at"],
        override_reason="Loyal customer known to the shop",
        override_category="customer_context",
    )
    with pytest.raises(ValidationError, match="INSUFFICIENT_EVIDENCE_RESTRICTED"):
        s.DecisionView.model_validate(doc)


def test_a_restriction_lapses_at_its_review_date() -> None:
    """US-607: no permanent label. On its review date an approved restriction stops applying."""
    doc = load("decision_result.marco_approved.json")["decision"]
    assert doc["in_force"] and doc["review_at"]
    lapsed = {**doc, "as_of": f"{doc['review_at']}T08:00:00Z", "in_force": False}
    s.DecisionView.model_validate(lapsed)
    with pytest.raises(ValidationError, match="in_force"):
        s.DecisionView.model_validate({**lapsed, "in_force": True})


def test_lift_takes_a_reason_and_no_levers() -> None:
    body = {
        "decision_id": "DEC-000088-1",
        "action": "lift",
        "override_reason": "Reviewed at the due date: returns back in line",
        "override_category": "periodic_review",
        "approver": "luca",
    }
    assert s.DecisionRequest.model_validate(body).action == "lift"
    for key in ("override_reason", "override_category"):
        with pytest.raises(ValidationError):
            s.DecisionRequest.model_validate({**body, key: None})
    with pytest.raises(ValidationError):
        s.DecisionRequest.model_validate({**body, "levers": []})


def test_review_versions_lift_or_renew_the_restriction() -> None:
    """A version created by a review (trigger review) is a person's outcome of the review: a lift
    (overridden to the standard levers) or a renewal (the engine's fresh restriction, approved).
    Never a proposal, never anything else."""
    lift = s.DecisionResult.model_validate(load("decision_result.lift.json")).decision
    assert (lift.revision_trigger, lift.state, lift.levers) == ("review", "overridden", [])
    assert lift.supersedes is not None and lift.version == 2 and lift.in_force
    renewal = load("decision_result.marco_approved.json")["decision"]
    renewal.update(
        revision_trigger="review", version=2, supersedes="DEC-000102-0",
        created_at=renewal["approved_at"], decided_on=renewal["approved_at"][:10],
    )  # fmt: skip
    s.DecisionView.model_validate(renewal)  # an approved restriction: a renewal
    proposed = load("case_card.marco.json")["decision"]
    proposed.update(revision_trigger="review", version=2, supersedes="DEC-000102-0")
    with pytest.raises(ValidationError, match="trigger review"):
        s.DecisionView.model_validate(proposed)
    upgrade = load("case_card.giulia.json")["decision"]
    upgrade.update(
        revision_trigger="review", version=2, supersedes="DEC-000101-0", state="approved",
        approver="luca", approved_at=upgrade["as_of"],
    )  # fmt: skip
    with pytest.raises(ValidationError, match="trigger review"):
        s.DecisionView.model_validate(upgrade)


def test_queue_filters_restrictions_due_for_review() -> None:
    assert "review_due" in s.QueueQuery.model_fields
    assert {"review_at", "review_due"} <= set(s.QueueItem.model_fields)
    assert "review" in get_args(s.RevisionTrigger)
    assert {"lift", "renew"} <= set(get_args(s.DecisionAction))


def test_every_revision_trigger_has_a_producer() -> None:
    """Initial: the intake function. evidence_added, link_disputed: the recompute. review: a lift
    or a renewal. Nothing produced scores_refreshed, so it is gone (ADR-15 section 10)."""
    assert set(get_args(s.RevisionTrigger)) == {
        "initial", "evidence_added", "link_disputed", "review"
    }  # fmt: skip
    assert "restriction_renewed" in DECISION_AUDIT_ACTIONS & HUMAN_ONLY_ACTIONS
    assert "restriction_renewed" not in REASON_REQUIRED_ACTIONS  # like approve


def test_renew_takes_no_levers_and_no_reason() -> None:
    """renew approves the engine's fresh restriction, as approve does: no levers, no reason."""
    body = {
        "decision_id": "DEC-000088-1",
        "action": "renew",
        "approver": "luca",
    }
    s.DecisionRequest.model_validate(body)
    Draft202012Validator(schema("decision_request")).validate(body)
    bad = (
        {**body, "levers": []},
        {**body, "override_reason": "Still returning", "override_category": "periodic_review"},
    )
    for doc in bad:
        with pytest.raises(ValidationError):
            s.DecisionRequest.model_validate(doc)
        assert not Draft202012Validator(schema("decision_request")).is_valid(doc)


# ---- Drafts and messages come from llm/ (rules 1 and 10, ADR-08, ADR-14) ------------------------


def _refill(text: dict[str, Any], **fields: str) -> dict[str, Any]:
    """A rendered text with other field values, written into the text too."""
    out = {**text, "fields": {**text["fields"], **fields}}
    for key, value in fields.items():
        out["text"] = out["text"].replace(text["fields"][key], value)
    return out


@pytest.mark.parametrize(
    "fields", [{"deadline": "1 gennaio 2027"}, {"terms": "reso entro 90 giorni"}]
)
def test_draft_fields_are_the_decision_deadline_and_terms(fields: dict[str, str]) -> None:
    card = load("case_card.marco.json")
    s.CaseCard.model_validate(card)
    texts = card["draft"]["message"]["texts"]
    texts[0] = _refill(texts[0], **fields)
    s.CustomerMessageView.model_validate(card["draft"]["message"])  # consistent on its own
    with pytest.raises(ValidationError, match="template fields"):
        s.CaseCard.model_validate(card)


def test_the_api_has_no_renderer_of_its_own() -> None:
    """B2: one source of truth. Template fields, texts and clauses come from llm/."""
    for name in ("deadline_text", "terms_text", "amount_text", "render_customer_message",
                 "check_connective_text", "required_clause"):  # fmt: skip
        assert not hasattr(s, name), name
    card = s.CaseCard.model_validate(load("case_card.marco.json"))
    decision = s.proposal_decision(card.decision)
    for text in card.draft.message.texts if card.draft else ():
        expected = llm_messages.template_fields(decision, NAVIGLI_BASE_TERMS, text.lang)
        assert {k: v for k, v in text.fields.model_dump().items() if v is not None} == expected
        assert text.text == render_selection(
            card.draft.message.sentence_ids, decision, NAVIGLI_BASE_TERMS, text.lang
        )


def test_messages_are_phrasebook_ids_composed_by_code() -> None:
    """ADR-14 decision 8: a draft carries the ids the model chose, the texts code composed
    from them in Italian and English, the fields and the clause; free text is refused."""
    card = load("case_card.marco.json")
    message = card["draft"]["message"]
    assert set(message) == {"sentence_ids", "legal_clause_kind", "texts"}
    assert [t["lang"] for t in message["texts"]] == ["it", "en"]
    assert message["legal_clause_kind"] == "withdrawal"
    for text in message["texts"]:
        assert text["text"].endswith(CLAUSES["withdrawal"][text["lang"]])
    with pytest.raises(ValidationError):
        s.CustomerMessageView.model_validate({**message, "connective_text": "Ciao {terms}"})
    choice = load("decision_request.override.json")["message"]
    assert set(choice) == {"lang", "sentence_ids"}


# ---- Explanation statements (US-305, ADR-14, findings B2 and B3) ---------------------------------


def _statement(card: dict[str, Any], index: int) -> dict[str, Any]:
    out: dict[str, Any] = card["draft"]["explanation"][index]
    return out


def test_a_statement_carries_the_code_built_facts_it_cites() -> None:
    """Entity, metric, value and period come from the facts of the llm_case, never from text."""
    card = load("case_card.marco.json")
    first = _statement(card, 0)
    assert first["fact_ids"] == [f["id"] for f in first["facts"]]
    assert {"id", "entity", "metric", "value", "period", "nature"} == set(first["facts"][0])
    assert first["facts"][0]["metric"] == "risk_score"
    assert first["facts"][0]["value"] == card["decision"]["inputs"]["risk"]
    s.ExplanationStatement.model_validate(first)
    with pytest.raises(ValidationError, match="order of fact_ids"):
        s.ExplanationStatement.model_validate({**first, "facts": first["facts"][::-1]})
    with pytest.raises(ValidationError):
        s.ExplanationStatement.model_validate({**first, "entity": "C-MARCO-1"})


@pytest.mark.parametrize(
    "text",
    [
        "Risk score 0.87; 9 returns in 12 months.",  # numbers no cited fact holds
        "Risk score 0.87 from returns 0.83.",  # a value of a fact it does not cite
        "Risk score 0.9: an estimate.",  # rounded, not the fact
        "Risk score zero point eight seven.",  # a number in words
    ],
)
def test_every_number_of_a_statement_is_a_cited_value(text: str) -> None:
    """B3: the value check cannot be satisfied with a blank, a word or a value the statement
    does not state: llm.validator grounds every number on the facts the statement cites."""
    card = load("case_card.marco.json")
    _statement(card, 0)["text"] = text
    with pytest.raises(ValidationError, match="UNGROUNDED_NUMBER"):
        s.CaseCard.model_validate(card)


def test_a_statement_that_names_another_decision_is_refused() -> None:
    card = load("case_card.marco.json")
    _statement(card, 2)["text"] = "The case is in Q2: verify the shared payment cards."
    with pytest.raises(ValidationError, match="DECISION_ECHO"):
        s.CaseCard.model_validate(card)


# ---- Matrix, checkout terms and link disputes ---------------------------------------------------


def _point(**changes: Any) -> dict[str, Any]:
    point = {
        "customer_id": "C-1",
        "display_name": None,
        "risk": 0.2,
        "value_eur": 10.0,
        "evidence_quality": "sufficient",
        "quadrant": "Q2",
        "cluster_id": None,
        "returns_12m": 1,
        "welcome_coupons": 0,
    }
    return {**point, **changes}


def test_insufficient_matrix_points_carry_no_risk() -> None:
    s.MatrixPoint.model_validate(_point(evidence_quality="insufficient", quadrant=None, risk=None))
    with pytest.raises(ValidationError):
        s.MatrixPoint.model_validate(_point(evidence_quality="insufficient", quadrant=None))
    with pytest.raises(ValidationError):
        s.MatrixPoint.model_validate(_point(risk=None))


def test_checkout_terms_less_generous_than_base_come_from_a_decision_with_a_review() -> None:
    doc = load("checkout_evaluate_response.marco.json")
    doc["base_terms"] = s.Terms.from_policy(NAVIGLI_BASE_TERMS).model_dump()
    s.CheckoutEvaluateResponse.model_validate(doc)
    with pytest.raises(ValidationError, match="review"):
        s.CheckoutEvaluateResponse.model_validate({**doc, "review_at": None})
    as_base = {**doc, "terms_source": "base", "quadrant": None, "review_at": None}
    with pytest.raises(ValidationError, match="base"):
        s.CheckoutEvaluateResponse.model_validate({**as_base, "coupon_block": None})


def test_terms_snapshot_from_base_has_the_base_terms() -> None:
    snapshot = load("case_card.giulia.json")["order_terms"]
    snapshot.update(base_terms=snapshot["terms"], review_at=None)
    s.TermsSnapshotView.model_validate(snapshot)
    upgraded = {**snapshot["terms"], "return_window_days": 60}
    with pytest.raises(ValidationError, match="base"):
        s.TermsSnapshotView.model_validate({**snapshot, "terms": upgraded})
    restricted = {**snapshot["terms"], "instant_refund": False}
    decision = {**snapshot, "source": "DEC-000102-1", "quadrant_at_purchase": "Q4"}
    with pytest.raises(ValidationError, match="review"):
        s.TermsSnapshotView.model_validate({**decision, "terms": restricted})
    s.TermsSnapshotView.model_validate({**decision, "terms": restricted, "review_at": "2026-12-27"})


# ---- Restrictions lapse where they reach customers (US-607, US-402) -----------------------------


def test_terms_snapshot_from_a_restriction_is_shown_before_its_review_date() -> None:
    """Terms from a decision are shown only while the restriction is in force: a snapshot shown
    on or after the review date would make a lapsed restriction reach a new order."""
    snapshot = load("case_card.giulia.json")["order_terms"]
    restricted = {
        **snapshot,
        "source": "DEC-000102-1",
        "quadrant_at_purchase": "Q4",
        "terms": {**snapshot["terms"], "instant_refund": False},
    }
    shown = date.fromisoformat(snapshot["shown_at"][:10])
    s.TermsSnapshotView.model_validate(
        {**restricted, "review_at": (shown + timedelta(days=1)).isoformat()}
    )
    for review in (shown - timedelta(days=30), shown):
        with pytest.raises(ValidationError, match="review"):
            s.TermsSnapshotView.model_validate({**restricted, "review_at": review.isoformat()})


def test_checkout_terms_and_coupon_block_lapse_at_their_review_date() -> None:
    doc = load("checkout_evaluate_response.marco.json")
    s.CheckoutEvaluateResponse.model_validate(doc)
    shown = date.fromisoformat(doc["shown_at"][:10])
    for review in (date(2026, 9, 1), shown):
        with pytest.raises(ValidationError, match="review"):
            s.CheckoutEvaluateResponse.model_validate({**doc, "review_at": review.isoformat()})
    with pytest.raises(ValidationError, match="review"):
        s.CheckoutEvaluateResponse.model_validate({**doc, "shown_at": "2027-01-10T10:00:00Z"})
    block = {**doc["coupon_block"], "review_at": shown.isoformat()}
    with pytest.raises(ValidationError, match="coupon block"):
        s.CheckoutEvaluateResponse.model_validate({**doc, "coupon_block": block})


def test_a_cluster_coupon_application_lapses_at_its_review_date() -> None:
    """The coupon block of the linked accounts ends at its review date, like the restriction
    that applied it (US-607: no permanent label)."""
    rules = load("coupon_rules.json")
    app = rules["applications"][0]
    assert app["state"] == "confirmed" and app["in_force"] is True
    assert app["as_of"] == rules["as_of"]
    s.CouponRuleApplication.model_validate(app)
    lapsed = {**app, "as_of": f"{app['review_at']}T08:00:00Z"}
    with pytest.raises(ValidationError, match="in_force"):
        s.CouponRuleApplication.model_validate(lapsed)
    s.CouponRuleApplication.model_validate({**lapsed, "in_force": False})
    reversed_ = load("coupon_rule_application.reversed.json")
    assert reversed_["state"] == "reversed" and reversed_["in_force"] is False
    with pytest.raises(ValidationError, match="in_force"):
        s.CouponRuleApplication.model_validate({**reversed_, "in_force": True})


def test_lifting_a_cluster_restriction_reverses_its_coupon_application() -> None:
    """A lift of a Q4 version with a coupon block ends the block on every linked account: the
    result carries the application, reversed by the person who lifted, with the lift's reason."""
    approved = s.DecisionResult.model_validate(load("decision_result.marco_approved.json"))
    before = approved.decision
    assert before.coupon_rule is not None and approved.coupon_application is not None
    assert approved.coupon_application.state == "confirmed" and approved.coupon_application.in_force
    when = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    reason = "Rectification asked by the customer: the linked accounts are three colleagues"
    request, result = build_api_examples.review_result(
        build_api_examples.marco().engine, before, "lift", "luca", when,
        entry_id=30, reason=reason, category="customer_context",
    )  # fmt: skip
    assert request.action == "lift" and request.decision_id == before.decision_id
    app = result.coupon_application
    assert app is not None and app.application_id == before.coupon_rule.application_id
    assert (app.state, app.reversed_by, app.reversal_reason, app.in_force) == (
        "reversed",
        "luca",
        reason,
        False,
    )
    assert app.decision_id == before.decision_id == result.decision.supersedes
    assert result.decision.coupon_rule is None and result.decision.levers == []
    doc = result.model_dump(mode="json")
    s.DecisionResult.model_validate(doc)
    for path, value in (
        (("coupon_application",), None),
        (("coupon_application", "reversed_by"), "sara"),
        (("coupon_application", "application_id"), "CRA-000099"),
    ):
        with pytest.raises(ValidationError, match="coupon"):
            s.DecisionResult.model_validate(mutated(doc, path, value))


def test_a_restriction_due_for_review_is_renewed_with_a_new_version() -> None:
    """renew (US-607): at or after the review date a person approves the engine's fresh
    restriction, which gets a new review date; the lapsed version stays in the log."""
    lorenzo = build_api_examples.simple_case(build_api_examples.SIMPLE_BY_KEY["lorenzo"])
    v1 = lorenzo.card.decision
    assert v1.review_at is not None and not v1.in_force
    when = datetime(2026, 9, 30, 19, 20, tzinfo=UTC)
    request, result = build_api_examples.review_result(
        lorenzo.engine, v1, "renew", "luca", when, entry_id=15
    )
    assert (request.action, request.levers, request.override_reason) == ("renew", None, None)
    v2 = result.decision
    assert (v2.version, v2.supersedes, v2.revision_trigger, v2.state) == (
        2,
        v1.decision_id,
        "review",
        "approved",
    )
    assert v2.requires_approval and v2.in_force and v2.approver == "luca"
    assert v2.decided_on == when.date() >= v1.review_at
    assert v2.review_at is not None and v2.review_at > v2.decided_on
    assert result.audit_entry.action == "restriction_renewed"
    assert result.outbox_message is None  # a review sends no message (ADR-15 section 11)
    doc = result.model_dump(mode="json")
    s.DecisionResult.model_validate(doc)
    early = mutated(doc, ("audit_entry", "before", "review_at"), "2026-12-01")
    with pytest.raises(ValidationError, match="review date"):
        s.DecisionResult.model_validate(early)
    with pytest.raises(ValidationError):
        s.DecisionResult.model_validate(
            mutated(doc, ("audit_entry", "action"), "decision_approved")
        )


def test_a_new_decision_version_has_a_new_decision_id() -> None:
    """restriction_lifted and case_recomputed create the next version: before and after are two
    versions, never one."""
    page = load("audit_page.json")
    new_versions = [x for x in page["items"] if x["action"] in ("restriction_lifted",
                                                               "case_recomputed")]  # fmt: skip
    assert {x["action"] for x in new_versions} == {"restriction_lifted", "case_recomputed"}
    for item in new_versions:
        s.AuditEntry.model_validate(item)
        same = mutated(item, ("before", "decision_id"), item["after"]["decision_id"])
        with pytest.raises(ValidationError, match="new version"):
            s.AuditEntry.model_validate(same)


def test_regenerated_draft_fields_are_the_decision_fields() -> None:
    """The regenerated draft is checked like the draft of a case card (rule 10)."""
    doc = load("regenerate_response.json")
    s.RegenerateResponse.model_validate(doc)
    assert doc["decision"]["decision_id"] == load("regenerate_request.json")["decision_id"]
    assert [t["lang"] for t in doc["draft"]["message"]["texts"]] == ["en"]
    texts = doc["draft"]["message"]["texts"]
    texts[0] = _refill(texts[0], deadline="1 January 2027")
    with pytest.raises(ValidationError, match="template fields"):
        s.RegenerateResponse.model_validate(doc)


def test_alternatives_share_the_basis_of_their_stored_version() -> None:
    """US-602: every alternative of a decision uses the unit costs and the basis of the stored
    version; a realized set among simulated ones is refused."""
    card = load("case_card.marco.json")
    s.CaseCard.model_validate(card)
    nb = card["alternatives"][1]["net_benefit"]
    nb["assumptions"]["basis"] = "realized"
    for metric in [c["amount"] for c in nb["components"]] + [nb["total"]]:
        metric["provenance"]["nature"] = "synthetic"
    s.NetBenefitView.model_validate(nb)  # valid alone
    with pytest.raises(ValidationError, match="basis"):
        s.CaseCard.model_validate(card)


def test_a_dispute_from_a_case_names_the_decision_shown() -> None:
    body = {"reason": "Card used once for a gift", "author": "sara", "return_id": "RET-000106"}
    with pytest.raises(ValidationError, match="decision_id"):
        s.LinkDisputeRequest.model_validate(body)
    s.LinkDisputeRequest.model_validate({**body, "decision_id": "DEC-000106-1"})
    with pytest.raises(ValidationError, match="decision_id"):
        s.LinkDisputeRequest.model_validate({**body, "return_id": None, "decision_id": "DEC-1"})


# ---- Audit payloads, decision history and endpoint errors (US-604, ADR-15) ---------------------


def test_decision_audit_entries_carry_a_typed_payload() -> None:
    result = load("decision_result.marco_approved.json")
    entry = result["audit_entry"]
    with pytest.raises(ValidationError):
        s.AuditEntry.model_validate({**entry, "after": {"state": "approved"}})
    assert "DecisionAuditState" in schema("audit_page")["$defs"]


def test_earlier_decision_versions_are_served() -> None:
    endpoints = {(ep.method, ep.path): ep for ep in s.ENDPOINTS}
    ep = endpoints[("GET", "/api/decisions/{decision_id}")]
    assert ep.response == "decision_record" and "US-604" in ep.stories and 404 in ep.errors


def test_role_and_llm_errors_are_declared() -> None:
    """A warehouse user gets 403 on case actions; regeneration and the agent need the LLM."""
    by_key = {(ep.method, ep.path): ep for ep in s.ENDPOINTS}
    for path in (
        "/api/returns/{return_id}/decision",
        "/api/returns/{return_id}/regenerate",
        "/api/returns/{return_id}/evidence",
        "/api/links/{link_id}/dispute",
        "/api/agent/ask",
    ):
        assert 403 in by_key[("POST", path)].errors, path
        assert "warehouse" not in by_key[("POST", path)].roles, path
    for ep in s.ENDPOINTS:
        assert (403 in ep.errors) == (set(ep.roles) != set(s.DEMO_ROLES)), ep.path
        llm = ep.path in ("/api/returns/{return_id}/regenerate", "/api/agent/ask")
        assert (503 in ep.errors) == llm, ep.path
    openapi = json.loads((SCHEMAS / "openapi.json").read_text())
    for path, method in (
        ("/api/agent/ask", "post"),
        ("/api/returns/{return_id}/regenerate", "post"),
    ):
        assert {"403", "503"} <= set(openapi["paths"][path][method]["responses"]), path


def test_responses_with_plain_numbers_carry_a_provenance() -> None:
    """Rule 11: a plain number is covered by the provenance of its section."""
    for name in ("queue_response", "recompute_response", "inspection", "link_dispute_response"):
        assert "provenance" in schema(name)["required"], name
    card = schema("case_card")["$defs"]
    assert "provenance" in card["RiskView"]["required"]
    assert card["RevisionSummary"]["properties"]["risk"]["anyOf"][0] == {"$ref": "#/$defs/Metric"}
    assert "provenance" in card["DecisionInputs"]["required"]
    audit = schema("audit_page")["$defs"]["DecisionAuditState"]
    assert audit["properties"]["risk"]["anyOf"][0] == {"$ref": "#/$defs/Metric"}


# Plain numbers that are not figures (rule 11 is about figures shown as data): counters and
# positions, versions, run settings, contract terms and their thresholds. Every other plain
# number of a response sits in a section that carries a provenance.
NOT_FIGURES: frozenset[tuple[str, str]] = frozenset(
    {
        ("AuditPage", "total"), ("AuditPage", "page"), ("AuditPage", "page_size"),
        ("AuditEntry", "entry_id"),
        ("OutboxResponse", "total"),
        ("DecisionView", "version"), ("DecisionAuditState", "version"),
        ("RevisionSummary", "version"),
        ("Terms", "return_window_days"),  # a term of the return policy, not a measurement
        ("Lever", "value"),  # the value of a policy lever (a window in days, a flag)
        ("GenerationInfo", "model_calls"), ("GenerationInfo", "latency_ms"),
        ("GenerationAuditState", "model_calls"),
        ("ToolCallEvent", "seq"), ("ToolCallEvent", "step"),
        ("ToolResultEvent", "seq"), ("ToolResultEvent", "step"),
        ("TokenEvent", "seq"), ("AnswerEvent", "seq"), ("RefusalEvent", "seq"),
        ("ErrorEvent", "seq"), ("DoneEvent", "seq"), ("DoneEvent", "steps_used"),
        ("Benchmark", "config"), ("BenchmarkResult", "concurrency"),
        ("BenchmarkResult", "batch_size"),
        ("LlmStats", "window_s"),
        ("FollowUp", "weeks_after"),
    }
)  # fmt: skip


def _leaf_types(annotation: Any) -> Iterator[Any]:
    """The concrete types inside an annotation: unions, containers and Annotated unwrapped."""
    origin = get_origin(annotation)
    if origin is Annotated:
        yield from _leaf_types(get_args(annotation)[0])
    elif origin is Literal:
        return  # a constant, not a value
    elif origin in (Union, UnionType, list, tuple, dict):
        args = get_args(annotation)
        for arg in args[1:] if origin is dict else args:
            yield from _leaf_types(arg)
    else:
        yield annotation


def _uncovered_numbers(
    model: type[BaseModel], covered: bool, seen: set[tuple[type[BaseModel], bool]]
) -> Iterator[tuple[str, str]]:
    """(model, field) of every int or float field with no provenance on its path. A model with a
    Provenance field covers its subtree; a Metric carries its own; a NetBenefitView is covered
    by the provenance of its total, which every component shares."""
    if model is s.Metric or (model, covered) in seen:
        return
    seen.add((model, covered))
    fields = model.model_fields
    own = (
        covered
        or model is s.NetBenefitView
        or any(s.Provenance in set(_leaf_types(f.annotation)) for f in fields.values())
    )
    for name, field in fields.items():
        for leaf in _leaf_types(field.annotation):
            if leaf in (int, float) and not own:
                yield model.__name__, name
            elif isinstance(leaf, type) and issubclass(leaf, BaseModel):
                yield from _uncovered_numbers(leaf, own, seen)


def test_every_plain_number_of_a_response_has_a_covering_provenance() -> None:
    """Rule 11, NFR-16: walk every response model; a plain number outside a section with a
    provenance is a defect (decision inputs and audit risks were, before ADR-15 section 10)."""
    names = {ep.response for ep in s.ENDPOINTS} | {"error"}
    seen: set[tuple[type[BaseModel], bool]] = set()
    found = {
        hit for name in sorted(names) for hit in _uncovered_numbers(s.API_MODELS[name], False, seen)
    }
    assert found <= NOT_FIGURES, sorted(found - NOT_FIGURES)
    assert ("DecisionInputs", "risk") not in found and ("DecisionAuditState", "risk") not in found


# ---- Round 3 of the review (findings D1..D9): each probe is a mutation that must be refused --

RENEWED_AT = datetime(2026, 9, 30, 19, 20, tzinfo=UTC)


@functools.cache
def _lorenzo_renewal() -> dict[str, Any]:
    """The renewal of Lorenzo's lapsed Q3 restriction, as review_result builds it."""
    lorenzo = build_api_examples.simple_case(build_api_examples.SIMPLE_BY_KEY["lorenzo"])
    _, result = build_api_examples.review_result(
        lorenzo.engine, lorenzo.card.decision, "renew", "luca", RENEWED_AT, entry_id=15
    )
    return result.model_dump(mode="json")


@functools.cache
def _marco_lift() -> dict[str, Any]:
    """A lift of Marco's approved Q4 restriction with its cluster coupon application."""
    approved = s.DecisionResult.model_validate(load("decision_result.marco_approved.json"))
    _, result = build_api_examples.review_result(
        build_api_examples.marco().engine, approved.decision, "lift", "luca",
        datetime(2026, 10, 2, 9, 30, tzinfo=UTC), entry_id=30,
        reason="Rectification asked by the customer", category="customer_context",
    )  # fmt: skip
    return result.model_dump(mode="json")


def _renewal() -> dict[str, Any]:
    return copy.deepcopy(_lorenzo_renewal())


def test_d1_an_early_renewal_cannot_move_its_decision_day() -> None:
    """D1: a renewal two months before the review date, with decided_on moved to 2030 so that
    the early-renewal rule passes, and a restriction running past the 180-day maximum."""
    doc = _renewal()
    s.DecisionResult.model_validate(doc)
    doc["audit_entry"]["before"]["review_at"] = "2026-12-01"
    doc["decision"]["decided_on"] = "2030-01-01"
    doc["decision"]["review_at"] = doc["audit_entry"]["after"]["review_at"] = "2030-06-29"
    if doc["coupon_application"] is not None:
        doc["coupon_application"]["review_at"] = "2030-06-29"
    with pytest.raises(ValidationError, match="decided_on"):
        s.DecisionResult.model_validate(doc)


def test_d1_a_decision_is_decided_no_later_than_it_is_created() -> None:
    """D1: Marco's approved Q4 with decided_on 2030-01-01 and review_at 2030-06-29 would be in
    force from 2026-09-30 for almost four years."""
    view = load("decision_result.marco_approved.json")["decision"]
    s.DecisionView.model_validate(view)
    far = {**view, "decided_on": "2030-01-01", "review_at": "2030-06-29"}
    with pytest.raises(ValidationError, match="decided_on"):
        s.DecisionView.model_validate(far)


def test_d1_a_review_version_is_decided_on_the_day_of_the_review() -> None:
    """D1: a lift whose decided_on is moved back to June."""
    doc = load("decision_result.lift.json")
    s.DecisionResult.model_validate(doc)
    doc["decision"]["decided_on"] = "2026-06-01"
    with pytest.raises(ValidationError, match="decided_on"):
        s.DecisionResult.model_validate(doc)


def test_d2_checkout_terms_and_coupon_blocks_have_a_bounded_review_date() -> None:
    """D2: a review date in 2099 would make the restriction never lapse."""
    doc = load("checkout_evaluate_response.marco.json")
    s.CheckoutEvaluateResponse.model_validate(doc)
    with pytest.raises(ValidationError, match="review"):
        s.CheckoutEvaluateResponse.model_validate({**doc, "review_at": "2099-01-01"})
    block = {**doc["coupon_block"], "review_at": "2099-01-01"}
    with pytest.raises(ValidationError, match="review"):
        s.CheckoutEvaluateResponse.model_validate({**doc, "coupon_block": block})
    snapshot = load("checkout_confirm_response.giulia.json")["terms_snapshot"]
    restricted = {
        **snapshot,
        "source": "DEC-000102-1",
        "quadrant_at_purchase": "Q4",
        "terms": {**snapshot["terms"], "instant_refund": False},
        "review_at": "2026-12-27",
    }
    s.TermsSnapshotView.model_validate(restricted)
    with pytest.raises(ValidationError, match="review"):
        s.TermsSnapshotView.model_validate({**restricted, "review_at": "2099-01-01"})


def test_d2_a_coupon_application_has_a_bounded_review_date() -> None:
    app = load("coupon_rules.json")["applications"][0]
    s.CouponRuleApplication.model_validate(app)
    with pytest.raises(ValidationError, match="review"):
        s.CouponRuleApplication.model_validate({**app, "review_at": "2099-01-01", "in_force": True})


@pytest.mark.parametrize(
    ("shown_at", "confirmed_at"),
    [
        ("2026-12-26T23:00:00Z", "2027-03-01T10:00:00Z"),  # two months after the review date
        ("2026-12-26T23:50:00Z", "2026-12-27T00:05:00Z"),  # across midnight on the review day
    ],
)
def test_d3_a_snapshot_is_confirmed_before_its_review_date(
    shown_at: str, confirmed_at: str
) -> None:
    """D3: terms of a restriction shown before its review date cannot bind an order placed on
    or after it (/confirm answers 409 and the client evaluates again)."""
    snapshot = load("case_card.giulia.json")["order_terms"]
    doc = {
        **snapshot,
        "source": "DEC-000102-1",
        "quadrant_at_purchase": "Q4",
        "terms": {**snapshot["terms"], "instant_refund": False},
        "review_at": "2026-12-27",
        "shown_at": shown_at,
        "order_id": "ORD-999999",
        "confirmed_at": "2026-12-26T23:30:00Z" if confirmed_at > "2027" else shown_at,
    }
    s.TermsSnapshotView.model_validate(doc)
    with pytest.raises(ValidationError, match="review"):
        s.TermsSnapshotView.model_validate({**doc, "confirmed_at": confirmed_at})


def test_d4_a_cluster_block_names_its_coupon_application_in_the_audit_state() -> None:
    state = load("decision_result.marco_approved.json")["audit_entry"]["after"]
    s.DecisionAuditState.model_validate(state)
    assert "cluster_coupons_disabled:true" in state["lever_ids"]
    with pytest.raises(ValidationError, match="coupon application"):
        s.DecisionAuditState.model_validate({**state, "coupon_application_id": None})
    other = {**state, "lever_ids": ["return_window_days:14"]}
    with pytest.raises(ValidationError, match="coupon application"):
        s.DecisionAuditState.model_validate(other)


def test_d4_a_lift_cannot_drop_the_reversal_of_the_coupon_block() -> None:
    doc = copy.deepcopy(_marco_lift())
    s.DecisionResult.model_validate(doc)
    doc["audit_entry"]["before"]["coupon_application_id"] = None
    doc["coupon_application"] = None
    with pytest.raises(ValidationError):
        s.DecisionResult.model_validate(doc)


def _reversed_application(recompute: dict[str, Any]) -> dict[str, Any]:
    when = recompute["decision"]["created_at"]
    return {
        "application_id": "CRA-000040",
        "rule_version": "coupon-rule-v1",
        "cluster_id": "CL-000588",
        "decision_id": recompute["before"]["decision_id"],
        "affected_customer_ids": ["C-ROSSI-CARD-1", "C-ROSSI-CARD-2", "C-ROSSI-CARD-3"],
        "state": "reversed",
        "proposed_at": "2026-09-26T11:03:00Z",
        "confirmed_by": "sara",
        "confirmed_at": "2026-09-26T12:00:00Z",
        "review_at": "2026-12-25",
        "reversible": True,
        "reversed_by": "sara",
        "reversed_at": when,
        "reversal_reason": "Superseded by the recompute after the evidence",
        "as_of": recompute["decision"]["as_of"],
        "in_force": False,
    }


def test_d4_a_recompute_reverses_the_coupon_block_of_the_version_it_supersedes() -> None:
    """ADR-15: a recompute that supersedes a version with a confirmed cluster coupon block
    reverses it, and the response carries the reversal (like a lift)."""
    doc = load("recompute_response.rossi_card.json")
    s.RecomputeResponse.model_validate(doc)
    assert doc["coupon_application"] is None and doc["before"]["state"] == "proposed"
    blocking = copy.deepcopy(doc)
    blocking["before"].update(
        state="approved",
        lever_ids=[*doc["before"]["lever_ids"], "cluster_coupons_disabled:true"],
    )
    with pytest.raises(ValidationError, match="coupon"):
        s.RecomputeResponse.model_validate(blocking)
    s.RecomputeResponse.model_validate(
        {**blocking, "coupon_application": _reversed_application(doc)}
    )
    still = {**_reversed_application(doc), "state": "confirmed", "reversed_by": None,
             "reversed_at": None, "reversal_reason": None, "in_force": True}  # fmt: skip
    with pytest.raises(ValidationError, match="coupon"):
        s.RecomputeResponse.model_validate({**blocking, "coupon_application": still})
    with pytest.raises(ValidationError, match="coupon"):
        s.RecomputeResponse.model_validate(
            {**doc, "coupon_application": _reversed_application(doc)}
        )


def test_d5_renewal_entries_are_checked_for_timing_and_a_new_review_date() -> None:
    entry = _renewal()["audit_entry"]
    s.AuditEntry.model_validate(entry)
    early = copy.deepcopy(entry)
    early["before"]["review_at"] = "2026-12-01"  # renewed on 30 September, before the date
    no_date = copy.deepcopy(entry)
    no_date["after"]["review_at"] = None
    same = copy.deepcopy(entry)
    same["after"]["review_at"] = entry["before"]["review_at"]
    for doc in (early, no_date, same):
        with pytest.raises(ValidationError, match="review"):
            s.AuditEntry.model_validate(doc)


def test_d5_an_audit_state_has_a_review_date_exactly_for_a_restriction() -> None:
    state = _renewal()["audit_entry"]["after"]
    with pytest.raises(ValidationError, match="review"):
        s.DecisionAuditState.model_validate({**state, "review_at": None})
    with pytest.raises(ValidationError, match="review"):
        s.DecisionAuditState.model_validate({**state, "requires_approval": False})


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("audit_entry", "at"), "2020-01-01T00:00:00Z"),
        (("outbox_message", "created_at"), "2020-01-01T00:00:00Z"),
        (("audit_entry", "rules_version"), "policy-1999"),
        (("audit_entry", "model_versions"), {"risk": "risk-xgb-9.9.9"}),
        (("audit_entry", "assumptions_version"), "assumptions-v9"),
        (("audit_entry", "before", "quadrant"), "Q1"),
        (("audit_entry", "before", "risk", "value"), 0.01),
        (("audit_entry", "before", "lever_ids"), ["return_window_days:14"]),
    ],
)
def test_d6_the_result_entry_is_tied_to_the_decision(path: Path_, value: Any) -> None:
    """D6: the time of the entry and of the message, the versions, and the proposal recorded
    as before are the decision's."""
    doc = load("decision_result.marco_approved.json")
    s.DecisionResult.model_validate(doc)
    with pytest.raises(ValidationError):
        s.DecisionResult.model_validate(mutated(doc, path, value))


def test_d6_the_before_of_an_approval_is_the_proposal() -> None:
    """D6 probe 3: a before that says a non-restrictive Q1 proposal was approved."""
    doc = load("decision_result.marco_approved.json")
    before = doc["audit_entry"]["before"]
    before.update(
        quadrant="Q1", lever_ids=[], requires_approval=False, review_at=None,
        coupon_application_id=None,
    )  # fmt: skip
    before["risk"]["value"] = 0.01
    s.DecisionAuditState.model_validate(before)  # a valid state on its own
    with pytest.raises(ValidationError, match="proposal"):
        s.DecisionResult.model_validate(doc)


def _payload_numbers(node: Any, inside_metric: bool = False) -> Iterator[Any]:
    if isinstance(node, dict):
        metric = set(node) == {"value", "unit", "provenance"}
        for key, value in node.items():
            yield from _payload_numbers(value, inside_metric or (metric and key == "value"))
    elif isinstance(node, list):
        for value in node:
            yield from _payload_numbers(value, inside_metric)
    elif isinstance(node, int | float) and not isinstance(node, bool) and not inside_metric:
        yield node


def test_d7_audit_payload_numbers_carry_their_provenance() -> None:
    """D7: every number in the before and after of an audit entry, typed or not, is a Metric
    (or a counter of a typed record); the compensation of an inspection is a Metric."""
    page = load("audit_page.json")
    for entry in page["items"]:
        if entry["entity_type"] in ("decision", "llm_generation"):
            continue  # typed: the walker of the response models covers them
        for key in ("before", "after"):
            assert list(_payload_numbers(entry[key])) == [], (entry["action"], key)
    inspection = next(e for e in page["items"] if e["action"] == "inspection_confirmed")
    assert inspection["after"]["compensation"]["unit"] == "eur"
    plain = mutated(inspection, ("after", "compensation"), 17.98)
    with pytest.raises(ValidationError, match="Metric"):
        s.AuditEntry.model_validate(plain)
    nested = mutated(inspection, ("after", "detail"), {"amounts": [1.5]})
    with pytest.raises(ValidationError, match="Metric"):
        s.AuditEntry.model_validate(nested)


def _kpi_with_realized(**changes: Any) -> dict[str, Any]:
    doc = load("kpi.json")
    simulated = s.NetBenefitView.model_validate(doc["net_benefit_simulated"])
    week = doc["period_start"]
    assumptions = simulated.assumptions.model_copy(
        update={"version": f"kpi/{week}/realized", "basis": "realized", **changes}
    )
    provenance = simulated.total.provenance.model_copy(update={"nature": "synthetic"})
    realized = s.NetBenefitView.build(s.Assumptions.model_validate(assumptions.model_dump()),
                                      provenance)  # fmt: skip
    return {**doc, "net_benefit_realized": realized.model_dump(mode="json")}


def test_d8_the_realized_kpi_set_uses_the_stored_version_of_the_simulated_one() -> None:
    s.KpiResponse.model_validate(_kpi_with_realized())
    for changes in (
        {"based_on": None},
        {"based_on": "assumptions-v9"},
        {"logistics_cost_per_return_eur": 24.0},
    ):
        with pytest.raises(ValidationError, match="realized"):
            s.KpiResponse.model_validate(_kpi_with_realized(**changes))


def test_d8_a_realized_set_passes_check_unit_costs_against_its_stored_version() -> None:
    realized = s.KpiResponse.model_validate(_kpi_with_realized()).net_benefit_realized
    assert realized is not None
    stored = s.SimulatorAssumptionsResponse.model_validate(
        load("simulator_assumptions.v1.json")
    ).current.net_benefit.assumptions
    s.check_unit_costs(realized.assumptions, stored)
    simulated_label = realized.assumptions.model_copy(update={"version": "kpi/x"})
    with pytest.raises(ValueError, match="basis"):
        s.check_unit_costs(simulated_label, stored)
