"""Shield-layer fixtures: shared test fixtures for e2e."""

import pytest

from tests._support.fixtures import (  # noqa: F401  re-export
    ci_echo_standin,
    drop_inherited_real_github_env,
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
from tests._support.v012_install import (  # noqa: F401  re-export
    OLD_VERSION,
    build_wheel,
    install_global,
)


@pytest.fixture(scope="session")
def v012_wheels(tmp_path_factory):
    """Candidate wheels for the install journey (built once per worker)."""
    root = tmp_path_factory.mktemp("v012-wheels")
    return {
        "current": build_wheel(root / "current"),
        "old": build_wheel(root / "old", version=OLD_VERSION),
    }


@pytest.fixture(scope="session")
def v012_global(v012_wheels, tmp_path_factory):
    """Isolated global prefix carrying the current wheel (§2.5)."""
    prefix = tmp_path_factory.mktemp("v012-global")
    return install_global(prefix, v012_wheels["current"])


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    drop_inherited_real_github_env(monkeypatch)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
