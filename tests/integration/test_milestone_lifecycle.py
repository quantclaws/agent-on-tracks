"""Integration: M-MILESTONE trace close and retry tail (FR-0276, IF-MILESTONE-001).

Drives the real release chain (shared walker -> M-RELEASE/AWAITING_RELEASE ->
``trac release`` -> ``trac run`` executes M-PUBLISH then M-MILESTONE) over the
loopback CI stand-in and a bare remote: the closing tail really closes and
seals, so these anchors assert the realized effects (read-only manifest,
measured refs cleanup, byte-verifiable trace) rather than the pre-wiring
stubs.
"""

from __future__ import annotations

import json
import stat
import subprocess

import pytest

from tests.e2e.helpers import (
    assert_temp_refs_empty,
    generate_report_md,
    walk_and_release,
)

pytestmark = pytest.mark.integration


def _assert_trace_binding(events, trace):
    """trace closed implies candidate / preview / evidence / ops digests bound."""
    preview = [
        e["payload"]
        for e in events
        if e["type"] == "release.previewed"
        and e["payload"]["candidate_sha"] == trace["candidate_sha"]
    ][-1]
    assert trace["preview_digest"] == preview["preview_digest"]
    assert trace["artifact_digest"] == preview["artifact_digest"]
    assert trace["evidence_digests"] == preview["evidence_digests"]
    assert trace["evidence_digests"], "evidence digests must aggregate"
    assert trace["operation_digests"], "operation records must be traced"
    assert all(
        op["idempotency_key"].startswith("sha256:")
        for op in trace["operation_digests"]
    )


# AC-FR0276-01@v0.8 TRACKS-TRACE trace closed sealed refs clean and report mutual verification
def test_trace_closed_sealed_refs_clean(host_repo, trac, event_log, ci_echo_standin):
    run_id = walk_and_release(trac, host_repo)
    # A planted temp ref must be deleted for real, not merely listed.
    subprocess.run(
        ["git", "update-ref", "refs/trac/tmp/keep", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    assert trac("run").returncode == 0, "publish + M-MILESTONE must complete"
    events = event_log(run_id)

    closed = [e for e in events if e["type"] == "milestone.trace_closed"]
    assert closed, "milestone.trace_closed must appear with trace_digest"
    assert all(
        "trace_digest" in c["payload"]
        and c["payload"]["trace_digest"].startswith("sha256:")
        for c in closed
    )
    trace = closed[-1]["payload"]
    _assert_trace_binding(events, trace)

    sealed = [e for e in events if e["type"] == "milestone.sealed"]
    assert sealed
    sealed_payload = sealed[-1]["payload"]
    assert sealed_payload["readonly"] is True
    manifest_path = (
        host_repo
        / ".tracks"
        / "runtime"
        / "blobs"
        / "release"
        / trace["candidate_sha"]
        / "manifest.json"
    )
    assert manifest_path.is_file(), "the read-only seal manifest must exist"
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(manifest_path.parent.stat().st_mode) == 0o555
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["candidate_sha"] == trace["candidate_sha"]
    assert manifest["trace_digest"] == trace["trace_digest"]
    assert sealed_payload["manifest_digest"] == manifest["manifest_digest"]

    cleaned = [e for e in events if e["type"] == "refs.cleaned"]
    assert cleaned
    assert cleaned[-1]["payload"]["remaining"] == 0
    assert "refs/trac/tmp/keep" in cleaned[-1]["payload"]["removed"]
    # git for-each-ref refs/trac/tmp must be empty
    assert_temp_refs_empty(host_repo)

    status = trac("status")
    assert "terminal=released" in status.stdout
    report = generate_report_md(trac, host_repo)
    assert "Release trace" in report
    assert trace["trace_digest"] in report


# AC-FR0276-02@v0.8 TRACKS-TRACE retry tail no republish on archive failure
def test_retry_tail_no_republish(host_repo, trac, event_log, ci_echo_standin):
    run_id = walk_and_release(trac, host_repo)
    candidate = [
        e for e in event_log(run_id) if e["type"] == "release.previewed"
    ][-1]["payload"]["candidate_sha"]

    # Archive (read-only seal) failure: block the content-addressed seal dir.
    blocker = host_repo / ".tracks" / "runtime" / "blobs" / "release" / candidate
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("seal blocked\n", encoding="utf-8")

    trac("run")
    events = event_log(run_id)
    seal_attention = [
        e
        for e in events
        if e["type"] == "attention.required"
        and e["payload"].get("area") == "milestone_seal"
    ]
    assert seal_attention, "the archive failure must be audited, not swallowed"
    assert not [e for e in events if e["type"] == "run.completed"]
    assert not [
        e
        for e in events
        if e["type"] == "milestone.sealed"
        and e["payload"].get("readonly") is True
    ], "a failed archive must never claim sealed"
    done_before = [
        e
        for e in events
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert done_before, "publish succeeded before the archive failure"
    trace_before = [
        e["payload"]["trace_digest"]
        for e in events
        if e["type"] == "milestone.trace_closed"
    ]

    # Repair the archive store: the retry tail must only finish the tail.
    blocker.unlink()
    assert trac("run").returncode == 0, "the closing tail must resume"
    events2 = event_log(run_id)
    done_after = [
        e
        for e in events2
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert len(done_after) == len(done_before), "retry must not republish"
    skipped = [
        e
        for e in events2
        if e["type"] == "publish.executed"
        and e["payload"].get("status") == "reconciled_skip"
    ]
    # The retry tail may reconcile already-done operations as skipped.
    assert skipped or len(done_after) == len(done_before)
    completed = [e for e in events2 if e["type"] == "run.completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["terminal_state"] == "released"
    assert [
        e["payload"]["trace_digest"]
        for e in events2
        if e["type"] == "milestone.trace_closed"
    ] == trace_before
    assert completed[0]["payload"]["trace_digest"] == trace_before[-1]
    assert [
        e for e in events2 if e["type"] == "milestone.sealed"
    ], "the resumed tail must seal after the repair"
    assert "terminal=released" in trac("status").stdout
