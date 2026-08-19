"""Sage anchor validation + redispatch (IF-HOTFIX-004, FR-0240-04/05).

CLI-driven hotfix entry with fake ``sage:SAGE_TRIAGE=bad_anchor`` to exercise
the ``verdict.failed(anchor_invalid)`` event outlet, the anchored-set report
and the ``AWAIT_HUMAN`` parking (IF-HOTFIX-002). Asserts land on the
interfaces §4 outlets (CLI stdout / events table) only.

``trac hotfix`` (IF-HOTFIX-001) is a Devon foundation task not yet
registered — the CLI returns USAGE / exit 1, which is the legal Red signal
until Devon implements it.
"""

from __future__ import annotations

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline
from tracks.executor.hotfix import validate_anchor_refs


# AC-FR0240-04@v0.6 TRACKS-TRACE Sage anchor validated reports anchored AC set
def test_sage_anchor_validated_reports_anchored_set(trac, host_repo):
    """AC-FR0240-04@v0.6: the SAGE_TRIAGE outcome is a cross-version AC
    reference set (e.g. ``AC-FR0030-01@v0.5``). After Sage anchors on the
    fixture AC, the program validator confirms each ref resolves to a real
    ``### <ac_id>`` heading in ``.tracks/projects/<version>/acceptance.md``
    and the ``anchor.validated`` event is emitted with that anchored set
    (interfaces §2a / §4b CLI outlet).

    Failure mode (legal Red): ``trac hotfix`` is a Devon foundation task
    (IF-HOTFIX-001) not yet registered — the CLI returns USAGE / exit 1
    before the anchored-set line / ``anchor.validated`` event can appear.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    # CLI drive: on ANCHORED the run surfaces the anchored cross-version AC
    # set so the operator can audit the anchor evidence chain (§2a #1 / §4b).
    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    assert "AC-FR0030-01" in r.stdout


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

    Failure mode (legal Red): ``validate_anchor_refs`` raises
    ``NotImplementedError("IF-HOTFIX-004: validate_anchor_refs")`` before
    the redispatch-budget assertion can fire; the ``trac hotfix`` CLI
    surface (IF-HOTFIX-001) is a Devon foundation task not yet registered,
    so the behavior-driven drive returns USAGE / exit 1 and writes no
    events.
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


# AC-NFR0100-02@v0.6 TRACKS-TRACE anchor validation traceable with retry count
def test_anchor_validation_traceable_with_retry_count(host_repo, trac):
    """AC-NFR0100-02@v0.6: anchor validation outcomes (pass / fail / redispatch
    count) are append-only and replay-traceable via ``trac replay`` /
    ``trac report``.

    Failure mode (legal Red): the ``trac hotfix`` command and the
    anchor-related event types (``anchor.validated`` /
    ``verdict.failed(anchor_invalid)``) are Devon foundation tasks; the
    subprocess returns USAGE / exit 1, no events are written, and the
    replay probe cannot find the anchor evidence.
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
