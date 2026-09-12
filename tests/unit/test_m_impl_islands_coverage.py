"""Behavior coverage for the M-IMPL island gate chain (``m_impl_islands``):

demo-registry parsing fallbacks, taskgraph error collection, the ISLAND_2
FULL-ledger routing (FULL_1/FULL_F/FIXED/OPEN/corrupt) and the stale-identity
settlement closure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tracks.executor import m_impl_islands as islands
from tracks.executor.m_impl_islands import MImplIslandsMixin
from tracks.executor.test_select import LedgerCorruptionError


class _Cmd:
    command_id = "CMD-ISLAND"


class _Store:
    def __init__(self, events=()):
        self._events = list(events)

    def events(self, _run_id):
        return list(self._events)


class _Host(MImplIslandsMixin):
    def __init__(self, tmp_path: Path, *, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.version = "v0.8"
        self.store = _Store(events)
        self.emitted: list = []
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)

    def _vdir(self):
        return self._vdir_path

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _emit_gate_failure(self, cmd, **kwargs):
        self.emitted.append(("gate_failed", kwargs))

    def _requirements_baseline_dir(self, state, vdir):
        return None

    def _read_taskgraph(self, tasks_path):
        return "{}", None

    def _taskgraph_errors(self, *_args, **_kwargs):
        return []

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


def test_demo_registry_host_fallbacks(tmp_path, monkeypatch):
    asset = Path(islands.__file__).resolve().parent.parent / "assets" / "demo_host" / "architecture.md"
    original = Path.read_text

    def boom(self, *args, **kwargs):
        if self == asset:
            raise OSError("missing asset")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    assert islands._demo_registry_host() is None
    monkeypatch.undo()

    def broken_table(self, *args, **kwargs):
        if self == asset:
            return "no quality registry here"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_table)
    assert islands._demo_registry_host() is None
    monkeypatch.undo()

    def empty_host(self, *args, **kwargs):
        if self == asset:
            return "[quality_registry]\nhost = \"\"\n[[quality_guard]\n"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", empty_host)
    assert islands._demo_registry_host() is None
    monkeypatch.undo()

    def no_host(self, *args, **kwargs):
        if self == asset:
            return "[quality_registry]\nother = \"x\"\n[[quality_guard]\n"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_host)
    assert islands._demo_registry_host() is None
    monkeypatch.undo()

    monkeypatch.setattr(islands, "_demo_registry_host", lambda: None)
    assert islands._canonical_demo_hosts() == ("tracks",)


def test_island_taskgraph_errors_read_and_parse(tmp_path):
    host = _Host(tmp_path)
    state = SimpleNamespace(taskgraph_digest=None, hotfix_issue=None, hotfix_anchor_acs=[])
    host._read_taskgraph = lambda _path: (None, "unreadable tasks")
    assert host._island_taskgraph_errors(host._vdir_path, host._vdir_path / "tasks.json", state) == [
        "unreadable tasks"
    ]

    host._read_taskgraph = lambda _path: ("{not json", None)
    errors = host._island_taskgraph_errors(
        host._vdir_path, host._vdir_path / "tasks.json", state
    )
    assert errors


def test_island_taskgraph_errors_digest_and_architecture(tmp_path):
    host = _Host(tmp_path)
    raw = '{"schema": 2, "tasks": []}'
    host._read_taskgraph = lambda _path: (raw, None)
    state = SimpleNamespace(
        taskgraph_digest="0" * 64, hotfix_issue=None, hotfix_anchor_acs=[]
    )
    assert host._island_taskgraph_errors(
        host._vdir_path, host._vdir_path / "tasks.json", state
    ) == ["tasks.json changed after taskgraph.committed"]

    state = SimpleNamespace(taskgraph_digest=None, hotfix_issue=None, hotfix_anchor_acs=[])
    errors = host._island_taskgraph_errors(
        host._vdir_path, host._vdir_path / "tasks.json", state
    )
    assert "architecture.md missing" in errors


def _island_state(**overrides):
    base = {
        "version": "v0.8",
        "current_attempt": 0,
        "island_2_passed": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_check_island_2_reconcile_and_reach_failure(tmp_path, monkeypatch):
    host = _Host(tmp_path)
    state = _island_state(island_2_passed=True)
    host._do_check_island_2(_Cmd(), state, None, True)
    assert host.emitted == []

    reach = SimpleNamespace(status="fail", errors=["e1"], islands=["i1"], __str__=lambda self: "REACH")
    monkeypatch.setattr("tracks.checks.reach.check_reach_file", lambda repo: reach)
    host._do_check_island_2(_Cmd(), state, None, False)
    assert host.emitted[0][0] == "verdict.failed"
    assert "reach check failed" in host.emitted[0][1]["reason"]
    assert len(host.emitted) == 1


def test_check_island_2_ledger_routing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    state = _island_state()
    done = SimpleNamespace(seq=1, type="full.executed", payload={"passed": True})

    host = _Host(tmp_path)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {})
    host._do_check_island_2(_Cmd(), state, None, False)
    assert ("full", "FULL_1") in host.emitted

    host = _Host(tmp_path, events=[done])
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "OPEN"})
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    host._do_check_island_2(_Cmd(), state, None, False)
    assert host.emitted == [("settle", {"n": "OPEN"})]

    host = _Host(tmp_path, events=[done])
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: True)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "CLASSIFIED"})
    host._do_check_island_2(_Cmd(), state, None, False)
    assert ("full", "FULL_F") in host.emitted
    assert ("passed", False) in host.emitted

    host = _Host(tmp_path, events=[done])
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "FIXED"})
    host._do_check_island_2(_Cmd(), state, None, False)
    assert host.emitted == [("prove_fixed", {"n": "FIXED"})]


def test_check_island_2_corrupt_ledger_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    done = SimpleNamespace(seq=1, type="full.executed", payload={"passed": True})
    host = _Host(tmp_path, events=[done])
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "STALE"})
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    host._do_check_island_2(_Cmd(), _island_state(), None, False)
    assert host.emitted[0][0] == "gate_failed"
    assert "LedgerCorruptionError" in host.emitted[0][1]["reason"]


def test_check_island_2_full_failure_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    host = _Host(tmp_path)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {})
    host._execute_full_round = lambda *_a: {
        "failed_nodes": ["tests/unit/test_a.py::t"],
        "failures": [],
        "evidence_by_node": {},
        "outcomes_ref": "ref",
    }
    host._do_check_island_2(_Cmd(), _island_state(), None, False)
    assert host.emitted[0][0] == "record_failures"
    assert host.emitted[1][0] == "verdict.failed"
    assert "FULL chain failures" in host.emitted[1][1]["reason"]


def test_check_island_2_full_reconcile_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tracks.checks.reach.check_reach_file",
        lambda repo: SimpleNamespace(status="pass", errors=[], islands=[]),
    )
    full_row = SimpleNamespace(payload={"passed": False, "failed_nodes": ["n1"]})
    host = _Host(
        tmp_path,
        events=[
            SimpleNamespace(seq=1, type="full.executed", payload=full_row.payload)
        ],
    )
    host._waived_nodes = lambda failing: set(failing)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {})
    reconciled: list = []
    host._reconcile_full_failure_wal = lambda cmd, state, row, ledger: reconciled.append(row)
    host._do_check_island_2(_Cmd(), _island_state(), None, False)
    assert reconciled
    assert ("full", "FULL_F") in host.emitted


def test_empty_ledger_round_name_corrupt_and_waived(tmp_path):
    host = _Host(tmp_path)
    rows = [SimpleNamespace(payload={"passed": False, "failed_nodes": ["n1"]})]
    try:
        host._empty_ledger_round_name(rows)
    except LedgerCorruptionError as exc:
        assert "opened no ledger identities" in str(exc)
    else:  # pragma: no cover - explicit guard
        raise AssertionError("expected LedgerCorruptionError")

    host._waived_nodes = lambda failing: set(failing)
    assert host._empty_ledger_round_name(rows) == "FULL_F"
    passed = [SimpleNamespace(payload={"passed": True, "failed_nodes": []})]
    assert host._empty_ledger_round_name(passed) == "FULL_F"


def test_settle_stale_island_2_open_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        islands,
        "settle_stale_identities",
        lambda ledger, evidence, failures: [
            {"payload": {"from_state": "OPEN", "to_state": "FIXED"}}
        ],
    )
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "OPEN"})
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: True)
    host = _Host(tmp_path)
    host._execute_full_round = lambda *_a: {
        "failed_nodes": [],
        "failures": [],
        "evidence_by_node": {},
        "outcomes_ref": "ref",
    }
    MImplIslandsMixin._settle_stale_island_2_open(host, _Cmd(), _island_state(), {"n": "OPEN"})
    transitions = [e for e in host.emitted if e[0] == "ledger.transitioned"]
    assert transitions
    assert transitions[0][1]["actor"] == "runtime"
    assert ("passed", False) in host.emitted

    host = _Host(tmp_path)
    host._execute_full_round = lambda *_a: {
        "failed_nodes": ["tests/unit/x.py::t"],
        "failures": [],
        "evidence_by_node": {},
        "outcomes_ref": "ref",
    }
    MImplIslandsMixin._settle_stale_island_2_open(host, _Cmd(), _island_state(), {"n": "OPEN"})
    assert host.emitted[-1][0] == "verdict.failed"
    assert "settlement round failures" in host.emitted[-1][1]["reason"]


def test_settle_stale_island_2_open_fixed_and_unclean(tmp_path, monkeypatch):
    monkeypatch.setattr(islands, "settle_stale_identities", lambda *a: [])
    host = _Host(tmp_path)
    host._execute_full_round = lambda *_a: {
        "failed_nodes": [],
        "failures": [],
        "evidence_by_node": {},
        "outcomes_ref": "ref",
    }
    monkeypatch.setattr(islands, "ledger_is_clean", lambda ledger: False)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "FIXED"})
    MImplIslandsMixin._settle_stale_island_2_open(
        host, _Cmd(), _island_state(), {"n": "OPEN"}
    )
    assert host.emitted[-1][0] == "prove_fallback"

    host = _Host(tmp_path)
    monkeypatch.setattr(islands, "rebuild_ledger", lambda _events: {"n": "CLASSIFIED"})
    MImplIslandsMixin._settle_stale_island_2_open(
        host, _Cmd(), _island_state(), {"n": "OPEN"}
    )
    assert host.emitted[-1][0] == "verdict.failed"
    assert "left the FULL ledger unclean" in host.emitted[-1][1]["reason"]
