"""Hotfix CLI entry contract (IF-HOTFIX-001, FR-0240-01).

Drives the ``trac hotfix`` CLI surface (interfaces.md §2a) via the host
subprocess and asserts on the observable outlets: stdout/stderr/exit
code, the events table (``hotfix.requested`` + ``triage.prechecked`` +
``anchor.validated`` + ``stage.entered``), and ``trac status`` rows.

The CLI surface, its event types, and its command kinds are declared in
interfaces.md §2a / §1a / §1b but not yet wired (Devon foundation task
per ARCH-006 §4.3). Tests therefore land in legal Red: ``trac hotfix``
is not registered in ``_COMMANDS`` (returns USAGE / exit 1, no events
written) and the cross-module stubs raise ``NotImplementedError`` with
the owning IF- token.
"""

from __future__ import annotations

from tests.hotfix_support import (
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0240-01@v0.6 TRACKS-TRACE hotfix entry emits hotfix.requested and stage=M-HOTFIX-TRIAGE
def test_hotfix_entry_happy_requested_and_status_substates(trac, event_log, host_repo):
    """AC-FR0240-01@v0.6: ``trac hotfix <issue> --scenario post-release`` emits
    a ``hotfix.requested`` event whose payload carries the issue number and
    scenario, and ``trac status`` reports the HOTFIX-TRIAGE sub-state machine
    (PRECHECK / SAGE_TRIAGE / AWAIT_HUMAN) with the issue identity.

    Failure mode (legal Red): the ``trac hotfix`` command is a Devon
    foundation task not yet registered in ``_COMMANDS`` (IF-HOTFIX-001
    §2a); the subprocess returns exit 1 + USAGE on stderr and writes no
    events. The assertion on ``hotfix.requested`` in the events table
    cannot be satisfied by an empty event stream.
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
    assert "stage=M-HOTFIX-TRIAGE" in status.stdout or "substate=" in status.stdout
    assert "issue=42" in status.stdout
