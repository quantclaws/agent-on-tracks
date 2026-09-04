"""Shield-layer fixtures: shared test fixtures + closure seed."""

from __future__ import annotations

from typing import Any

import pytest

# Re-export shared fixtures so every Shield suite sees the same helpers.
from tests._support.fixtures import (  # noqa: F401  re-export
    event_log,
    host_repo,
    steps,
    trac,
)


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """M2 forensic capture (convergence plan 2026-09-05).

    On a call-phase failure, capture the host repo's live git state +
    assertion context (and preserve the repo) BEFORE the temp fixture is
    torn down -- DIAGNOSE then rules on real evidence instead of static
    post-teardown inference (T-042 :78 "wrong tree" misdiagnosis class).
    Inert unless the runtime sets TRAC_FORENSICS_DIR.
    """
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        host_repo = item.funcargs.get("host_repo")
        if host_repo is not None:
            from tests._support.forensics import (
                capture_failure_forensics,
                forensics_dir,
            )

            if forensics_dir() is not None:
                capture_failure_forensics(item, rep, host_repo)


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
    seeded_host_repo = request.getfixturevalue("host_repo")
    from tests.integration.v07_closure_seed import seed_v07_closure_chain

    seed_v07_closure_chain(seeded_host_repo)
    yield
