"""ANCHORED: fix branch + baseline inheritance + M-DESIGN entry
(IF-HOTFIX-005, FR-0241-01/02/03).

CLI-driven ``trac hotfix`` with asserts on the git/file public outlets
(interfaces §4c: ``git branch --list fix/{N}`` / ``git log --oneline`` /
hotfix project dir existence). ``trac hotfix`` (IF-HOTFIX-001) is
registered (Devon foundation landed); the legal Red for this round is the
IF-HOTFIX-010 baseline-resolver seam: the hotfix delta run parks at
``awaiting=escalation reason=[trace] test-plan validate requires
acceptance.md in same dir`` until the inherited baseline resolver is
wired into the run-loop validate step.
"""

from __future__ import annotations

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    git,
    seed_host_issues,
    seed_release_branch,
    seed_v05_approved_baseline,
)


# AC-FR0241-01@v0.6 TRACKS-TRACE ANCHORED creates isolated fix branch per scenario
def test_anchored_creates_isolated_fix_branch_per_scenario(trac, host_repo):
    """AC-FR0241-01@v0.6: ANCHORED creates ``fix/{issue}`` from ``main`` HEAD
    (post-release) or from the active release branch HEAD (dev). Runtime is
    the sole branch authority. Asserts on the git public outlet (interfaces
    §4c ``git branch --list fix/{N}`` / ``git log --oneline fix/{N} ^main``),
    not on unenumerated dict keys.

    Failure mode (legal Red): the hotfix entry CLI is registered
    (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010
    baseline-resolver seam. The run parks at ``awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same
    dir``, so the downstream fix-branch / baseline assertions cannot
    bind until the seam is wired.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    # Scenario A: post-release -> fix/42 from main HEAD.
    r_a = trac("hotfix", "42", "--scenario", "post-release")
    assert r_a.returncode == 0, r_a.stderr
    branch_a = git(host_repo, "branch", "--list", "fix/42")
    assert "fix/42" in branch_a, "ANCHORED must create fix/42 (git outlet)"
    log_a = git(host_repo, "log", "--oneline", "fix/42", "^main")
    assert log_a.strip() == ""  # base = main HEAD, no new commits yet

    # Scenario B: dev -> fix/50 from active release branch HEAD. The dev
    # precheck needs v0.6 approved (the release branch is releases/v0.6)
    # plus the release branch itself; seed both so PRECHECK passes.
    seed_v05_approved_baseline(host_repo, version="v0.6")
    seed_release_branch(host_repo, "releases/v0.6")
    r_b = trac("hotfix", "50", "--scenario", "dev")
    assert r_b.returncode == 0, r_b.stderr
    branch_b = git(host_repo, "branch", "--list", "fix/50")
    assert "fix/50" in branch_b, "ANCHORED must create fix/50 (git outlet)"
    log_b = git(host_repo, "log", "--oneline", "fix/50", "^releases/v0.6")
    assert log_b.strip() == ""  # base = active release HEAD

    # IF-HOTFIX-010 seam (legal Red): both ANCHORED runs must continue
    # from M-DESIGN into M-TEST; the unimplemented baseline-resolver parks
    # them at awaiting=escalation, so the drive-to-M-TEST assertion fails
    # until the seam is wired.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0241-02@v0.6 TRACKS-TRACE baseline inheritance: no requirement-stage artifacts
def test_baseline_inheritance_no_requirement_stage_artifacts(host_repo, trac):
    """AC-FR0241-02@v0.6: the inherited baseline (``baseline.inherited``)
    is a source-approval record - it never copies / re-approves the
    target-version trio. The hotfix project directory carries no M-STORY
    / M-SPEC / M-ACC / M-REQ-APPROVAL artifacts of its own.

    Failure mode (legal Red): the hotfix entry CLI is registered
    (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010
    baseline-resolver seam. The run parks at ``awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same
    dir``, so the baseline-inheritance event / hotfix project directory
    delta assertions cannot bind until the seam is wired.
    """
    from pathlib import Path

    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # The hotfix project dir + delta docs are materialized by the M-DESIGN
    # delta dispatch (IF-HOTFIX-005 stage.entered(M-DESIGN) -> delta docs).
    cont = trac("run")
    assert cont.returncode == 0, cont.stderr

    # The hotfix project directory exists and carries delta docs only.
    hotfix_dir = Path(host_repo / ".tracks" / "projects" / "v0.5-hotfix-42")
    assert hotfix_dir.exists()
    assert not (hotfix_dir / "story.md").exists()
    assert not (hotfix_dir / "spec.md").exists()
    assert not (hotfix_dir / "acceptance.md").exists()

    # IF-HOTFIX-010 seam (legal Red): the run must continue from M-DESIGN
    # into M-TEST; the baseline-resolver seam parks it at awaiting=escalation.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0241-03@v0.6 TRACKS-TRACE ANCHORED enters M-DESIGN and run continues
def test_anchored_enters_mdesign_and_run_continues(trac, host_repo, event_log):
    """AC-FR0241-03@v0.6: ANCHORED directly enters ``stage.entered(M-DESIGN)``;
    the run is continuable via ``trac run`` (FR-0242接续).

    Failure mode (legal Red): the hotfix entry CLI is registered
    (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010
    baseline-resolver seam. The run parks at ``awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same
    dir``, so ``stage.entered(M-DESIGN)`` cannot appear downstream
    and ``trac run`` cannot continue the run past the M-DESIGN trace
    validation gate.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    assert "stage=M-DESIGN" in r.stdout

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    stages = [e for e in evs if e["type"] == "stage.entered"]
    assert any(e["payload"]["stage"] == "M-DESIGN" for e in stages)

    # The run is continuable (trac run does not reject the hotfix run).
    cont = trac("run")
    assert cont.returncode == 0, cont.stderr
    assert "stage=M-DESIGN" not in cont.stdout  # progressed past entry
