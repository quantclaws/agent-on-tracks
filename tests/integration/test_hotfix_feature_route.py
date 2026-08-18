"""FEATURE_ROUTE exit + fail-closed (IF-HOTFIX-002 / IF-HOTFIX-005 / IF-HOTFIX-009,
FR-0241-04, NFR-0100-03).

Covers the FEATURE_ROUTE terminal (``backlog.recorded`` + no branch) and
the fail-closed invariant (no ``fix/{N}`` branch created on either
REJECTED or FEATURE_ROUTE).
"""

from __future__ import annotations

import subprocess

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


# AC-FR0241-04@v0.6 TRACKS-TRACE FEATURE_ROUTE exits without branch or dangling run
def test_feature_route_exits_without_branch_or_dangling_run(trac, host_repo, event_log):
    """AC-FR0241-04@v0.6: FEATURE_ROUTE -> ``backlog.recorded`` +
    ``run.completed(terminal_state=feature_route)``. No ``fix/{issue}``
    branch is created; no dangling worktree / active run is left behind.

    Failure mode (legal Red): ``trac hotfix`` (incl. its ``feature-route``
    sub-action) is not yet registered in ``_COMMANDS`` (IF-HOTFIX-001);
    the subprocess returns USAGE / exit 1 and writes no events.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "77", "--scenario", "post-release", simulate="sage:SAGE_TRIAGE=no_anchor")
    assert r.returncode == 0, r.stderr
    assert "awaiting=awaiting_human" in r.stdout

    r_fr = trac("hotfix", "feature-route")
    assert r_fr.returncode == 0, r_fr.stderr

    # No fix branch and no dangling worktree after FEATURE_ROUTE.
    out = subprocess.run(
        ["git", "branch", "--list", "fix/77"],
        cwd=host_repo, capture_output=True, text=True,
    ).stdout
    assert not out.strip()

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    assert "backlog.recorded" in types_seq
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert completed and completed[-1]["payload"]["terminal_state"] == "feature_route"


# AC-NFR0100-03@v0.6 TRACKS-TRACE fail closed: no branch and no auto feature route
def test_fail_closed_no_branch_and_no_auto_feature_route(trac, host_repo, event_log):
    """AC-NFR-0100-03@v0.6: REJECTED and FEATURE_ROUTE never create a
    ``fix/{issue}`` branch (fail-closed). When anchoring fails (NO_ANCHOR
    or redispatch exhaustion), the system parks at AWAIT_HUMAN and never
    auto-routes to FEATURE_ROUTE (FR-0240-05) - the operator must
    explicitly confirm the feature-route sub-action.

    Failure mode (legal Red): ``trac hotfix`` is not yet registered in
    ``_COMMANDS`` (IF-HOTFIX-001); the non-bug REJECTED case writes no
    ``triage.prechecked`` event (the contract requires it with
    ``reason=not_bug``) and the NO_ANCHOR case never reaches
    ``awaiting=awaiting_human`` in stdout. Both assertions fail on the
    missing-symbol state.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    # Case 1: non-bug issue #99 -> REJECTED with reason=not_bug (closed set).
    r99 = trac("hotfix", "99", "--scenario", "post-release")
    assert r99.returncode != 0, "non-bug REJECTED must exit non-zero"
    # The triage.prechecked REJECTED event must land with reason=not_bug.
    evs = event_log()
    rejected = [
        e for e in evs
        if e["type"] == "triage.prechecked"
        and e["payload"].get("status") == "rejected"
        and e["payload"].get("reason") == "not_bug"
    ]
    assert rejected, "REJECTED must record triage.prechecked reason=not_bug"
    # No fix/99 branch on REJECTED (fail-closed).
    out99 = subprocess.run(
        ["git", "branch", "--list", "fix/99"],
        cwd=host_repo, capture_output=True, text=True,
    ).stdout
    assert not out99.strip()

    # Case 2: no_anchor bug #77 -> parks at AWAIT_HUMAN, NEVER auto-feature.
    r77 = trac(
        "hotfix", "77", "--scenario", "post-release",
        simulate="sage:SAGE_TRIAGE=no_anchor",
    )
    assert r77.returncode == 0, "AWAIT_HUMAN is a non-error park (exit 0)"
    assert "awaiting=awaiting_human" in r77.stdout
    # No auto-feature-route: backlog.recorded must NOT appear without an
    # explicit `trac hotfix feature-route` sub-action.
    evs = event_log()
    backlog = [e for e in evs if e["type"] == "backlog.recorded"]
    assert not backlog, "NO_ANCHOR must not auto-route to FEATURE_ROUTE"
    # No fix/77 branch on AWAIT_HUMAN (fail-closed until anchor decided).
    out77 = subprocess.run(
        ["git", "branch", "--list", "fix/77"],
        cwd=host_repo, capture_output=True, text=True,
    ).stdout
    assert not out77.strip()
