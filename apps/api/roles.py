"""Who may call what (ADR-15 section 5): three demo users with fixed roles, no authentication.

The role is the role of the actor the request names: the person field of its body (approver,
author, inspector...), one of DEMO_USERS. An endpoint answers 403 forbidden_role when that role
is not among its `roles` (apps.api.schemas.ENDPOINTS). Every GET is open to the three roles; the
checkout is called by the simulated shop, with no role check.

The UI may also say which demo user it acts for in the optional header X-ReturnRing-Actor
(ADR-16). When present it must name a demo user, and a body that names a person must name the
same one: Ahmed's screen cannot approve as Sara.

The public demo (PUBLIC_READ_ONLY=on; spec 11, "Sicurezza": the demo views are public and read
only; ADR-15 section 15, H21) answers 403 forbidden_role to every write route but the two the
public pages use: the agent and the checkout evaluation, each rate limited per client
(check_public_rate; an IPv6 client by its /64), and a 429 says when to retry (Retry-After). The
telemetry data is behind the proxy's basic auth.
"""

from __future__ import annotations

import ipaddress
from typing import Final

from fastapi import Request
from pydantic import BaseModel

from apps.api.errors import forbidden_role, rate_limited
from apps.api.ratelimit import PUBLIC_RATE_LIMIT, PUBLIC_RATE_WINDOW_S, RateLimiter
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
# The writes the public read-only demo keeps (H21): the agent and the checkout evaluation.
PUBLIC_WRITES: Final = frozenset({("POST", "/api/agent/ask"), ("POST", "/api/checkout/evaluate")})
READ_ONLY: Final = (
    "the public demo is read-only (PUBLIC_READ_ONLY): {method} {path} changes the demo and is "
    "refused; the demo views, the agent and the checkout evaluation stay open"
)


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


def public_read_only(request: Request) -> bool:
    runtime = getattr(request.app.state, "runtime", None)
    settings = getattr(runtime, "settings", None)
    return bool(getattr(settings, "public_read_only", False))


def authorize(
    request: Request, method: str, path: str, body: BaseModel | None = None
) -> Role | None:
    """check_role for a route: the endpoint's roles, the body's person, the actor header; on the
    public read-only demo, 403 for every write but PUBLIC_WRITES."""
    ep = endpoint(method, path)
    if (
        ep.method != "GET"
        and public_read_only(request)
        and (ep.method, ep.path) not in PUBLIC_WRITES
    ):
        raise forbidden_role(READ_ONLY.format(method=ep.method, path=ep.path))
    return check_role(ep, actor_of(ep, body), request.headers.get(ACTOR_HEADER))


def network_key(host: str) -> str:
    """The rate-limit key of an address: an IPv6 client by its /64 prefix (one subscriber
    usually holds a whole /64), an IPv4-mapped one by its IPv4 address; anything else as is."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(address), 64), strict=False))
    return str(address)


def trusted_proxy_hops(request: Request) -> int:
    """TRUSTED_PROXY_HOPS of the app's settings (1 when the request has no app: a bare test)."""
    app = request.scope.get("app")
    settings = getattr(getattr(getattr(app, "state", None), "runtime", None), "settings", None)
    return int(getattr(settings, "trusted_proxy_hops", 1))


def client_key(request: Request) -> str:
    """The client of a request, for the rate limits: the address the nearest trusted proxy saw
    (network_key: an IPv6 client by its /64). With TRUSTED_PROXY_HOPS=n it is the n-th
    X-Forwarded-For entry from the right: the entries before it come from the client and can be
    anything, so keying on the first one let a visitor be a new client on every request (PR 15
    review). With 0, or fewer entries than n, the TCP peer."""
    hops = trusted_proxy_hops(request)
    if hops > 0:
        entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",")]
        entries = [e for e in entries if e]
        if len(entries) >= hops:
            return network_key(entries[-hops])
    return network_key(request.client.host) if request.client is not None else "unknown"


def check_public_rate(request: Request) -> None:
    """On the public read-only demo, 429 rate_limited past PUBLIC_RATE_LIMIT writes of one
    client in the window (spec 11: the public endpoints are rate limited)."""
    if not public_read_only(request):
        return
    limiter = getattr(request.app.state, "public_rate_limiter", None)
    if not isinstance(limiter, RateLimiter):
        limiter = RateLimiter(PUBLIC_RATE_LIMIT, PUBLIC_RATE_WINDOW_S)
        request.app.state.public_rate_limiter = limiter
    wait = limiter.retry_after(client_key(request))
    if wait is not None:
        raise rate_limited(
            f"at most {PUBLIC_RATE_LIMIT} requests of the public demo per client every "
            f"{PUBLIC_RATE_WINDOW_S:.0f} s",
            wait,
        )
