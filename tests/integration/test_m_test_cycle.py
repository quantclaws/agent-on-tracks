"""M-TEST full cycle integration tests (FR-0010~0070, SM-01 coverage).

Fake-channel end-to-end: DISPATCH -> WRITE -> COLLECT -> PRISM_REVIEW ->
RED_CHECK -> EXIT -> test.committed -> stage.exited -> run.completed(boundary).
"""

from tests.e2e.helpers import walk_to_m_test_complete
from tests.e2e.test_happy_path import types
from tests.integration.helpers import assert_sm01_event_sequence, m_test_events


# AC-FR0010-01@v0.4 TRACKS-TRACE enter dispatch
def test_enter_dispatch(trac, event_log):
    """AC-FR0010-01@v0.4: M-DESIGN EXIT -> stage.entered(M-TEST) -> DISPATCH."""
    run_id = walk_to_m_test_complete(trac)
    evs = m_test_events(event_log(run_id))
    assert evs[0]["type"] == "stage.entered"
    assert evs[0]["payload"]["stage"] == "M-TEST"
    # SM-01.2: DISPATCH -> first Shield dispatch
    dispatch = next(
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "shield"
    )
    assert dispatch["payload"]["command"]["params"]["substate"] == "WRITE"


# AC-FR0030-01@v0.4 TRACKS-TRACE collect independent
def test_collect_independent(trac, event_log):
    """AC-FR0030-01@v0.4: Runtime independently collects (collect_tests command)."""
    run_id = walk_to_m_test_complete(trac)
    evs = m_test_events(event_log(run_id))
    collect = [
        e
        for e in evs
        if e["type"] == "command.issued" and e["payload"]["command"]["kind"] == "collect_tests"
    ]
    assert len(collect) == 1  # exactly one independent collection


# AC-FR0030-03@v0.4 TRACKS-TRACE collected event evidence
def test_collected_event_evidence(trac, event_log):
    """AC-FR0030-03@v0.4: collection evidence lands in test.collected event."""
    run_id = walk_to_m_test_complete(trac)
    evs = m_test_events(event_log(run_id))
    collected = [e for e in evs if e["type"] == "test.collected"]
    assert len(collected) == 1
    assert collected[0]["payload"]["status"] == "passed"
    assert collected[0]["payload"]["collected_count"] > 0
    assert collected[0]["payload"]["errors"] == []


# AC-FR0010-03@v0.4 TRACKS-TRACE full M-TEST cycle
def test_full_m_test_cycle(trac, event_log):
    """AC-FR0010-03@v0.4: SM-01 transition sequence in the event log."""
    run_id = walk_to_m_test_complete(trac)
    evs = m_test_events(event_log(run_id))
    ts = types(evs)
    # The event sequence must include all SM-01 milestones
    assert "stage.entered" in ts
    assert "outcome.received" in ts  # Shield outcome
    assert_sm01_event_sequence(ts)
    assert evs[-1]["payload"]["terminal_state"] == "boundary"


# AC-NFR0040-01@v0.4 TRACKS-TRACE events append only
def test_events_append_only(trac, event_log, host_repo):
    """AC-NFR0040-01@v0.4: M-TEST events are append-only (seq monotonic, no
    rewrites). Verified by checking seq continuity in the events table."""
    import sqlite3

    run_id = walk_to_m_test_complete(trac)
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    seqs = [
        r[0] for r in conn.execute("SELECT seq FROM events WHERE run_id=? ORDER BY seq", (run_id,))
    ]
    conn.close()
    assert seqs == list(range(1, len(seqs) + 1))  # contiguous, no gaps/rewrites


# AC-NFR0040-02@v0.4 TRACKS-TRACE rebuild projections
def test_rebuild_projections(trac, event_log, host_repo):
    """AC-NFR0040-02@v0.4: drop projections, rebuild from events -> same state."""
    import sqlite3

    walk_to_m_test_complete(trac)
    r1 = trac("status")
    assert "terminal=boundary" in r1.stdout
    db = host_repo / ".tracks" / "runtime" / "tracks.db"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM runs")  # drop projections
    conn.commit()
    conn.close()
    r2 = trac("status")
    assert r2.returncode == 0
    assert "terminal=boundary" in r2.stdout  # rebuilt from events


def test_replay_recovery(trac, event_log):
    """SM-01 休眠回放@v0.4: event replay recovers M-TEST state."""
    run_id = walk_to_m_test_complete(trac)
    r = trac("replay", run_id)
    assert r.returncode == 0
    assert "status=completed" in r.stdout
