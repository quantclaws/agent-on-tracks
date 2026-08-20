"""E2E happy path: hotfix journey scenario A (post-release).

End-to-end user journey per test-plan §11 scenario A: ``trac hotfix 42
--scenario post-release`` drives the full triage -> delta M-DESIGN ->
M-TEST -> M-IMPL -> boundary sequence in one subprocess pipeline.
Fake backend ``TRAC_FAKE_SIMULATE`` injects deterministic Sage / Prism /
Devon outcome branches; the kernel / executor are NOT mocked.

Asserts on the user-visible outlets: stdout run-event lines, ``trac
status`` terminal row, ``trac replay`` sequence, ``trac report`` md
content, and the host_repo git state (fix/42 branch preserved, working
tree restored to the previous branch).
"""

from __future__ import annotations

from tests.hotfix_support import (
    git,
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0240-01@v0.6 TRACKS-TRACE hotfix journey happy post-release emits hotfix.requested and reaches boundary
# AC-FR0241-03@v0.6 TRACKS-TRACE journey enters M-DESIGN and continues to boundary
# AC-FR0242-01@v0.6 TRACKS-TRACE journey active hotfix run selects and discriminates from suspended
# AC-FR0246-01@v0.6 TRACKS-TRACE journey reaches boundary terminal and keeps fix branch
# AC-FR0246-03@v0.6 TRACKS-TRACE journey replay and report show full hotfix journey
def test_hotfix_journey_happy_post_release(trac, host_repo, event_log):
    """Scenario A full happy path (test-plan §11). Seed the host repo with
    an approved v0.5 baseline + bug issue #42; drive ``trac hotfix 42
    --scenario post-release`` and successive ``trac run`` dispatches to
    boundary. Asserts cover every AC marker above via the observable
    outlets declared in interfaces.md §4.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    registered in ``_COMMANDS`` (IF-HOTFIX-001 landed) (IF-HOTFIX-001 §2a); the very
    first subprocess exits 0 (entry registered) but the downstream journey parks at the IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir). Every
    downstream assertion (run-event lines, terminal status, replay
    sequence, git fix-branch preservation) fails because the journey
    never starts.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)
    # Record the pre-journey branch so we can assert restoration to it.
    pre_branch = git(host_repo, "branch", "--show-current").strip()

    # 1. Hotfix entry: run established, hotfix.requested emitted, status
    #    reports the entry sub-state machine + issue identity.
    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    assert "hotfix.requested" in r.stdout
    assert "stage=M-DESIGN" in r.stdout  # anchored -> M-DESIGN directly
    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"

    # 2. trac status discriminates the active hotfix run.
    status = trac("status")
    assert "branch=fix/42" in status.stdout
    assert "scenario=post-release" in status.stdout
    assert "issue=42" in status.stdout

    # 3. Drive M-DESIGN delta -> M-TEST RED-first -> M-IMPL isolated -> boundary.
    for _ in range(6):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr

    # 4. Boundary terminal state; fix/42 preserved; working tree restored.
    status = trac("status")
    assert "terminal=boundary" in status.stdout
    assert "fix/42" in status.stdout
    out = git(host_repo, "branch", "--list", "fix/42")
    assert "fix/42" in out
    # Working tree returned to the pre-journey branch (no dangling run).
    post_branch = git(host_repo, "branch", "--show-current").strip()
    assert post_branch == pre_branch

    # 5. Replay shows the full journey sequence.
    replay = trac("replay", run_id)
    for token in (
        "hotfix.requested",
        "triage.prechecked",
        "anchor.validated",
        "stage.entered",
        "run.completed",
    ):
        assert token in replay.stdout

    # 6. Report (md) surfaces the boundary + anchor + branch evidence.
    report = trac(
        "report", "--run-id", run_id,
        "--output", ".tracks/runtime/report", "--format", "md",
    )
    assert report.returncode == 0, report.stderr
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in (host_repo / ".tracks" / "runtime" / "report").rglob("*.md")
    )
    assert "boundary" in body
    assert "AC-FR0030-01@v0.5" in body or "AC-FR0030-01" in body
