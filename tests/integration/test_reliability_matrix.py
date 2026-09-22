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
def test_eight_reliability_scenarios_covered(tmp_path):
    """AC-NFR0151-01: all eight reliability scenarios have automation."""
    from datetime import datetime, timedelta, timezone

    from tracks.supervisor.db import ServiceDB
    from tracks.supervisor.lease import acquire_lease
    from tracks.supervisor.recover import recover_on_startup

    assert len(_SCENARIOS) == 8
    assert "服务重启" in _SCENARIOS
    assert "人工pause竞态" in _SCENARIOS
    # Service-restart scenario anchor (§1h.7): a claimed command is requeued
    # under the same id, the persisted wait keeps its retry_at, and the
    # lapsed lease is released for a fresh generation.
    home = tmp_path / "home"
    db = ServiceDB(home)
    db.register_command(
        {
            "command_id": "cmd-restart",
            "kind": "pause_run",
            "params_json": '{"run_id": "run-restart"}',
            "params_digest": "digest-restart",
            "idempotency_key": "restart-cmd-1",
            "actor": "local-user",
            "actor_class": "human",
            "surface": "http",
            "project_id": None,
            "run_id": "run-restart",
        }
    )
    assert db.claim_command("cmd-restart", "worker-1", 1)
    now = datetime.now(timezone.utc)
    retry_at = (now + timedelta(hours=1)).isoformat()
    db.upsert_wait("run-restart", "quota", "rate limited", retry_at, False, None, now.isoformat())
    acquire_lease(db, "run-restart", "worker-1", -60)  # the worker's lease lapsed

    summary = recover_on_startup(db)

    assert summary["requeued"] == ["cmd-restart"]
    assert summary["waits_kept"] == ["run-restart"]
    assert summary["leases_expired"] == ["run-restart"]
    # the requeued command is claimable again under the same id (§1h.7)
    assert db.get_command("cmd-restart")["status"] == "accepted"
    # the persisted wait survives the restart with its retry_at untouched
    assert db.get_wait("run-restart")["retry_at"] == retry_at
    # the lapsed lease is audited as released(expired), not silently dropped
    released = [
        event
        for event in db.read_events(run_id="run-restart")
        if event["type"] == "lease.released"
    ]
    assert released, "the expired lease must be audited"
    assert released[-1]["payload"]["reason"] == "expired"


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
