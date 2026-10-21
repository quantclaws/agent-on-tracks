"""R1/R2/R3 runtime hotfix acceptance (run 01M4HS35E0S9NYV0NB3NTW05H0).

R1 m_impl_state._on_task_completed: a retained task's task.completed must
    still advance the machine (TASK_DISPATCH / ISLAND_GATE_2 + residency
    clearing) without double-counting, so decide() stops re-issuing the
    same verify_task.
R2 m_impl_ledger._do_complete_task: the repair-family flip branch
    (CLASSIFIED->FIXED) must also land the task's task.completed once, so
    the task stops blocking select_task. No double-emit.
R3 m_impl_islands._do_check_island_2: the settlement branch must be
    reachable for an all-CLASSIFIED ledger (no OPEN/FIXED) on an all-green
    round; genuinely unsettleable states (STALE-only) stay fail-closed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor import m_impl_islands as islands
from tracks.executor.m_impl_islands import MImplIslandsMixin
from tracks.executor.m_impl_ledger import MImplLedgerMixin
from tracks.executor.taskgraph_parse import TaskNode
from tracks.executor.test_select import (
    ledger_is_clean,
    rebuild_ledger,
    settle_stale_identities,
)
from tracks.kernel import State, decide
from tracks.kernel.events import Command
from tracks.kernel.m_impl_state import _on_task_completed


def _ev(seq, type, payload=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {})


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="ledger_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# R1: retained-task completion echo advances the machine
# ---------------------------------------------------------------------------


def _r1_verification_state(**overrides):
    base = {
        "run_id": "RUN",
        "stage": "M-IMPL",
        "substate": "RED",
        "current_task_id": "T-001",
        "current_task_metadata": {
            "task_id": "T-001",
            "description": "verification-only 验收闭口(T-001)",
        },
        "retained_completed_task_ids": ["T-001"],
        "tasks_total": 2,
        "tasks_completed": 1,
    }
    base.update(overrides)
    return State(**base)


def test_r1_retained_completion_advances_without_double_count():
    s = _r1_verification_state()
    before = decide(s)
    assert before is not None and before.kind == "verify_task"
    assert before.params["task_id"] == "T-001"

    _on_task_completed(s, {"task_id": "T-001"}, None)

    assert s.tasks_completed == 1  # no double count
    assert s.substate == "TASK_DISPATCH"
    assert s.current_task_id is None
    assert s.current_task_metadata is None
    assert s.current_manifest is None
    assert s.green_committed is False
    assert s.refactor_done is False
    assert s.r_tree_identity is None
    after = decide(s)
    assert after is not None and after.kind != "verify_task"
    assert after.kind == "select_task"


def test_r1_retained_completion_routes_to_island_gate_2_when_done():
    s = _r1_verification_state(
        retained_completed_task_ids=["T-001", "T-002"],
        tasks_total=2,
        tasks_completed=2,
    )
    _on_task_completed(s, {"task_id": "T-002"}, None)
    assert s.tasks_completed == 2
    assert s.substate == "ISLAND_GATE_2"
    assert s.current_task_id is None


# ---------------------------------------------------------------------------
# R2: repair-family flip must not swallow task completion
# ---------------------------------------------------------------------------


class _LedgerStore:
    def __init__(self, events=(), state=None):
        self._events = list(events)
        self._state = state or State(run_id="RUN")

    def events(self, run_id):
        return list(self._events)

    def state(self, run_id):
        return self._state


class _LedgerHost(MImplLedgerMixin):
    def __init__(self, tmp_path: Path, *, events=(), state=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _LedgerStore(events, state)
        self.emitted: list[tuple] = []
        self.home = tmp_path / ".tracks"
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.projection_rebuilds = 0

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _rebuild_task_log_projection(self):
        self.projection_rebuilds += 1

    def _task_node(self, raw: dict) -> TaskNode:
        return TaskNode(
            task_id=raw["task_id"],
            issue_number=raw.get("issue_number", 1),
            description=raw.get("description", ""),
            ac_refs=tuple(raw.get("ac_refs", ())),
            fr_refs=tuple(raw.get("fr_refs", ())),
            if_ids=tuple(raw.get("if_ids", ())),
            test_refs=tuple(raw.get("test_refs", ())),
            scope_boundary=raw.get("scope_boundary", ""),
            depends_on=tuple(raw.get("depends_on", ())),
            batch=raw.get("batch", "1"),
            parallel=raw.get("parallel", False),
            budget=raw.get("budget", 3),
        )


def _flip_host(tmp_path: Path, *, events, state, flipped: bool) -> _LedgerHost:
    host = _LedgerHost(tmp_path, events=events, state=state)
    host._transition_full_ledger = lambda *a, **k: flipped
    return host


def test_r2_flip_branch_lands_task_completed_once(tmp_path: Path):
    state = State(run_id="RUN", full_chain_round="FULL_1", writelock_held=True)
    host = _flip_host(
        tmp_path,
        events=[_ev(1, "taskgraph.committed", {})],
        state=state,
        flipped=True,
    )
    host._do_complete_task(_cmd("C-1", task_id="T-010"), state, "T-010", False)
    kinds = [e[0] for e in host.emitted]
    assert kinds == ["task.completed", "writelock.released"]
    assert host.emitted[0][1] == {"task_id": "T-010"}
    assert host.projection_rebuilds == 1


def test_r2_flip_branch_does_not_double_emit(tmp_path: Path):
    state = State(run_id="RUN", full_chain_round="FULL_1", writelock_held=True)
    host = _flip_host(
        tmp_path,
        events=[
            _ev(1, "taskgraph.committed", {}),
            _ev(2, "task.completed", {"task_id": "T-010"}),
        ],
        state=state,
        flipped=True,
    )
    host._do_complete_task(_cmd("C-2", task_id="T-010"), state, "T-010", False)
    assert [e[0] for e in host.emitted] == ["writelock.released"]


def test_r2_flip_branch_skips_completed_echo_for_retained(tmp_path: Path):
    state = State(
        run_id="RUN",
        full_chain_round="FULL_1",
        writelock_held=True,
        retained_completed_task_ids=["T-010"],
    )
    host = _flip_host(
        tmp_path, events=[_ev(1, "taskgraph.committed", {})], state=state, flipped=True
    )
    host._do_complete_task(_cmd("C-3", task_id="T-010"), state, "T-010", False)
    assert [e[0] for e in host.emitted] == ["writelock.released"]


def test_r2_landed_completion_unblocks_select_task(tmp_path: Path):
    """T-010 started-not-completed blocks select_task; once the flip's
    task.completed lands, the same select proceeds to start (not silent)."""
    refs = [{"task_id": "T-010", "scope_boundary": "a", "depends_on": []}]
    base_events = [
        _ev(1, "stage.entered", {"stage": "M-IMPL"}),
        _ev(2, "task.started", {"task_id": "T-010"}),
    ]
    host = _LedgerHost(
        tmp_path,
        events=list(base_events),
        state=State(run_id="RUN", task_refs=list(refs)),
    )
    host._task_manifest = lambda task, state: {"task_id": task.task_id}
    host._start_task = lambda cmd, chosen, manifest: host.emitted.append(
        ("started", {"task_id": chosen.task_id}, {})
    )
    host._do_select_task(_cmd("C-S1"), host.store.state("RUN"), "T", False)
    assert host.emitted == []  # blocked: started_this_cycle - completed = {T-010}

    host.store = _LedgerStore(
        [*base_events, _ev(3, "task.completed", {"task_id": "T-010"})],
        State(
            run_id="RUN",
            task_refs=[
                {"task_id": "T-010", "scope_boundary": "a", "depends_on": []},
                {"task_id": "T-011", "scope_boundary": "a", "depends_on": []},
            ],
        ),
    )
    host._do_select_task(_cmd("C-S2"), host.store.state("RUN"), "T", False)
    assert host.emitted and host.emitted[0][0] == "started"
    assert host.emitted[0][1]["task_id"] == "T-011"


# ---------------------------------------------------------------------------
# R3: all-CLASSIFIED settlement reachability
# ---------------------------------------------------------------------------


class _IslandCmd:
    command_id = "CMD-ISLAND"


class _IslandStore:
    def __init__(self, events=()):
        self._events = list(events)

    def events(self, _run_id):
        return list(self._events)


class _IslandHost(MImplIslandsMixin):
    def __init__(self, tmp_path: Path, *, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.version = "v0.8"
        self.store = _IslandStore(events)
        self.emitted: list = []
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)

    def _vdir(self):
        return self._vdir_path

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _emit_gate_failure(self, cmd, **kwargs):
        self.emitted.append(("gate_failed", kwargs))

    def _pass_island_2(self, cmd, state, replay=False):
        self.emitted.append(("passed", replay))

    def _prove_fixed_ledger_entries(self, cmd, state, ledger):
        self.emitted.append(("prove_fixed", ledger))

    def _prove_fixed_via_fallback(self, cmd, state, ledger):
        self.emitted.append(("prove_fallback", ledger))

    def _settle_stale_island_2_open(self, cmd, state, ledger):
        self.emitted.append(("settle", ledger))

    def _execute_full_round(self, cmd, state, round_name, ledger):
        self.emitted.append(("full", round_name))
        return {
            "failed_nodes": [],
            "failures": [],
            "evidence_by_node": {},
            "outcomes_ref": "ref",
        }

    def _record_full_failures(self, cmd, state, full, ledger):
        self.emitted.append(("record_failures", full))

    def _waived_nodes(self, failing):
        return set()

    def _known_red_nodes(self):
        return set()


def _island_state(**overrides):
    base = {"version": "v0.8", "current_attempt": 0, "island_2_passed": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_r3_all_classified_ledger_routes_to_settlement(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    done = SimpleNamespace(seq=1, type="full.executed", payload={"passed": True})
    host = _IslandHost(tmp_path, events=[done])
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "CLASSIFIED"})
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    host._do_check_island_2(_IslandCmd(), _island_state(), None, False)
    assert host.emitted == [("settle", {"n": "CLASSIFIED"})]


def test_r3_stale_only_ledger_stays_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    done = SimpleNamespace(seq=1, type="full.executed", payload={"passed": True})
    host = _IslandHost(tmp_path, events=[done])
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "STALE"})
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    host._do_check_island_2(_IslandCmd(), _island_state(), None, False)
    assert host.emitted[0][0] == "gate_failed"
    assert "LedgerCorruptionError" in host.emitted[0][1]["reason"]


def test_r3_all_classified_all_green_settles_to_proven():
    """Ledger-protocol integration: OPEN->CLASSIFIED history plus an
    all-green round settles CLASSIFIED->FIXED, and the fallback proof
    FIXED->PROVEN leaves a clean (all-PROVEN) ledger."""
    node, sig = "tests/unit/a.py::t", "sig-1"
    opened = {
        "seq": 1,
        "type": "ledger.opened",
        "payload": {"node": node, "failure_signature": sig, "state": "OPEN"},
    }
    classified = {
        "seq": 2,
        "type": "ledger.transitioned",
        "payload": {"node": node, "failure_signature": sig, "from": "OPEN", "to": "CLASSIFIED"},
    }
    assert rebuild_ledger([opened, classified]) != {}
    assert not ledger_is_clean(rebuild_ledger([opened, classified]))

    ledger = rebuild_ledger([opened, classified])
    settlement = settle_stale_identities(ledger, [node], [])
    assert len(settlement) == 1
    assert settlement[0]["payload"]["from"] == "CLASSIFIED"
    assert settlement[0]["payload"]["to"] == "FIXED"

    fixed = rebuild_ledger(
        [opened, classified, {"seq": 3, **settlement[0]}]
    )
    assert list(fixed.values()) == ["FIXED"]

    proven = rebuild_ledger(
        [
            opened,
            classified,
            {"seq": 3, **settlement[0]},
            {
                "seq": 4,
                "type": "ledger.transitioned",
                "payload": {
                    "node": node,
                    "failure_signature": sig,
                    "from": "FIXED",
                    "to": "PROVEN",
                    "reason": "fallback FULL proof result",
                },
            },
        ]
    )
    assert ledger_is_clean(proven)
