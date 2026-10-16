"""Single-service deployment boundary: API routing, SPA history and admin protection."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.public_demo import add_public_ui, create_public_app


@pytest.fixture
def ui_dir(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>ReturnRing demo</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-123.js").write_text("console.log('demo');")
    (tmp_path / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    return tmp_path


def app(ui_dir: Path, *, read_only: bool = True, password: str = "test-password") -> FastAPI:
    api = FastAPI()

    @api.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/telemetry")
    def telemetry() -> dict[str, str]:
        return {"data": "admin-only"}

    @api.post("/api/checkout/evaluate")
    def evaluate() -> dict[str, str]:
        return {"status": "evaluated"}

    return add_public_ui(api, ui_dir=ui_dir, admin_password=password, public_read_only=read_only)


@pytest.mark.parametrize("path", ["/", "/queue", "/returns/RET-000001", "/system"])
def test_browser_history_routes_serve_the_built_ui(ui_dir: Path, path: str) -> None:
    with TestClient(app(ui_dir)) as client:
        response = client.get(path)
        assert response.status_code == 200
        assert "ReturnRing demo" in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    ["/api", "/api/not-a-route", "/healthz/missing", "/readyz/missing", "/assets/missing.js"],
)
def test_api_and_missing_assets_never_fall_back_to_spa(ui_dir: Path, path: str) -> None:
    with TestClient(app(ui_dir)) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert "ReturnRing demo" not in response.text


def test_real_api_route_and_assets_take_precedence(ui_dir: Path) -> None:
    with TestClient(app(ui_dir)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        script = client.get("/assets/app-123.js")
        assert script.status_code == 200 and script.text == "console.log('demo');"
        assert client.get("/favicon.svg").headers["content-type"] == "image/svg+xml"
        assert client.head("/queue").status_code == 200
        assert not client.head("/queue").content


@pytest.mark.parametrize("password", ["test-password", ""])
def test_telemetry_fails_closed_without_valid_admin_credentials(
    ui_dir: Path, password: str
) -> None:
    with TestClient(app(ui_dir, password=password)) as client:
        for headers in ({}, {"Authorization": "Basic invalid-base64"}):
            response = client.get("/api/telemetry", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"].startswith("Basic ")
        assert client.get("/api/telemetry", auth=("another-user", password)).status_code == 401
        assert client.get("/api/telemetry", auth=("admin", "wrong-password")).status_code == 401
        if not password:
            assert client.get("/api/telemetry", auth=("admin", "")).status_code == 401


def test_admin_can_read_telemetry_and_unicode_passwords_are_supported(ui_dir: Path) -> None:
    password = "demo-pässword:with-colon"
    encoded = base64.b64encode(f"admin:{password}".encode()).decode()
    with TestClient(app(ui_dir, password=password)) as client:
        response = client.get("/api/telemetry", headers={"Authorization": f"Basic {encoded}"})
        assert response.status_code == 200
        assert response.json() == {"data": "admin-only"}


def test_public_checkout_stays_open_but_writable_mode_needs_admin(ui_dir: Path) -> None:
    with TestClient(app(ui_dir)) as client:
        assert client.post("/api/checkout/evaluate").status_code == 200
    with TestClient(app(ui_dir, read_only=False)) as client:
        assert client.post("/api/checkout/evaluate").status_code == 401
        response = client.post("/api/checkout/evaluate", auth=("admin", "test-password"))
        assert response.status_code == 200


@pytest.mark.parametrize("read_only_env", [None, "", " "])
def test_factory_defaults_to_read_only_and_preserves_api_write_refusal(
    ui_dir: Path, monkeypatch: pytest.MonkeyPatch, read_only_env: str | None
) -> None:
    monkeypatch.setenv("PUBLIC_DEMO_UI_DIR", str(ui_dir))
    if read_only_env is None:
        monkeypatch.delenv("PUBLIC_READ_ONLY", raising=False)
    else:
        monkeypatch.setenv("PUBLIC_READ_ONLY", read_only_env)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LLM_MODE", "mock")
    api = create_public_app()
    assert api.state.runtime.settings.public_read_only
    with TestClient(api) as client:
        response = client.post(
            "/api/returns/RET-000001/regenerate",
            json={"decision_id": "DEC-000001", "languages": ["it"], "requested_by": "sara"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "forbidden_role"
