"""`trac retry` stdout contract regressions (test_trac_retry.py).

A successful retry event append must print the required activity line
``human.retry event appended; escalation gate cleared; attempt budget reset``
followed by the formatted run state. Failed/invalid retries (not awaiting
escalation) must NOT print the activity line and must exit non-zero.
"""

from tests.m_test_support import make_dispatch_agent_payload
from tracks.cli.main import cmd_retry
from tracks.store import Store

_ACTIVITY_LINE = (
    "human.retry event appended; escalation gate cleared; "
    "attempt budget reset")


def _seed_escalation(store, run_id, version="v0.1"):
    """M-TEST awaiting=escalation: 3x no_diff_justified Shield cycles."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-TEST"})
    for attempt in range(1, 4):
        store.append(
            run_id, version, "command.issued",
            {"command": make_dispatch_agent_payload(
                attempt=attempt, review_round=1,
                command_id=f"shield-{attempt}")},
        )
        store.append(
            run_id, version, "outcome.received",
            {"role": "shield", "status": "done",
             "artifact_ref": "tests", "self_report": "wrote"},
        )
        store.append(
            run_id, version, "verdict.failed",
            {"check": "no_diff_justified",
             "reason": "reviewer rejected no-diff explanation",
             "attempt": attempt},
        )


def _setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(repo / ".tracks")
    return store, repo, "RUN"


def test_retry_success_prints_activity_line_then_state(tmp_path, capsys):
    """A successful retry appends human.retry and prints the required
    activity line plus the formatted run state."""
    store, repo, run_id = _setup(tmp_path)
    _seed_escalation(store, run_id)
    state = store.state(run_id)
    assert state.awaiting == "escalation", state.awaiting

    rc = cmd_retry(repo, "--actor", "Tester")

    out, err = capsys.readouterr()
    store.close()
    assert rc == 0
    assert err == ""
    assert _ACTIVITY_LINE in out, f"stdout={out!r}"
    assert f"run {run_id}:" in out, f"stdout={out!r}"
    assert out.index(_ACTIVITY_LINE) < out.index(f"run {run_id}:"), (
        "activity line must precede the state output")


def test_retry_not_escalation_prints_no_activity_line(tmp_path, capsys):
    """A retry at a non-escalation gate fails closed without printing the
    activity line and appends no human.retry event."""
    store, repo, run_id = _setup(tmp_path)
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})

    rc = cmd_retry(repo, "--actor", "Tester")

    out, err = capsys.readouterr()
    retries = [e for e in store.events(run_id) if e.type == "human.retry"]
    store.close()
    assert rc == 1
    assert _ACTIVITY_LINE not in out, f"stdout={out!r}"
    assert "run not awaiting escalation" in err
    assert retries == []


def test_retry_success_resets_attempt_budget(tmp_path, capsys):
    """human.retry clears the escalation gate and resets the attempt budget;
    decide() then re-dispatches from a fresh attempt=1."""
    from tracks.kernel import decide

    store, repo, run_id = _setup(tmp_path)
    _seed_escalation(store, run_id)
    before = store.state(run_id)
    assert before.current_attempt == 3, before.current_attempt

    rc = cmd_retry(repo, "--actor", "Tester")

    out, _ = capsys.readouterr()
    state = store.state(run_id)
    cmd = decide(state)
    store.close()
    assert rc == 0
    assert state.awaiting is None, state.awaiting
    assert state.status == "active", state.status
    assert state.current_attempt == 0, state.current_attempt
    assert cmd is not None
    assert cmd.params.get("attempt") == 1, cmd.params
