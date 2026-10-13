"""Who may call what (ADR-15 section 5): three demo users with fixed roles, no authentication.

The role is the role of the actor the request names: the person field of its body (approver,
author, inspector...), one of DEMO_USERS. An endpoint answers 403 forbidden_role when that role
is not among its `roles` (apps.api.schemas.ENDPOINTS). Every GET is open to the three roles; the
checkout is called by the simulated shop, with no role check.

The UI may also say which demo user it acts for in the optional header X-ReturnRing-Actor
(ADR-16). When present it must name a demo user, and a body that names a person must name the
same one: Ahmed's screen cannot approve as Sara.
"""

from __future__ import annotations

from typing import Final

from fastapi import Request
from pydantic import BaseModel

from apps.api.errors import forbidden_role
from apps.api.schemas import ENDPOINTS, Endpoint
from contracts.vocab import DEMO_ROLES, DEMO_USERS, Role

ACTOR_HEADER: Final = "X-ReturnRing-Actor"
# The person field of each request model (a HumanActor field: never "system", rule 5).
ACTOR_FIELDS: Final[dict[str, str]] = {
    "decision_request": "approver",
    "regenerate_request": "requested_by",
    "evidence_request": "author",
    "link_dispute_request": "author",
    "inspection_create_request": "inspector",
    "inspection_confirm_request": "inspector",
    "simulator_assumptions_update": "author",
    "catalog_task_create": "author",
    "catalog_task_update": "author",
    "coupon_rule_create": "approved_by",
    "coupon_application_reverse": "author",
    "agent_ask_request": "asked_by",
}
_BY_ROUTE: Final[dict[tuple[str, str], Endpoint]] = {(ep.method, ep.path): ep for ep in ENDPOINTS}


def endpoint(method: str, path: str) -> Endpoint:
    """The contract entry of a route, by method and path template ("/api/returns/{return_id}")."""
    try:
        return _BY_ROUTE[(method.upper(), path)]
    except KeyError:
        raise KeyError(f"{method} {path} is not an endpoint of ADR-15") from None


def actor_of(ep: Endpoint, body: BaseModel | None) -> str | None:
    """The person a request body names, if its model has a person field."""
    if body is None or ep.request is None or ep.request not in ACTOR_FIELDS:
        return None
    value = getattr(body, ACTOR_FIELDS[ep.request])
    return value if isinstance(value, str) else None


def _roles_text(roles: tuple[Role, ...]) -> str:
    return " or ".join(roles)


def check_role(ep: Endpoint, actor: str | None, header_actor: str | None = None) -> Role | None:
    """The role of the request's actor, or ApiError 403 forbidden_role (ADR-15 section 5)."""
    if header_actor is not None:
        if header_actor not in DEMO_USERS:
            raise forbidden_role(f"{header_actor} is not a demo user ({', '.join(DEMO_USERS)})")
        if actor is not None and actor != header_actor:
            raise forbidden_role(f"the request is made by {header_actor}, the body names {actor}")
    person = actor if actor is not None else header_actor
    if person is None:
        if ep.roles != DEMO_ROLES and ep.request in ACTOR_FIELDS:
            raise forbidden_role(f"{ep.method} {ep.path} needs a person: {_roles_text(ep.roles)}")
        return None
    role = DEMO_USERS.get(person)
    if role is None:
        raise forbidden_role(f"{person} is not a demo user ({', '.join(DEMO_USERS)})")
    if role not in ep.roles:
        raise forbidden_role(
            f"{person} ({role}) cannot call {ep.method} {ep.path}: {_roles_text(ep.roles)} only"
        )
    return role


def authorize(
    request: Request, method: str, path: str, body: BaseModel | None = None
) -> Role | None:
    """check_role for a route: the endpoint's roles, the body's person, the actor header."""
    ep = endpoint(method, path)
    return check_role(ep, actor_of(ep, body), request.headers.get(ACTOR_HEADER))
