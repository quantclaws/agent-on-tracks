"""E2E happy path: awaiting -> manual anchor -> continue journey.

End-to-end user journey per test-plan §11 awaiting journey: a bug issue
(#77) for which Sage reports NO_ANCHOR parks at AWAIT_HUMAN; the operator
manually anchors an AC; the journey then proceeds identically to
scenario A from ANCHORED onward (FR-0240-06 / AC-NFR0110-01).

Fake backend ``TRAC_FAKE_SIMULATE=sage:SAGE_TRIAGE=no_anchor`` injects
the NO_ANCHOR branch; the kernel / executor are NOT mocked.
"""

from __future__ import annotations

from tests._support.hotfix_support import (
    git,
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0240-06@v0.6 TRACKS-TRACE await journey manual anchor proceeds to ANCHORED
# AC-NFR0110-01@v0.6 TRACKS-TRACE await journey suspended run observable and recoverable
def test_hotfix_await_journey_manual_anchor(trac, host_repo, event_log):
    """Awaiting -> manual anchor -> continue happy path (test-plan §11).
    Seed host repo with bug issue #77 (no anchor); drive to AWAIT_HUMAN,
    manually anchor, then proceed to boundary. Asserts cover the AC
    markers above.

    Failure mode (legal Red): the hotfix entry CLI is registered
    (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010
    baseline-resolver seam. The downstream journey parks at M-DESIGN
    awaiting=escalation reason=[trace] test-plan validate requires
    acceptance.md in same dir, so the boundary terminal and manual-
    anchor acceptance assertions cannot bind until the inherited-baseline
    resolver is wired.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
    pre_branch = git(host_repo, "branch", "--show-current").strip()

    # 1. Hotfix entry with Sage NO_ANCHOR -> parks at AWAIT_HUMAN.
    r = trac(
        "hotfix", "77", "--scenario", "post-release",
        simulate="sage:SAGE_TRIAGE=no_anchor",
    )
    assert r.returncode == 0, r.stderr
    assert "awaiting=awaiting_human" in r.stdout
    assert "origin=hotfix-triage" in r.stdout
    assert "issue=77" in r.stdout
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"

    # 2. trac status reports the suspended state.
    status = trac("status")
    assert "awaiting=awaiting_human" in status.stdout
    assert "issue=77" in status.stdout

    # 3. Manual anchor: operator picks AC-FR0030-01@v0.5.
    r_anchor = trac("hotfix", "anchor", "AC-FR0030-01@v0.5")
    assert r_anchor.returncode == 0, r_anchor.stderr
    assert "anchor recorded: AC-FR0030-01@v0.5" in r_anchor.stdout

    # 4. Journey proceeds identically to scenario A from ANCHORED onward.
    for _ in range(6):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    status = trac("status")
    assert "terminal=boundary" in status.stdout
    assert "fix/77" in status.stdout

    # 5. No FEATURE_ROUTE backlog event on the manual-anchor path.
    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    backlog = [e for e in evs if e["type"] == "backlog.recorded"]
    assert not backlog  # manual anchor -> no feature_route backlog
    assert "human.anchor" in types_seq
    # anchor.validated(source=human) lands and the journey continues.
    anchors = [e for e in evs if e["type"] == "anchor.validated"]
    assert anchors
    assert anchors[-1]["payload"]["source"] == "human"

    # 6. Working tree restored to the pre-journey branch.
    post_branch = git(host_repo, "branch", "--show-current").strip()
    assert post_branch == pre_branch
