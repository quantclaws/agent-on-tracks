"""Human anchor / feature-route sub-actions (IF-HOTFIX-001, FR-0240-06).

Covers the two AWAIT_HUMAN sub-actions in interfaces.md §2a #4 / #5:
``trac hotfix anchor AC-FRXXXX-YY@v ...`` and ``trac hotfix feature-route``.
Both feed ``submit_human_result`` -> ``human.anchor(mode=...)``.

The CLI surface and ``human.anchor`` event type are Devon foundation
tasks not yet wired; tests land in legal Red via USAGE / exit 1 and
absent event types.
"""

from __future__ import annotations

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


# AC-FR0240-06@v0.6 TRACKS-TRACE human anchor manual + feature route sub-actions
def test_human_anchor_manual_and_feature_route(trac, host_repo):
    """AC-FR0240-06@v0.6: at AWAIT_HUMAN the operator can either

    * ``trac hotfix anchor <AC@v ...>`` -> ``human.anchor(mode=manual)`` ->
      ``validate_anchor`` -> on pass, ANCHORED (SM-01.8);
    * ``trac hotfix feature-route`` -> ``human.anchor(mode=feature_route)``
      -> ``backlog.recorded(decision=feature_route)`` ->
      ``run.completed(terminal_state=feature_route)`` (SM-01.9/.11).

    Both sub-actions are explicit Human decisions; the system never auto-
    routes from AWAIT_HUMAN to FEATURE_ROUTE (NFR-0100-03).

    Failure mode (legal Red): the ``trac hotfix`` surface (incl. its
    ``anchor`` / ``feature-route`` sub-actions) is not yet registered in
    ``_COMMANDS`` (IF-HOTFIX-001); both invocations return USAGE / exit 1
    and write no ``human.anchor`` / ``backlog.recorded`` events.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
    # First bring the run to AWAIT_HUMAN (fake no_anchor).
    r = trac("hotfix", "77", "--scenario", "post-release", simulate="sage:SAGE_TRIAGE=no_anchor")
    assert r.returncode == 0, r.stderr
    assert "awaiting=awaiting_human" in r.stdout

    # Sub-action 1: manual anchor.
    r_anchor = trac("hotfix", "anchor", "AC-FR0030-01@v0.5")
    assert r_anchor.returncode == 0, r_anchor.stderr
    assert "anchor recorded: AC-FR0030-01@v0.5" in r_anchor.stdout

    # Sub-action 2: feature-route (re-run from a fresh AWAIT_HUMAN).
    r2 = trac("hotfix", "77", "--scenario", "post-release", simulate="sage:SAGE_TRIAGE=no_anchor")
    assert r2.returncode == 0, r2.stderr
    assert "awaiting=awaiting_human" in r2.stdout
    r_fr = trac("hotfix", "feature-route")
    assert r_fr.returncode == 0, r_fr.stderr
    assert "feature route recorded" in r_fr.stdout
    assert "backlog" in r_fr.stdout
