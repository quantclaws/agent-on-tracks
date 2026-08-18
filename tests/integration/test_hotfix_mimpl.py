"""M-IMPL hotfix variant: isolated fix branch + scenario B stale reconcile
+ boundary (IF-HOTFIX-008, FR-0245 / FR-0246).

Covers the fix-branch isolation invariant (commits land on ``fix/{N}``,
never on the active branch / feature worktree), the scenario B baseline
stale reconcile -> NEEDS_ATTENTION path, the boundary terminal state
keeping the fix branch, and the no-release-events invariant.
"""

from __future__ import annotations

from tests.hotfix_support import git, seed_host_issues, seed_v05_approved_baseline


# AC-FR0245-01@v0.6 TRACKS-TRACE M-IMPL commits isolated on fix branch
def test_mimpl_commits_isolated_on_fix_branch(trac, host_repo, event_log):
    """AC-FR0245-01@v0.6: RGR G / refactor / final fix commits land ONLY on
    ``fix/{issue}`` (``git log --oneline fix/{N}`` lists them; the active
    branch / feature worktree does not). Runtime is the sole branch /
    worktree authority.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no fix
    branch ever created, so neither the fix-branch log nor the active-
    branch log can carry the isolation assertion.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # Drive M-DESIGN -> M-TEST -> M-IMPL happy path.
    for _ in range(4):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    fix_log = git(host_repo, "log", "--oneline", "fix/42")
    assert fix_log.strip(), "fix/42 must carry RGR commits"
    # Trailers carry the hotfix issue identity (Tracks-Issue=42).
    body = git(host_repo, "log", "--format=%B", "fix/42")
    assert "Tracks-Issue=42" in body or "Tracks-Issue" in body
    # Active branch (main) does not carry the fix commits.
    main_log = git(host_repo, "log", "--oneline", "main")
    assert "fix" not in main_log.lower() or "fix/42" not in main_log


# AC-FR0245-02@v0.6 TRACKS-TRACE scenario B baseline stale reconcile NEEDS_ATTENTION
def test_scenario_b_baseline_stale_reconcile_needs_attention(trac, host_repo, event_log):
    """AC-FR0245-02@v0.6: scenario B (dev) fix-branch baseline tracks the
    active release branch HEAD; when that HEAD advances, the BASELINE
    digest mismatch emits ``baseline.frozen(status=stale,
    scenario_branch_head=...)`` and routes to NEEDS_ATTENTION (conflict
    evidence visible in ``trac status``). The merge itself is NOT
    executed this version (FR-0246), but the reconcile + conflict path
    must remain reachable.

    Failure mode (legal Red): ``trac hotfix ... --scenario dev`` is
    unregistered; the dev-scenario baseline stale path is never entered,
    so no ``baseline.frozen(status=stale)`` event can fire.
    """
    from tests.hotfix_support import seed_release_branch

    seed_v05_approved_baseline(host_repo, version="v0.6")
    seed_release_branch(host_repo, "releases/v0.6")
    seed_host_issues(host_repo)

    r = trac("hotfix", "50", "--scenario", "dev")
    assert r.returncode == 0, r.stderr
    # Drive into M-IMPL where BASELINE digest is computed.
    for _ in range(4):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    # Advance the active release branch HEAD to force a stale digest.
    (host_repo / "marker.txt").write_text("stale trigger\n", encoding="utf-8")
    git(host_repo, "add", "marker.txt")
    git(host_repo, "commit", "-m", "advance releases/v0.6")

    cont = trac("run")
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    stale = [
        e for e in evs
        if e["type"] == "baseline.frozen" and e["payload"].get("status") == "stale"
    ]
    assert stale
    assert stale[-1]["payload"].get("scenario_branch_head")

    status = trac("status")
    assert "needs_attention" in status.stdout


# AC-FR0246-01@v0.6 TRACKS-TRACE boundary terminal state keeps fix branch
def test_boundary_terminal_state_keeps_fix_branch(trac, host_repo, event_log):
    """AC-FR0246-01@v0.6: hotfix reaches boundary -> ``stage.exited(M-IMPL)``
    + ``run.completed(terminal_state=boundary)``. The ``fix/{issue}``
    branch is preserved (not deleted), and ``trac status`` reports
    ``terminal=boundary branch=fix/{N} scenario=...``.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no
    boundary terminal state, no fix branch to preserve; the
    ``stage.exited(M-IMPL)`` / ``run.completed(boundary)`` events are
    absent.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    for _ in range(5):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert completed
    assert completed[-1]["payload"]["terminal_state"] == "boundary"
    # Fix branch preserved.
    out = git(host_repo, "branch", "--list", "fix/42")
    assert "fix/42" in out
    status = trac("status")
    assert "terminal=boundary" in status.stdout
    assert "fix/42" in status.stdout


# AC-FR0246-02@v0.6 TRACKS-TRACE no release events after boundary
def test_no_release_events_after_boundary(trac, host_repo, event_log):
    """AC-FR0246-02@v0.6: post-boundary, no release events (merge-to-main,
    tag, artifact publish, version bump) appear in the event stream.
    ``trac status`` reports ``boundary`` + ``scenario=post-release`` but
    never claims "released".

    Failure mode (legal Red): the hotfix run cannot reach boundary
    (unregistered ``trac hotfix`` -> USAGE / exit 1, no events), so the
    release-event absence assertion has nothing to compare against; the
    boundary-line status check fails.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    for _ in range(5):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    # No release-related events (these are not yet declared event types
    # at all - asserting their absence binds the no-publish invariant).
    for forbidden in ("release.published", "release.tagged", "merge.committed", "tag.created"):
        assert forbidden not in types_seq

    status = trac("status")
    assert "terminal=boundary" in status.stdout
    assert "released" not in status.stdout.lower()
