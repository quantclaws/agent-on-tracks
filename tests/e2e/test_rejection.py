"""NO-GO / PARK teardown (FR-09): AC-09a, AC-09b."""
import sqlite3
import subprocess

import pytest


def git_out(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


@pytest.mark.parametrize("decision", ["no-go", "park"])
def test_rejection_tears_down_branch(host_repo, trac, event_log, decision):
    assert trac("init").returncode == 0
    r = trac("start", "v0.1", stdin="一个被拒绝的需求")
    assert r.returncode == 0, r.stderr
    assert trac("run").returncode == 0
    assert trac("triage", decision).returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr

    # branch gone, back on main (AC-09a)
    assert git_out(host_repo, "branch", "--list", "releases/v0.1") == ""
    assert git_out(host_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"

    evs = event_log()
    terminal = decision.replace("-", "_")
    assert any(
        e["type"] == "run.completed"
        and e["payload"]["terminal_state"] == terminal
        for e in evs
    )
    assert any(e["type"] == "backlog.recorded" for e in evs)

    # backlog projection row recorded the rejection
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version, decision FROM backlog").fetchall()
    conn.close()
    assert ("v0.1", terminal) in rows

    r = trac("status")
    assert r.returncode == 0 and "completed" in r.stdout
