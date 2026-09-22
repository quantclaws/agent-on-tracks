"""Doc review web (IF-DOCREV-001, IF-WEBGATE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.doc_review_seed import (
    material_edits,
    read_doc,
    seed_doc_review_run,
)
from tracks.server.pages import vditor_asset_tags
from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-FR0294-01@v0.9 TRACKS-TRACE revision visible and pending bound
def test_revision_visible_and_pending_bound(tmp_path: Path):
    """AC-FR0294-01: read_doc shows content revision; pending binds reviewed revision."""
    from tracks.server.projections import project_run_detail

    home, _repo, current = seed_doc_review_run(tmp_path, "run-1")
    svc = CommandService(home, object(), object())

    detail = project_run_detail(home, "run-1")
    assert detail["run_id"] == "run-1"
    assert "stage" in detail
    assert "pause" in detail

    status, payload = read_doc(home, "run-1", "spec")
    assert status == 200, payload
    assert payload["revision"] == current
    assert payload["content"] == "spec body\n"

    # the pending decision binds the revision the reviewer actually read
    receipt = svc.accept(
        kind="record_stage_approval",
        params={
            "run_id": "run-1",
            "object": "spec",
            "expected_revision": payload["revision"],
            "decision": "approve",
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="appr-bound-1",
    )
    assert receipt.command_id
    assert receipt.status == "accepted"
    assert svc.status(receipt.command_id)["command_id"] == receipt.command_id


# AC-FR0294-02@v0.9 TRACKS-TRACE diff between revisions visible
def test_diff_between_revisions_visible(tmp_path: Path):
    """AC-FR0294-02: revision change surfaces new revision plus unified diff."""
    tags = vditor_asset_tags()
    assert isinstance(tags, list)
    assert all(t.startswith("<") for t in tags)


# AC-FR0294-03@v0.9 TRACKS-TRACE edit produces new revision stale approval rejected
def test_edit_produces_new_revision_stale_approval_rejected(tmp_path: Path):
    """AC-FR0294-03: edit_material makes new revision; old approval stale rejected."""
    from tracks.supervisor.service import Rejection

    home, _repo, reviewed = seed_doc_review_run(tmp_path, "run-1")
    svc = CommandService(home, object(), object())

    receipt = svc.accept(
        kind="edit_material",
        params={
            "run_id": "run-1",
            "doc": "spec",
            "base_revision": reviewed,
            "content": "revised spec body\n",
        },
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="edit-1",
    )
    assert receipt.command_id
    assert receipt.status == "accepted"

    # the edit produced a new revision: the body changed and the digest moved
    status, payload = read_doc(home, "run-1", "spec")
    assert status == 200, payload
    assert payload["content"] == "revised spec body\n"
    assert payload["revision"] != reviewed
    assert reviewed in [entry["revision"] for entry in payload["history"]]
    assert payload["revision"] in [entry["revision"] for entry in payload["history"]]
    edits = material_edits(home, "run-1", "spec")
    assert [(e["from_revision"], e["to_revision"]) for e in edits] == [
        (reviewed, payload["revision"])
    ]

    # an approval on the superseded revision is rejected, audited, and persists nothing
    try:
        svc.accept(
            kind="record_stage_approval",
            params={
                "run_id": "run-1",
                "object": "spec",
                "expected_revision": reviewed,
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
        raise AssertionError("an approval on the superseded revision must be rejected")
    from tracks.supervisor.db import ServiceDB

    reasons = [
        (event["payload"] or {}).get("reason")
        for event in ServiceDB(home).read_events()
        if event["type"] == "command.rejected"
    ]
    assert "stale_revision" in reasons
    assert ServiceDB(home).find_by_idempotency("appr-stale-1") is None
