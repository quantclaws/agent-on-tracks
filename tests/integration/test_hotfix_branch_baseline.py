"""ANCHORED: fix branch + baseline inheritance + M-DESIGN entry
(IF-HOTFIX-005, FR-0241-01/02/03).

Direct call into the frozen ``tracks.executor.hotfix.complete_hotfix_entry``
stub plus the git/file outlets asserted via the host subprocess.

Stubs raise ``NotImplementedError("IF-HOTFIX-005: ...")`` - legal Red.
"""

from __future__ import annotations

from tests.hotfix_support import (
    git,
    seed_host_issues,
    seed_release_branch,
    seed_v05_approved_baseline,
)
from tracks.executor.hotfix import complete_hotfix_entry, locate_target_version  # noqa: F401


# AC-FR0241-01@v0.6 TRACKS-TRACE ANCHORED creates isolated fix branch per scenario
def test_anchored_creates_isolated_fix_branch_per_scenario(host_repo):
    """AC-FR0241-01@v0.6: ANCHORED creates ``fix/{issue}`` from ``main`` HEAD
    (post-release) or from the active release branch HEAD (dev). Runtime
    is the sole branch authority.

    Failure mode (legal Red): ``complete_hotfix_entry`` is a frozen stub
    (IF-HOTFIX-005); the call raises ``NotImplementedError`` before the
    git assertions can fire.
    """
    seed_v05_approved_baseline(host_repo)

    # Scenario A: post-release -> base = main HEAD.
    out_a = complete_hotfix_entry(
        repo=host_repo,
        run_id="hotfix-42",
        issue=42,
        scenario="post-release",
        target_version="v0.5",
        anchor_acs=["AC-FR0030-01@v0.5"],
    )
    assert out_a["branch"] == "fix/42"
    log_a = git(host_repo, "log", "--oneline", "fix/42", "^main")
    assert log_a.strip() == ""  # base = main HEAD, no new commits yet

    # Scenario B: dev -> base = active release branch HEAD.
    seed_release_branch(host_repo, "releases/v0.6")
    out_b = complete_hotfix_entry(
        repo=host_repo,
        run_id="hotfix-50",
        issue=50,
        scenario="dev",
        target_version="v0.6",
        anchor_acs=["AC-FR0030-01@v0.6"],
    )
    assert out_b["branch"] == "fix/50"
    log_b = git(host_repo, "log", "--oneline", "fix/50", "^releases/v0.6")
    assert log_b.strip() == ""  # base = active release HEAD


# AC-FR0241-02@v0.6 TRACKS-TRACE baseline inheritance: no requirement-stage artifacts
def test_baseline_inheritance_no_requirement_stage_artifacts(host_repo, trac):
    """AC-FR0241-02@v0.6: the inherited baseline (``baseline.inherited``)
    is a source-approval record - it never copies / re-approves the
    target-version trio. The hotfix project directory carries no M-STORY
    / M-SPEC / M-ACC / M-REQ-APPROVAL artifacts of its own.

    Failure mode (legal Red): the ``trac hotfix`` surface is unregistered
    (USAGE / exit 1) and ``complete_hotfix_entry`` raises
    ``NotImplementedError`` (IF-HOTFIX-005), so no hotfix project
    directory is ever created.
    """
    from pathlib import Path

    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    # The hotfix project directory exists and carries delta docs only.
    hotfix_dir = Path(host_repo / ".tracks" / "projects" / "v0.5-hotfix-42")
    assert hotfix_dir.exists()
    assert not (hotfix_dir / "story.md").exists()
    assert not (hotfix_dir / "spec.md").exists()
    assert not (hotfix_dir / "acceptance.md").exists()


# AC-FR0241-03@v0.6 TRACKS-TRACE ANCHORED enters M-DESIGN and run continues
def test_anchored_enters_mdesign_and_run_continues(trac, host_repo, event_log):
    """AC-FR0241-03@v0.6: ANCHORED directly enters ``stage.entered(M-DESIGN)``;
    the run is continuable via ``trac run`` (FR-0242接续).

    Failure mode (legal Red): unregistered ``trac hotfix`` -> USAGE /
    exit 1, no events, so ``stage.entered(M-DESIGN)`` cannot appear and
    ``trac run`` cannot continue a non-existent hotfix run.
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
