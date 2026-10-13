"""Lever catalogue: the one vocabulary for policy levers in code, JSON contracts and the audit log.

One entry per field of policy.types.Levers, with future_terms expanded into the ReturnTerms fields
(ADR-13, docs/spec.md section 8.6). A lever travels in two forms:

- as an object {id, value, scope} in JSON contracts (llm_case, decision_request);
- as the string "id:value" (for example "instant_refund:false") wherever a single identifier is
  needed: Levers.lever_ids, the LLM decision echo, the validator and the audit log.

A lever whose scope is all_orders also holds for orders already placed, so it can only be an
upgrade: its value is always true (rule 3). A list of levers names each lever at most once.

This module must not import policy/: policy.types imports it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

LeverScope = Literal[
    "future_orders",  # orders placed after the decision, shown at checkout (rule 3)
    "future_orders_cluster",  # future orders of every account of the cluster
    "all_orders",  # a concession that also holds for orders already placed: value true only
    "current_order",  # the order being placed (size advice at checkout)
    "communication",  # a notice to the customer, not a term
]
LEVER_SCOPES: tuple[LeverScope, ...] = (
    "future_orders",
    "future_orders_cluster",
    "all_orders",
    "current_order",
    "communication",
)
LeverValueType = Literal["integer", "boolean"]
LEGAL_MINIMUM_WINDOW_DAYS = 14  # policy.legal_guard.LEGAL_WITHDRAWAL_DAYS, checked by a test


class LeverError(ValueError):
    """An unknown lever id or a value of the wrong type."""


@dataclass(frozen=True)
class LeverSpec:
    id: str
    value_type: LeverValueType
    scope: LeverScope
    minimum: int | None = None  # integer levers only


LEVERS: tuple[LeverSpec, ...] = (
    LeverSpec("return_window_days", "integer", "future_orders", LEGAL_MINIMUM_WINDOW_DAYS),
    LeverSpec("free_return_shipping", "boolean", "future_orders"),
    LeverSpec("instant_refund", "boolean", "future_orders"),
    LeverSpec("free_size_exchange", "boolean", "all_orders"),
    LeverSpec("coupons_enabled", "boolean", "future_orders"),  # the customer's own coupons
    # The welcome_coupon:false of the former spec 19.B example, whose scope is the cluster.
    LeverSpec("cluster_coupons_disabled", "boolean", "future_orders_cluster"),
    LeverSpec("transparency_notice", "boolean", "communication"),
    LeverSpec("size_advice", "boolean", "current_order"),
    LeverSpec("priority_size_exchange", "boolean", "all_orders"),
)
LEVERS_BY_ID: dict[str, LeverSpec] = {lever.id: lever for lever in LEVERS}

_INTEGER_RE = re.compile(r"0|[1-9][0-9]*")  # canonical: no sign, no leading zero


class LeverObject(TypedDict):
    id: str
    value: int | bool
    scope: LeverScope


def _spec(lever_id: str) -> LeverSpec:
    spec = LEVERS_BY_ID.get(lever_id) if isinstance(lever_id, str) else None
    if spec is None:
        raise LeverError(f"Unknown lever id {lever_id!r}")
    return spec


def _check_value(spec: LeverSpec, value: int | bool) -> None:
    if spec.value_type == "boolean":
        if not isinstance(value, bool):
            raise LeverError(f"{spec.id} takes a boolean, got {value!r}")
        if spec.scope == "all_orders" and value is not True:
            raise LeverError(
                f"{spec.id} has scope all_orders: it reaches orders already placed, so it can "
                "only be granted (true), never withdrawn (rule 3)"
            )
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise LeverError(f"{spec.id} takes an integer, got {value!r}")
    if spec.minimum is not None and value < spec.minimum:
        raise LeverError(f"{spec.id} must be at least {spec.minimum}, got {value}")


def format_lever_id(lever_id: str, value: int | bool) -> str:
    """The "id:value" string of a lever, for example "return_window_days:14"."""
    spec = _spec(lever_id)
    _check_value(spec, value)
    text = str(value).lower() if isinstance(value, bool) else str(value)
    return f"{spec.id}:{text}"


def parse_lever_id(text: str) -> tuple[str, int | bool]:
    """Inverse of format_lever_id; rejects unknown ids and malformed values."""
    lever_id, sep, raw = text.partition(":")
    if not sep:
        raise LeverError(f"Lever id {text!r} has no value")
    spec = _spec(lever_id)
    value: int | bool
    if spec.value_type == "boolean":
        if raw not in ("true", "false"):
            raise LeverError(f"{spec.id} takes true or false, got {raw!r}")
        value = raw == "true"
    else:
        if not _INTEGER_RE.fullmatch(raw):
            raise LeverError(f"{spec.id} takes a non-negative integer, got {raw!r}")
        value = int(raw)
    _check_value(spec, value)
    return spec.id, value


def lever_from_id(text: str) -> LeverObject:
    """The contract object of an "id:value" string, with the scope from the catalogue."""
    lever_id, value = parse_lever_id(text)
    return make_lever(lever_id, value)


def make_lever(lever_id: str, value: int | bool) -> LeverObject:
    """The contract object of a lever, checked against the catalogue, with its scope."""
    spec = _spec(lever_id)
    _check_value(spec, value)
    return LeverObject(id=spec.id, value=value, scope=spec.scope)


def check_lever_objects(items: Iterable[Mapping[str, Any]]) -> list[LeverObject]:
    """Check a list of lever objects from a JSON contract the way its schema does: known ids,
    values of the right type, the catalogue scope, no other key, and each lever at most once."""
    out: list[LeverObject] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"id", "value", "scope"}:
            raise LeverError(f"a lever is an object with exactly id, value and scope: {item!r}")
        lever = make_lever(item["id"], item["value"])
        if item["scope"] != lever["scope"]:
            raise LeverError(f"{lever['id']} has scope {lever['scope']}, not {item['scope']!r}")
        if lever["id"] in seen:
            raise LeverError(f"{lever['id']} appears more than once in one list of levers")
        seen.add(lever["id"])
        out.append(lever)
    return out


def _value_schema(spec: LeverSpec) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": spec.value_type}
    if spec.minimum is not None:
        schema["minimum"] = spec.minimum
    if spec.scope == "all_orders":
        schema["const"] = True  # an upgrade only, see _check_value
    return schema


def lever_json_schema() -> dict[str, Any]:
    """JSON Schema of one lever object, embedded in llm_case and decision_request (a test keeps
    the files equal to this)."""
    return {
        "type": "object",
        "required": ["id", "value", "scope"],
        "additionalProperties": False,
        "properties": {
            "id": {"enum": [lever.id for lever in LEVERS]},
            "value": {"type": ["integer", "boolean"]},
            "scope": {"enum": list(LEVER_SCOPES)},
        },
        "allOf": [
            {
                "if": {"required": ["id"], "properties": {"id": {"const": lever.id}}},
                "then": {
                    "properties": {"value": _value_schema(lever), "scope": {"const": lever.scope}}
                },
            }
            for lever in LEVERS
        ],
    }


def lever_list_json_schema() -> dict[str, Any]:
    """JSON Schema of the `levers` array of llm_case and decision_request: lever objects, each
    lever id at most once (also with different values), no repeated object, at most one object per
    catalogue lever (maxItems keeps the array bounded for guided decoding, ADR-14)."""
    return {
        "type": "array",
        "maxItems": len(LEVERS),
        "uniqueItems": True,
        "items": lever_json_schema(),
        "allOf": [
            {
                "contains": {"required": ["id"], "properties": {"id": {"const": lever.id}}},
                "minContains": 0,
                "maxContains": 1,
            }
            for lever in LEVERS
        ],
    }
