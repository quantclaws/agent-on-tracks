"""Integration: real M-MILESTONE close chain (FR-0276, FR-0284, IF-MILESTONE-001).

Drives the full release chain (shared walker -> M-RELEASE/AWAITING_RELEASE ->
``trac release`` -> ``trac run`` executes M-PUBLISH then M-MILESTONE) over the
loopback CI stand-in and a real bare remote, then asserts the realized closing
tail:

- the §1i trace carries the preview's ``artifact_digest``/``evidence_digests``/
  ``release_tag`` (plan-derived) and per-operation ``{kind,target,key,status}``
  records, with a byte-verifiable ``trace_digest``;
- temp refs planted under ``refs/trac/tmp`` are really deleted with a measured
  ``remaining=0``;
- the read-only archive manifest exists under
  ``.tracks/runtime/blobs/release/<candidate>/manifest.json`` (0444 in a 0555
  parent) and binds the trace digest;
- the fake known-issue registrations (#100/#101) land audited
  ``issue.closed state=skipped reason=not_authoritative`` events and never a
  claimed close; FAKE creation artifacts are not written to the map;
- an interrupted tail (trace-closed/sealed/refs.cleaned/run.completed removed)
  is continued by the next ``trac run`` sub-step by sub-step, without
  re-emitting any completed sub-step.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

from tests.e2e.helpers import init_bare_remote, walk_to_awaiting_release

pytestmark = pytest.mark.integration


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _latest(events, event_type, **match):
    found = [
        e
        for e in events
        if e["type"] == event_type
        and all(e["payload"].get(k) == v for k, v in match.items())
    ]
    assert found, f"{event_type} {match} must appear"
    return found[-1]


def _trace_of(events):
    return _latest(events, "milestone.trace_closed")["payload"]


def _drop_tail_events(host_repo, run_id, types):
    """Simulated interruption: drop the given durable events and rebuild the
    projections so the CLI sees the run active again (the runtime's own
    rebuild path, NFR-04)."""
    from tracks import paths
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    try:
        placeholders = ",".join("?" for _ in types)
        store.conn.execute(
            f"DELETE FROM events WHERE run_id=? AND type IN ({placeholders})",
            (run_id, *types),
        )
        store.conn.commit()
        store.rebuild_projections()
    finally:
        store.close()


def _walk_and_release(trac, host_repo):
    init_bare_remote(host_repo, "bare.git")
    run_id = walk_to_awaiting_release(trac)
    assert run_id
    assert trac("release", "--action", "release").returncode == 0
    return run_id


# AC-FR0276-01@v0.8 TRACKS-TRACE real trace aggregation seal and refs clean
def test_real_trace_seal_refs_and_close(host_repo, trac, event_log, ci_echo_standin):
    run_id = _walk_and_release(trac, host_repo)
    # Plant a temp ref before the publish+closing run: the cleanup must remove
    # it for real and measure an empty namespace afterwards.
    subprocess.run(
        ["git", "update-ref", "refs/trac/tmp/keep", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    assert trac("run").returncode == 0, "publish + M-MILESTONE must complete"
    events = event_log(run_id)

    trace = _trace_of(events)
    preview = _latest(
        events, "release.previewed", candidate_sha=trace["candidate_sha"]
    )["payload"]
    assert trace["artifact_digest"] == preview["artifact_digest"]
    assert trace["evidence_digests"] == preview["evidence_digests"]
    assert trace["evidence_digests"], "the preview evidence digests must aggregate"
    plan_steps = preview["operation_plan"]["steps"]
    tag_targets = [s.split(":", 1)[1] for s in plan_steps if s.startswith("tag:")]
    # merge-only plans declare no tag operation -> the explicit empty string.
    assert trace["release_tag"] == (tag_targets[0] if tag_targets else "")
    body = {k: v for k, v in trace.items() if k != "trace_digest"}
    expected = "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()
    assert trace["trace_digest"] == expected, "trace_digest must be byte-verifiable"

    ops = trace["operation_digests"]
    assert ops, "operation_digests must carry one record per operation"
    assert all(
        set(op) == {"kind", "target", "idempotency_key", "status"} for op in ops
    ), f"operation_digests shape drift: {ops!r}"
    executed = {
        e["payload"]["idempotency_key"]: e["payload"]
        for e in events
        if e["type"] == "publish.executed"
    }
    for op in ops:
        assert op["status"] == executed[op["idempotency_key"]]["status"]
    assert any(op["kind"] == "merge" and op["target"] == "main" for op in ops)
    for op in ops:
        assert op["idempotency_key"].startswith("sha256:")

    cleaned = _latest(events, "refs.cleaned")["payload"]
    assert cleaned["remaining"] == 0
    assert "refs/trac/tmp/keep" in cleaned["removed"]
    listed = subprocess.run(
        ["git", "for-each-ref", "refs/trac/tmp"],
        cwd=host_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listed.strip() == ""

    sealed = _latest(events, "milestone.sealed")["payload"]
    assert sealed["readonly"] is True
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
    assert manifest["event_count"] > 0 and manifest["event_summary"]
    assert manifest["blob_refs"], "the manifest must list the key evidence blobs"
    mbody = {k: v for k, v in manifest.items() if k != "manifest_digest"}
    assert manifest["manifest_digest"] == "sha256:" + hashlib.sha256(
        _canonical(mbody)
    ).hexdigest()
    assert sealed["manifest_digest"] == manifest["manifest_digest"]
    assert Path(sealed["seal_blob"]).name == "manifest.json"

    # FR-0284: known-issue registrations are non-authoritative identities; the
    # closer audits them as skipped and never claims a close.
    known_numbers = {
        e["payload"]["issue_number"]
        for e in events
        if e["type"] == "known_issue.registered"
    }
    assert {100, 101} <= known_numbers
    skipped = [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "skipped"
    ]
    assert known_numbers <= {s["issue_number"] for s in skipped}
    for entry in skipped:
        assert entry["reason"] == "not_authoritative"
        assert entry["trace_digest"] == trace["trace_digest"]
    assert not [
        e
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "closed"
    ], "the fake walk has no authoritative mapping to close"

    issue_map = json.loads(
        (host_repo / ".tracks" / "runtime" / "issue-map.json").read_text(
            encoding="utf-8"
        )
    )
    for entry in issue_map.values():
        assert entry["authoritative"] is False
        assert entry["source"] == "known_issue"
        assert entry["api_verified"] is False
    assert not any(
        str(entry.get("issue_number", "")).startswith("FAKE-")
        for entry in issue_map.values()
    ), "FAKE creation artifacts must not be persisted into the map"

    completed = _latest(events, "run.completed")["payload"]
    assert completed["terminal_state"] == "released"
    assert completed["release_tag"] == trace["release_tag"]
    assert completed["trace_digest"] == trace["trace_digest"]
    assert "terminal=released" in trac("status").stdout


# AC-FR0284-01@v0.8 TRACKS-TRACE authoritative mapping really closed and read back
def test_authoritative_issue_closed_via_api(
    host_repo, trac, event_log, ci_echo_standin
):
    run_id = _walk_and_release(trac, host_repo)
    # An authoritative mapping (API-verified creation accepted on a previous
    # run) is the only closable identity; the walk itself only has fake
    # known-issue registrations, so the closer's known-issue fallback still
    # audits #100/#101 as skipped alongside the real close.
    (host_repo / ".tracks" / "runtime" / "issue-map.json").write_text(
        json.dumps(
            {
                "FR-TEST": {
                    "issue_number": 12,
                    "repo": "acme/host",
                    "url": "https://github.com/acme/host/issues/12",
                    "baseline_digest": "sha256:" + "1" * 64,
                    "api_verified": True,
                    "authoritative": True,
                    "source": "issue_create",
                }
            }
        ),
        encoding="utf-8",
    )
    assert trac("run").returncode == 0
    events = event_log(run_id)
    trace = _trace_of(events)
    closed = [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "closed"
    ]
    assert len(closed) == 1, f"exactly the authoritative issue closes: {closed!r}"
    entry = closed[0]
    assert entry["issue_number"] == 12
    assert entry["api_verified"] is True
    assert entry["remote_state"] == "closed"
    assert entry["trace_digest"] == trace["trace_digest"]
    assert trace["candidate_sha"] in entry["comment"]
    assert trace["preview_digest"] in entry["comment"]
    assert trace["release_tag"] in entry["comment"]
    paths = ci_echo_standin.requests
    assert any(path.endswith("/issues/12/comments") for path in paths)
    assert any(path.endswith("/issues/12") for path in paths)
    skipped = [
        e["payload"]
        for e in events
        if e["type"] == "issue.closed" and e["payload"].get("state") == "skipped"
    ]
    assert {100, 101} <= {s["issue_number"] for s in skipped}


# AC-FR0276-02@v0.8 TRACKS-TRACE interrupted closing tail resumes item by item
def test_interrupted_tail_resumes_without_duplicate_events(
    host_repo, trac, event_log, ci_echo_standin
):
    run_id = _walk_and_release(trac, host_repo)
    assert trac("run").returncode == 0
    before = event_log(run_id)
    assert any(e["type"] == "run.completed" for e in before)
    trace = _trace_of(before)
    issue_numbers = {
        e["payload"]["issue_number"]
        for e in before
        if e["type"] == "issue.closed"
    }
    done_before = [
        e
        for e in before
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]

    # Simulated interruption: the trace-close and sealed/refs.cleaned/
    # run.completed sub-steps never landed (or were lost); the issue/project
    # sub-steps stay durable. This is the historical permanent-CLOSING stall
    # (a dropped trace_closed replayed zero events); the itemized walk must
    # rebuild exactly the missing steps.
    _drop_tail_events(
        host_repo,
        run_id,
        ("milestone.trace_closed", "milestone.sealed", "refs.cleaned", "run.completed"),
    )
    interrupted = event_log(run_id)
    assert not [e for e in interrupted if e["type"] == "run.completed"]

    assert trac("run").returncode == 0, "the closing tail must resume"
    after = event_log(run_id)

    def _count(events, event_type, predicate=lambda _p: True):
        return sum(
            1
            for e in events
            if e["type"] == event_type and predicate(e["payload"])
        )

    # Completed sub-steps are not re-emitted; missing ones land exactly once.
    assert _count(after, "milestone.trace_closed") == 1
    assert _count(after, "issue.closed") == len(issue_numbers)
    assert _count(after, "milestone.closed") == 1
    assert _count(after, "milestone.sealed") == 1
    assert _count(after, "refs.cleaned") == 1
    assert _count(after, "run.completed") == 1
    # No duplicate publish side effects on the tail resume.
    done_after = [
        e
        for e in after
        if e["type"] == "publish.executed" and e["payload"].get("status") == "done"
    ]
    assert len(done_after) == len(done_before)

    resumed_trace = _trace_of(after)
    assert resumed_trace["trace_digest"] == trace["trace_digest"]
    sealed = _latest(after, "milestone.sealed")["payload"]
    manifest_path = (
        host_repo
        / ".tracks"
        / "runtime"
        / "blobs"
        / "release"
        / trace["candidate_sha"]
        / "manifest.json"
    )
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o444
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert sealed["manifest_digest"] == manifest["manifest_digest"]
    assert "terminal=released" in trac("status").stdout
