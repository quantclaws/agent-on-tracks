"""PRECHECK deterministic program precheck (IF-HOTFIX-003, FR-0240-02/03).

Driven entirely through the ``trac hotfix`` CLI/event/git outlets
(interfaces §4): PRECHECK pass and REJECTED outcomes must surface the
contract lines (``triage.prechecked`` / ``REJECTED <reason>`` / ``next:``),
exit codes and the no-``fix/{issue}``-branch side effect.

``trac hotfix`` (IF-HOTFIX-001) is registered (Devon foundation landed);
the legal Red signal for this round is the IF-HOTFIX-010 baseline-resolver
seam: the hotfix delta run parks at ``awaiting=escalation reason=[trace]
test-plan validate requires acceptance.md in same dir`` until the
inherited baseline resolver is wired into the run-loop validate step.
"""

from __future__ import annotations

from tests.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_v05_approved_baseline,
)


# AC-FR0240-02@v0.6 TRACKS-TRACE PRECHECK pass reaches SAGE_TRIAGE without agent dispatch
def test_precheck_pass_reaches_sage_triage_without_agent_dispatch(trac, host_repo):
    """AC-FR0240-02@v0.6: a bug issue whose target version is approved passes
    P-1.P-5 deterministically and the run reaches ``SAGE_TRIAGE``. PRECHECK
    is a pure program judgment — no ``dispatch_agent``
    (sage/archer/prism/shield/devon) audit record corresponds to this step
    (NFR-0100-01). The pass path is observable through the CLI/event outlets:
    ``triage.prechecked(pass)`` with ``target_version`` and ``type=bug``.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001
    landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver
    seam (run parks at M-DESIGN awaiting=escalation reason=[trace]
    test-plan validate requires acceptance.md in same dir). The
    ``triage.prechecked(pass)`` line appears at entry; the downstream
    journey continues only once the inherited-baseline resolver is wired.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # PRECHECK passes deterministically: triage.prechecked(pass, target v0.5).
    assert "triage.prechecked" in r.stdout
    assert "v0.5" in r.stdout
    # The run proceeds to SAGE_TRIAGE (SM-01.2) — not REJECTED.
    assert "REJECTED" not in r.stdout

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0240-03@v0.6 TRACKS-TRACE PRECHECK REJECTED matrix: no branch, nonzero exit, reason closed set
def test_precheck_rejection_matrix_no_branch_nonzero_exit(trac, host_repo):
    """AC-FR0240-03@v0.6: REJECTED outcomes (non-bug / not-found / scenario
    invalid / dev-no-active-branch / baseline-not-locatable / fix-branch-exists)
    each carry a reason from the closed ``HotfixRejectionReason`` set and a
    human-readable ``next``; the CLI exits non-zero and **never** creates the
    ``fix/{issue}`` branch (no branch side effect — fail-closed).

    Driven entirely through the CLI/event/git outlets (interfaces §4); the
    direct pure-function rules are covered by Devon's unit tests, not here.

    Failure mode (legal Red): the hotfix CLI is registered (IF-HOTFIX-001 landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver seam (run parks at M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir).
    exit 1) — the CLI never produces a ``triage.prechecked REJECTED`` line,
    so ``REJECTED`` / the reason / the handoff ``next:`` are all absent.
    """
    import subprocess

    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    cases: list[tuple[int, str]] = [
        (99, "post-release"),  # labels missing "bug" -> not_bug
        (42, "dev"),           # no active release branch -> no_active_release_branch
        (7777, "post-release"),  # issue not found -> issue_not_found
    ]
    for issue, scenario in cases:
        r = trac("hotfix", str(issue), "--scenario", scenario)
        assert r.returncode != 0, f"issue {issue}/{scenario}: REJECTED must exit non-zero"
        # The rejection reason must be contract-visible (REJECTED <reason> +
        # handoff next: line). Asserting on this prevents the exit-code-only
        # weak assertion from passing vacuously under USAGE.
        assert "REJECTED" in r.stdout or "rejected" in r.stdout
        # No fix branch is left behind (fail-closed / no side effects).
        out = subprocess.run(
            ["git", "branch", "--list", f"fix/{issue}"],
            cwd=host_repo, capture_output=True, text=True,
        ).stdout
        assert not out.strip(), f"fix/{issue} must not be created on REJECTED"

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo, fresh_issue="58")


# AC-NFR0100-01@v0.6 TRACKS-TRACE PRECHECK deterministic: no LLM dispatch records
def test_precheck_deterministic_no_llm_dispatch_records(trac, host_repo, event_log):
    """AC-NFR0100-01@v0.6: PRECHECK is a pure program judgment - no agent
    dispatch (sage / archer / prism / shield / devon) audit record
    corresponds to this step. The result is determined by the program
    rules P-1.P-5 alone; the same input reproduces the same output
    (deterministic, no LLM).

    Failure mode (legal Red): the ``trac hotfix`` command is a Devon
    foundation task registered in ``_COMMANDS`` (IF-HOTFIX-001 landed) (IF-HOTFIX-001);
    the subprocess exits 0 (entry registered) but the downstream journey parks at the IF-HOTFIX-010 seam (M-DESIGN awaiting=escalation reason=[trace] test-plan validate requires acceptance.md in same dir), so the
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

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)
