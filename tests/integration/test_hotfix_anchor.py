"""Sage anchor validation + redispatch (IF-HOTFIX-004, FR-0240-04/05).

CLI-driven hotfix entry with fake ``sage:SAGE_TRIAGE=bad_anchor`` to exercise
the ``verdict.failed(anchor_invalid)`` event outlet, the anchored-set report
and the ``AWAIT_HUMAN`` parking (IF-HOTFIX-002). Asserts land on the
interfaces §4 outlets (CLI stdout / events table) only.

``trac hotfix`` (IF-HOTFIX-001) is registered (Devon foundation landed);
the legal Red signal for this round is the IF-HOTFIX-010 baseline-resolver
seam: the run parks at ``awaiting=escalation reason=[trace] line:1
test-plan validate requires acceptance.md in same dir`` until the
inherited baseline resolver is wired into the run-loop validate step.
"""

from __future__ import annotations

from tests._support.hotfix_support import (
    assert_hotfix_drives_to_mtest,
    seed_host_issues,
    seed_v05_approved_baseline,
)
from tracks.executor.hotfix import validate_anchor_refs


# AC-FR0240-04@v0.6 TRACKS-TRACE Sage anchor validated reports anchored AC set
def test_sage_anchor_validated_reports_anchored_set(trac, host_repo):
    """AC-FR0240-04@v0.6: the SAGE_TRIAGE outcome is a cross-version AC
    reference set (e.g. ``AC-FR0030-01@v0.5``). After Sage anchors on the
    fixture AC, the program validator confirms each ref resolves to a real
    ``### <ac_id>`` heading in ``.tracks/projects/<version>/acceptance.md``
    and the ``anchor.validated`` event is emitted with that anchored set
    (interfaces §2a / §4b CLI outlet).

    Failure mode (legal Red): the anchored-set report is emitted by the
    CLI entry phase, which is now implemented (IF-HOTFIX-001 foundation
    landed); the legal Red anchor is the IF-HOTFIX-010 baseline-resolver
    seam: the hotfix run parks at ``awaiting=escalation reason=[trace]
    test-plan validate requires acceptance.md in same dir`` until the
    inherited-baseline resolver is wired into the run-loop validate step.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    # CLI drive: on ANCHORED the run surfaces the anchored cross-version AC
    # set so the operator can audit the anchor evidence chain (§2a #1 / §4b).
    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    assert "AC-FR0030-01" in r.stdout

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)


# AC-FR0240-05@v0.6 TRACKS-TRACE anchor invalid redispatch <=3 then AWAIT_HUMAN, no auto feature
def test_anchor_invalid_redispatch_and_await_human_no_auto_feature(trac, host_repo, event_log):
    """AC-FR0240-05@v0.6: an invalid anchor reference (AC not present in the
    target-version acceptance.md) drives ``verdict.failed(check=
    anchor_invalid)`` and Sage redispatch up to 3 times; on exhaustion or
    NO_ANCHOR the sub-state parks at ``AWAIT_HUMAN`` and **never** auto-
    routes to FEATURE_ROUTE (NFR-0100-03).

    Two observation layers, both on interfaces §4 outlets:

    * pure-function contract (IF-HOTFIX-004): ``validate_anchor_refs``
      reports the bad cross-version ref as missing;
    * runtime behavior (IF-HOTFIX-002): with fake ``sage:SAGE_TRIAGE=
      bad_anchor`` the entry drives three consecutive anchor-validation
      failures (``verdict.failed(check=anchor_invalid, attempt=1..3)``
      append-only on the run event stream), then parks at
      ``awaiting=awaiting_human`` (SM-01.7) — and no ``backlog.recorded``
      ever appears (no auto feature-routing on anchor exhaustion).

    Failure mode (legal Red): the entry phase (IF-HOTFIX-001) and the
    anchor validator (IF-HOTFIX-004) are implemented; the legal Red signal
    is the IF-HOTFIX-010 seam. The bad-anchor redispatch assertions bound
    the entry behavior directly. Then the test drives the manual anchor
    (AWAIT_HUMAN -> ANCHORED) and continues the run: it must progress from
    M-DESIGN into the M-TEST / M-IMPL journey, which the unimplemented
    inherited-baseline resolver blocks (run parks at ``awaiting=escalation
    reason=[trace] test-plan validate requires acceptance.md in same dir``).
    Asserting ``stage=M-TEST`` in status is the legal-Red assertion — it
    fails until IF-HOTFIX-010 lands.
    """
    vdir = seed_v05_approved_baseline(host_repo)
    projects_dir = vdir.parent
    bad_refs = [("AC-FR9999-99", "v0.5")]  # not present in fixture acceptance

    # IF-HOTFIX-004: the program validator must report the miss.
    all_exist, missing = validate_anchor_refs(bad_refs, projects_dir)
    assert not all_exist
    assert missing  # human-locatable miss string

    # IF-HOTFIX-002 runtime behavior: three failing attempts then AWAIT_HUMAN.
    seed_host_issues(host_repo)
    r = trac("hotfix", "42", "--scenario", "post-release",
             simulate="sage:SAGE_TRIAGE=bad_anchor")
    assert r.returncode == 0, r.stderr
    assert "awaiting=awaiting_human" in r.stdout

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    failed = [
        e for e in evs
        if e["type"] == "verdict.failed"
        and e["payload"].get("check") == "anchor_invalid"
    ]
    assert len(failed) == 3, f"expected 3 verdict.failed(anchor_invalid), got {len(failed)}"
    for expected in (1, 2, 3):
        assert failed[expected - 1]["payload"].get("attempt") == expected, (
            f"attempt={expected} redispatch must be traceable, got "
            f"{failed[expected - 1]['payload'].get('attempt')}"
        )
    # No automatic feature routing on anchor exhaustion.
    backlogs = [e for e in evs if e["type"] == "backlog.recorded"]
    assert not backlogs, "anchor exhaustion must NOT auto-route to feature"

    # IF-HOTFIX-010 seam: resolve AWAIT_HUMAN via manual anchor, then the
    # run must progress from M-DESIGN into the M-TEST journey. The
    # unimplemented inherited-baseline resolver (IF-HOTFIX-010) parks the
    # run at M-DESIGN awaiting=escalation, so asserting stage=M-TEST is the
    # legal-Red signal.
    r_anchor = trac("hotfix", "anchor", "AC-FR0030-01@v0.5")
    assert r_anchor.returncode == 0, r_anchor.stderr
    for _ in range(2):
        cont = trac("run")
        assert cont.returncode == 0, cont.stderr
    status = trac("status")
    assert "stage=M-IMPL" in status.stdout or "stage=M-TEST" in status.stdout, (
        f"run must progress through M-TEST: {status.stdout.strip()}"
    )


# AC-NFR0100-02@v0.6 TRACKS-TRACE anchor validation traceable with retry count
def test_anchor_validation_traceable_with_retry_count(host_repo, trac):
    """AC-NFR0100-02@v0.6: anchor validation outcomes (pass / fail / redispatch
    count) are append-only and replay-traceable via ``trac replay`` /
    ``trac report``.

    Failure mode (legal Red): the entry phase (IF-HOTFIX-001) is
    registered; the legal Red anchor is IF-HOTFIX-010 (inherited baseline
    resolver not wired into the run-loop validate step). The run parks at
    ``awaiting=escalation reason=[trace] ... test-plan validate requires
    acceptance.md in same dir`` before the anchor-validated event can
    appear in the replay; the replay probe fails to find the anchor
    evidence.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    # A replay / report must surface the anchor validation evidence chain.
    # The default fixture drives the SAGE pass path (issue 42 anchored on
    # attempt 1), so replay deterministically shows ``anchor.validated``
    # with the attempt counter (interfaces §4a payload field). The
    # or-disjunction fallback is a hole: it would also pass when NO anchor
    # evidence exists (PRISM-V06-A3).
    replay = trac("replay", run_id)
    assert "anchor.validated" in replay.stdout
    # Attempt counter must be traceable (anchor.validated.attempt == 1).
    assert '"attempt": 1' in replay.stdout

    # IF-HOTFIX-010 baseline-resolver seam (legal Red): the run must
    # continue from M-DESIGN into the M-TEST journey; the unimplemented
    # inherited-baseline resolver parks it at awaiting=escalation, so
    # the drive-to-M-TEST assertion fails until IF-HOTFIX-010 lands.
    assert_hotfix_drives_to_mtest(trac, host_repo)
