"""Human anchor / feature-route sub-actions (IF-HOTFIX-001, FR-0240-06).

Covers the two AWAIT_HUMAN sub-actions in interfaces.md §2a #4 / #5:
``trac hotfix anchor AC-FRXXXX-YY@v .`` and ``trac hotfix feature-route``.
Both feed ``submit_human_result`` -> ``human.anchor(mode=.)``.

The CLI surface and ``human.anchor`` event type are Devon foundation
tasks now landed (IF-HOTFIX-001 registered); tests land in legal Red via
the IF-HOTFIX-010 baseline-resolver seam (the hotfix delta run parks at
M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires
acceptance.md in same dir until the resolver is wired).
"""

from __future__ import annotations

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


# AC-FR0240-06@v0.6 TRACKS-TRACE human anchor manual + feature route sub-actions
def test_human_anchor_manual_and_feature_route(trac, host_repo):
    """AC-FR0240-06@v0.6: at AWAIT_HUMAN the operator can either

    * ``trac hotfix anchor <AC@v .>`` -> ``human.anchor(mode=manual)`` ->
      ``validate_anchor`` -> on pass, ANCHORED (SM-01.8);
    * ``trac hotfix feature-route`` -> ``human.anchor(mode=feature_route)``
      -> ``backlog.recorded(decision=feature_route)`` ->
      ``run.completed(terminal_state=feature_route)`` (SM-01.9/.11).

    Both sub-actions are explicit Human decisions; the system never auto-
    routes from AWAIT_HUMAN to FEATURE_ROUTE (NFR-0100-03).

    Failure mode (legal Red): the hotfix entry CLI is registered
    (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010
    baseline-resolver seam. The run parks at ``awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same
    dir`` before the ``human.anchor`` / ``backlog.recorded`` events
    can bind; the downstream sub-action assertions are legal Red until
    the inherited-baseline resolver is wired.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
    # First bring the run to AWAIT_HUMAN (fake no_anchor).
    r = trac("hotfix", "77", "--scenario", "post-release", simulate="sage:SAGE_TRIAGE=no_anchor")
    assert r.returncode == 0, r.stderr
    assert "awaiting=awaiting_human" in r.stdout

    # Sub-action 1: feature-route first (never creates fix/77; the run
    # terminates feature_route with no dangling branch, §2a #5).
    r_fr = trac("hotfix", "feature-route")
    assert r_fr.returncode == 0, r_fr.stderr
    assert "feature route recorded" in r_fr.stdout
    assert "backlog" in r_fr.stdout

    # Sub-action 2: manual anchor on a fresh run with the same issue.
    # fix/77 was never created by sub-action 1, so PRECHECK P-5
    # (fix_branch_exists) does not reject this second entry (§1c).
    r2 = trac("hotfix", "77", "--scenario", "post-release", simulate="sage:SAGE_TRIAGE=no_anchor")
    assert r2.returncode == 0, r2.stderr
    assert "awaiting=awaiting_human" in r2.stdout
    r_anchor = trac("hotfix", "anchor", "AC-FR0030-01@v0.5")
    assert r_anchor.returncode == 0, r_anchor.stderr
    assert "anchor recorded: AC-FR0030-01@v0.5" in r_anchor.stdout
