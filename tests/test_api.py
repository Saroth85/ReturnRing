"""API skeleton: health, readiness and the labelled simulator example."""

from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app)


def test_healthz() -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_mode() -> None:
    body = client.get("/readyz").json()
    assert body["status"] == "ready" and body["mode"] in {"live", "cached"}


def test_simulator_example_is_labelled_and_correct() -> None:
    body = client.get("/api/simulator/assumptions").json()
    assert body["base"]["total_eur"] == "368.00"
    assert body["base"]["nature"] == "simulated"
    assert body["sensitivity"]["both"]["total_eur"] == "-192.00"
    assert "not a ReturnRing result" in body["source"]
