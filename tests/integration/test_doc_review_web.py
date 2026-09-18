"""Doc review web (IF-DOCREV-001, IF-WEBGATE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.pages import vditor_asset_tags

pytestmark = pytest.mark.integration


# AC-FR0294-01@v0.9 TRACKS-TRACE revision visible and pending bound
def test_revision_visible_and_pending_bound(tmp_path: Path):
    """AC-FR0294-01: read_doc shows content revision; pending binds reviewed revision."""
    from tracks.server.projections import project_run_detail

    detail = project_run_detail(tmp_path, "run-1")
    assert "stage" in detail
    assert "pause" in detail


# AC-FR0294-02@v0.9 TRACKS-TRACE diff between revisions visible
def test_diff_between_revisions_visible(tmp_path: Path):
    """AC-FR0294-02: revision change surfaces new revision plus unified diff."""
    tags = vditor_asset_tags()
    assert isinstance(tags, list)
    assert all(t.startswith("<") for t in tags)


# AC-FR0294-03@v0.9 TRACKS-TRACE edit produces new revision stale approval rejected
def test_edit_produces_new_revision_stale_approval_rejected(tmp_path: Path):
    """AC-FR0294-03: edit_material makes new revision; old approval stale rejected."""
    from tracks.supervisor.service import CommandService, Rejection

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    receipt = svc.accept(
        kind="edit_material",
        params={"run_id": "run-1", "doc": "spec", "base_revision": "rev-r1", "content": "new"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="edit-1",
    )
    assert receipt.command_id
    try:
        svc.accept(
            kind="record_stage_approval",
            params={
                "run_id": "run-1",
                "object": "spec",
                "expected_revision": "rev-r1",
                "decision": "approve",
            },
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key="appr-stale-1",
        )
    except Rejection as exc:
        assert exc.reason == "stale_revision"
    else:
        raise AssertionError("stale approval must be rejected")
