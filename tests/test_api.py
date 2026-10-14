"""The API core (ADR-15, ADR-16): app factory, settings, the two headers, ErrorResponse on every
error path, role checks, the 501 stubs of the three route groups, /healthz and /readyz."""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from apps.api.app import create_app
from apps.api.clock import FixedClock
from apps.api.errors import ApiError
from apps.api.roles import ACTOR_FIELDS, ACTOR_HEADER, check_role, endpoint
from apps.api.routes import health, manager, operator, other
from apps.api.routes.stubs import GROUPS, endpoints_of, group_of, implemented
from apps.api.schemas import (
    API_MODELS,
    CACHED_LABEL,
    ENDPOINTS,
    MODE_HEADER,
    REQUEST_ID_HEADER,
    Endpoint,
)
from apps.api.settings import Settings, async_database_url
from contracts.vocab import DEMO_ROLES, DEMO_USERS
from tests.api_harness import CLOCK_AT, Api, open_api, settings_for, validate_body

ROUTERS: dict[str, APIRouter] = {
    "health": health.router,
    "operator": operator.router,
    "manager": manager.router,
    "other": other.router,
}


# ---- Settings --------------------------------------------------------------------------------


def test_settings_defaults_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DEVICE", "LLM_MODE", "DEMO_MODE", "SEED", "DATABASE_URL", "SERVING_DIR"):
        monkeypatch.delenv(key, raising=False)
    s = Settings.from_env()
    assert (s.device, s.llm_mode, s.demo_mode, s.seed) == ("cpu", "mock", "live", 42)
    assert s.database_url is None and s.serving_dir == Path("serving")
    env = {
        "DEVICE": "gpu",
        "LLM_MODE": "vllm",
        "DEMO_MODE": "cached",
        "SEED": "7",
        "DATABASE_URL": "postgresql://u:p@postgres:5432/returnring",
        "SERVING_DIR": "/srv/serving",
        "VLLM_BASE_URL": "http://vllm:8001/v1",
        "VLLM_API_KEY": "secret",
    }
    s = Settings.from_env(env)
    assert s.cached and s.llm_mode == "vllm" and s.seed == 7
    assert s.database_url == "postgresql+asyncpg://u:p@postgres:5432/returnring"
    assert s.serving_dir == Path("/srv/serving") and "secret" not in repr(s)


def test_database_urls_use_the_async_drivers() -> None:
    assert async_database_url("sqlite:///x.db") == "sqlite+aiosqlite:///x.db"
    assert async_database_url("postgres://h/db") == "postgresql+asyncpg://h/db"
    assert async_database_url("postgresql+asyncpg://h/db") == "postgresql+asyncpg://h/db"
    with pytest.raises(ValueError):
        async_database_url("mysql://h/db")
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_MODE": "anthropic"})


# ---- Routes and the contract ------------------------------------------------------------------


def _routes(app_routes: list[Any]) -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for route in app_routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                found[(method, route.path)] += 1
    return found


def test_every_endpoint_of_the_contract_is_registered_once() -> None:
    app = create_app(Settings(device="cpu", llm_mode="mock", demo_mode="live", seed=1))
    found: Counter[tuple[str, str]] = Counter()
    for router in ROUTERS.values():
        found += _routes(router.routes)
    expected = {(ep.method, ep.path) for ep in ENDPOINTS}
    assert set(found) == expected
    assert all(n == 1 for n in found.values())
    paths = app.openapi()["paths"]
    assert {(m.upper(), p) for p, ops in paths.items() for m in ops} == expected


def test_every_endpoint_belongs_to_one_group_and_router() -> None:
    for ep in ENDPOINTS:
        group = group_of(ep)
        assert group in GROUPS
        assert (ep.method, ep.path) in _routes(ROUTERS[group].routes)
    assert sum(len(endpoints_of(g)) for g in GROUPS) == len(ENDPOINTS)
    assert {(ep.method, ep.path) for ep in implemented("operator", operator.router)} >= {
        ("GET", "/api/returns/{return_id}")
    }
    assert {ep.path for ep in implemented("health", health.router)} == {"/healthz", "/readyz"}


def test_every_role_restricted_body_names_its_person() -> None:
    """ADR-15 section 5: the role is the role of the person a request body names."""
    for ep in ENDPOINTS:
        if ep.request is None:
            continue
        model = API_MODELS[ep.request]
        if ep.roles != DEMO_ROLES:
            assert ep.request in ACTOR_FIELDS, ep.path
        if ep.request in ACTOR_FIELDS:
            assert ACTOR_FIELDS[ep.request] in model.model_fields, ep.request


def _decision_endpoint() -> Endpoint:
    return endpoint("POST", "/api/returns/{return_id}/decision")


def test_role_check_follows_the_role_table() -> None:
    ep = _decision_endpoint()
    assert check_role(ep, "sara") == "operator" and check_role(ep, "luca") == "manager"
    with pytest.raises(ApiError) as caught:
        check_role(ep, "ahmed")
    assert caught.value.status == 403 and caught.value.code == "forbidden_role"
    with pytest.raises(ApiError):
        check_role(ep, "mallory")
    with pytest.raises(ApiError):
        check_role(ep, "sara", header_actor="luca")  # Luca's screen cannot act as Sara
    assert check_role(endpoint("GET", "/api/kpi"), None, "ahmed") == "warehouse"
    assert check_role(endpoint("POST", "/api/checkout/evaluate"), None) is None  # the shop
    assert set(DEMO_USERS.values()) == set(DEMO_ROLES)


# ---- Headers and errors -----------------------------------------------------------------------


def _assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    validate_body("error", body)
    assert body["code"] == code
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert response.headers[MODE_HEADER] in ("live", "cached")
    return body


def test_healthz_and_headers(empty_api: Api) -> None:
    response = empty_api.client.get("/healthz")
    assert response.status_code == 200
    validate_body("healthz_response", response.json())
    assert response.headers[MODE_HEADER] == "live"
    assert response.headers[REQUEST_ID_HEADER].startswith("req-")
    echoed = empty_api.client.get("/healthz", headers={REQUEST_ID_HEADER: "ui-000042"})
    assert echoed.headers[REQUEST_ID_HEADER] == "ui-000042"
    bad = empty_api.client.get("/healthz", headers={REQUEST_ID_HEADER: "bad id with spaces"})
    assert bad.headers[REQUEST_ID_HEADER].startswith("req-")


def test_readyz_live(empty_api: Api) -> None:
    response = empty_api.client.get("/readyz")
    body = response.json()
    validate_body("readyz_response", body)
    assert body["status"] == "ready" and body["mode"] == "live" and body["label"] is None
    checks = {c["component"]: c["status"] for c in body["checks"]}
    assert checks["database"] == "ok" and checks["scores"] == "ok" and checks["models"] == "ok"
    assert checks["vllm"] == "disabled"  # LLM_MODE=mock
    assert checks["telemetry"] == "disabled"  # DEVICE=cpu, no amd-smi sample
    assert body["assumptions_version"] == "assumptions-v1"
    assert body["model_versions"]["risk"].startswith("risk-request-xgboost")


def test_readyz_cached_carries_its_label_and_needs_the_cache(
    serving: Any, serving_dir: Path, tmp_path: Path
) -> None:
    """The cached label is shown while the precomputed outputs are there (1.5.0, H6)."""
    copy = tmp_path / "serving"
    shutil.copytree(serving_dir, copy)
    (copy / "llm_cache").mkdir(exist_ok=True)
    (copy / "llm_cache" / "entry.json").write_text("{}", encoding="utf-8")
    settings = settings_for(copy, tmp_path / "none.sqlite", demo_mode="cached")
    for api in open_api(serving, settings, tmp_path / "none.sqlite"):
        response = api.client.get("/readyz")
        body = response.json()
        validate_body("readyz_response", body)
        assert response.headers[MODE_HEADER] == "cached"
        assert body["label"] == CACHED_LABEL and body["llm_mode"] == "vllm"
        checks = {c["component"]: c for c in body["checks"]}
        assert checks["vllm"]["status"] == "disabled"
        assert checks["telemetry"]["status"] == "disabled"  # the GPU is off
        assert checks["database"]["status"] == "unavailable"  # an empty file: no tables
        assert body["status"] == "not_ready"


def test_readyz_without_database_or_snapshot(tmp_path: Path) -> None:
    settings = Settings(
        device="cpu", llm_mode="mock", demo_mode="live", seed=1, serving_dir=tmp_path / "none"
    )
    with TestClient(create_app(settings, clock=FixedClock(CLOCK_AT))) as client:
        body = client.get("/readyz").json()
        validate_body("readyz_response", body)
        checks = {c["component"]: c["status"] for c in body["checks"]}
        assert body["status"] == "not_ready"
        assert checks["database"] == checks["scores"] == checks["models"] == "unavailable"
        _assert_error(client.get("/api/returns/RET-1"), 500, "internal")


def test_unknown_paths_and_methods_answer_error_responses(empty_api: Api) -> None:
    _assert_error(empty_api.client.get("/api/nothing"), 404, "not_found")
    _assert_error(empty_api.client.delete("/healthz"), 405, "invalid_request")
    _assert_error(empty_api.client.get("/api/returns/RET-NOPE"), 404, "not_found")


def test_invalid_bodies_and_queries_are_invalid_requests(empty_api: Api) -> None:
    client = empty_api.client
    body = _assert_error(
        client.post("/api/returns/RET-1/decision", json={"action": "approve"}),
        422,
        "invalid_request",
    )
    assert body["violations"] and all(v["code"] for v in body["violations"])
    _assert_error(client.get("/api/returns", params={"page": 0}), 422, "invalid_request")
    _assert_error(
        client.post("/api/checkout/evaluate", content=b"not json"), 422, "invalid_request"
    )


def _valid_body(name: str) -> dict[str, Any]:
    from tests.test_api_examples import load

    examples = {
        "decision_request": "decision_request.marco_approve.json",
        "decision_request_ahmed": "decision_request.forbidden_role.json",
    }
    data: dict[str, Any] = load(examples[name])
    return data


def test_a_forbidden_role_is_refused_before_anything_else(empty_api: Api) -> None:
    """Ahmed (warehouse) cannot act on a decision (ADR-15 section 5): 403, the example body."""
    client = empty_api.client
    body = _assert_error(
        client.post("/api/returns/RET-000102/decision", json=_valid_body("decision_request_ahmed")),
        403,
        "forbidden_role",
    )
    assert "ahmed (warehouse)" in body["message"]
    mismatch = client.post(
        "/api/returns/RET-000102/decision",
        json=_valid_body("decision_request"),
        headers={ACTOR_HEADER: "luca"},
    )
    _assert_error(mismatch, 403, "forbidden_role")
    _assert_error(client.get("/api/kpi", headers={ACTOR_HEADER: "mallory"}), 403, "forbidden_role")


def test_every_stub_answers_501_with_the_contract_error(empty_api: Api) -> None:
    """The route builders start from here: each stub validates its input as the contract does
    and answers an ErrorResponse 501."""
    from tests.test_api_examples import load

    index = load("index.json")
    client = empty_api.client
    real = {(ep.method, ep.path) for g, r in ROUTERS.items() for ep in implemented(g, r)}
    tried = set()
    for route in index["routes"]:
        key = (route["method"], route["endpoint"])
        if key in real or key in tried or route["scenario"] != "default" or route["status"] >= 400:
            continue
        tried.add(key)
        ep = endpoint(*key)
        params = load(route["query"]) if route["query"] else None
        body = load(route["request"]) if route["request"] else None
        response = client.request(route["method"], route["path"], params=params, json=body)
        body = _assert_error(response, 501, "internal")
        assert body["message"] == f"{ep.method} {ep.path} is not implemented yet"
    stubbed = {(ep.method, ep.path) for ep in ENDPOINTS} - real
    assert tried == stubbed


def test_an_unexpected_error_is_a_logged_500_with_the_headers(tmp_path: Path) -> None:
    settings = Settings(
        device="cpu", llm_mode="mock", demo_mode="cached", seed=1, serving_dir=tmp_path
    )
    app = create_app(settings, clock=FixedClock(CLOCK_AT))

    async def boom() -> None:
        raise RuntimeError("a bug")

    app.add_api_route("/boom", boom, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/boom", headers={REQUEST_ID_HEADER: "req-boom"})
        body = _assert_error(response, 500, "internal")
        assert body["request_id"] == "req-boom" and "a bug" not in body["message"]
        assert response.headers[MODE_HEADER] == "cached"
