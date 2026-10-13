"""DATA-01 (contracts), FAIR-01 (forbidden fields) and the lever catalogue at contract level.

ADR-13 records the data contracts v2 tested here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from jsonschema import Draft202012Validator

from contracts.calibration import QUANTILE_KEYS, check_calibration
from contracts.levers import LEVER_SCOPES, LEVERS, lever_json_schema, lever_list_json_schema
from contracts.parquet import (
    ANOMALY_STATUSES,
    AS_OF_TABLES,
    ASOS_REASON_CODES,
    BEHAVIOURS,
    COUPON_KINDS,
    DESCRIPTIVE_ONLY,
    EVENT_TABLES,
    FORBIDDEN_FEATURE_FIELDS,
    IDENTIFIER_KINDS,
    RETURN_STATES,
    SCENARIOS,
    SCHEMAS,
    TS,
    forbidden_features,
    is_forbidden_feature,
)
from policy.engine import NAVIGLI_BASE_TERMS, decide
from policy.types import Quadrant, ReturnReason
from tests.factories import THRESHOLDS, TODAY, request, scores

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["llm_case", "llm_output", "checkout_evaluate_response", "decision_request", "calibration"]


def schema_of(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / f"contracts/json/{name}.schema.json").read_text())
    return data


def example_of(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / f"contracts/examples/{name}.json").read_text())
    return data


def errors(name: str, doc: Any) -> list[str]:
    return [e.message for e in Draft202012Validator(schema_of(name)).iter_errors(doc)]


# ---- JSON contracts --------------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_json_schema_is_valid_and_example_conforms(name: str) -> None:
    schema = schema_of(name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example_of(name))


def test_override_requires_a_reason() -> None:
    assert errors("decision_request", {"action": "override", "approver": "sara"})


def test_future_window_never_below_14_in_checkout_contract() -> None:
    example = example_of("checkout_evaluate_response")
    example["terms"]["return_window_days"] = 7
    assert errors("checkout_evaluate_response", example)


# ---- Lever catalogue (POL-7, CON-05) -----------------------------------------------------------


def test_lever_schema_in_json_contracts_is_generated_from_the_catalogue() -> None:
    expected = lever_json_schema()
    assert schema_of("llm_case")["properties"]["levers"]["items"] == expected
    assert schema_of("decision_request")["properties"]["levers"]["items"] == expected


def test_lever_arrays_in_json_contracts_are_generated_from_the_catalogue() -> None:
    expected = lever_list_json_schema()
    assert expected["uniqueItems"] is True
    assert schema_of("llm_case")["properties"]["levers"] == expected
    assert schema_of("decision_request")["properties"]["levers"] == expected


ALL_ORDERS = [lever.id for lever in LEVERS if lever.scope == "all_orders"]


@pytest.mark.parametrize("lever_id", ALL_ORDERS)
def test_all_orders_levers_only_carry_true(lever_id: str) -> None:
    """C5: a lever that also holds for orders already placed can only be an upgrade."""
    assert ALL_ORDERS == ["free_size_exchange", "priority_size_exchange"]
    upgrade = {"id": lever_id, "value": True, "scope": "all_orders"}
    restriction = {"id": lever_id, "value": False, "scope": "all_orders"}
    for name in ("llm_case", "decision_request"):
        doc = example_of(name)
        doc["levers"] = [upgrade]
        assert errors(name, doc) == []
        doc["levers"] = [restriction]
        assert errors(name, doc), name


@pytest.mark.parametrize(
    "levers",
    [
        [  # the same lever twice with opposite values
            {"id": "instant_refund", "value": False, "scope": "future_orders"},
            {"id": "instant_refund", "value": True, "scope": "future_orders"},
        ],
        [  # the same object twice
            {"id": "transparency_notice", "value": True, "scope": "communication"},
            {"id": "transparency_notice", "value": True, "scope": "communication"},
        ],
        [
            {"id": "return_window_days", "value": 14, "scope": "future_orders"},
            {"id": "return_window_days", "value": 60, "scope": "future_orders"},
        ],
    ],
)
def test_lever_arrays_reject_duplicate_ids(levers: list[dict[str, Any]]) -> None:
    """C6: one value per lever in a decision."""
    for name in ("llm_case", "decision_request"):
        doc = example_of(name)
        doc["levers"] = levers
        assert errors(name, doc), name
        doc["levers"] = levers[:1]
        assert errors(name, doc) == [], name


def test_lever_scopes_are_the_contract_scopes() -> None:
    assert LEVER_SCOPES == (
        "future_orders",
        "future_orders_cluster",
        "all_orders",
        "current_order",
        "communication",
    )
    assert {lever.scope for lever in LEVERS} <= set(LEVER_SCOPES)


@pytest.mark.parametrize(
    "lever",
    [
        {"id": "refund_after_inspection", "value": True, "scope": "current_order"},  # LEG-02
        {"id": "welcome_coupon", "value": False, "scope": "future_orders_cluster"},  # spec 19.B
        {"id": "instant_refund", "value": False, "scope": "current_order"},  # retroactive
        {"id": "instant_refund", "value": 0, "scope": "future_orders"},
        {"id": "return_window_days", "value": 7, "scope": "future_orders"},
        {"id": "return_window_days", "value": True, "scope": "future_orders"},
        {"id": "transparency_notice", "value": True, "scope": "communication", "extra": 1},
        {"id": "size_advice", "value": False},
    ],
)
def test_lever_contract_rejects_unknown_ids_wrong_scopes_and_values(lever: dict[str, Any]) -> None:
    case = example_of("llm_case")
    case["levers"] = [lever]
    assert errors("llm_case", case)
    body = example_of("decision_request")
    body["levers"] = [lever]
    assert errors("decision_request", body)


def golden_q4_decision() -> Any:
    """The Q4 example: Navigli base, risk 0.87, value 42.10 EUR, a cluster (the ring of Marco)."""
    return decide(
        request(ReturnReason.CHANGED_MIND),
        scores(0.87, "42.10", cluster="R-MARCO"),
        NAVIGLI_BASE_TERMS,
        THRESHOLDS,
        TODAY,
    )


def test_llm_case_example_is_exactly_the_engine_q4_decision() -> None:
    decision = golden_q4_decision()
    case = example_of("llm_case")
    assert decision.quadrant is Quadrant.Q4
    assert case["path"] == decision.path.value
    assert case["quadrant"] == decision.quadrant.value
    assert case["evidence_quality"] == decision.evidence_quality.value
    assert case["levers"] == decision.levers.to_contract(NAVIGLI_BASE_TERMS)
    assert len(case["levers"]) == 6
    assert case["risk"]["score"] == 0.87
    assert Decimal(str(case["value_eur"])) == Decimal("42.10")


def test_llm_case_example_is_the_output_of_the_case_builder() -> None:
    """ADR-14: the example is what llm.case.build_case gives for the golden Q4 decision."""
    from dataclasses import replace

    from llm.case import ClusterSummary, ScoreFactor, build_case

    req = replace(request(ReturnReason.CHANGED_MIND), return_id="RET-000123")
    sc = scores(0.87, "42.10", cluster="R-MARCO")
    decision = decide(req, sc, NAVIGLI_BASE_TERMS, THRESHOLDS, TODAY)
    phrase = "7 linked accounts each used the welcome coupon"
    case = build_case(
        decision,
        req,
        NAVIGLI_BASE_TERMS,
        policy_text="Navigli Fashion return policy (demo).",
        scores=sc,
        factors=[ScoreFactor("cluster_welcome_coupons", 7, phrase, "cluster")],
        cluster=ClusterSummary("R-MARCO", 7, ("device", "address", "card")),
        customer_text="Non mi sta bene.",
    )
    assert case == example_of("llm_case")


def test_llm_output_example_echoes_the_same_six_lever_ids() -> None:
    decision = golden_q4_decision()
    output = example_of("llm_output")
    ids = decision.levers.lever_ids(NAVIGLI_BASE_TERMS)
    assert output["decision_echo"]["lever_ids"] == ids
    assert ids == [
        "return_window_days:14",
        "free_return_shipping:false",
        "instant_refund:false",
        "coupons_enabled:false",
        "cluster_coupons_disabled:true",
        "transparency_notice:true",
    ]


def test_llm_output_example_passes_the_python_validator() -> None:
    from llm.decision import serialize_decision
    from llm.validator import validate

    _, expected = serialize_decision(golden_q4_decision(), NAVIGLI_BASE_TERMS)
    result = validate(example_of("llm_output"), expected, example_of("llm_case"))
    assert result.ok, result.errors


def _unscored(case: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in case.items() if k not in ("risk", "value_eur", "cluster")}


def test_insufficient_evidence_case_lists_missing_evidence_and_has_no_quadrant() -> None:
    case = _unscored(example_of("llm_case"))
    case.update(evidence_quality="insufficient", quadrant=None, levers=[])
    case["placeholders"] = {"available": ["deadline"], "required": []}
    assert errors("llm_case", case)  # US-307: the missing evidence must be listed
    case["missing_evidence"] = []
    assert errors("llm_case", case)
    case["missing_evidence"] = ["fewer than 3 orders observed"]
    assert not errors("llm_case", case)
    assert errors("llm_case", {**case, "quadrant": "Q4"})
    lever = {"id": "transparency_notice", "value": True, "scope": "communication"}
    assert errors("llm_case", {**case, "levers": [lever]})
    assert errors("llm_case", {**case, "placeholders": {"available": [], "required": ["terms"]}})
    for key in ("risk", "value_eur", "cluster"):  # neither safe nor suspicious
        assert errors("llm_case", {**case, key: example_of("llm_case")[key]})


def test_guarantee_case_has_no_quadrant_no_lever_and_no_score() -> None:
    case = _unscored(example_of("llm_case"))
    case.update(path="guarantee", quadrant=None, levers=[])
    case["placeholders"] = {"available": [], "required": []}
    assert not errors("llm_case", case)
    assert errors("llm_case", {**case, "quadrant": "Q4"})
    lever = {"id": "instant_refund", "value": False, "scope": "future_orders"}
    assert errors("llm_case", {**case, "levers": [lever]})
    assert errors("llm_case", {**case, "placeholders": {"available": ["deadline"], "required": []}})
    for key in ("risk", "value_eur", "cluster"):  # rule 4: never scored
        assert errors("llm_case", {**case, key: example_of("llm_case")[key]})


def test_scored_withdrawal_case_needs_risk_value_and_quadrant() -> None:
    case = example_of("llm_case")
    for key in ("risk", "value_eur"):
        assert errors("llm_case", {k: v for k, v in case.items() if k != key})
    assert errors("llm_case", {**case, "quadrant": None})


def test_missing_evidence_items_are_strings() -> None:
    case = example_of("llm_case")
    assert errors("llm_case", {**case, "missing_evidence": [3]})
    assert not errors("llm_case", {**case, "missing_evidence": ["no identifier on file"]})


@pytest.mark.parametrize(
    "fact",
    [
        {"id": "F1", "entity": "customer", "metric": "risk_score", "value": 0.87, "period": "x"},
        {
            "id": "X1",
            "entity": "customer",
            "metric": "m",
            "value": 1,
            "period": "x",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "person",
            "metric": "m",
            "value": 1,
            "period": "x",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "M-1",
            "value": 1,
            "period": "x",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "m",
            "value": "1",
            "period": "x",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "m",
            "value": True,
            "period": "x",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "m",
            "value": 1,
            "period": "",
            "nature": "real",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "m",
            "value": 1,
            "period": "x",
            "nature": "estimated",
        },
        {
            "id": "F1",
            "entity": "customer",
            "metric": "m",
            "value": 1,
            "period": "x",
            "nature": "real",
            "note": "x",
        },
    ],
)
def test_facts_are_closed_typed_objects(fact: dict[str, Any]) -> None:
    case = example_of("llm_case")
    assert errors("llm_case", {**case, "facts": [fact]})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(customer_text="<customer_text>a</customer_text><b>"),
        lambda c: c.update(customer_text="no delimiters"),
        lambda c: c.update(placeholders={"available": ["refund"], "required": []}),
        lambda c: c.update(placeholders={"available": [], "required": ["deadline"]}),
        lambda c: c["risk"]["top_factors"][0].update(extra=1),
        lambda c: c["cluster"].update(shared=["fingerprint"]),
        lambda c: c.update(cluster=None),
        lambda c: c.update(languages=["it", "it"]),
    ],
)
def test_llm_case_is_closed_and_bounded(mutate: Any) -> None:
    case = example_of("llm_case")
    mutate(case)
    assert errors("llm_case", case)


def test_llm_output_schema_matches_the_python_model() -> None:
    """vLLM decodes with the JSON schema and the validator parses with pydantic: same limits."""
    from llm import phrasebook as p
    from llm import validator as v

    schema = schema_of("llm_output")
    props = schema["properties"]
    echo, explanation = props["decision_echo"], props["operator_explanation"]
    statement = explanation["items"]
    assert echo["properties"]["lever_ids"]["maxItems"] == v.MAX_LEVER_IDS
    assert explanation["maxItems"] == v.MAX_STATEMENTS and explanation["minItems"] == 1
    assert statement["properties"]["fact_ids"]["maxItems"] == v.MAX_FACTS_PER_STATEMENT
    assert statement["properties"]["text"]["maxLength"] == v.MAX_STATEMENT_CHARS
    assert statement["properties"]["label"]["enum"] == ["OBSERVED", "SCORE", "HYPOTHESIS"]
    message = props["customer_message"]
    assert message["required"] == ["sentence_ids"]  # no free customer prose (ADR-14, R1)
    ids = message["properties"]["sentence_ids"]
    assert (ids["minItems"], ids["maxItems"]) == (p.MIN_SENTENCES, p.MAX_SENTENCES)
    assert ids["items"]["enum"] == list(p.SENTENCE_IDS)

    def closed(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            if node.get("type") == "array":
                assert "maxItems" in node
            if node.get("type") == "string":
                assert "maxLength" in node and "pattern" not in node
            for child in node.values():
                closed(child)

    closed(schema)  # guided-decoding friendly: closed objects, bounded lists and strings


@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: o.update(operator_explanation="text"),
        lambda o: o["operator_explanation"][0].update(label="CAUSE"),
        lambda o: o["operator_explanation"][0].update(fact_ids=["F1"] * 5),
        lambda o: o["operator_explanation"][0].pop("fact_ids"),
        lambda o: o["customer_message"].update(it="Ciao, il reso lo paghiamo noi."),
        lambda o: o.update(customer_message={"it": "Ciao.", "en": "Hello."}),
        lambda o: o["customer_message"].update(sentence_ids=["OPEN_THANKS"]),
        lambda o: o["customer_message"].update(sentence_ids=["OPEN_THANKS"] * 6),
        lambda o: o["customer_message"].update(sentence_ids=["OPEN_THANKS", "Ciao!"]),
        lambda o: o["decision_echo"].update(lever_ids=["x"] * 10),
        lambda o: o.update(extra=1),
    ],
)
def test_llm_output_schema_and_python_model_reject_the_same_outputs(mutate: Any) -> None:
    from llm.validator import LlmOutput

    output = example_of("llm_output")
    mutate(output)
    assert errors("llm_output", output)
    with pytest.raises(ValueError):
        LlmOutput.model_validate(output)


# ---- Calibration contract (ASOS aggregates for the generator) ---------------------------------


def test_calibration_example_passes_the_semantic_checks() -> None:
    assert check_calibration(example_of("calibration")) == []


def test_calibration_example_is_labelled_and_holds_only_aggregates() -> None:
    doc = example_of("calibration")
    assert doc["provenance"]["nature"] in {"real", "synthetic", "simulated"}
    assert "illustrative" in doc["provenance"]["origin"].lower()  # values are not measured
    assert set(doc["reason_shares"]) == set(ASOS_REASON_CODES)

    def no_lists_of_objects(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                no_lists_of_objects(value)
        elif isinstance(node, list):
            assert all(not isinstance(item, dict | list) for item in node)

    no_lists_of_objects(doc)  # no row-level records


@pytest.mark.parametrize(
    "path, value",
    [
        (("provenance", "nature"), "estimated"),
        (("overall", "event_label_rate"), 1.2),
        (("reason_shares", "M"), 0.1),
        (("purchases_per_customer", "p99"), 3),
        (("rows",), [{"customer_hash": 1}]),
        (("join_coverage", "events_with_customer"), -0.1),
    ],
)
def test_calibration_schema_rejects_bad_documents(path: tuple[str, ...], value: Any) -> None:
    doc = example_of("calibration")
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert errors("calibration", doc)


def test_calibration_semantic_checks_catch_inconsistent_aggregates() -> None:
    doc = example_of("calibration")
    first = next(iter(doc["product_types"].values()))
    first["price_quantiles_gbp"]["p10"] = 10_000.0  # quantiles out of order
    first["variant_share"] = 0.9  # shares no longer sum to 1
    doc["reason_shares"]["A"] += 0.5
    doc["purchases_per_customer"]["p90"] = 0
    doc["provenance"]["period"] = {"start": "2021-12-01", "end": "2021-09-01"}
    found = check_calibration(doc)
    assert any("price_quantiles_gbp" in e for e in found)
    assert any("variant_share" in e for e in found)
    assert any("reason_shares" in e for e in found)
    assert any("purchases_per_customer" in e for e in found)
    assert any("period" in e for e in found)
    assert QUANTILE_KEYS == ("p10", "p25", "p50", "p75", "p90")


def test_calibration_semantic_checks_report_missing_parts_instead_of_crashing() -> None:
    assert check_calibration({})
    assert check_calibration({"product_types": {}, "reason_shares": {}, "provenance": {}})
    doc = example_of("calibration")
    next(iter(doc["product_types"].values()))["variant_share"] = "half"
    assert "product_types.*.variant_share: shares must be numbers" in check_calibration(doc)


# ---- Parquet contracts -------------------------------------------------------------------------


def test_store_tables_are_event_tables() -> None:
    assert {n for n in SCHEMAS if n.startswith("store/")} == EVENT_TABLES
    for name in EVENT_TABLES:
        schema = SCHEMAS[name]
        assert {"event_at", "available_at"} <= set(schema.names), name
        assert schema.field("event_at").type == TS and not schema.field("event_at").nullable
        assert schema.field("available_at").type == TS and not schema.field("available_at").nullable


def test_features_and_scores_are_as_of_tables() -> None:
    assert {n for n in SCHEMAS if n.startswith(("features/", "scores/"))} == AS_OF_TABLES
    for name in AS_OF_TABLES:
        assert SCHEMAS[name].field("as_of").type == TS, name


def test_rows_carry_only_facts_known_at_their_available_at() -> None:
    """CON-01: late facts (delivery, return outcome) live in their own event rows."""
    assert "delivered_at" not in SCHEMAS["store/orders"].names
    assert "status" not in SCHEMAS["store/returns"].names
    assert SCHEMAS["store/deliveries"].names == ["order_id", "event_at", "available_at"]
    assert SCHEMAS["store/return_events"].names == [
        "return_id",
        "state",
        "event_at",
        "available_at",
    ]
    assert RETURN_STATES == ("notified", "shipped_or_proof", "received", "refunded", "rejected")


def test_catalogue_holds_observable_attributes_and_truth_holds_latent_ones() -> None:
    variants = set(SCHEMAS["store/variants"].names)
    latent = {"base_return_rate", "defective", "defect_reason", "defect_start", "defect_end"}
    assert not variants & latent
    assert latent <= set(SCHEMAS["truth/variants"].names)
    assert {"variant_id", "product_id", "category", "brand", "price_eur", "size_range"} <= variants


def test_vocabularies() -> None:
    assert IDENTIFIER_KINDS == ("card", "email", "device", "address", "phone", "ip_prefix")
    assert COUPON_KINDS == ("welcome", "referral", "promo")
    assert BEHAVIOURS == ("normal", "size", "wardrober", "promo_ring", "shared_benign")
    assert SCENARIOS == ("main", "ood")
    assert ANOMALY_STATUSES == ("open", "task_opened", "resolved")
    assert ASOS_REASON_CODES == ("A", "B", "C", "D", "E", "D2", "F", "G", "H", "I", "J", "K", "L")


def test_terms_snapshot_carries_its_policy_context() -> None:
    """CON-12, ADR-05: which order, which policy (base or a decision), which quadrant."""
    schema = SCHEMAS["store/terms_snapshots"]
    assert not schema.field("order_id").nullable
    assert not schema.field("source").nullable
    assert schema.field("quadrant_at_purchase").nullable


def test_links_contract_for_us203_us302_us310() -> None:
    schema = SCHEMAS["features/links"]
    assert schema.names == [
        "customer_a",
        "customer_b",
        "kinds",
        "weight",
        "weight_decayed",
        "first_shared_at",
        "last_shared_at",
        "n_shared_events",
        "max_sharers",
        "disputed",
        "as_of",
    ]
    assert schema.field("kinds").type.value_type == pa.string()


def test_cluster_features_of_section_8_3() -> None:
    names = set(SCHEMAS["features/clusters"].names)
    assert {
        "n_distinct_cards",
        "creation_spread_days",
        "welcome_coupons_per_account",
        "cluster_return_rate",
        "strong_id_share",
    } <= names


def test_raw_asos_contracts_follow_adr10() -> None:
    events = SCHEMAS["raw/asos_events"]
    assert events.names == ["split", "variant_hash", "customer_hash", "is_returned"]
    assert events.field("variant_hash").type == pa.int64()
    assert events.field("is_returned").type == pa.bool_()
    shares = [f"reason_share_{code}" for code in ASOS_REASON_CODES]
    customers = SCHEMAS["raw/asos_customers"].names
    variants = SCHEMAS["raw/asos_variants"].names
    assert customers[customers.index("reason_share_A") :][:13] == shares
    assert variants[variants.index("reason_share_A") :][:13] == shares
    assert {"is_male", "shipping_country", "year_of_birth"} == DESCRIPTIVE_ONLY
    assert set(customers) >= DESCRIPTIVE_ONLY
    assert not any(name.startswith(("Country_", "country_")) for name in customers)


def test_return_reason_vocabulary_is_the_policy_enum() -> None:
    from contracts.checks import CHECKS, OneOf

    reason = [c for c in CHECKS["store/returns"] if isinstance(c, OneOf) and c.column == "reason"]
    assert reason and set(reason[0].values) == {r.value for r in ReturnReason}


# ---- Forbidden features (FAIR-01, ADR-11, ASOS-7) -----------------------------------------------

# The 74 column names of the real ASOS node tables (customer 30, product 44), read from the pickle
# headers on 12 Oct 2026 (origin: ASOS GraphReturns, OSF c793h; nature: real column names only).
ASOS_CUSTOMER_COLUMNS = [
    "hash(customerId)",
    "yearOfBirth",
    "isMale",
    "shippingCountry",
    "premier",
    "salesPerCustomer",
    "returnsPerCustomer",
    "customerReturnRate",
    *[f"customerId_level_return_code_{c}" for c in "ABCDEDFGHIJKL"],
    *[f"Country_{c}" for c in "ABCDEFGHI"],
]
ASOS_PRODUCT_COLUMNS = [
    "hash(variantID)",
    "hash(productID)",
    "productType",
    "hash(supplierRef)",
    "brandDesc",
    "avgGbpPrice",
    "avgDiscountValue",
    "salesPerProduct",
    "returnsPerProduct",
    "productReturnRate",
    *[f"variantID_level_return_code_{c}" for c in "ABCDEDFGHIJKL"],
    *[f"Brand_{c}" for c in "ABCDEFGIJK"],
    *[f"productType_{c}" for c in "ABCDEFGHIJK"],
]


def test_every_real_asos_protected_or_identifier_column_is_forbidden() -> None:
    assert len(ASOS_CUSTOMER_COLUMNS) + len(ASOS_PRODUCT_COLUMNS) == 74
    forbidden = set(forbidden_features(ASOS_CUSTOMER_COLUMNS + ASOS_PRODUCT_COLUMNS))
    expected = {
        "hash(customerId)",
        "yearOfBirth",
        "isMale",
        "shippingCountry",
        *[f"Country_{c}" for c in "ABCDEFGHI"],
        "hash(variantID)",
        "hash(productID)",
        "hash(supplierRef)",
    }
    assert forbidden == expected


@pytest.mark.parametrize(
    "name",
    [
        "gender",
        "GENDER",
        "customer_gender",
        "is_male",
        "isMale",
        "ismale",
        "IS_MALE",
        "sex",
        "country",
        "shipping_country",
        "shippingcountry",
        "country_a",
        "Country_I",
        "age",
        "Age",
        "customer_age",
        "age_band",
        "year_of_birth",
        "yearOfBirth",
        "yearofbirth",
        "birth_year",
        "name",
        "display_name",
        "displayName",
        "first_name",
        "value_hash",
        "customer_hash",
        "hash(customerId)",
        "email",
        "phone",
        "ip_prefix",
        "device_id",
        # Raw identifiers and country proxies (review finding C4).
        "email_normalized",
        "normalized_email",
        "address_normalized",
        "ip_prefix_24",
        "device",
        "customer_email",
        "shipping_address",
        "billing_address",
        "customer_ip",
        "device_fingerprint",
        "phone_e164",
        "postcode",
        "zip_code",
        "nationality",
        "customer_nationality",
        "shippingAddress",
        "customerEmail",
        # Spellings without separators or abbreviated (review P4).
        "ipprefix24",
        "ipv4prefix",
        "ipv6_prefix",
        "ip24",
        "customerip",
        "customerIp",
        "remoteip",
        "ipaddr",
        "remote_addr",
        "shipping_addr",
        "shippingaddr",
        "billing_addr",
        "addr",
        "shippingzip",
        "zip5",
        "zip_5",
        "mobile",
        "mobile_number",
        "customer_mobile",
        "msisdn",
        "imei",
        "device_imei",
        "mac",
        "mac_addr",
        "card",
        "card_last4",
        "card_bin",
        "cardlast4",
        "cardbin",
        "credit_card",
        "paymentcard",
        "pan",
        # Round 3 (separators and abbreviations, the same verdict with or without them).
        "mobilenumber",
        "postalcode",
        "postal_code",
        "cardnum",
        "card_num",
        "cardno",
        "cardid",
        "cardtoken",
        "cc_last4",
        "cclast4",
        "ccnumber",
        "addr1",
        "addr2",
        "signupip",
        "lastloginip",
        "visitorip",
        "buyerip",
        "registrationip",
        "ip24prefix",
        "ipblock",
        "iprange",
        "ipsubnet",
        "useragent",
        "user_agent",
        "idfa",
        "androidid",
        "advertisingid",
    ],
)
def test_forbidden_feature_patterns(name: str) -> None:
    assert is_forbidden_feature(name)


@pytest.mark.parametrize(
    "name",
    [
        "average_price",
        "page_views",
        "usage",
        "stage",
        "percentage_returned",
        "n_distinct_cards",
        "shared_strong_ids",
        "strong_id_share",
        "welcome_coupons_per_account",
        "customer_id",
        "variant_id",
        "cluster_id",
        "premier",
        "brandDesc",
        "productType",
        "max_sharers",
        "message_length",
        "anomaly_z",
        # Aggregates over identifiers are allowed (C4): n_*, *_count, shared_*, *_share.
        "n_shared_devices",
        "n_distinct_emails",
        "device_count",
        "address_count",
        "shared_addresses",
        "shared_device",
        "email_share",
        "ip_prefix_share",
        "shipping_cost",
        "zipper_defect_rate",
        # Words that only contain an identifier stem (review P4: no false positives).
        "free_return_shipping",
        "card_count",
        "shared_cards",
        "n_shared_cards",
        "discard_rate",
        "scorecard_points",
        "machine_washable",
        "membership_days",
        "tip_amount",
        "trip_count",
        "expanded_view",
        "n_distinct_ip_prefixes",
        "ip_prefix_count",
        "mobile_share",
        # Round 3: no fragment across a word boundary, mobile and card only as the head noun.
        "time_in_transit",
        "delivery_time_in_days",
        "time_in_cart",
        "time_index",
        "lifetime_items",
        "regime_index",
        "is_mobile",
        "mobile_checkout",
        "paid_by_card",
        "gift_card_used",
    ],
)
def test_allowed_feature_names(name: str) -> None:
    assert not is_forbidden_feature(name)


def test_compatibility_set_is_covered_by_the_patterns() -> None:
    assert {"gender", "country", "display_name", "name", "value_hash"} <= FORBIDDEN_FEATURE_FIELDS
    assert all(is_forbidden_feature(n) for n in FORBIDDEN_FEATURE_FIELDS)
    assert all(is_forbidden_feature(n) for n in DESCRIPTIVE_ONLY)


@pytest.mark.parametrize("name", sorted(AS_OF_TABLES))
def test_features_and_scores_have_no_forbidden_fields(name: str) -> None:
    assert forbidden_features(SCHEMAS[name].names) == [], name


def test_store_and_raw_tables_do_hold_the_columns_that_features_must_not() -> None:
    """The check is not vacuous: the source tables have names, hashes and protected columns."""
    assert forbidden_features(SCHEMAS["store/customers"].names) == ["display_name"]
    assert forbidden_features(SCHEMAS["store/identifiers"].names) == ["value_hash"]
    assert set(forbidden_features(SCHEMAS["raw/asos_customers"].names)) == {
        "customer_hash",
        *DESCRIPTIVE_ONLY,
    }


def test_legal_minimum_window_matches_the_legal_guard() -> None:
    from contracts.checks import CHECKS, InRange
    from policy.legal_guard import LEGAL_WITHDRAWAL_DAYS

    window = [
        c
        for c in CHECKS["store/terms_snapshots"]
        if isinstance(c, InRange) and c.column == "return_window_days"
    ]
    assert window and window[0].min == LEGAL_WITHDRAWAL_DAYS
    spec = next(lever for lever in LEVERS if lever.id == "return_window_days")
    assert spec.minimum == LEGAL_WITHDRAWAL_DAYS


def test_dates_in_contract_examples_are_iso() -> None:
    period = example_of("calibration")["provenance"]["period"]
    assert date.fromisoformat(period["start"]) <= date.fromisoformat(period["end"])
