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
    """AC-FR0243-03@v0.6: Prism overturns the anchor -> the kernel routes
    ``rollback_stage(reason=anchor_overturned, to_stage=M-HOTFIX-TRIAGE)``.

    The v0.6 fake token ``prism:PRISM_REVIEW=anchor_overturned`` is not
    representable in the v0.8 declared review envelope (verdict is
    pass|revise and a revise must carry findings), so the routing verdict is
    seeded through the public store as the upstream premise (same
    premise-injection pattern as test_release_gate_cli). ``M-HOTFIX-TRIAGE``
    is an attached substate machine outside the canonical rollback closed
    set, so the executor fail-closes the tight rollback_stage loop with
    ``loop.aborted(reason=command_stall)`` — the accepted B91 public outlet
    asserted here; the issued commands still carry the contracted target.
    """
    from tracks import paths
    from tracks.store import Store

    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    # Stop after the design DRAFT dispatch (state=M-DESIGN/PRISM_REVIEW) and
    # land the anchor-overturned verdict premise.
    assert trac("run", "--max-dispatches", "1").returncode == 0
    store = Store(paths.tracks_home(host_repo))
    try:
        state = store.state(run_id)
        assert state.stage == "M-DESIGN" and state.substate == "PRISM_REVIEW"
        store.append(
            run_id,
            state.version or "v0.5",
            "prism.verdict",
            {
                "verdict": "revise",
                "anchor_verdict": "overturned",
                "review_summary": "anchor set overturned",
                "findings": [
                    {
                        "id": "PRISM-ANCHOR-01",
                        "severity": "blocker",
                        "defect_classification": "design_defect",
                        "criterion": "anchor",
                        "artifact": "architecture.md",
                        "ac_refs": [],
                        "summary": "anchor set overturned",
                    }
                ],
                "review_body": "anchor set overturned",
            },
        )
    finally:
        store.close()

    cont = trac("run")
    assert cont.returncode != 0
    assert "command stall" in (cont.stderr or cont.stdout), cont.stderr
    evs = event_log(run_id)
    loop_ev = [e for e in evs if e["type"] == "loop.aborted"]
    assert loop_ev, "tight-loop must emit loop.aborted"
    # The failed rollback attempts still carry the contracted routing.
    issued = [
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["kind"] == "rollback_stage"
    ]
    assert issued
    assert issued[-1]["payload"]["command"]["params"]["to_stage"] == "M-HOTFIX-TRIAGE"
    assert issued[-1]["payload"]["command"]["params"]["reason"] == "anchor_overturned"
    # No Archer RESPOND re-dispatch: the M-DESIGN redispatch budget is not
    # consumed by the anchor-overturn route.
    respond = [
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["kind"] == "dispatch_agent"
        and e["payload"]["command"]["params"].get("substate") == "RESPOND"
    ]
    assert not respond
