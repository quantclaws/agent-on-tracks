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
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; re-entry after full implementation (run 01M2QTJB).
def test_diff_between_revisions_visible(tmp_path: Path):
    """AC-FR0294-02: adjacent revisions show a visible diff; the revision change
    surfaces the new revision, and the vendored Vditor snapshot reconciles."""
    from tests._support.doc_review_seed import (
        doc_diff,
        read_doc,
        seed_doc_review_run,
        vditor_snapshot_mismatches,
    )
    from tracks.supervisor.service import CommandService

    # adjacent revisions: an edit moves the material to a new revision and the
    # diff between the two is visible through the §2b #16 handler
    home, _repo, reviewed = seed_doc_review_run(tmp_path, "run-1")
    svc = CommandService(home, object(), object())
    svc.accept(
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
        idempotency_key="diff-edit-1",
    )
    status, payload = read_doc(home, "run-1", "spec")
    assert status == 200, payload
    current = payload["revision"]
    assert current != reviewed, "the revision change must surface the new revision"

    status, payload = doc_diff(home, "run-1", "spec", reviewed, current)
    assert status == 200, payload
    assert payload["from"] == reviewed
    assert payload["to"] == current
    unified = payload["unified_diff"]
    assert "-spec body" in unified and "+revised spec body" in unified, (
        f"the adjacent-revision diff must show the change: {unified!r}"
    )

    # the review page hosts the Vditor assets from the origin site (§2b)
    tags = vditor_asset_tags()
    assert isinstance(tags, list)
    assert all(t.startswith("<") for t in tags)
    assert all("/static/vendor/vditor/" in tag for tag in tags)

    # the vendored snapshot reconciles against the manifest (test-plan §2.4/§7)
    assert vditor_snapshot_mismatches() == [], (
        "vendored Vditor assets deviate from manifest.json: "
        f"{vditor_snapshot_mismatches()}"
    )


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
