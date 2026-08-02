"""Pytest fixtures for the opt-in live channel."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.e2e_live.harness import (
    LIVE_ENV,
    InstalledBackend,
    LiveTracDriver,
    load_scenarios,
    prepare_live_install,
    timeout_env,
)


@pytest.fixture(autouse=True)
def _force_live_backend(monkeypatch):
    """Override the root deterministic fake-backend fixture for this directory."""
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")


def _provider_config() -> dict[str, str]:
    return {key: os.environ.get(key, "").strip() for key in LIVE_ENV}


@pytest.fixture
def live_enabled():
    """Skip ordinary live tests only when one provider variable is absent."""
    config = _provider_config()
    missing = [key for key, value in config.items() if not value]
    if missing:
        pytest.skip(
            f"live channel unconfigured (missing {', '.join(missing)}); "
            "set TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY to enable"
        )
    return config


@pytest.fixture
def live_root(live_enabled):
    """Create a disposable Git host outside the workspace."""
    base = Path(os.environ.get("TMPDIR", "/tmp")) / "tracks" / "live-e2e"
    base.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        candidate = base / f"run{index:03d}"
        try:
            candidate.mkdir()
            break
        except FileExistsError:
            index += 1
    repo = candidate.resolve()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test Human"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("live e2e host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
    )
    print(f"LIVE_E2E_HOST={repo}", flush=True)
    return repo


@pytest.fixture
def live_install(live_root, live_enabled):
    return prepare_live_install(live_root)


@pytest.fixture
def live_scenarios():
    return load_scenarios()


@pytest.fixture
def live_github_repo(live_root, live_enabled, live_install, monkeypatch):
    """Require disposable GitHub coordinates and usable auth for full live."""
    value = os.environ.get("TRACKS_E2E_GITHUB_REPO", "").strip()
    if not value:
        raise AssertionError(
            "full live journey requires TRACKS_E2E_GITHUB_REPO; "
            f"host={live_root}; install_log={live_install.install_log}"
        )
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        try:
            auth = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=30
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            auth = None
        token = auth.stdout.strip() if auth is not None and auth.returncode == 0 else ""
    if not token:
        raise AssertionError(
            "full live journey requires GitHub auth via GITHUB_TOKEN or `gh auth token`; "
            f"host={live_root}; install_log={live_install.install_log}"
        )
    slug = value.removesuffix(".git").rstrip("/")
    if "github.com" in slug:
        slug = slug.split("github.com", maxsplit=1)[1].lstrip("/:")
    if not re.fullmatch(r"[^/]+/[^/]+", slug):
        raise AssertionError(f"TRACKS_E2E_GITHUB_REPO must be owner/name, got {value!r}")
    monkeypatch.setenv("TRAC_GITHUB_REPO", slug)
    monkeypatch.setenv("GITHUB_TOKEN", token)
    print(f"LIVE_E2E_REMOTE=git@github.com:{slug}.git", flush=True)
    return slug


@pytest.fixture
def host_with_opencode_config(live_root, live_enabled):
    """Write provider configuration only; never create Agent definitions."""
    config = live_enabled
    dot = live_root / ".opencode"
    dot.mkdir(parents=True, exist_ok=True)
    (dot / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    config["TRAC_LIVE_PROVIDER"]: {
                        "options": {
                            "baseURL": config["TRAC_LIVE_BASE_URL"],
                            "apiKey": "{env:TRAC_LIVE_API_KEY}",
                        }
                    }
                },
                "model": f"{config['TRAC_LIVE_PROVIDER']}/{config['TRAC_LIVE_MODEL']}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return live_root


@pytest.fixture
def live_backend(host_with_opencode_config, live_root, live_enabled, live_install, monkeypatch):
    """Proxy direct live-agent plumbing tests through the installed package."""
    config = live_enabled
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("TRAC_AGENT_CONSOLE_INPUT", "")
    timeout = timeout_env("TRAC_AGENT_TIMEOUT", 120)
    return InstalledBackend(
        live_root,
        live_install,
        f"{config['TRAC_LIVE_PROVIDER']}/{config['TRAC_LIVE_MODEL']}",
        timeout,
    )


@pytest.fixture
def live_trac(live_root, live_enabled, live_install, live_scenarios, request):
    """Return a bounded driver for the installed ``trac`` console script."""
    driver = LiveTracDriver(
        live_root,
        live_install,
        live_scenarios,
        timeout_env("TRAC_AGENT_TIMEOUT", 120),
        timeout_env("TRAC_LIVE_COMMAND_TIMEOUT", 360),
        timeout_env("TRAC_LIVE_TOTAL_TIMEOUT", 1800),
        timeout_env("TRAC_LIVE_MAX_COMMANDS", 48),
        timeout_env("TRAC_LIVE_MAX_STAGE_DISPATCHES", 8),
        timeout_env("TRAC_LIVE_MAX_REVIEW_DISPATCHES", 2),
        timeout_env("TRAC_LIVE_MAX_REVIEW_ROUNDS", 2),
    )
    request.addfinalizer(driver.finalize)
    return driver.run
