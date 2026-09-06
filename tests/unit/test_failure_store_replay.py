"""Unit: failure-review store replay across process restarts (IF-FAILURE-001).

Operator OOB 2026-09-06 (user-authorized): the failure-review store is
process-local; under the M7 handover regime the loop is replaced at every
drift boundary, so the failure.stored / failure.acked stream must replay
into the selection rule or every post-restart DIAGNOSE runs evidenceless
(live 2026-09-06: two consecutive unknown escalations in round 17 while the
round's records sat on the stream).
"""

from types import SimpleNamespace

from tracks.executor.failure_review import restore_from_events, select_failure


def _ev(etype, eseq, **payload):
    return SimpleNamespace(type=etype, seq=eseq, payload=payload)


def test_replay_restores_stored_records_and_selects():
    run = "RUN-REPLAY-STORE"
    events = [
        _ev(
            "failure.stored",
            eseq=1,
            failure_id="17-archer-2",
            run_id=run,
            round=17,
            source="archer",
            record={"failure_class": "timeout", "self_report": "agent stream inactive"},
            seq=0,
        ),
        _ev(
            "failure.stored",
            eseq=2,
            failure_id="17-oob-green-1",
            run_id=run,
            round=17,
            source="oob",
            record={"failure_class": "impl_defect", "self_report": "green gate 25 nodes"},
            seq=1,
        ),
    ]
    assert restore_from_events(run, events) == 2
    selected = select_failure(run, "prism", 17)
    assert selected is not None
    assert selected["failure_id"] == "17-oob-green-1"  # latest by seq


def test_replay_is_idempotent_and_acks_suppress_selection():
    run = "RUN-REPLAY-ACK"
    events = [
        _ev(
            "failure.stored",
            eseq=1,
            failure_id="17-oob-1",
            run_id=run,
            round=17,
            source="oob",
            record={"failure_class": "impl_defect", "self_report": "x"},
            seq=0,
        ),
        _ev("failure.acked", eseq=2, failure_id="17-oob-1", role="prism"),
    ]
    assert restore_from_events(run, events) == 1
    assert restore_from_events(run, events) == 0  # idempotent per failure_id
    # acked for prism -> the shared rule stops re-injecting it for prism
    assert select_failure(run, "prism", 17) is None
    # an un-acked role still sees it
    assert select_failure(run, "shield", 17)["failure_id"] == "17-oob-1"


def test_replay_ignores_unrelated_events_and_malformed_records():
    run = "RUN-REPLAY-JUNK"
    events = [
        _ev("verdict.passed", eseq=1, check="island_2"),
        _ev("failure.stored", eseq=2, record={"failure_class": "x"}),  # no failure_id
    ]
    assert restore_from_events(run, events) == 0
    assert select_failure(run, "prism", 17) is None
