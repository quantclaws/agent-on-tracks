"""Shield-layer fixtures for live e2e tests."""

import pytest

from tests._support.fixtures import (  # noqa: F401
    event_log,
    host_repo,
    steps,
    trac,
)


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
