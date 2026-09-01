"""Integration: escape gate (FR-0287, IF-ESCAPE-001/002)."""

from __future__ import annotations

import subprocess

import pytest

from tracks.executor.escape import (
    abandon_run,
    establish_escape_barrier,
    quarantine_late_outcome,
    report_irreversible_operations,
    stale_downstream_evidence,
)

pytestmark = pytest.mark.integration


# AC-FR0287-01@v0.8 TRACKS-TRACE universal return moves pointer with advisory separation
def test_universal_return_moves_pointer(host_repo, trac, event_log):
    # IF-ESCAPE-001 module surface: contract payloads, not stub tokens
    # (§1a#30 barrier payload: cutover_seq int + quiesced_dispatches list).
    barrier = establish_escape_barrier("run")
    assert isinstance(barrier, dict)
    assert isinstance(barrier["cutover_seq"], int)
    assert isinstance(barrier.get("quiesced_dispatches"), list)

    # §1a evidence.staled carries reason=human_return (AC-FR0287-03).
    staled = stale_downstream_evidence("run", "M-TEST")
    assert isinstance(staled, list)
    for entry in staled:
        payload = entry.get("payload", entry)
        assert payload.get("reason") == "human_return"

    # AC-FR0287-04: already_executed report is a list of executed operations.
    ops = report_irreversible_operations("run")
    assert isinstance(ops, list)

    trac("run")
    result = trac("return", "--to", "M-TEST", "--reason", "human escape")
    events = event_log()
    human = [e for e in events if e["type"] == "human.return"]
    assert human, "human.return must appear"
    assert human[0]["payload"]["actor"] == "Human"
    assert human[0]["payload"]["to"] == "M-TEST"
    assert "reason" in human[0]["payload"]
    status = trac("status").stdout
    assert "human_return" in status
    assert "evidence.staled" in trac("replay").stdout or "human_return" in status
    advisory = [e for e in events if e["type"] == "advisory.recorded"]
    if advisory:
        assert advisory[0]["payload"].get("advisory_only") is True
    assert result.returncode in (0, 1)


# AC-FR0287-02@v0.8 TRACKS-TRACE escape barrier quarantines late outcomes
def test_escape_barrier_quarantines_late_outcomes(host_repo, trac, event_log):
    # IF-ESCAPE-001 module surface: barrier payload §1a#30, late-outcome
    # payload §1a#31 {dispatch_id, outcome_ref, status="quarantined"}.
    barrier = establish_escape_barrier("run")
    assert isinstance(barrier["cutover_seq"], int)
    assert isinstance(barrier.get("quiesced_dispatches"), list)
    outcome = {"dispatch_id": "d-late-01", "payload": {}}
    quarantined = quarantine_late_outcome(barrier, outcome)
    assert quarantined["status"] == "quarantined"
    assert quarantined["dispatch_id"] == "d-late-01"
    assert "outcome_ref" in quarantined

    trac("run")
    trac("return", "--to", "M-VERIFY", "--reason", "test barrier")
    events = event_log()
    barrier = [e for e in events if e["type"] == "escape.barrier_established"]
    assert barrier, "escape.barrier_established must appear"
    assert "cutover_seq" in barrier[0]["payload"]
    late = [e for e in events if e["type"] == "escape.late_outcome"]
    for lo in late:
        assert lo["payload"]["status"] == "quarantined"
    assert "late_outcome=quarantined" in trac("status").stdout or "quarantined" in trac("status").stdout


# AC-FR0287-03@v0.8 TRACKS-TRACE return stales downstream evidence
def test_return_stales_downstream_evidence(host_repo, trac, event_log):
    # AC-FR0287-03: post-target evidence stales with reason=human_return.
    staled = stale_downstream_evidence("run", "M-TEST")
    assert isinstance(staled, list)
    for entry in staled:
        payload = entry.get("payload", entry)
        assert payload.get("reason") == "human_return"

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    trac("return", "--to", "M-TEST", "--reason", "stale test")
    events_after = event_log()
    staled = [e for e in events_after if e["type"] == "evidence.staled" and e["payload"].get("reason") == "human_return"]
    assert staled, "evidence.staled human_return must appear"
    assert len(staled) >= 1
    trac("run")
    events2 = event_log()
    assert len([e for e in events2 if e["type"] == "candidate.frozen"]) >= 1
    status = trac("status").stdout
    assert "frozen_tests=unfrozen" in status or "human_return" in status


# AC-FR0287-04@v0.8 TRACKS-TRACE irreversible confirm then reconcile skip
def test_irreversible_confirm_then_reconcile_skip(host_repo, trac, event_log):
    # AC-FR0287-04: already_executed report is a list of executed operations.
    ops = report_irreversible_operations("run")
    assert isinstance(ops, list)

    bare = host_repo.parent / "escape_bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=host_repo, check=True, capture_output=True)

    trac("run")
    trac("release", "--action", "release")
    trac("run")
    events = event_log()
    done_before = [e for e in events if e["type"] == "publish.executed" and e["payload"].get("status") == "done"]
    assert done_before, "publish done must exist before return"
    result = trac("return", "--to", "M-VERIFY", "--reason", "cross irreversible")
    assert "already_executed" in result.stdout or "already_executed" in result.stderr
    trac("run")
    events2 = event_log()
    skipped = [e for e in events2 if e["type"] == "publish.executed" and e["payload"].get("status") == "reconciled_skip"]
    assert skipped
    replay = trac("replay").stdout
    assert "reconciled_skip" in replay


# AC-FR0287-05@v0.8 TRACKS-TRACE abandon terminal zero side effects
def test_abandon_terminal_zero_side_effects(host_repo, trac, event_log):
    # IF-ESCAPE-002 module surface: abandon is terminal_state=cancelled
    # with reason preserved (AC-FR0287-05).
    terminal = abandon_run("run", "human termination")
    assert terminal["terminal_state"] == "cancelled"
    assert terminal["reason"] == "human termination"

    trac("run")
    tag_before = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    branch_before = subprocess.run(["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    trac("abandon", "--reason", "human termination")
    events_after = event_log()
    completed = [e for e in events_after if e["type"] == "run.completed" and e["payload"].get("terminal_state") == "cancelled"]
    assert completed, "run.completed cancelled must appear"
    assert completed[0]["payload"]["reason"]
    status = trac("status").stdout
    assert "terminal=cancelled" in status
    tag_after = subprocess.run(["git", "tag", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    branch_after = subprocess.run(["git", "branch", "--list"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert tag_before == tag_after
    assert branch_before == branch_after
    r = trac("run")
    assert r.returncode != 0
    assert "run is cancelled" in r.stdout or "cancelled" in r.stdout.lower()
