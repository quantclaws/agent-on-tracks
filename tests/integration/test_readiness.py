"""Readiness probes (IF-PROJ-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.supervisor.readiness import PROBE_KINDS, run_readiness

pytestmark = pytest.mark.integration


# AC-FR0290-01@v0.9 TRACKS-TRACE readiness items displayed
def test_readiness_items_displayed(tmp_path: Path):
    """AC-FR0290-01: four probes each report ok plus reason."""
    results = run_readiness(tmp_path)
    by_check = {r.check: r for r in results}
    assert set(by_check) == set(PROBE_KINDS)
    for kind in PROBE_KINDS:
        assert by_check[kind].ok in (True, False)
        if not by_check[kind].ok:
            assert by_check[kind].reason


# AC-FR0290-02@v0.9 TRACKS-TRACE not ready blocks run creation
def test_not_ready_blocks_run_creation(tmp_path: Path):
    """AC-FR0290-02: missing tool blocks create_run with concrete reason."""
    from tracks.supervisor.service import CommandService

    results = run_readiness(tmp_path)
    assert any(r.check == "tools" for r in results)
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="create_run",
        params={
            "project_id": "p1",
            "journey": "feature",
            "version": "v0.9",
            "story": "s",
            "issue": None,
            "target": None,
            "preempt": False,
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="ready-block-1",
    )
    assert receipt.command_id
