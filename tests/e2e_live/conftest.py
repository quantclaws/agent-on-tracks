"""Live opencode E2E channel fixtures (test-plan §2 / §4b / TP-003 §6.4).

The live channel proves the real-agent pipeline (materialize -> subprocess ->
JSON protocol -> target diff -> audit) against a real opencode + env-configured
provider/model. It asserts protocol/startup/permission/product/recovery only and
NEVER asserts prompt/text content (AC-0104). Missing credentials -> skip, never
fail (AC-0105).

This conftest deliberately OVERRIDES the root ``_force_fake_backend`` autouse
fixture so the deterministic fake channel cannot hijack the live job.
"""

from __future__ import annotations

import json
import os

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
def live_backend(live_enabled, host_repo, monkeypatch):
    """OpencodeBackend wired to a real opencode + the env provider/model.

    Also exports the provider env to the subprocess so opencode can resolve
    provider credentials beyond what opencode.json references via {env:...}.
    """
    cfg = live_enabled
    for k, v in cfg.items():
        monkeypatch.setenv(k, v)
    # OpencodeBackend resolves its canonical prompts from tracks/agents/ and
    # materializes them into host_repo/.opencode/agents/ — exactly what the
    # live run must exercise.
    return OpencodeBackend(host_repo, "v0.2", timeout=120)


@pytest.fixture
def host_with_opencode_config(host_repo, live_enabled):
    """Write opencode.json into the host repo so `opencode run` resolves the
    provider/model/baseURL. Keys are referenced via {env:...} — never inlined
    into the repo tree."""
    cfg = live_enabled
    (host_repo / "opencode.json").write_text(
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
    return host_repo
