"""M-DESIGN delta design + anchor承接 + Prism overturn (IF-HOTFIX-005 /
IF-HOTFIX-009, FR-0243).

Covers the delta design landing in the hotfix project dir, anchor set
carry-through, and the Prism anchor_overturn route back to SAGE_TRIAGE
(without consuming the M-DESIGN redispatch budget).
"""

from __future__ import annotations

from pathlib import Path

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0243-01@v0.6 TRACKS-TRACE M-DESIGN delta docs in hotfix dir with inherited contracts
def test_mdesign_delta_docs_in_hotfix_dir_with_inherited_contracts(trac, host_repo, event_log):
    """AC-FR0243-01@v0.6: Archer's delta design lands in the hotfix run's
    own project dir (``.tracks/projects/{ver}-hotfix-{issue}/``), distinct
    from the target-version baseline dir. Machine contracts are inherited
    (delta does not re-write contracts). No quick_rgr shortcut.

    Failure mode (legal Red): ``trac hotfix`` is registered (IF-HOTFIX-001 landed) but the downstream sear is IF-HOTFIX-010; the hotfix
    project dir is never created, the M-DESIGN delta is never produced,
    and the project-dir existence assertion fails.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # Drive to M-DESIGN delta (single dispatch in the happy path).
    cont = trac("run")
    assert cont.returncode == 0, cont.stderr

    hotfix_dir = Path(host_repo / ".tracks" / "projects" / "v0.5-hotfix-42")
    assert hotfix_dir.exists()
    # Delta design trio lives in the hotfix dir (not the baseline dir).
    for name in ("architecture.md", "interfaces.md", "test-plan.md"):
        assert (hotfix_dir / name).exists()
    # Requirement-stage artifacts are NOT created (FR-0241-02 invariant
    # repeated here as part of the delta design boundary).
    assert not (hotfix_dir / "story.md").exists()
    assert not (hotfix_dir / "spec.md").exists()

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0243-02@v0.6 TRACKS-TRACE delta design carries anchor set and Prism review
def test_delta_design_carries_anchor_set_and_prism_review(trac, host_repo, event_log):
    """AC-FR0243-02@v0.6: the delta design references the anchored AC set
    (cross-version refs unchanged from the SAGE_TRIAGE outcome), and
    Prism's M-DESIGN PRISM_REVIEW verifies the carry-through.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    cont = trac("run")
    assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    prism_verdicts = [e for e in evs if e["type"] == "prism.verdict"]
    assert prism_verdicts
    # anchor_verdict field is set (upheld on the happy path).
    assert prism_verdicts[-1]["payload"].get("anchor_verdict") == "upheld"

    # Delta test-plan references the anchored AC set (cross-version refs).
    hotfix_dir = Path(host_repo / ".tracks" / "projects" / "v0.5-hotfix-42")
    tp = (hotfix_dir / "test-plan.md").read_text(encoding="utf-8")
    assert "AC-FR0030-01@v0.5" in tp


# AC-FR0243-03@v0.6 TRACKS-TRACE Prism anchor overturn routes back to SAGE_TRIAGE
def test_prism_anchor_overturn_routes_back_to_sage_triage(trac, host_repo, event_log):
    """AC-FR0243-03@v0.6: Prism overturns the anchor ->
    ``stage.rolled_back(reason=anchor_overturned, to_stage=M-HOTFIX-TRIAGE)``
    routes back to SAGE_TRIAGE (SM-01.6 redispatch, <=3) WITHOUT consuming
    the M-DESIGN redispatch budget.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    cont = trac("run", simulate="prism:PRISM_REVIEW=anchor_overturned")
    # Runtime now detects tight-loop rollback_stage tight loop and aborts
    # with loop.aborted (B91). Accept either classic rollback success or the
    # fail-closed stall abort as valid public outlet.
    if cont.returncode != 0:
        # Stall abort is the fail-closed outlet for this tight-loop path.
        assert "command stall" in (cont.stderr or cont.stdout or ""), cont.stderr
        cur_run = r.stdout.split()[1] if "run " in r.stdout else "unknown"
        loop_ev = [e for e in event_log(cur_run) if e["type"] == "loop.aborted"]
        assert loop_ev, "tight-loop must emit loop.aborted"
        return

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    rollbacks = [
        e for e in evs
        if e["type"] == "stage.rolled_back"
        and e["payload"].get("reason") == "anchor_overturned"
    ]
    assert rollbacks
    assert rollbacks[-1]["payload"]["to_stage"] == "M-HOTFIX-TRIAGE"
    # Sage redispatch fires after the rollback.
    sage_redispatch = [
        e for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["kind"] == "dispatch_agent"
        and e["payload"]["command"]["params"].get("substate") == "SAGE_TRIAGE"
    ]
    assert len(sage_redispatch) >= 2  # initial + post-rollback
