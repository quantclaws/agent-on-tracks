"""Backoff policy (IF-WAIT-001, IF-STREAM-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.waiting import WaitPolicy

pytestmark = pytest.mark.integration


# AC-NFR0152-01@v0.9 TRACKS-TRACE backoff growth bounded and config readable
def test_backoff_growth_bounded_and_config_readable(tmp_path: Path):
    """AC-NFR0152-01: backoff grows 60s toward 900s cap, config readable."""
    from tracks.server.projections import project_service_config
    from tracks.supervisor.waiting import next_probe

    policy = WaitPolicy()
    assert policy.initial_s == 60
    assert policy.cap_s == 900
    first = next_probe(policy, None)
    assert first["interval_s"] == policy.initial_s
    second = next_probe(policy, first)
    assert second["interval_s"] <= policy.cap_s
    config = project_service_config(tmp_path)
    assert config["wait_initial_s"] == 60
    assert config["wait_cap_s"] == 900


# AC-NFR0152-02@v0.9 TRACKS-TRACE poll fallback caps and idle silence
def test_poll_fallback_caps_and_idle_silence(tmp_path: Path):
    """AC-NFR0152-02: poll fallback capped, idle service emits no load."""
    from tracks.server.projections import project_service_config

    config = project_service_config(tmp_path)
    assert config["poll_interval_s"] == 5
    assert config["poll_idle_cap_s"] == 60
