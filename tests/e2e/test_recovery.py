"""Single-writer lock + interrupt/recovery (FR-27, FR-29):
AC-27a/b, AC-29a/b, AC-29d (reconcile is also covered at integration level).

The blocking agent (`simulate=...=hang`) holds the lock mid-dispatch; a second
process must fail fast with the holder PID and write nothing (D-07).
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

TRAC = Path(sys.executable).parent / "trac"


def start_to_draft(trac):
    assert trac("init").returncode == 0
    assert trac("start", "v0.1", stdin="需要恢复测试的需求").returncode == 0
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0


def spawn_hanging_run(host_repo):
    env = {k: v for k, v in os.environ.items() if k not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE")}
    env["TRAC_FAKE_SIMULATE"] = "scribe:DRAFT=hang"
    proc = subprocess.Popen(
        [str(TRAC), "run"],
        cwd=host_repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    lock = host_repo / ".tracks" / "runtime" / "lock"
    for _ in range(100):  # wait until the run actually holds the lock
        if lock.exists():
            return proc, lock
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("first trac run never acquired the lock")


def event_count(host_repo):
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()


def test_lock_rejects_second_writer(host_repo, trac, event_log):
    start_to_draft(trac)
    proc, lock = spawn_hanging_run(host_repo)
    try:
        holder = lock.read_text().strip()
        assert holder == str(proc.pid)

        # AC-27a: second run fails fast, stderr carries the holder PID
        r = trac("run")
        assert r.returncode == 1 and holder in r.stderr

        # AC-27b: human command equally rejected, no event appended
        before = event_count(host_repo)
        r = trac("triage", "go")
        assert r.returncode == 1 and holder in r.stderr
        assert event_count(host_repo) == before
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)


def test_crash_recovery_reissues_without_consuming_attempt(host_repo, trac, event_log):
    """AC-29b: kill while command.issued is on disk and the result is not;
    the next run re-executes the dangling dispatch, attempt count unchanged."""
    start_to_draft(trac)
    proc, lock = spawn_hanging_run(host_repo)
    # The lock is acquired before decide()/issue(); wait for the crash point
    # stated by this test rather than killing in the pre-dispatch window.
    for _ in range(100):
        issued = [e for e in event_log() if e["type"] == "command.issued"]
        if issued and issued[-1]["payload"]["command"]["params"].get("substate") == "DRAFT":
            break
        time.sleep(0.05)
    else:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        raise AssertionError("hanging run never issued the scribe:DRAFT dispatch")
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)
    assert lock.exists()  # stale lock left behind by the killed process

    evs = event_log()
    dangling = [e for e in evs if e["type"] == "command.issued"][-1]
    assert dangling["payload"]["command"]["params"]["substate"] == "DRAFT"
    assert not any(
        e["type"] == "outcome.received" and e["command_id"] == dangling["command_id"] for e in evs
    )

    # recovery run: stale lock reclaimed, dangling dispatch re-executed
    r = trac("run")
    assert r.returncode == 0, r.stderr
    evs = event_log()
    outcomes = [
        e
        for e in evs
        if e["type"] == "outcome.received" and e["command_id"] == dangling["command_id"]
    ]
    assert outcomes, "dangling dispatch was not re-executed on recovery"
    assert not any(e["type"] == "verdict.failed" for e in evs)  # no attempt burned
    assert "awaiting=review" in r.stdout  # forward progress to the human gate


def test_resume_after_human_gate_does_not_redispatch(host_repo, trac, event_log):
    """AC-29a: stopping at awaiting_human and resuming later never repeats
    already-completed dispatches."""
    start_to_draft(trac)
    assert trac("run").returncode == 0  # to HUMAN_REVIEW (M-STORY)
    assert not (host_repo / ".tracks" / "runtime" / "lock").exists()  # lock released

    def dispatch_keys(evs):
        out = []
        for e in evs:
            if e["type"] != "command.issued":
                continue
            c = e["payload"]["command"]
            if c["kind"] == "dispatch_agent":
                out.append((c["params"]["role"], c["params"]["substate"]))
        return out

    before = dispatch_keys(event_log())
    assert trac("review", "no-comment").returncode == 0
    assert trac("run").returncode == 0  # M-SPEC to HUMAN_REVIEW
    after = dispatch_keys(event_log())
    assert after[: len(before)] == before  # history untouched
    # no duplicate of an already-completed (role, substate) pair
    assert len([k for k in after if k == ("scribe", "DRAFT")]) == 1
