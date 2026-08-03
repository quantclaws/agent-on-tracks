"""Shared E2E helpers (kept out of individual test modules to avoid R0801 duplication)."""
import re


def walk_to_await_human(trac, stdin="构建一个事件溯源运行时"):
    """init → start → triage go → three review rounds → AWAIT_HUMAN (FR-0180)."""
    assert trac("init").returncode == 0
    r = trac("start", "v0.1", stdin=stdin)
    assert r.returncode == 0, r.stderr
    run_id = re.search(r"run (\S+) started", r.stdout).group(1)
    assert trac("run").returncode == 0
    assert trac("triage", "go").returncode == 0
    for _ in ("M-STORY", "M-SPEC", "M-ACC"):
        r = trac("run")
        assert r.returncode == 0, r.stderr
        assert "awaiting=review" in r.stdout
        assert trac("review", "no-comment").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "awaiting=approval" in r.stdout  # FR-0180: hard human gate
    return run_id


def walk_to_design_complete(trac, stdin="构建一个事件溯源运行时"):
    """approval → M-DESIGN (Archer drafts the trio, Prism passes) →
    run.completed(terminal_state="boundary") — no human gate in M-DESIGN
    (BS-05), so a single `trac run` after approval reaches the terminal state
    (Decision A: M-IMPL not implemented in v0.3)."""
    run_id = walk_to_await_human(trac, stdin=stdin)
    assert trac("approve", "--actor", "Aaron").returncode == 0
    r = trac("run")
    assert r.returncode == 0, r.stderr
    assert "status=completed" in r.stdout
    assert "awaiting=-" in r.stdout
    return run_id


def dispatches(evs, substate=None):
    """Filter event-log rows to `dispatch_agent` command.issued events,
    optionally narrowed to a substate."""
    out = []
    for e in evs:
        if e["type"] != "command.issued":
            continue
        cmd = e["payload"]["command"]
        if cmd["kind"] != "dispatch_agent":
            continue
        if substate and cmd["params"].get("substate") != substate:
            continue
        out.append(e)
    return out


def assert_escalation_after_three_failures(trac, evs, run_result, fails, substate="DRAFT"):
    """Shared escalation assertion (NFR-06a de-dup): exactly 3 failed attempts,
    no dispatch after the last failure, awaiting=escalation in run + status."""
    assert len(fails) == 3  # exactly 3 attempts, then stop
    assert len(dispatches(evs, substate)) == 3
    last_fail_seq = fails[-1]["seq"]
    assert not [d for d in dispatches(evs) if d["seq"] > last_fail_seq]
    assert "awaiting=escalation" in run_result.stdout
    r = trac("status")
    assert r.returncode == 0 and "escalation" in r.stdout
