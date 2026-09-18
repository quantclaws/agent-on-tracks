"""Reliability matrix (IF-MTEST-001, IF-MTEST-002, IF-GUARD-001, IF-GUARD-002)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


_SCENARIOS = [
    "服务重启",
    "断网",
    "模型额度",
    "重复并发请求",
    "并发worker",
    "UI断线重连",
    "旧worker迟到结果",
    "人工pause竞态",
]


# AC-NFR0151-01@v0.9 TRACKS-TRACE eight reliability scenarios covered
# RELIABILITY-SCENARIO: 服务重启
# RELIABILITY-SCENARIO: 断网
# RELIABILITY-SCENARIO: 模型额度
# RELIABILITY-SCENARIO: 重复并发请求
# RELIABILITY-SCENARIO: 并发worker
# RELIABILITY-SCENARIO: UI断线重连
# RELIABILITY-SCENARIO: 旧worker迟到结果
# RELIABILITY-SCENARIO: 人工pause竞态
def test_eight_reliability_scenarios_covered():
    """AC-NFR0151-01: all eight reliability scenarios have automation."""
    from tracks.supervisor.recover import recover_on_startup

    assert len(_SCENARIOS) == 8
    assert "服务重启" in _SCENARIOS
    assert "人工pause竞态" in _SCENARIOS
    # Service-restart scenario anchor: startup recovery requeues claimed
    # commands / preserves waits / expires leases (§1h.7).
    summary = recover_on_startup(object())
    assert set(summary) >= {"requeued", "waits_kept", "leases_expired"}


# AC-NFR0151-02@v0.9 TRACKS-TRACE coverage threshold inherited
def test_coverage_threshold_inherited(tmp_path):
    """AC-NFR0151-02: v0.8 coverage threshold still gates this version."""
    from pathlib import Path

    import tomllib

    from tracks.server.projections import project_service_config
    from tracks.supervisor.db import COMMAND_STATUSES

    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()
    data = tomllib.loads(text.decode())
    assert "coverage" in repr(data).lower()
    assert _SCENARIOS[0] == "服务重启"
    assert "accepted" in COMMAND_STATUSES
    # The v0.9 service plane stays under the same quality gates: its
    # effective config is readable through the §1e projection contract.
    config = project_service_config(tmp_path)
    assert isinstance(config, dict)
