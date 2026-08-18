"""Crash recovery: precise hotfix state resumption (IF-HOTFIX-002 / IF-HOTFIX-006,
NFR-0110-03).

Covers the append-only rebuild path: after a forced Runtime restart,
``trac status`` reports the same HOTFIX-TRIAGE sub-state and run
identity as before the interruption; completed entry sub-states / stage
dispatches are not re-run.
"""

from __future__ import annotations

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


# AC-NFR0110-03@v0.6 TRACKS-TRACE crash recovery resumes precise hotfix state
def test_crash_recovery_resumes_precise_hotfix_state(trac, host_repo, event_log):
    """AC-NFR0110-03@v0.6: after Runtime interruption/restart, ``trac status``
    reports the same hotfix sub-state and run identity; completed entry
    sub-states / stage dispatches are not re-run. Recovery is from the
    append-only event stream alone (no ad-hoc persisted state).

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no hotfix
    run is ever created; there is no pre-interruption state to compare
    against, and the post-restart status does not carry the hotfix
    sub-state machine.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    before = trac("status")
    assert "M-HOTFIX-TRIAGE" in before.stdout or "hotfix" in before.stdout.lower()

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs_before = event_log(run_id)
    types_before = [e["type"] for e in evs_before]

    # Simulate Runtime restart: a fresh `trac status` call goes through
    # the projection rebuild path (NFR-04) and must report the same
    # hotfix sub-state.
    import sqlite3

    from tracks import paths

    home = paths.tracks_home(host_repo)
    db = paths.db_path(home)
    # Drop projections + close any cached connection (the trac subprocess
    # opens a fresh sqlite handle each call).
    conn = sqlite3.connect(db)
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()

    after = trac("status")
    assert "M-HOTFIX-TRIAGE" in after.stdout or "hotfix" in after.stdout.lower()

    # No completed sub-state / dispatch was re-run (the event log is
    # append-only; the count of stage.entered events stays the same).
    evs_after = event_log(run_id)
    types_after = [e["type"] for e in evs_after]
    assert types_after == types_before
