"""Live opencode E2E channel fixtures (test-plan §2 / §4b / TP-003 §6.4).

The live channel proves the real-agent pipeline (materialize -> subprocess ->
JSON protocol -> target diff -> audit) against a real opencode + env-configured
provider/model. It asserts protocol/startup/permission/product/recovery only and
NEVER asserts prompt/text content (AC-0104). Missing credentials -> skip, never
fail (AC-0105).

This conftest deliberately OVERRIDES the root ``_force_fake_backend`` autouse
fixture so the deterministic fake channel cannot hijack the live job.

Working directory: every test gets its own git repo under
``${TMPDIR}/tracks/live-e2e/`` — never inside the workspace. Provider/model
config lives in ``.opencode/opencode.json`` (mirroring the project's own
``.opencode/`` layout), and ``OpencodeBackend`` materializes agents into
``.opencode/agents/`` under the same root.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tracks.effects.opencode import OpencodeBackend

# Env contract (TP-003 §4b, Aaron §3.1): provider/model/baseURL/apiKey.
LIVE_ENV = ("TRAC_LIVE_PROVIDER", "TRAC_LIVE_MODEL", "TRAC_LIVE_BASE_URL", "TRAC_LIVE_API_KEY")


@pytest.fixture(autouse=True)
def _force_live_backend(monkeypatch):
    """Live channel overrides the root fake-forcing fixture (see module doc)."""
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")


def _live_env() -> dict:
    """Provider/model config from env; empty dict if the live channel is unset."""
    return {k: os.environ.get(k, "").strip() for k in LIVE_ENV}


@pytest.fixture
def live_enabled():
    """Skip the live channel when credentials are missing (AC-0105).

    A job that intends to run live exports all four variables; a default local
    test run has none of them and must skip cleanly instead of failing.
    """
    missing = [k for k, v in _live_env().items() if not v]
    if missing:
        pytest.skip(
            f"live channel unconfigured (missing {', '.join(missing)}); "
            "set TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY to enable"
        )
    return _live_env()


@pytest.fixture
def live_root(live_enabled, tmp_path_factory):
    """A clean, per-test git working root under ${TMPDIR}/tracks/live-e2e/.

    Each test gets its own subdirectory (indexed) so runs never collide. The
    root is a real git repo (the agent's working tree) and hosts the opencode
    configuration under ``.opencode/opencode.json`` + materialized ``agents/``
    — mirroring the project's own ``tracks/.opencode/`` layout. Lives under
    the system temp dir, never inside the workspace.
    """
    base = Path(os.environ.get("TMPDIR", "/tmp")) / "tracks" / "live-e2e"
    base.mkdir(parents=True, exist_ok=True)
    # Unique per test even under parallel runs.
    n = 0
    while True:
        candidate = base / f"run{n:03d}"
        try:
            candidate.mkdir(parents=False)
            break
        except FileExistsError:
            n += 1
    repo = candidate
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("live e2e host\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def host_with_opencode_config(live_root, live_enabled):
    """Lay down ``.opencode/opencode.json`` in the live root so ``opencode run
    --dir <root>`` resolves provider/model/baseURL. API key is referenced via
    ``{env:...}`` — never inlined into the tree. Mirrors the project's own
    ``.opencode/opencode.json`` structure (config under .opencode/)."""
    cfg = live_enabled
    dot = live_root / ".opencode"
    dot.mkdir(parents=True, exist_ok=True)
    (dot / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    cfg["TRAC_LIVE_PROVIDER"]: {
                        "options": {
                            "baseURL": cfg["TRAC_LIVE_BASE_URL"],
                            "apiKey": "{env:TRAC_LIVE_API_KEY}",
                        },
                    },
                },
                "model": f"{cfg['TRAC_LIVE_PROVIDER']}/{cfg['TRAC_LIVE_MODEL']}",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return live_root


@pytest.fixture
def live_backend(live_root, live_enabled, monkeypatch):
    """OpencodeBackend wired to a real opencode + the env provider/model.

    Exports the provider env to the subprocess so opencode can resolve provider
    credentials referenced via ``{env:...}`` in ``.opencode/opencode.json``.
    """
    cfg = live_enabled
    for k, v in cfg.items():
        monkeypatch.setenv(k, v)
    provider = cfg["TRAC_LIVE_PROVIDER"]
    model_name = cfg["TRAC_LIVE_MODEL"]
    return OpencodeBackend(live_root, "v0.2", timeout=300, model=f"{provider}/{model_name}")
