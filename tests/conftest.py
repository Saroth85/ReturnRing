"""Shared pytest configuration.

Hypothesis runs are pinned (round 4 of the adversarial review, finding U1): the default profile
"ci" derandomizes every property test (a seed derived from the test itself, and so no example
database: derandomize implies database=None), so `make test` gives the same examples on every
machine whether or not a .hypothesis directory exists. Every failing example found so far is
also an explicit @example of its test. HYPOTHESIS_PROFILE=explore runs random examples with the
database, to look for new failures; add any failure it finds as an @example.

The fixtures of the API conformance harness (api, empty_api, seeded_db, serving, serving_data,
serving_dir) come from tests/api_harness.py, registered as a plugin wherever the API's
dependencies are installed. The GraphSAGE image (`make gnn-test-gpu`) has PyTorch and DGL but no
FastAPI: it imports this file and runs only the GraphSAGE tests, so the harness is not loaded
there (tests/test_infra.py checks what the image needs).
"""

from __future__ import annotations

import importlib.util
import os

import pytest
from hypothesis import settings

settings.register_profile("ci", derandomize=True, deadline=None, print_blob=True)
settings.register_profile("explore", derandomize=False, deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

API_HARNESS = "tests.api_harness"


def pytest_configure(config: pytest.Config) -> None:
    if importlib.util.find_spec("fastapi") is not None:
        config.pluginmanager.import_plugin(API_HARNESS)
