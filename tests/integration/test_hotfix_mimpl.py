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

    # Isolation is topological (interfaces §4c): ``git log fix/{N}`` lists the
    # fix commits AND the active branch (main) does not carry them. We assert
    # on SHA reachability, not commit-message text — a deviant runtime that
    # lands the fix commit on main with any message must be killed.
    fix_log = git(host_repo, "log", "--oneline", "fix/42")
    assert fix_log.strip(), "fix/42 must carry RGR commits"
    # Trailers bind the hotfix issue identity exactly (interfaces §4a:
    # Tracks-Issue=<hotfix issue>).
    body = git(host_repo, "log", "--format=%B", "fix/42")
    assert "Tracks-Issue=42" in body
    # Every fix/42 commit must be absent from main: ``git cherry main fix/42``
    # prefixes each commit not in main with ``+`` (in main -> ``-``).
    cherry = git(host_repo, "cherry", "main", "fix/42")
    rows = [ln for ln in cherry.splitlines() if ln.strip()]
    assert rows, "fix/42 must carry commits not present on main (isolation)"
    non_plus = [ln for ln in rows if not ln.startswith("+")]
    assert not non_plus, (
        f"fix commits leaked onto main (git cherry shows in-main commits): {non_plus[:3]}"
    )


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
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"

    # Park the run INSIDE M-IMPL (active, non-terminal) before advancing
    # the branch (PRISM-V06-07): the IF-HOTFIX-008 stale reconcile requires
    # the release branch to advance WHILE the run is still resident in
    # M-IMPL — driving unbounded runs first would already reach the
    # boundary, leaving no active M-IMPL checkpoint to reconcile. Step one
    # dispatch at a time and stop at the first M-IMPL sighting.
    for _ in range(20):
        cont = trac("run", "--max-dispatches", "1")
        assert cont.returncode == 0, cont.stderr
        probe = trac("status")
        if "stage=M-IMPL" in probe.stdout and "terminal=" not in probe.stdout:
            break
    assert "stage=M-IMPL" in probe.stdout, (
        "run must be parked inside M-IMPL before the branch advance"
    )
    assert "terminal=" not in probe.stdout, "run must not be terminal yet"

    # Advance the ACTIVE RELEASE BRANCH (releases/v0.6) HEAD to force a
    # stale digest.  The current checkout is frozen onto fix/50 by
    # IF-HOTFIX-005 (complete_hotfix_entry = create_branch + checkout), so
    # ``git commit`` would land on fix/50, NOT releases/v0.6 — the stale
    # detection input (``git rev-parse {active_branch}``, IF-HOTFIX-008)
    # would never change.  We use git plumbing to advance releases/v0.6
    # without disturbing the current checkout.
    base = git(host_repo, "rev-parse", "releases/v0.6").strip()
    (host_repo / "marker.txt").write_text("stale trigger\n", encoding="utf-8")
    git(host_repo, "add", "marker.txt")
    tree = git(host_repo, "write-tree").strip()
    head = git(host_repo, "commit-tree", tree, "-p", base, "-m", "advance releases/v0.6").strip()
    git(host_repo, "update-ref", "refs/heads/releases/v0.6", head)
    # Restore the index to a clean state (undo the staging of marker.txt).
    git(host_repo, "reset", "-q")
    (host_repo / "marker.txt").unlink(missing_ok=True)
    # Sanity: releases/v0.6 HEAD really advanced.
    assert git(host_repo, "rev-parse", "releases/v0.6").strip() != base

    cont = trac("run")
    evs = event_log(run_id)
    stale = [
        e for e in evs
        if e["type"] == "baseline.frozen" and e["payload"].get("status") == "stale"
    ]
    assert stale
    assert stale[-1]["payload"].get("scenario_branch_head")

    status = trac("status")
    assert "needs_attention" in status.stdout.lower()


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
