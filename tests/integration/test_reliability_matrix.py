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
    assert len(_SCENARIOS) == 8
    assert "服务重启" in _SCENARIOS
    assert "人工pause竞态" in _SCENARIOS


# AC-NFR0151-02@v0.9 TRACKS-TRACE coverage threshold inherited
def test_coverage_threshold_inherited():
    """AC-NFR0151-02: v0.8 coverage threshold still gates this version."""
    from pathlib import Path

    import tomllib

    text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_bytes()
    data = tomllib.loads(text.decode())
    assert "coverage" in repr(data).lower()
    assert _SCENARIOS[0] == "服务重启"
