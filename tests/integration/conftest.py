"""Shared fixtures for tests/integration (Shield-owned support asset).

Currently hosts ONLY the SHIELD_FIX seed application diagnosed by T-013
(review round 8): the IF-CLOSURE-001 integration anchors depend on a
host_repo v0.7 candidate-bound evidence chain (approved AC + baseline /
mutation / FULL evidence at one candidate digest, test-plan §2.4). Frozen
test bodies stay untouched; the seed attaches around them via an autouse
fixture scoped strictly to the anchor module.
"""

from __future__ import annotations

from typing import Any

import pytest

_ANCHOR_MODULE = "tests.integration.test_trace_closure"


@pytest.fixture(autouse=True)
def _v07_closure_seed(request: Any):
    """Seed the closure evidence chain for the trace-closure module only.

    All other integration nodes receive the plain empty ``host_repo`` — this
    wrapper must not change any inherited fixture semantics. For the anchor
    module the seed supplies the Shield-side data premise so a conforming
    implementation can close ``closure=candidate-bound`` (SHIELD_FIX: missing
    seed was a test defect, GREEN failures caused solely by its absence are
    attributed to Shield, not to the implementation).

    The ``host_repo`` fixture is requested ONLY for the anchor module (via the
    nested fixture below). Requesting it unconditionally in an autouse fixture
    forced ``host_repo`` (``mkdir tmp_path/host`` + git init) to run for every
    integration node, colliding with ``tests/integration/helpers.make_repo``
    which creates the same ``tmp_path/host`` path (FileExistsError across 89
    nodes in FULL). Non-anchor nodes must never instantiate host_repo just
    because this autouse fixture exists.
    """
    if request.module.__name__ != _ANCHOR_MODULE:
        yield
        return
    host_repo = request.getfixturevalue("host_repo")
    from tests.integration.v07_closure_seed import seed_v07_closure_chain

    seed_v07_closure_chain(host_repo)
    yield
