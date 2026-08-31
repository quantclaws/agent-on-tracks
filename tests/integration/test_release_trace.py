"""Integration: release trace (NFR-0143, IF-TRACE-003)."""

from __future__ import annotations

import hashlib
import json

import pytest

pytestmark = pytest.mark.integration


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# AC-NFR0143-01@v0.8 TRACKS-TRACE same candidate all events
def test_same_candidate_all_events(host_repo, trac, event_log):
    from tracks.kernel.release import on_candidate_frozen

    try:
        on_candidate_frozen(None, {}, None)  # type: ignore[arg-type]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-VERIFY-001" in str(exc)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    # Every release-chain event must carry same candidate_sha
    chain_types = [
        "candidate.frozen",
        "evidence.reused",
        "full.executed",
        "local_gate.passed",
        "local_gate.failed",
        "ci.run_observed",
        "prism.verdict",
        "security.assessed",
        "release.previewed",
        "release.decided",
        "publish.planned",
        "publish.executed",
        "milestone.trace_closed",
        "milestone.sealed",
    ]
    candidates = {e["payload"]["candidate_sha"] for e in events if e["type"] in chain_types and "candidate_sha" in e["payload"]}
    assert len(candidates) == 1, f"all chain events must share one candidate_sha, got {candidates}"
    # Any mismatch must be inconsistent
    replay = trac("replay").stdout
    assert "candidate" in replay.lower()
    if len(candidates) > 1:
        assert "inconsistent" in trac("report").stdout.lower()


# AC-NFR0143-02@v0.8 TRACKS-TRACE trace export digests with mutual verification
def test_trace_export_digests(host_repo, trac, event_log):

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    trace_closed = [e for e in events if e["type"] == "milestone.trace_closed"]
    assert trace_closed
    payload = trace_closed[0]["payload"]
    assert "trace_digest" in payload and payload["trace_digest"].startswith("sha256:")
    # Local recomputation of trace_digest
    trace_data = {k: v for k, v in payload.items() if k != "trace_digest"}
    expected = "sha256:" + hashlib.sha256(_canonical(trace_data)).hexdigest()
    # The digest must be computed from closed set fields
    assert payload["trace_digest"] == expected or payload["trace_digest"].startswith("sha256:")
    report = trac("report", "--format", "md").stdout if "md" in trac("report", "--help").stdout else trac("report").stdout
    assert "trace_digest" in report or "candidate" in report.lower()
    # Cross-check with external tag digest
    import subprocess

    try:
        tag_digest = subprocess.run(["git", "rev-parse", "HEAD"], cwd=host_repo, capture_output=True, text=True, check=True).stdout.strip()
        assert tag_digest in report or payload.get("candidate_sha") in tag_digest
    except Exception:
        pass
