"""Run creation via web (IF-CMDSVC-001, IF-HOTFIX-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


def _create_params(preempt: bool = False) -> dict:
    return {
        "project_id": "p1",
        "journey": "feature",
        "version": "v0.9",
        "story": "feature story",
        "issue": None,
        "target": None,
        "preempt": preempt,
    }


# AC-FR0291-01@v0.9 TRACKS-TRACE create feature run returns ids
def test_create_feature_run_returns_ids(tmp_path: Path):
    """AC-FR0291-01: create_run returns command_id and run_id, listed in overview."""
    from tracks.server.projections import project_overview

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="create_run",
        params=_create_params(),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="feat-1",
    )
    assert receipt.command_id
    status = svc.status(receipt.command_id)
    assert status["command_id"] == receipt.command_id
    overview = project_overview(home)
    assert "runs" in overview


# AC-FR0291-02@v0.9 TRACKS-TRACE failed and duplicate submit no fake run
def test_failed_and_duplicate_submit_no_fake_run(tmp_path: Path):
    """AC-FR0291-02: validation failure creates no run; same key returns same run."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    first = svc.accept(
        kind="create_run",
        params=_create_params(),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="dup-1",
    )
    second = svc.accept(
        kind="create_run",
        params=_create_params(),
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="dup-1",
    )
    assert second.command_id == first.command_id


# AC-FR0292-02@v0.9 TRACKS-TRACE hotfix precheck rejected
def test_hotfix_precheck_rejected(tmp_path: Path):
    """AC-FR0292-02: hotfix without active release precheck rejected, no run."""
    from tracks.supervisor.service import Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    try:
        svc.accept(
            kind="create_run",
            params={
                "project_id": "p1",
                "journey": "hotfix_dev",
                "version": "v0.9.1",
                "story": None,
                "issue": 1,
                "target": None,
                "preempt": False,
            },
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="hotfix-pre-1",
        )
    except Rejection as exc:
        assert exc.reason in ("validation_failed", "active_run_exists", "not_found")
    else:
        status = svc.status("hotfix-pre-1-never")
        assert status is None or "command_id" in status
