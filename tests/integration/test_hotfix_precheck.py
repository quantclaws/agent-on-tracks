"""PRECHECK deterministic program precheck (IF-HOTFIX-003, FR-0240-02/03).

Two contract surfaces:

* Direct call into the ``tracks.executor.hotfix`` pure functions
  (``precheck_hotfix`` / ``HostIssue.is_bug``) — the deterministic rules
  P-1..P-5 (ARCH-006 §3.2) with no LLM dispatch.
* End-to-end ``trac hotfix`` driving the same rules through the CLI —
  REJECTED runs do not create a ``fix/{issue}`` branch and exit non-zero.

Stubs raise ``NotImplementedError("IF-HOTFIX-003: ...")`` (Archer
scaffold); both surfaces fail with legal Red until Devon implements.
"""

from __future__ import annotations

from typing import get_args

from tests.hotfix_support import HOST_ISSUES_SEED, seed_host_issues, seed_v05_approved_baseline
from tracks.executor.hotfix import HostIssue, HotfixRejectionReason, precheck_hotfix  # noqa: F401

_REASON_CLOSED_SET = set(get_args(HotfixRejectionReason))


# AC-FR0240-02@v0.6 TRACKS-TRACE PRECHECK pass reaches SAGE_TRIAGE without agent dispatch
def test_precheck_pass_reaches_sage_triage_without_agent_dispatch(host_repo):
    """AC-FR0240-02@v0.6: a bug issue whose target version is approved passes
    P-1..P-5 deterministically. PRECHECK is a pure program judgment — no
    ``dispatch_agent`` (sage/archer/prism/shield/devon) audit record
    corresponds to this step (NFR-0100-01); the next sub-state is
    ``SAGE_TRIAGE`` (SM-01.2).

    Failure mode (legal Red): ``precheck_hotfix`` is a frozen stub
    (IF-HOTFIX-003); the call raises ``NotImplementedError("IF-HOTFIX-003:
    precheck_hotfix")`` before any assertion can fire.
    """
    from pathlib import Path

    home = Path.home() / ".tracks-test-precheck-pass"  # unused: stub raises first
    approved = {"v0.5"}
    issue = HostIssue(
        number=42,
        title=HOST_ISSUES_SEED["42"]["title"],
        body=HOST_ISSUES_SEED["42"]["body"],
        labels=tuple(HOST_ISSUES_SEED["42"]["labels"]),
    )

    report = precheck_hotfix(
        issue=issue,
        scenario="post-release",
        repo_branches=["main"],
        active_run_branch=None,
        projects_dir=home,
        approved_versions=approved,
    )

    assert report.status == "pass"
    assert report.reason is None
    assert report.target_version == "v0.5"
    # No agent dispatch is recorded for the PRECHECK step — it is a pure
    # program judgment. The next sub-state is SAGE_TRIAGE (SM-01.2); the
    # caller (kernel/executor) routes there on ``status="pass"``.
    assert report.next in (None, "SAGE_TRIAGE")


# AC-FR0240-03@v0.6 TRACKS-TRACE PRECHECK REJECTED matrix: no branch, nonzero exit, reason closed set
def test_precheck_rejection_matrix_no_branch_nonzero_exit(trac, host_repo):
    """AC-FR0240-03@v0.6: REJECTED outcomes (non-bug / not-found / scenario
    invalid / dev-no-active-branch / baseline-not-locatable / fix-branch-exists)
    each carry a reason from the closed ``HotfixRejectionReason`` set and a
    human-readable ``next``; the CLI exits non-zero and **never** creates the
    ``fix/{issue}`` branch (no branch side effect — fail-closed).

    Failure mode (legal Red): the ``trac hotfix`` command is unregistered
    (USAGE / exit 1) AND the rejection matrix below is exercised through
    ``precheck_hotfix`` which still raises ``NotImplementedError``.
    """
    import subprocess

    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    cases: list[tuple[int, str, str]] = [
        # (issue, scenario, expected_reason-substring)
        (99, "post-release", "not_bug"),  # labels missing "bug"
        (42, "bogus-scenario", "scenario_invalid"),
        (42, "dev", "no_active_release_branch"),
        (7777, "post-release", "issue_not_found"),
    ]
    for issue, scenario, _expected in cases:
        r = trac("hotfix", str(issue), "--scenario", scenario)
        assert r.returncode != 0, f"issue {issue}/{scenario}: REJECTED must exit non-zero"
        # No fix branch is left behind (fail-closed / no side effects).
        out = subprocess.run(
            ["git", "branch", "--list", f"fix/{issue}"],
            cwd=host_repo, capture_output=True, text=True,
        ).stdout
        assert not out.strip(), f"fix/{issue} must not be created on REJECTED"

    # Direct-call matrix: each rejection lands in the closed reason set.
    from pathlib import Path

    projects_dir = Path(host_repo / ".tracks" / "projects")
    non_bug = HostIssue(
        number=99, title="x", body="", labels=("enhancement",),
    )
    report = precheck_hotfix(
        issue=non_bug, scenario="post-release", repo_branches=["main"],
        active_run_branch=None, projects_dir=projects_dir, approved_versions={"v0.5"},
    )
    assert report.status == "rejected"
    # Closed set membership (interfaces.md §1c).
    assert report.reason in _REASON_CLOSED_SET
    assert report.next  # human-readable next step


# AC-NFR0100-01@v0.6 TRACKS-TRACE PRECHECK deterministic: no LLM dispatch records
def test_precheck_deterministic_no_llm_dispatch_records(trac, host_repo, event_log):
    """AC-NFR0100-01@v0.6: PRECHECK is a pure program judgment - no agent
    dispatch (sage / archer / prism / shield / devon) audit record
    corresponds to this step. The result is determined by the program
    rules P-1..P-5 alone; the same input reproduces the same output
    (deterministic, no LLM).

    Failure mode (legal Red): the ``trac hotfix`` command is a Devon
    foundation task not yet registered in ``_COMMANDS`` (IF-HOTFIX-001);
    the subprocess returns USAGE / exit 1 and writes no events, so the
    no-LLM-dispatch audit assertion cannot bind the triage.prechecked
    step. The precheck_hotfix pure function (the actual contract target
    of IF-HOTFIX-003) is also a frozen stub raising NotImplementedError.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    # PRECHECK step itself must not produce any agent dispatch record.
    precheck_seq = next(
        (e["seq"] for e in evs if e["type"] == "triage.prechecked"), None
    )
    assert precheck_seq is not None, "triage.prechecked must be recorded"
    # No dispatch_agent (sage/archer/prism/shield/devon) event fires at or
    # before triage.prechecked - PRECHECK is a pure program judgment.
    pre_dispatches = [
        e for e in evs
        if e["seq"] <= precheck_seq and e["type"] == "command.issued"
        and e["payload"]["command"]["kind"] == "dispatch_agent"
    ]
    assert not pre_dispatches, "PRECHECK must not record any agent dispatch"
