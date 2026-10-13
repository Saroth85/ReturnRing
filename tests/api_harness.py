"""Conformance harness of the API (ADR-15, ADR-16) for the route builders.

Fixtures (registered in tests/conftest.py):

- serving_data (session): a tiny generated store, Navigli Fashion at scale 0.05, seed 7, from the
  illustrative calibration example, with its account graph, customer features, product anomalies,
  risk models (python -m ml.risk, an offline job that reads the synthetic truth) and the batch
  scoring. Every number is synthetic and none is a result.
- serving_dir (session): the serving snapshot of serving_data (python -m pipeline.serving).
- serving (session): that snapshot loaded in memory (apps.api.serving.Serving).
- seeded_db (session): a SQLite database after the demo reset (python -m apps.api.reset): the
  seeds and the intake of every open return, golden cases first, drafts by the mock model.
- api: an app on a fresh copy of seeded_db, a fixed clock at CLOCK_AT, LLM_MODE=mock,
  DEMO_MODE=live, and its TestClient (api.client). api.run(fn) runs `async fn(conn)` on the
  same database, for assertions on rows.
- empty_api: the same app on a database with the seeds only (no return yet).

Helpers: validate_body(name, body) checks a response or request body against
contracts/json/api/<name>.schema.json (and the pydantic model of the contract);
golden_return(serving, customer_id) is the open return of a golden customer.

Use them in a route module's tests:

    def test_queue(api: Api) -> None:
        response = api.client.get("/api/returns")
        assert response.status_code == 200
        validate_body("queue_response", response.json())
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncConnection

from apps.api.app import create_app
from apps.api.clock import FixedClock
from apps.api.db import create_schema, make_engine
from apps.api.reset import reset
from apps.api.schemas import API_MODELS
from apps.api.services.generation import Generator
from apps.api.services.seeds import load_seeds
from apps.api.serving import Serving
from apps.api.settings import Settings
from llm.client import LlmClient
from ml.reference.model import TrainConfig
from pipeline.anomalies.build import build as build_anomalies
from pipeline.anomalies.build import store_decision_times
from pipeline.features.__main__ import store_month_ends
from pipeline.features.build import build as build_features
from pipeline.generator.calibration import load_calibration
from pipeline.generator.core import generate
from pipeline.graph.build import main as graph_main
from pipeline.serving.export import export

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "json" / "api"
CALIBRATION = ROOT / "contracts" / "examples" / "calibration.json"
SCALE = 0.05
SEED = 7
FAST = TrainConfig(folds=3, min_train_lines=500, min_validation_lines=100)
CLOCK_AT = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)  # the day after the snapshot (2026-10-01)

T = TypeVar("T")


@cache
def schema(name: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SCHEMAS / f"{name}.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(document)
    return document


def validate_body(name: str, body: Any) -> None:
    """`body` is a valid `name` of the contract: its JSON Schema and its pydantic model."""
    Draft202012Validator(schema(name)).validate(body)
    API_MODELS[name].model_validate(body)


def sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def build_store(out: Path) -> Path:
    """The tiny data root: store, graph, features, anomalies, risk models and scores."""
    from ml.risk.__main__ import main as risk_main  # offline job: reads the synthetic truth

    generate(load_calibration(CALIBRATION), out, seed=SEED, scale=SCALE, scenario="main")
    assert graph_main(["--store", str(out)]) == 0
    build_features(out, out, store_month_ends(out), config=FAST, clusters_root=out)
    build_anomalies(out, out, store_decision_times(out))
    argv = ["--data", str(out), "--out", str(out / "artifacts"), "--scores", str(out)]
    assert risk_main([*argv, "--no-results", "--seed", str(SEED)]) == 0
    return out


def settings_for(serving_dir: Path, database: Path, **changes: Any) -> Settings:
    base = Settings(
        device="cpu",
        llm_mode="mock",
        demo_mode="live",
        seed=SEED,
        database_url=sqlite_url(database),
        serving_dir=serving_dir,
    )
    return Settings(**{**base.__dict__, **changes})


async def _seed_only(url: str, at: datetime) -> None:
    engine = make_engine(url)
    try:
        await create_schema(engine)
        async with engine.begin() as conn:
            await load_seeds(conn, at)
    finally:
        await engine.dispose()


async def _reset(url: str, serving: Serving, settings: Settings) -> dict[str, Any]:
    engine = make_engine(url)
    try:
        return await reset(engine, serving, Generator(settings))
    finally:
        await engine.dispose()


@dataclass
class Api:
    """An app under test with its client, clock, serving snapshot and database."""

    app: FastAPI
    client: TestClient
    clock: FixedClock
    serving: Serving
    settings: Settings
    database: Path

    def run(self, fn: Callable[[AsyncConnection], Awaitable[T]]) -> T:
        """Run `await fn(conn)` in a transaction on the app's database."""

        async def go() -> T:
            engine = make_engine(sqlite_url(self.database))
            try:
                async with engine.begin() as conn:
                    return await fn(conn)
            finally:
                await engine.dispose()

        return asyncio.run(go())


def open_api(
    serving: Serving,
    settings: Settings,
    database: Path,
    *,
    clock: FixedClock | None = None,
    llm_client: LlmClient | None = None,
) -> Iterator[Api]:
    the_clock = clock or FixedClock(CLOCK_AT)
    app = create_app(settings, clock=the_clock, serving=serving, llm_client=llm_client)
    with TestClient(app) as client:
        yield Api(app, client, the_clock, serving, settings, database)


def golden_return(serving: Serving, customer_id: str) -> str:
    """The open return of a golden customer in the tiny store."""
    req = serving.frame("scores/request_scores")
    found = sorted(req.loc[req["customer_id"] == customer_id, "return_id"])
    if not found:
        pytest.skip(f"no open return of {customer_id} in the tiny store")
    return str(found[0])


# ---- Fixtures ------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def serving_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_store(tmp_path_factory.mktemp("api-store"))


@pytest.fixture(scope="session")
def serving_dir(serving_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("api-serving") / "serving"
    export(serving_data, serving_data / "artifacts", out, seed=SEED)
    return out


@pytest.fixture(scope="session")
def serving(serving_dir: Path) -> Serving:
    return Serving.load(serving_dir)


@pytest.fixture(scope="session")
def seeded_db(
    serving: Serving, serving_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Path, dict[str, Any]]:
    path = tmp_path_factory.mktemp("api-db") / "seeded.sqlite"
    settings = settings_for(serving_dir, path)
    summary = asyncio.run(_reset(sqlite_url(path), serving, settings))
    assert not summary["failed"], summary["failed"]
    return path, summary


@pytest.fixture
def api(
    serving: Serving,
    serving_dir: Path,
    seeded_db: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> Iterator[Api]:
    database = tmp_path / "api.sqlite"
    shutil.copyfile(seeded_db[0], database)
    yield from open_api(serving, settings_for(serving_dir, database), database)


@pytest.fixture
def empty_api(serving: Serving, serving_dir: Path, tmp_path: Path) -> Iterator[Api]:
    database = tmp_path / "empty.sqlite"
    asyncio.run(_seed_only(sqlite_url(database), serving.as_of))
    yield from open_api(serving, settings_for(serving_dir, database), database)
