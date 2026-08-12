"""Focused unit tests for the rollback_stage run-loop boundary.

After ``_do_rollback_stage`` persists ``stage.rolled_back``, ``run_loop``
returns the projected rolled-back state ONLY for the durable stop boundary:
a ``rollback_stage`` with reason ``stub_gap`` (M-TEST/M-IMPL -> M-DESIGN,
whose downstream cycle cannot be re-entered without external correction).
Every other rollback reason (scope_overflow, human_return, diagnose_rollback)
continues in the same invocation so the upstream agent is dispatched with
evidence (FR-11).  A later explicit ``trac run`` continues from the rolled-
back stage/substate.  Crash recovery of a pending ``rollback_stage``
reconciles without emitting a duplicate event and applies the same boundary
rule to the pending command's params (not its kind alone).
"""
from __future__ import annotations

from pathlib import Path

from tests.unit.helpers import (
    git_repo as _repo,
)
from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel import decide, project
from tracks.kernel.events import Command
from tracks.store import Store

_STORE_EVENTS_HUMAN_RETURN = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-STORY"}),
    ("human.return", {"reason": "re-scope", "to_stage": "M-STORY"}),
]

_STORE_EVENTS_SCOPE_OVERFLOW = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-SPEC"}),
    ("verdict.failed", {"check": "scope_overflow", "attempt": 0}),
]

# D-32 / SM-01.12: an M-TEST Shield WRITE stub_gap failed outcome routes to
# DIAGNOSE/stub_gap, from which decide() rolls back to M-DESIGN.
_STORE_EVENTS_STUB_GAP = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-TEST"}),
    ("command.issued", {"command": {
        "kind": "dispatch_agent",
        "params": {"role": "shield", "substate": "WRITE"},
        "command_id": "C1"}}),
    ("outcome.received", {"role": "shield", "status": "failed",
                          "failure_class": "stub_gap",
                          "self_report": "test-task contract invalid"}),
]


def _store_with(repo: Path, events: list, version: str = "v0.1") -> Store:
    store = Store(paths.tracks_home(repo))
    for t, p in events:
        store.append("RUN", version, t, p)
    return store


class _StubBackend:
    def __init__(self, result):
        self.result = result

    def act(self, role, substate, doc, doc_path, assignment=None):
        return dict(self.result)


def _dispatches(events):
    """command.issued dispatch_agent events."""
    return [e for e in events
            if e.type == "command.issued"
            and e.payload["command"]["kind"] == "dispatch_agent"]


def _dispatches_after(events, marker):
    """dispatch_agent command.issued events emitted after ``marker``."""
    return [e for e in _dispatches(events) if e.seq > marker.seq]


def _stub_failed_backend():
    """A failed-outcome backend so the run parks at the dispatch gate instead
    of entering the result pipeline."""
    return _StubBackend({"status": "failed", "failure_class": "agent_failed",
                         "self_report": "nope"})


# -- Non-stub_gap rollbacks continue in the same invocation --------------------

def test_rollback_continues_after_human_return(tmp_path):
    """human_return is NOT a durable boundary: run_loop rolls back to
    M-STORY and continues in the same invocation to dispatch the scribe."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_HUMAN_RETURN)
    ex = Executor(store, repo, "RUN", max_dispatches=1)
    ex.backend = _stub_failed_backend()
    state = ex.run_loop()

    assert state.stage == "M-STORY"
    assert state.substate == "DRAFT"
    assert state.status == "active"

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0].payload["to_stage"] == "M-STORY"
    assert rolled_back[0].payload["reason"] == "human_return"

    dispatches = _dispatches(events)
    assert len(dispatches) == 1
    assert dispatches[0].payload["command"]["params"]["substate"] == "DRAFT"


def test_rollback_continues_after_scope_overflow_with_evidence(tmp_path):
    """scope_overflow (M-SPEC -> M-STORY) is NOT a durable boundary: run_loop
    continues in the same invocation and dispatches Scribe DRAFT carrying the
    overflow evidence (FR-11 / AC-20a)."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_SCOPE_OVERFLOW)
    ex = Executor(store, repo, "RUN", max_dispatches=1)
    ex.backend = _stub_failed_backend()
    state = ex.run_loop()

    assert state.stage == "M-STORY"
    assert state.substate == "DRAFT"
    assert state.status == "active"

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0].payload["to_stage"] == "M-STORY"
    assert rolled_back[0].payload["reason"] == "scope_overflow"

    dispatches = _dispatches(events)
    assert len(dispatches) == 1
    params = dispatches[0].payload["command"]["params"]
    assert params["role"] == "scribe" and params["substate"] == "DRAFT"
    assert params["evidence"]["check"] == "scope_overflow"
    assert params["substate"] != "TRIAGE"


# -- stub_gap is the durable stop boundary ------------------------------------

def test_stub_gap_rollback_returns_at_boundary(tmp_path):
    """rollback_stage(stub_gap) is the only durable stop boundary: run_loop
    returns after stage.rolled_back with no auto-dispatched upstream work."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_STUB_GAP)
    ex = Executor(store, repo, "RUN")
    state = ex.run_loop()

    assert state.stage == "M-DESIGN"
    assert state.substate == "DRAFT"
    assert state.status == "active"

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0].payload["to_stage"] == "M-DESIGN"
    assert rolled_back[0].payload["reason"] == "stub_gap"

    assert len(_dispatches_after(events, rolled_back[0])) == 0


# -- Later explicit run continuation after stub_gap ----------------------------

def test_later_run_continues_from_stub_gap_rollback(tmp_path):
    """After the stub_gap boundary return, a new run_loop invocation
    continues from M-DESIGN (dispatch_agent for the design trio) -- the
    explicit continuation, never an auto-reentry in the same invocation."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_STUB_GAP)

    ex1 = Executor(store, repo, "RUN")
    state_after_rollback = ex1.run_loop()
    assert state_after_rollback.stage == "M-DESIGN"
    assert state_after_rollback.substate == "DRAFT"
    assert state_after_rollback.pending is None

    cmd = decide(state_after_rollback)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"

    ex2 = Executor(store, repo, "RUN", max_dispatches=1)
    ex2.backend = _stub_failed_backend()
    ex2.run_loop()

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    dispatched = _dispatches_after(events, rolled_back[0])
    assert len(dispatched) == 1
    params = dispatched[0].payload["command"]["params"]
    assert params["role"] == "archer" and params["substate"] == "DRAFT"


# -- Pending rollback recovery -------------------------------------------------

def _store_with_pending_rollback(repo: Path) -> Store:
    """Store with a command.issued for rollback_stage(human_return) but NO
    stage.rolled_back event — simulates a crash between issue and result."""
    store = Store(paths.tracks_home(repo))
    cid = "pending-rollback-cid"
    for t, p in _STORE_EVENTS_HUMAN_RETURN:
        store.append("RUN", "v0.1", t, p)
    store.append(
        "RUN", "v0.1", "command.issued",
        {"command": {
            "kind": "rollback_stage",
            "params": {"to_stage": "M-STORY", "reason": "human_return"},
            "command_id": cid,
        }},
        command_id=cid,
    )
    return store


def _store_with_pending_stub_gap_rollback(repo: Path) -> Store:
    """Same crash-window fixture for the stub_gap rollback (M-TEST ->
    M-DESIGN): command.issued present, stage.rolled_back absent."""
    store = Store(paths.tracks_home(repo))
    cid = "pending-stub-gap-cid"
    for t, p in _STORE_EVENTS_STUB_GAP:
        store.append("RUN", "v0.1", t, p)
    store.append(
        "RUN", "v0.1", "command.issued",
        {"command": {
            "kind": "rollback_stage",
            "params": {"to_stage": "M-DESIGN", "reason": "stub_gap"},
            "command_id": cid,
        }},
        command_id=cid,
    )
    return store


def test_pending_stub_gap_recovery_returns_at_boundary(tmp_path):
    """A pending rollback_stage(stub_gap) is reconciled by _recover; run_loop
    returns at the durable boundary with exactly one stage.rolled_back and no
    auto-dispatched upstream work."""
    repo = _repo(tmp_path)
    store = _store_with_pending_stub_gap_rollback(repo)

    state_before = store.state("RUN")
    assert state_before.pending is not None
    assert state_before.pending["kind"] == "rollback_stage"

    ex = Executor(store, repo, "RUN")
    state = ex.run_loop()

    assert state.stage == "M-DESIGN"
    assert state.substate == "DRAFT"
    assert state.status == "active"

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0].payload["to_stage"] == "M-DESIGN"
    assert rolled_back[0].payload["reason"] == "stub_gap"

    assert len(_dispatches_after(events, rolled_back[0])) == 0


def test_pending_human_return_recovery_continues(tmp_path):
    """A pending rollback_stage(human_return) is reconciled by _recover but is
    NOT a durable boundary: run_loop continues in the same invocation and
    issues the rolled-back Scribe dispatch (exactly one stage.rolled_back)."""
    repo = _repo(tmp_path)
    store = _store_with_pending_rollback(repo)

    state_before = store.state("RUN")
    assert state_before.pending is not None
    assert state_before.pending["kind"] == "rollback_stage"

    ex = Executor(store, repo, "RUN", max_dispatches=1)
    ex.backend = _stub_failed_backend()
    state = ex.run_loop()

    assert state.stage == "M-STORY"
    assert state.substate == "DRAFT"
    assert state.status == "active"

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1
    assert rolled_back[0].payload["reason"] == "human_return"

    dispatches = _dispatches(events)
    assert len(dispatches) == 1
    assert dispatches[0].payload["command"]["params"]["substate"] == "DRAFT"


def test_rollback_stage_idempotent_on_reconcile(tmp_path):
    """When stage.rolled_back already exists for a command_id, calling
    _do_rollback_stage with reconcile=True must NOT emit a duplicate."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_HUMAN_RETURN)
    cid = "already-rolled-back-cid"

    store.append(
        "RUN", "v0.1", "command.issued",
        {"command": {
            "kind": "rollback_stage",
            "params": {"to_stage": "M-STORY", "reason": "human_return"},
            "command_id": cid,
        }},
        command_id=cid,
    )
    store.append(
        "RUN", "v0.1", "stage.rolled_back",
        {"from_stage": "M-STORY", "to_stage": "M-STORY",
         "reason": "human_return"},
        command_id=cid,
    )

    ex = Executor(store, repo, "RUN")
    state = store.state("RUN")
    cmd = Command(kind="rollback_stage",
                  params={"to_stage": "M-STORY", "reason": "human_return"},
                  command_id=cid)
    ex._do_rollback_stage(cmd, state, None, reconcile=True)

    events = list(store.events("RUN"))
    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1


# -- Replay/idempotency --------------------------------------------------------

def test_replay_after_rollback_is_deterministic(tmp_path):
    """Replaying the event log after a human_return rollback yields a stable
    state with no pending command and decide() returning the next dispatch."""
    repo = _repo(tmp_path)
    store = _store_with(repo, _STORE_EVENTS_HUMAN_RETURN)
    ex = Executor(store, repo, "RUN", max_dispatches=1)
    ex.backend = _stub_failed_backend()
    ex.run_loop()

    events = list(store.events("RUN"))
    state = project(events)

    assert state.stage == "M-STORY"
    assert state.substate == "DRAFT"
    assert state.status == "active"
    assert state.pending is None

    rolled_back = [e for e in events if e.type == "stage.rolled_back"]
    assert len(rolled_back) == 1

    cmd = decide(state)
    assert cmd is not None
    assert cmd.kind == "dispatch_agent"
