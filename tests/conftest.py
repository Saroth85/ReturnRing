"""Shared pytest configuration.

Hypothesis runs are pinned (round 4 of the adversarial review, finding U1): the default profile
"ci" derandomizes every property test (a seed derived from the test itself, and so no example
database: derandomize implies database=None), so `make test` gives the same examples on every
machine whether or not a .hypothesis directory exists. Every failing example found so far is
also an explicit @example of its test. HYPOTHESIS_PROFILE=explore runs random examples with the
database, to look for new failures; add any failure it finds as an @example.
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("ci", derandomize=True, deadline=None, print_blob=True)
settings.register_profile("explore", derandomize=False, deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
