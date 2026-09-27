"""Shield-layer fixtures: shared test fixtures for e2e."""

import pytest

from tests._support.fixtures import (  # noqa: F401  re-export
    ci_echo_standin,
    event_log,
    host_repo,
    steps,
    trac,
)

# v0.10 (IF-WEBUI-001 / NFR-0155): the Playwright/Chromium ui-layer fixture
# surface (real browser, isolated contexts, browser-restart storage state).
from tests._support.ui_browser import (  # noqa: F401  re-export
    SERVE_PASSWORD,
    browser,
    context,
    page,
    restart_browser,
    ui_serve,
)


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
