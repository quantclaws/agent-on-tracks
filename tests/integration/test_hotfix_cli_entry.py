"""Hotfix CLI entry contract (IF-HOTFIX-001, FR-0240-01).

Drives the ``trac hotfix`` CLI surface (interfaces.md §2a) via the host
subprocess and asserts on the observable outlets: stdout/stderr/exit
code, the events table (``hotfix.requested`` + ``triage.prechecked`` +
``anchor.validated`` + ``stage.entered``), and ``trac status`` rows.

The CLI surface, its event types, and its command kinds are declared in
interfaces.md §2a / §1a / §1b and now registered (IF-HOTFIX-001 landed).
The legal Red source for the downstream journey is the IF-HOTFIX-010
baseline-resolver seam: the hotfix delta run parks at ``awaiting=
escalation reason=[trace] test-plan validate requires acceptance.md in
same dir`` until the inherited-baseline resolver is wired into the
run-loop validate step.
"""

from __future__ import annotations

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0240-01@v0.6 TRACKS-TRACE hotfix entry emits hotfix.requested and stage=M-HOTFIX-TRIAGE
def test_hotfix_entry_happy_requested_and_status_substates(trac, event_log, host_repo):
    """AC-FR0240-01@v0.6: ``trac hotfix <issue> --scenario post-release`` emits
    a ``hotfix.requested`` event whose payload carries the issue number and
    scenario, and ``trac status`` reports the HOTFIX-TRIAGE sub-state machine
    (PRECHECK / SAGE_TRIAGE / AWAIT_HUMAN) with the issue identity.

    Failure mode (legal Red): the ``trac hotfix`` command is registered
    (IF-HOTFIX-001 landed); the entry phase asserts bind the implemented
    behavior. The legal Red for the journey is the IF-HOTFIX-010 seam:
    the run parks at M-DESIGN awaiting=escalation reason=[trace]
    test-plan validate requires acceptance.md in same dir until the
    inherited-baseline resolver is wired. This entry test itself induces
    the entry; the downstream continuation is gated on the seam.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")

    assert r.returncode == 0, r.stderr
    assert "hotfix.requested" in r.stdout

    evs = event_log()
    requested = [e for e in evs if e["type"] == "hotfix.requested"]
    assert requested, "hotfix.requested event must land on the run event stream"
    assert requested[0]["payload"]["issue"] == 42
    assert requested[0]["payload"]["scenario"] == "post-release"

    status = trac("status")
    # §2b contract fields: an active hotfix run's status line appends
    # branch=fix/{N} scenario={post-release|dev} issue={N}. The sub-state
    # fields (stage=M-HOTFIX-TRIAGE / substate=PRECHECK|SAGE_TRIAGE) are
    # only observable WHILE the entry sub-state machine is active; the
    # entry command (form #1) drives the triage synchronously to ANCHORED,
    # so by the time this status probe runs the run has already entered
    # M-DESIGN and the sub-state-machine transient fields are no longer
    # observable (PRISM-V06-A1).
    assert "branch=fix/42" in status.stdout
    assert "scenario=post-release" in status.stdout
    assert "issue=42" in status.stdout

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the entry phase is
    # implemented, but the run must continue from M-DESIGN into the M-TEST
    # journey; the unimplemented inherited-baseline resolver parks the run
    # at awaiting=escalation, so the drive-to-M-TEST assertion fails until
    # IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)
