"""`trac retry` stdout contract regressions (test_trac_retry.py).

A successful retry event append must print the required activity line
``human.retry event appended; escalation gate cleared; attempt budget reset``
followed by the formatted run state. Failed/invalid retries (not awaiting
escalation) must NOT print the activity line and must exit non-zero.
"""

from tests.unit.m_test_support import make_dispatch_agent_payload
from tracks.cli.main import cmd_retry
from tracks.store import Store

_ACTIVITY_LINE = "human.retry event appended; escalation gate cleared; attempt budget reset"


def _seed_escalation(store, run_id, version="v0.1"):
    """M-TEST awaiting=escalation: 3x no_diff_justified Shield cycles."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-TEST"})
    # D-41 v3: the pre-WRITE R1 snapshot precedes the Shield WRITE dispatches.
    store.append(run_id, version, "test.baseline_captured", {"status": "passed"})
    for attempt in range(1, 4):
        store.append(
            run_id,
            version,
            "command.issued",
            {
                "command": make_dispatch_agent_payload(
                    attempt=attempt, review_round=1, command_id=f"shield-{attempt}"
                )
            },
        )
        store.append(
            run_id,
            version,
            "outcome.received",
            {"role": "shield", "status": "done", "artifact_ref": "tests", "self_report": "wrote"},
        )
        store.append(
            run_id,
            version,
            "verdict.failed",
            {
                "check": "no_diff_justified",
                "reason": "reviewer rejected no-diff explanation",
                "attempt": attempt,
            },
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
        "activity line must precede the state output"
    )


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


# -- B84 (#84): M-IMPL+rollback+lineage ordinary retry gate --------------------


def _seed_m_impl_lineage_rollback(store, run_id, version="v0.1"):
    """M-IMPL awaiting=rollback with last_failure.check=lineage."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    store.append(run_id, version, "verdict.failed",
                 {"check": "lineage", "reason": "lineage mismatch", "attempt": 1})


def _seed_m_impl_acgap_rollback(store, run_id, version="v0.1"):
    """M-IMPL awaiting=rollback with last_failure.check=ac_gap (DIAGNOSE)."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    # full_suite routes to DIAGNOSE
    store.append(run_id, version, "verdict.failed",
                 {"check": "full_suite", "attempt": 1})
    # ac_gap in DIAGNOSE sets awaiting=rollback
    store.append(run_id, version, "verdict.failed",
                 {"check": "ac_gap", "reason": "missing AC coverage", "attempt": 1})


def _seed_m_test_acgap_rollback(store, run_id, version="v0.1"):
    """M-TEST awaiting=rollback with last_failure.check=ac_gap."""
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": "M-TEST"})
    store.append(run_id, version, "verdict.failed",
                 {"check": "ac_gap", "reason": "missing AC coverage", "attempt": 1})


def test_retry_m_impl_rollback_lineage_succeeds(tmp_path, capsys):
    """B84 (#84): M-IMPL+rollback+lineage allows ordinary retry (preserves
    evidence, no --clear-evidence needed)."""
    store, repo, run_id = _setup(tmp_path)
    _seed_m_impl_lineage_rollback(store, run_id)
    state = store.state(run_id)
    assert state.awaiting == "rollback", state.awaiting
    assert state.stage == "M-IMPL", state.stage
    assert (state.last_failure or {}).get("check") == "lineage"

    rc = cmd_retry(repo, "--actor", "Tester")

    out, err = capsys.readouterr()
    retries = [e for e in store.events(run_id) if e.type == "human.retry"]
    store.close()
    assert rc == 0, f"expected success, got rc={rc} err={err!r}"
    assert _ACTIVITY_LINE in out, f"stdout={out!r}"
    assert len(retries) == 1, "human.retry must be appended"
    assert not retries[0].payload.get("clear_evidence", False), (
        "ordinary retry must not clear evidence"
    )


def test_retry_m_impl_rollback_acgap_rejected(tmp_path, capsys):
    """B84 (#84): M-IMPL+rollback with a non-lineage check (ac_gap) must
    still be rejected by ordinary retry."""
    store, repo, run_id = _setup(tmp_path)
    _seed_m_impl_acgap_rollback(store, run_id)
    state = store.state(run_id)
    assert state.awaiting == "rollback", state.awaiting
    assert state.stage == "M-IMPL", state.stage
    assert (state.last_failure or {}).get("check") == "ac_gap", (
        state.last_failure
    )

    rc = cmd_retry(repo, "--actor", "Tester")

    out, err = capsys.readouterr()
    retries = [e for e in store.events(run_id) if e.type == "human.retry"]
    store.close()
    assert rc == 1, f"expected rejection, got rc={rc}"
    assert _ACTIVITY_LINE not in out, f"stdout={out!r}"
    assert retries == [], "no human.retry may be appended"
    assert "run not awaiting escalation" in err, f"stderr={err!r}"


def test_retry_non_m_impl_rollback_rejected(tmp_path, capsys):
    """B84 (#84): non-M-IMPL rollback (M-TEST+ac_gap) must still be rejected
    by ordinary retry."""
    store, repo, run_id = _setup(tmp_path)
    _seed_m_test_acgap_rollback(store, run_id)
    state = store.state(run_id)
    assert state.awaiting == "rollback", state.awaiting
    assert state.stage == "M-TEST", state.stage

    rc = cmd_retry(repo, "--actor", "Tester")

    out, err = capsys.readouterr()
    retries = [e for e in store.events(run_id) if e.type == "human.retry"]
    store.close()
    assert rc == 1, f"expected rejection, got rc={rc}"
    assert _ACTIVITY_LINE not in out, f"stdout={out!r}"
    assert retries == [], "no human.retry may be appended"
    assert "run not awaiting escalation" in err, f"stderr={err!r}"
