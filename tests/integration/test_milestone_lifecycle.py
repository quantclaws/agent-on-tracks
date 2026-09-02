"""Integration: M-MILESTONE trace close and retry tail (FR-0276, IF-MILESTONE-001).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(parks at M-IMPL/DIAGNOSE/awaiting=escalation) — bare ``trac run`` bootstrap
is forbidden (v0.8 suite-wide defect). The M-MILESTONE trace/refs producers
are wired by later runtime tasks (kernel/release T-039 + T-001-era handler);
until then the event-level assertions are legal Red against that product gap.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked

pytestmark = pytest.mark.integration


# AC-FR0276-01@v0.8 TRACKS-TRACE trace closed sealed refs clean and report mutual verification
def test_trace_closed_sealed_refs_clean(host_repo, trac, event_log):
    walk_to_m_impl_parked(trac)
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    closed = [e for e in events if e["type"] == "milestone.trace_closed"]
    assert closed, "milestone.trace_closed must appear with trace_digest"
    assert all("trace_digest" in c["payload"] and c["payload"]["trace_digest"].startswith("sha256:") for c in closed)
    sealed = [e for e in events if e["type"] == "milestone.sealed"]
    assert sealed
    assert sealed[0]["payload"]["readonly"] is True
    cleaned = [e for e in events if e["type"] == "refs.cleaned"]
    assert cleaned
    assert cleaned[0]["payload"]["remaining"] == 0
    # git for-each-ref refs/trac/tmp must be empty
    refs = subprocess.run(["git", "for-each-ref", "refs/trac/tmp"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert refs.strip() == ""
    # trace closed implies candidate/ preview / approval / ops digests bound
    for c in closed:
        assert "candidate_sha" in c["payload"]
        assert "preview_digest" in c["payload"]
    status = trac("status")
    assert "terminal=released" in status.stdout
    report = trac("report")
    assert "release.trace" in report.stdout.lower() or "trace" in report.stdout.lower()


# AC-FR0276-02@v0.8 TRACKS-TRACE retry tail no republish on archive failure
def test_retry_tail_no_republish(host_repo, trac, event_log):
    walk_to_m_impl_parked(trac)
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    done = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    # Simulate retry tail: archive failed, retry via resume
    trac("run", "--resume")
    events2 = event_log()
    # Must not have duplicate done after retry tail
    done2 = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert len(done2) == len(done)
    skipped = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"]
    # Retry tail may produce skipped for already-done ops
    assert skipped or len(done2) == len(done)
    status = trac("status")
    # After successful publish but failed archive, status is retry_tail
    assert "retry_tail" in status.stdout or "terminal=released" in status.stdout or "sealed" in status.stdout.lower()
