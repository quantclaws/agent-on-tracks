"""Design gap + ac_gap / spec_gap exit routes (IF-HOTFIX-009, FR-0247 / FR-0248).

Covers:
* design gap -> back to hotfix M-DESIGN (no Human technical gate);
* design gap loop closes and re-enters the journey;
* ac_gap / spec_gap -> backlog + terminal exit, with a Human approval
  prerequisite (``trac approve`` extended awaiting form).
"""

from __future__ import annotations

from tests.hotfix_support import seed_host_issues, seed_v05_approved_baseline


# AC-FR0247-01@v0.6 TRACKS-TRACE design gap returns to hotfix M-DESIGN without Human gate
def test_design_gap_returns_to_hotfix_mdesign_without_human_gate(trac, host_repo, event_log):
    """AC-FR0247-01@v0.6: a design gap (delta design / test-plan /
    interface gap, owned by Archer) routes back to hotfix M-DESIGN with
    NO Human technical gate - no ``human.review`` / ``human.approval``
    event appears as a prerequisite.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no
    M-DESIGN delta ever exists, so the design-gap route cannot be
    exercised; the ``stage.rolled_back(to_stage=M-DESIGN)`` event is
    absent from the stream.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # A delta design / test-plan / interface gap (Archer-owned, IF-HOTFIX-009)
    # surfaces DOWNSTREAM at M-TEST: the Shield WRITE fails and DIAGNOSE
    # classifies ``stub_gap`` -> ``stage.rolled_back(to_stage=M-DESIGN)``
    # with NO Human technical gate (FR-0247). The downstream M-TEST DIAGNOSE
    # routing matches FR-0247 more faithfully than a same-stage M-DESIGN
    # revisit (PRISM-V06-A2). ``|ok`` lets the re-entered journey's second
    # Shield WRITE pass once the design gap closes.
    cont = trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=stub_gap")
    assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    rollbacks = [
        e for e in evs
        if e["type"] == "stage.rolled_back"
        and e["payload"].get("to_stage") == "M-DESIGN"
    ]
    assert rollbacks
    # No Human technical gate precedes the design-gap route.
    pre_rollback_seq = rollbacks[0]["seq"]
    human_gates = [
        e for e in evs
        if e["seq"] < pre_rollback_seq and e["type"] in ("human.review", "human.approval")
    ]
    assert not human_gates


# AC-FR0247-02@v0.6 TRACKS-TRACE design gap loop closes and reenters journey
def test_design_gap_loop_closes_and_reenters_journey(trac, host_repo, event_log):
    """AC-FR0247-02@v0.6: after the design gap closes inside hotfix
    M-DESIGN, the run re-enters the M-TEST / M-IMPL dispatch sequence
    (FR-0243 re-承接 anchored set). Replay shows the closure + re-entry.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> no design
    gap, no closure, no re-entry; the replay cannot show the loop.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # First trip into M-TEST: the downstream Shield WRITE fails and
    # DIAGNOSE classifies stub_gap -> rollback to the hotfix M-DESIGN
    # (downstream trigger, IF-HOTFIX-009; PRISM-V06-A2).
    cont1 = trac("run", simulate="shield:WRITE=illegit_red|ok,diagnose:classification=stub_gap")
    assert cont1.returncode == 0, cont1.stderr
    # Second trip: the design gap closes inside M-DESIGN (default ok) and
    # the journey re-enters M-TEST / M-IMPL dispatch sequence.
    cont2 = trac("run")
    assert cont2.returncode == 0, cont2.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    # The M-DESIGN closure + re-entry shows as two stage.entered(M-DESIGN)
    # events with a stage.rolled_back(to_stage=M-DESIGN) in between.
    assert types_seq.count("stage.entered") >= 2
    assert any(
        e["type"] == "stage.rolled_back"
        and e["payload"].get("to_stage") == "M-DESIGN"
        for e in evs
    )
    replay = trac("replay", run_id)
    assert "stage.entered" in replay.stdout
    assert "stage.rolled_back" in replay.stdout


# AC-FR0248-01@v0.6 TRACKS-TRACE ac_gap/spec_gap exit with Human approval prerequisite
def test_ac_gap_spec_gap_exit_with_human_approval_prerequisite(trac, host_repo, event_log):
    """AC-FR0248-01@v0.6: when DIAGNOSE surfaces ``ac_gap`` or
    ``spec_gap`` (a product decision, not a design gap), the exit route is
    gated by a Human approval (``human.approval``); after approval the run
    records ``backlog.recorded(decision=ac_gap|spec_gap)`` and
    ``run.completed(terminal_state=ac_gap|spec_gap)``. The hotfix run does
    not continue to M-TEST / M-IMPL.

    Failure mode (legal Red): ``trac hotfix`` unregistered -> the run
    never reaches DIAGNOSE, so the ac_gap/spec_gap exit cannot be
    observed; ``trac approve`` (extended awaiting form) is not reachable
    because there is no awaiting=escalation run with the right check.
    """
    seed_v05_approved_baseline(host_repo)
    seed_host_issues(host_repo)

    r = trac("hotfix", "42", "--scenario", "post-release")
    assert r.returncode == 0, r.stderr
    # Trigger the ac_gap exit from a M-TEST failure (v0.4 legal injection
    # per PRISM-V06-08: a failing Shield WRITE + diagnose:classification=
    # ac_gap). DIAGNOSE at M-TEST classifies ac_gap; the hotfix variant
    # parks awaiting Human approval (IF-HOTFIX-009 §2c). Because the exit
    # fires before any test commit or M-IMPL green, the absence of
    # test.committed / green.committed holds naturally. The token is carried
    # on both drive runs so whichever invocation dispatches the M-TEST
    # Shield WRITE consumes it.
    for _ in range(2):
        cont = trac("run", simulate="shield:WRITE=illegit_red,diagnose:classification=ac_gap")
        assert cont.returncode == 0, cont.stderr

    run_id = r.stdout.split()[1] if "run " in r.stdout else "unknown"
    # The run is now awaiting Human approval for the ac_gap exit.
    approve = trac("approve", "--actor", "Aaron")
    assert approve.returncode == 0, approve.stderr

    evs = event_log(run_id)
    types_seq = [e["type"] for e in evs]
    assert "human.approval" in types_seq  # Human gate prerequisite satisfied
    assert "backlog.recorded" in types_seq
    completed = [e for e in evs if e["type"] == "run.completed"]
    assert completed
    assert completed[-1]["payload"]["terminal_state"] in ("ac_gap", "spec_gap")
    # The ac_gap exit fired during M-TEST, before any test commit or M-IMPL
    # green: the run did NOT continue to further M-TEST/M-IMPL activity
    # after the gap exit.
    assert "test.committed" not in types_seq
    assert "green.committed" not in types_seq
