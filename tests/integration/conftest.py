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
def _v07_closure_seed(request: Any, host_repo):
    """Seed the closure evidence chain for the trace-closure module only.

    All other integration nodes receive the plain empty ``host_repo`` — this
    wrapper must not change any inherited fixture semantics. For the anchor
    module the seed supplies the Shield-side data premise so a conforming
    implementation can close ``closure=candidate-bound`` (SHIELD_FIX: missing
    seed was a test defect, GREEN failures caused solely by its absence are
    attributed to Shield, not to the implementation).
    """
    if request.module.__name__ != _ANCHOR_MODULE:
        yield
        return
    from tests.integration.v07_closure_seed import seed_v07_closure_chain

    seed_v07_closure_chain(host_repo)
    yield
