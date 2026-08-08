"""Validation failure -> evidence-carrying re-dispatch -> escalation
(FR-11, FR-12, NFR-03): AC-11a, AC-12a, AC-N03a.
"""
import os
import select
import signal
import subprocess
import sys
import time

from tests.e2e.helpers import assert_escalation_after_three_failures, dispatches


def start_to_draft(trac):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需要重派的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0


def test_failed_validation_redispatches_with_evidence(trac, event_log):
    start_to_draft(trac)
    # first validation fails, second passes (AC-11a)
    r = trac("run", simulate="validator:story.md=schema_fail|ok")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0]["payload"]["check"] == "schema"
    fail_seq = fails[0]["seq"]

    # story.md was not committed before the failure
    commits = [e for e in evs if e["type"] == "story.committed"]
    assert all(c["seq"] > fail_seq for c in commits)

    # the re-dispatch (after the failure) carries the failure evidence
    redispatch = [
        d for d in dispatches(evs, "DRAFT") if d["seq"] > fail_seq
    ]
    assert redispatch, "no re-dispatch after verdict.failed"
    evidence = redispatch[0]["payload"]["command"]["params"]["evidence"]
    assert evidence["check"] == "schema" and evidence["reason"]

    # NFR-03: the agent self-reported "done" yet the runtime still failed it
    outcome_before_fail = [
        e for e in evs
        if e["type"] == "outcome.received" and e["seq"] < fail_seq
    ]
    assert outcome_before_fail[-1]["payload"]["status"] == "done"


def test_three_failures_escalate(trac, event_log):
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    fails = [e for e in evs if e["type"] == "verdict.failed"]
    assert_escalation_after_three_failures(trac, evs, r, fails)


def test_escalation_shows_attempts_and_reason(trac, event_log):
    """Fix 3: escalation output includes attempts=3 and the failure reason."""
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert r.returncode == 0, r.stderr
    assert "awaiting=escalation" in r.stdout
    assert "attempts=3" in r.stdout
    assert "reason=[schema]" in r.stdout
    r = trac("status")
    assert r.returncode == 0
    assert "attempts=3" in r.stdout
    assert "reason=[schema]" in r.stdout


def test_retry_appends_event_and_clears_escalation(trac, event_log):
    """Fix 4: `trac retry` at awaiting=escalation appends human.retry and
    clears the escalation gate (active, not awaiting). It does NOT call
    run_loop or block in an agent call; the operator runs `trac run` next."""
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert "awaiting=escalation" in r.stdout
    evs_before = event_log()
    assert not [e for e in evs_before if e["type"] == "human.retry"]

    # retry: appends human.retry, clears escalation, does NOT dispatch
    r = trac("retry")
    assert r.returncode == 0, r.stderr
    assert "status=active" in r.stdout
    assert "awaiting=-" in r.stdout

    evs = event_log()
    retry_events = [e for e in evs if e["type"] == "human.retry"]
    assert len(retry_events) == 1
    # No new dispatch after retry (run_loop was NOT called)
    dispatches_after = [
        d for d in dispatches(evs) if d["seq"] > retry_events[0]["seq"]
    ]
    assert not dispatches_after, "retry must not dispatch"


def test_retry_then_run_uses_fresh_budget(trac, event_log):
    """Fix 4: after `trac retry`, an explicit `trac run` dispatches with a
    fresh 3-attempt budget (3 more failures before re-escalation)."""
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert "awaiting=escalation" in r.stdout

    r = trac("retry")
    assert r.returncode == 0, r.stderr
    assert "status=active" in r.stdout

    # explicit run: fresh budget, 3 more failures
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert r.returncode == 0, r.stderr
    assert "awaiting=escalation" in r.stdout
    assert "attempts=3" in r.stdout

    evs = event_log()
    retry_events = [e for e in evs if e["type"] == "human.retry"]
    assert len(retry_events) == 1
    fails_after = [
        e for e in evs
        if e["type"] == "verdict.failed" and e["seq"] > retry_events[0]["seq"]
    ]
    assert len(fails_after) == 3


def test_retry_rejected_when_not_escalated(trac, event_log):
    """`trac retry` is only legal at awaiting=escalation."""
    start_to_draft(trac)
    # run is active (DRAFT), not escalated
    r = trac("retry")
    assert r.returncode != 0
    assert "not awaiting escalation" in r.stderr
    assert not [e for e in event_log() if e["type"] == "human.retry"]


def test_retry_clear_evidence_at_escalation_drops_evidence(trac, event_log):
    """`trac retry --clear-evidence` at escalation records clear_evidence=true
    and drops the stale failure evidence; the next `trac run` dispatches DRAFT
    at attempt=1 with NO evidence in the command params."""
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert "awaiting=escalation" in r.stdout

    r = trac("retry", "--clear-evidence")
    assert r.returncode == 0, r.stderr
    assert "status=active" in r.stdout
    assert "awaiting=-" in r.stdout

    evs = event_log()
    retry_ev = [e for e in evs if e["type"] == "human.retry"][-1]
    assert retry_ev["payload"]["clear_evidence"] is True
    assert retry_ev["payload"]["actor"]

    # next run dispatches DRAFT with no evidence, attempt=1
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    after = [d for d in dispatches(evs, "DRAFT") if d["seq"] > retry_ev["seq"]]
    assert after, "no DRAFT dispatch after clear-evidence retry"
    params = after[0]["payload"]["command"]["params"]
    assert params["attempt"] == 1
    assert "evidence" not in params


def test_retry_clear_evidence_in_active_state_recovers(trac, host_repo, event_log):
    """Recovery scenario: ordinary retry cleared escalation, a DRAFT dispatch
    was issued (carrying stale evidence) and killed before producing a result,
    leaving the run active with doc_dispatched stuck True. `trac retry
    --clear-evidence` is accepted at active state, records clear_evidence=true,
    and the next run dispatches attempt=1 with no evidence."""
    start_to_draft(trac)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert "awaiting=escalation" in r.stdout
    # ordinary retry -> active
    assert trac("retry").returncode == 0
    assert "status=active" in trac("status").stdout

    # a run issues a DRAFT dispatch carrying the stale evidence; kill it before
    # it produces a result, simulating Maestro killing the dispatch.
    env = {k: v for k, v in os.environ.items()
           if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # wait for the DRAFT dispatch command.issued to land
        for _ in range(100):
            evs = event_log()
            last_issued = [e for e in evs if e["type"] == "command.issued"]
            if last_issued and last_issued[-1]["payload"]["command"]["params"].get(
                    "substate") == "DRAFT":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("killed run never issued a DRAFT dispatch")
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        proc.wait(timeout=10)
        raise

    # active state: ordinary retry rejected
    r = trac("retry")
    assert r.returncode != 0
    assert "not awaiting escalation" in r.stderr

    # clear-evidence retry accepted at active state
    r = trac("retry", "--clear-evidence")
    assert r.returncode == 0, r.stderr
    assert "status=active" in r.stdout
    assert "awaiting=-" in r.stdout
    retry_ev = [e for e in event_log() if e["type"] == "human.retry"][-1]
    assert retry_ev["payload"]["clear_evidence"] is True

    # next run dispatches DRAFT attempt=1 with no evidence
    r = trac("run")
    assert r.returncode == 0, r.stderr
    after = [d for d in dispatches(event_log(), "DRAFT")
             if d["seq"] > retry_ev["seq"]]
    assert after
    params = after[0]["payload"]["command"]["params"]
    assert params["attempt"] == 1
    assert "evidence" not in params


def test_retry_clear_evidence_respects_writer_lock(trac, host_repo, event_log):
    """`trac retry --clear-evidence` acquires the writer lock; if a `trac run`
    holds it, the retry is rejected with the holder PID and appends nothing
    (same lock discipline as every mutating command)."""
    start_to_draft(trac)
    # escalate, ordinary retry -> active state (the recovery scenario target)
    r = trac("run", simulate="validator:story.md=schema_fail")
    assert "awaiting=escalation" in r.stdout
    assert trac("retry").returncode == 0
    assert "status=active" in trac("status").stdout
    # spawn a hanging run that holds the lock
    env = {k: v for k, v in os.environ.items()
           if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        lock = host_repo / ".tracks" / "runtime" / "lock"
        for _ in range(100):
            if lock.exists():
                break
            time.sleep(0.05)
        else:
            raise AssertionError("hanging run never acquired lock")
        holder = lock.read_text().strip()
        before = len([e for e in event_log() if e["type"] == "human.retry"])
        # clear-evidence retry rejected while the run holds the lock
        r = trac("retry", "--clear-evidence")
        assert r.returncode == 1
        assert holder in r.stderr
        after = len([e for e in event_log() if e["type"] == "human.retry"])
        assert after == before  # nothing appended
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)


def test_progress_start_line_flushed_before_blocking_backend(trac, host_repo):
    """Fix 5: the dispatch start line is flushed to stderr before the backend
    call blocks. Uses a hang-token FakeBackend (time.sleep(600)) and reads
    stderr while the backend is still blocked."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="progress test").returncode == 0
    env = {k: v for k, v in os.environ.items()
           if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:TRIAGE=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # Wait up to 5s for stderr to become readable (start line flushed
        # before the backend's time.sleep(600) blocks).
        readable, _, _ = select.select([proc.stderr.fileno()], [], [], 5.0)
        assert readable, "start line not flushed within 5s"
        data = os.read(proc.stderr.fileno(), 4096).decode()
        assert "dispatch" in data
        assert "scribe" in data
        assert "TRIAGE" in data
        # Process should still be running (backend blocked in sleep)
        assert proc.poll() is None, "process exited early (backend did not block)"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_progress_failed_completion_line(trac, event_log):
    """Fix 5: a failed dispatch emits a completion line with failure_class."""
    start_to_draft(trac)
    r = trac("run", simulate="scribe:DRAFT=fail")
    assert r.returncode == 0, r.stderr
    stderr_lines = [line for line in r.stderr.splitlines() if line.strip()]
    fail_lines = [line for line in stderr_lines if "failed" in line]
    assert fail_lines, "expected at least one failure completion line on stderr"
    assert "[agent_failed]" in " ".join(fail_lines)


def test_progress_covers_recovered_dispatch(trac, host_repo, event_log):
    """Fix 5: a recovered pending dispatch also emits progress lines."""
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="recovery test").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    # Start a hanging DRAFT dispatch, kill it
    env = {k: v for k, v in os.environ.items()
           if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_AGENT_BACKEND"] = "fake"
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tracks.cli.main", "run"],
        cwd=host_repo, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    lock = host_repo / ".tracks" / "runtime" / "lock"
    for _ in range(100):
        if lock.exists():
            break
        time.sleep(0.05)
    else:
        proc.kill()
        raise AssertionError("hanging run never acquired lock")
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    # Recovery run: should emit dispatch progress for the recovered dispatch
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "dispatch" in r.stderr
    assert "scribe" in r.stderr
    assert "DRAFT" in r.stderr
