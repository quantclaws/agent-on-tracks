"""Shield-layer fixtures: shared test fixtures for e2e."""

import pytest

from tests._support.fixtures import (  # noqa: F401  re-export
    ci_echo_standin,
    event_log,
    host_repo,
    steps,
    trac,
)


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
