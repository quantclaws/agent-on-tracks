"""Behavior coverage for the M-IMPL ledger mixin (``MImplLedgerMixin``).

Drives baseline freeze/scenario binding, taskgraph commit gates (B33/B50/B89),
anchor-surface satisfiability, retained completions, task selection and the
writelock lease recovery WAL directly through a bare mixin host (FR-0150,
FR-0245, FR-0286).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_ledger as ledger
from tracks.executor.m_impl_ledger import (
    MImplLedgerMixin,
    _anchor_ids,
    _anchor_set_digest,
    _batch_key,
    _scenario_bound_digest,
)
from tracks.executor.taskgraph_parse import TaskNode
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _Store:
    def __init__(self, events=(), state=None):
        self._events = list(events)
        self._state = state or State(run_id="RUN")

    def events(self, run_id):
        return list(self._events)

    def state(self, run_id):
        return self._state


class _Host(MImplLedgerMixin):
    def __init__(self, tmp_path: Path, *, events=(), state=None):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(events, state)
        self.emitted: list[tuple] = []
        self.issued: list[tuple] = []
        self.home = tmp_path / ".tracks"
        self.store.home = self.home
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.unit_run = "pytest"
        self.projection_rebuilds = 0

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def issue(self, cmd, command_id=None):
        self.issued.append((cmd, command_id))

    def _vdir(self) -> Path:
        return self._vdir_path

    def _frozen_test_paths(self) -> list[str]:
        return ["tests/unit"]

    def _approval_digest(self) -> str:
        return "APPROVAL"

    def _issue_evidence(self) -> str:
        return "ISSUES"

    def _dirty_tree_stamp(self) -> str:
        return "TREE"

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
            unit_refs=tuple(raw.get("unit_refs", ())),
            acceptance_refs=tuple(raw.get("acceptance_refs", ())),
            schema=raw.get("schema", 2),
            deferred_refs=tuple(raw.get("deferred_refs", ())),
            integration=raw.get("integration", False),
        )

    def _rebuild_task_log_projection(self):
        self.projection_rebuilds += 1

    def _transition_full_ledger(self, *args):
        return False


def _task(**kwargs) -> TaskNode:
    base = {
        "task_id": "T-1",
        "issue_number": 1,
        "description": "d",
        "ac_refs": ("AC-FR0001-01",),
        "fr_refs": (),
        "if_ids": (),
        "test_refs": (),
        "scope_boundary": "tracks/app.py",
        "depends_on": (),
        "batch": "1",
        "parallel": False,
        "budget": 3,
    }
    base.update(kwargs)
    return TaskNode(**base)


def _ev(seq, type, payload=None, command_id=None):
    return SimpleNamespace(seq=seq, type=type, payload=payload or {}, command_id=command_id)


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="ledger_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------


def test_anchor_ids_prefers_acceptance_and_dedupes():
    t1 = SimpleNamespace(acceptance_refs=("tests/integration/a.py::t1",), test_refs=("ignored",))
    t2 = SimpleNamespace(acceptance_refs=None, test_refs=("tests/integration/a.py::t1", "tests/e2e/b.py::t2", 3))
    assert _anchor_ids([t1, t2]) == ["tests/e2e/b.py::t2", "tests/integration/a.py::t1"]


def test_anchor_set_digest_is_order_insensitive():
    a = _anchor_set_digest(["b", "a"])
    b = _anchor_set_digest(["a", "b"])
    assert a == b == hashlib.sha256("\n".join(["a", "b"]).encode()).hexdigest()


def test_batch_key_numeric_and_symbolic():
    assert _batch_key("3") == (0, "00000000000000000003")
    assert _batch_key("phase-a") == (1, "phase-a")
    assert _batch_key(None) == (1, "None")


def test_scenario_bound_digest_folds_head():
    assert _scenario_bound_digest("D", None) == "D"
    bound = _scenario_bound_digest("D", "HEAD")
    assert bound != "D"
    assert bound == hashlib.sha256(b"digest:D\nscenario_branch_head:HEAD").hexdigest()


def test_taskgraph_structure_and_coverage_errors():
    cyclic = [
        _task(task_id="T-1", depends_on=("T-2",), scope_boundary="a"),
        _task(task_id="T-2", depends_on=("T-1",), scope_boundary="a"),
    ]
    errors = ledger._taskgraph_structure_errors(cyclic)
    assert any("DAG" in e or "cycle" in e.lower() for e in errors) or errors
    coverage_errors = ledger._taskgraph_coverage_errors(
        [_task(ac_refs=("AC-FR9999-01",))], ["AC-FR0001-01"], set()
    )
    assert coverage_errors


# ---------------------------------------------------------------------------
# baseline inputs
# ---------------------------------------------------------------------------


def test_requirements_baseline_dir(tmp_path: Path):
    host = _Host(tmp_path)
    plain = State(run_id="RUN")
    assert host._requirements_baseline_dir(plain, host._vdir()) == host._vdir()
    hotfix = State(run_id="RUN", hotfix_issue=7, hotfix_target_version="v0.5")
    expected = host.store.home / "projects" / "v0.5"
    assert host._requirements_baseline_dir(hotfix, host._vdir()) == expected


def test_design_checkpoint_sha_only_inside_design_window(tmp_path: Path):
    events = [
        _ev(1, "result.checkpointed", {"commit_sha": "outside-before"}),
        _ev(2, "stage.entered", {"stage": "M-DESIGN"}),
        _ev(3, "result.checkpointed", {"commit_sha": "inside"}),
        _ev(4, "result.checkpointed", {"commit_sha": "  "}),
        _ev(5, "stage.exited", {"stage": "M-DESIGN"}),
        _ev(6, "result.checkpointed", {"commit_sha": "outside-after"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._design_checkpoint_sha() == "inside"
    host.store = _Store(events=[e for e in events if e.type != "result.checkpointed"])
    assert host._design_checkpoint_sha() == ""


def test_freeze_scenario_head_branches(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    assert host._freeze_scenario_head(State(run_id="RUN")) is None
    assert (
        host._freeze_scenario_head(
            State(run_id="RUN", hotfix_issue=7, hotfix_scenario="post-release")
        )
        is None
    )
    host.store = _Store([_ev(1, "branch.created", {"base": "main"})])
    assert (
        host._freeze_scenario_head(
            State(run_id="RUN", hotfix_issue=7, hotfix_scenario="dev")
        )
        is None
    )
    host.store = _Store([_ev(1, "branch.created", {"base": "releases/v0.5"})])
    monkeypatch.setattr(ledger, "git", lambda *a, **k: SimpleNamespace(stdout="HEAD-SHA\n"))
    assert (
        host._freeze_scenario_head(
            State(run_id="RUN", hotfix_issue=7, hotfix_scenario="dev")
        )
        == "HEAD-SHA"
    )
    monkeypatch.setattr(ledger, "git", lambda *a, **k: SimpleNamespace(stdout="\n"))
    assert (
        host._freeze_scenario_head(
            State(run_id="RUN", hotfix_issue=7, hotfix_scenario="dev")
        )
        is None
    )


def test_do_freeze_baseline_reconcile_and_emit(tmp_path: Path):
    host = _Host(tmp_path)
    host._baseline_frozen_payload = lambda state: {"status": "current", "digest": "D"}
    host._do_freeze_baseline(_cmd("C-1"), State(baseline_frozen=True), "T", reconcile=True)
    assert host.emitted == []
    host._do_freeze_baseline(_cmd("C-1"), State(), "T", reconcile=False)
    assert host.emitted == [("baseline.frozen", {"status": "current", "digest": "D"}, {"command_id": "C-1"})]


def test_baseline_frozen_payload_current_missing_and_advanced(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(ledger, "git", lambda repo, *a, **k: SimpleNamespace(stdout="main\n"))
    monkeypatch.setattr(ledger, "m_impl_baseline_digest", lambda *a, **k: "D")
    monkeypatch.setattr(ledger, "m_impl_baseline_missing", lambda *a, **k: [])
    monkeypatch.setattr(ledger, "m_impl_baseline_summary", lambda *a, **k: "SUMMARY")
    monkeypatch.setattr(ledger, "load_contract", lambda repo: object())
    payload = host._baseline_frozen_payload(State(run_id="RUN"))
    assert payload["status"] == "current"
    assert payload["digest"] == "D"
    assert payload["summary"] == "SUMMARY"

    monkeypatch.setattr(ledger, "m_impl_baseline_missing", lambda *a, **k: ["missing-doc"])
    assert host._baseline_frozen_payload(State(run_id="RUN"))["status"] == "stale"

    monkeypatch.setattr(ledger, "m_impl_baseline_missing", lambda *a, **k: [])
    host._last_baseline_digest = lambda: "OLD"
    advanced = host._baseline_frozen_payload(State(run_id="RUN"))
    assert advanced["status"] == "stale"


def test_baseline_frozen_payload_dev_scenario_requires_branch_head(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(ledger, "git", lambda repo, *a, **k: SimpleNamespace(stdout="main\n"))
    monkeypatch.setattr(ledger, "m_impl_baseline_digest", lambda *a, **k: "D")
    monkeypatch.setattr(ledger, "m_impl_baseline_missing", lambda *a, **k: [])
    monkeypatch.setattr(ledger, "m_impl_baseline_summary", lambda *a, **k: "S")
    monkeypatch.setattr(ledger, "load_contract", lambda repo: object())
    host._freeze_scenario_head = lambda state: None
    host._last_baseline_digest = lambda: ""
    payload = host._baseline_frozen_payload(
        State(run_id="RUN", hotfix_issue=7, hotfix_scenario="dev")
    )
    assert payload["status"] == "stale"
    assert "scenario_branch_head" in payload["missing"]


def test_baseline_frozen_payload_contract_invalid(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(ledger, "git", lambda repo, *a, **k: SimpleNamespace(stdout="main\n"))
    monkeypatch.setattr(ledger, "m_impl_baseline_digest", lambda *a, **k: "D")
    captured = {}

    def _missing(*a, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ledger, "m_impl_baseline_missing", _missing)
    monkeypatch.setattr(ledger, "m_impl_baseline_summary", lambda *a, **k: "S")

    def _boom(repo):
        raise ledger.ContractError("no contract")

    monkeypatch.setattr(ledger, "load_contract", _boom)
    assert host._baseline_frozen_payload(State(run_id="RUN"))["status"] == "current"
    assert captured["contract_valid"] is False


# ---------------------------------------------------------------------------
# commit taskgraph
# ---------------------------------------------------------------------------


def test_do_commit_taskgraph_reconcile_and_read_errors(tmp_path: Path):
    host = _Host(tmp_path)
    host._do_commit_taskgraph(_cmd(), State(taskgraph_committed=True), "T", reconcile=True)
    assert host.projection_rebuilds == 1
    assert host.emitted == []

    host._read_taskgraph = lambda path: ("", "tasks.json not found")
    host._emit_taskgraph_failure = lambda cmd, state, reason, evidence="": host.emitted.append(
        ("failure", {"reason": reason, "evidence": evidence}, {})
    )
    host._do_commit_taskgraph(_cmd(), State(), "T", reconcile=False)
    assert host.emitted[0][1]["reason"] == "tasks.json not found"

    host._read_taskgraph = lambda path: ("raw", None)
    host2 = _Host(tmp_path)
    host2._read_taskgraph = lambda path: ("raw", None)
    host2._emit_taskgraph_failure = lambda cmd, state, reason, evidence="": host2.emitted.append(
        ("failure", {"reason": reason, "evidence": evidence}, {})
    )
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ledger, "parse_tasks_json", lambda raw: ([], "parse error"))
    host2._do_commit_taskgraph(_cmd(), State(), "T", reconcile=False)
    assert host2.emitted[0][1]["reason"] == "parse error"
    monkeypatch.undo()


def test_do_commit_taskgraph_error_and_probe_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._read_taskgraph = lambda path: ("{}", None)
    monkeypatch.setattr(ledger, "parse_tasks_json", lambda raw: ([_task()], None))
    host._taskgraph_errors = lambda *a: ["structure error"]
    monkeypatch.setattr(ledger, "validate_acceptance_coverage", lambda tasks, plan: (True, []))
    host._emit_taskgraph_failure = lambda cmd, state, reason, evidence="": host.emitted.append(
        ("failure", {"reason": reason, "evidence": evidence}, {})
    )
    host._do_commit_taskgraph(_cmd(), State(), "T", reconcile=False)
    assert host.emitted[0][1]["reason"] == "structure error"

    host2 = _Host(tmp_path)
    host2._read_taskgraph = lambda path: ("{}", None)
    monkeypatch.setattr(ledger, "parse_tasks_json", lambda raw: ([_task()], None))
    host2._taskgraph_errors = lambda *a: []
    monkeypatch.setattr(ledger, "validate_acceptance_coverage", lambda tasks, plan: (False, ["anchor missing"]))
    host2._b89_planning_gate = lambda *a: ({"violations": []}, [])
    host2._emit_taskgraph_failure = lambda cmd, state, reason, evidence="": host2.emitted.append(
        ("failure", {"reason": reason, "evidence": evidence}, {})
    )
    host2._do_commit_taskgraph(_cmd(), State(), "T", reconcile=False)
    assert host2.emitted[0][1]["reason"] == "anchor missing"

    host3 = _Host(tmp_path)
    host3._read_taskgraph = lambda path: ("{}", None)
    monkeypatch.setattr(ledger, "parse_tasks_json", lambda raw: ([_task()], None))
    host3._taskgraph_errors = lambda *a: []
    monkeypatch.setattr(ledger, "validate_acceptance_coverage", lambda tasks, plan: (True, []))
    host3._b89_planning_gate = lambda *a: ({"violations": []}, [])
    host3._probe_anchor_types = lambda tasks: SimpleNamespace(errors=["anchor red"], advisory=lambda: [])
    host3._emit_taskgraph_failure = lambda cmd, state, reason, evidence="": host3.emitted.append(
        ("failure", {"reason": reason, "evidence": evidence}, {})
    )
    host3._do_commit_taskgraph(_cmd(), State(), "T", reconcile=False)
    assert host3.emitted[0][1]["reason"] == "anchor red"


def test_do_commit_taskgraph_success_payload(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._read_taskgraph = lambda path: ("{}", None)
    monkeypatch.setattr(ledger, "parse_tasks_json", lambda raw: ([_task()], None))
    host._taskgraph_errors = lambda *a: []
    monkeypatch.setattr(ledger, "validate_acceptance_coverage", lambda tasks, plan: (True, []))
    host._b89_planning_gate = lambda *a: ({"violations": [], "advisories": []}, ["adv"])
    host._probe_anchor_types = lambda tasks: SimpleNamespace(
        errors=[], advisory=lambda: ["[taskgraph] advisory line"]
    )
    monkeypatch.setattr(ledger, "probe_summary", lambda report: {"probe": True})
    host._tasks_md = lambda tasks: "# Tasks\n"
    host._retained_completed_ids = lambda tasks: ["T-0"]
    host._scope_anchor_advisories = lambda tasks, vdir: ["scope debt"]
    host._do_commit_taskgraph(_cmd("C-9"), State(), "T", reconcile=False)
    event, payload, kwargs = host.emitted[0]
    assert event == "taskgraph.committed"
    assert kwargs == {"command_id": "C-9"}
    assert payload["task_count"] == 1
    assert payload["anchor_probe"]["probe"] is True
    assert payload["anchor_probe"]["satisfiability"] == {"violations": [], "advisories": []}
    assert payload["scope_anchor_advisory"] == ["scope debt"]
    assert payload["retained_completed_task_ids"] == ["T-0"]
    assert (host._vdir() / "tasks.md").read_text(encoding="utf-8") == "# Tasks\n"
    assert host.projection_rebuilds == 1


def test_retained_completed_ids_uses_event_history(tmp_path: Path):
    task = _task(task_id="T-1")
    old_payload = MImplLedgerMixin._task_payload(task)
    events = [
        _ev(1, "taskgraph.committed", {"tasks": [old_payload]}),
        _ev(2, "task.completed", {"task_id": "T-1"}),
        _ev(3, "task.completed", {"task_id": "T-9"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._retained_completed_ids([task]) == ["T-1"]
    host.store = _Store([])
    assert host._retained_completed_ids([_task()]) == []


def test_scope_anchor_advisories_swallows_missing_surface(tmp_path: Path):
    host = _Host(tmp_path)
    assert host._scope_anchor_advisories([_task()], host._vdir()) == []


# ---------------------------------------------------------------------------
# b89 planning gate
# ---------------------------------------------------------------------------


def test_b89_planning_gate_legacy_skips(tmp_path: Path):
    host = _Host(tmp_path)
    satisfiability, advisories = host._b89_planning_gate(
        [_task(schema=1)], host._vdir(), "plan", []
    )
    assert satisfiability["skipped"] == "schema-1"
    assert advisories == []


def test_b89_scope_existence_errors_and_infra(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    errors: list = []
    monkeypatch.setattr(ledger, "validate_scope_existence", lambda *a, **k: (False, ["scope gone"]))
    host._b89_scope_existence([_task()], host._vdir(), "plan", errors)
    assert errors == ["scope gone"]
    monkeypatch.setattr(
        ledger, "validate_scope_existence", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("infra"))
    )
    infra_errors: list = []
    host._b89_scope_existence([_task()], host._vdir(), "plan", infra_errors)
    assert infra_errors and infra_errors[0].startswith("scope existence infra failure")


def test_b89_satisfiability_violations_skip_and_infra(tmp_path: Path):
    host = _Host(tmp_path)
    host._evaluate_satisfiability = lambda tasks, vdir: (["v1"], ["a1"], None)
    errors: list = []
    satisfiability, advisories = host._b89_satisfiability([_task()], host._vdir(), errors)
    assert satisfiability == {"violations": ["v1"], "advisories": ["a1"]}
    assert errors == ["v1"]
    assert advisories == ["a1"]

    host._evaluate_satisfiability = lambda tasks, vdir: ([], ["a2"], "skipped-infra")
    satisfiability, advisories = host._b89_satisfiability([_task()], host._vdir(), [])
    assert satisfiability["skipped"] == "skipped-infra"

    host._evaluate_satisfiability = lambda tasks, vdir: (_ for _ in ()).throw(RuntimeError("boom"))
    satisfiability, advisories = host._b89_satisfiability([_task()], host._vdir(), [])
    assert satisfiability["skipped"].startswith("infra:")
    assert advisories == []


def test_probe_anchor_types_missing_contract(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        ledger, "load_contract", lambda repo: (_ for _ in ()).throw(ledger.ContractError("no"))
    )
    report = host._probe_anchor_types([_task()])
    assert report.skipped_reason
    monkeypatch.setattr(ledger, "load_contract", lambda repo: object())
    monkeypatch.setattr(ledger, "probe_task_anchors", lambda repo, tasks, contract: "probed")
    assert host._probe_anchor_types([_task()]) == "probed"


# ---------------------------------------------------------------------------
# anchor surface freshness / satisfiability
# ---------------------------------------------------------------------------


def test_current_tree_stamp_falls_back_to_surface(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)

    def _boom():
        raise RuntimeError("no stamp")

    host._dirty_tree_stamp = _boom
    monkeypatch.setattr("tracks.executor.anchor_surface._dirty_tree_stamp", lambda repo: "SURFACE")
    assert host._current_tree_stamp(host.repo) == "SURFACE"


def test_fresh_anchor_surface_validates_schema_digest_tree(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    path = host._vdir() / "anchor-surface.json"
    monkeypatch.setattr(
        "tracks.executor.anchor_surface.load_anchor_surface",
        lambda p: {"schema": 1, "anchor_set_digest": "D", "recorded_tree": "T"},
    )
    assert host._fresh_anchor_surface(path, "D", "T") == {
        "schema": 1,
        "anchor_set_digest": "D",
        "recorded_tree": "T",
    }
    assert host._fresh_anchor_surface(path, "D2", "T") is None

    def _raise(p):
        raise ValueError("bad sidecar")

    monkeypatch.setattr("tracks.executor.anchor_surface.load_anchor_surface", _raise)
    assert host._fresh_anchor_surface(path, "D", "T") is None


def test_regen_anchor_surface_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    sidecar = host._vdir() / "anchor-surface.json"
    monkeypatch.setattr(
        ledger, "load_contract", lambda repo: (_ for _ in ()).throw(ledger.ContractError("none"))
    )
    surface, skipped = host._regen_anchor_surface(host.repo, ["a"], sidecar)
    assert surface is None and skipped.startswith("infra: no contract")

    monkeypatch.setattr(ledger, "load_contract", lambda repo: object())
    monkeypatch.setattr(
        "tracks.executor.anchor_surface.collect_anchor_surface",
        lambda repo, anchors, contract, jobs: (_ for _ in ()).throw(RuntimeError("collect")),
    )
    surface, skipped = host._regen_anchor_surface(host.repo, ["a"], sidecar)
    assert surface is None and skipped.startswith("infra:")

    monkeypatch.setattr(
        "tracks.executor.anchor_surface.collect_anchor_surface",
        lambda repo, anchors, contract, jobs: {"schema": 1},
    )
    surface, skipped = host._regen_anchor_surface(host.repo, ["a"], sidecar)
    assert surface == {"schema": 1} and skipped is None
    assert (sidecar.parent / sidecar.name).exists()


def test_satisfiability_surface_uses_fresh_or_regen(tmp_path: Path):
    host = _Host(tmp_path)
    host._fresh_anchor_surface = lambda *a: {"fresh": True}
    assert host._satisfiability_surface([_task()], host._vdir()) == ({"fresh": True}, None)
    host._fresh_anchor_surface = lambda *a: None
    host._regen_anchor_surface = lambda *a: (None, "infra: x")
    assert host._satisfiability_surface([_task()], host._vdir()) == (None, "infra: x")


def test_evaluate_satisfiability_violations_and_infra(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._satisfiability_surface = lambda tasks, vdir: ({"surface": 1}, None)
    monkeypatch.setattr(
        "tracks.executor.taskgraph.validate_anchor_satisfiability",
        lambda tasks, surface: (False, ["v"], ["a"]),
    )
    assert host._evaluate_satisfiability([_task()], host._vdir()) == (["v"], ["a"], None)

    host._satisfiability_surface = lambda tasks, vdir: (None, "infra: skip")
    assert host._evaluate_satisfiability([_task()], host._vdir()) == ([], [], "infra: skip")

    host._satisfiability_surface = lambda tasks, vdir: ({"surface": 1}, None)
    monkeypatch.setattr(
        "tracks.executor.taskgraph.validate_anchor_satisfiability",
        lambda tasks, surface: (_ for _ in ()).throw(RuntimeError("regression")),
    )
    violations, advisories, skipped = host._evaluate_satisfiability([_task()], host._vdir())
    assert (violations, advisories) == ([], [])
    assert skipped.startswith("infra:")


# ---------------------------------------------------------------------------
# taskgraph docs / payloads
# ---------------------------------------------------------------------------


def test_read_taskgraph_missing_unreadable_and_ok(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    assert host._read_taskgraph(tmp_path / "missing.json")[1].startswith("tasks.json not found")
    path = tmp_path / "tasks.json"
    path.write_text("{}", encoding="utf-8")
    assert host._read_taskgraph(path) == ("{}", None)
    monkeypatch.setattr(
        Path, "read_text", lambda self, **k: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"", 0, 1, "bad"))
    )
    raw, error = host._read_taskgraph(path)
    assert raw == "" and error.startswith("tasks.json unreadable")


def test_emit_taskgraph_failure_payload(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_taskgraph_failure(_cmd("C-2"), State(current_attempt=1), "why", "ev")
    event, payload, kwargs = host.emitted[0]
    assert event == "verdict.failed"
    assert payload == {"check": "taskgraph", "reason": "why", "evidence": "ev", "attempt": 2}
    assert kwargs == {"command_id": "C-2"}


def test_taskgraph_errors_with_anchor_acs_and_docs(tmp_path: Path):
    acc = tmp_path / "acceptance.md"
    acc.write_text("- AC-FR0001-01\n", encoding="utf-8")
    iface = tmp_path / "interfaces.md"
    iface.write_text("## 5. IF Registry\n\n- IF-IMPL-001\n", encoding="utf-8")
    tasks = [_task(ac_refs=("AC-FR0001-01", "AC-FR0002-01"))]
    errors = MImplLedgerMixin._taskgraph_errors(
        tmp_path, tasks, tmp_path, anchor_acs=["AC-FR0001-01@v0.5"]
    )
    assert any("unanchored AC AC-FR0002-01" in e for e in errors)


def test_taskgraph_docs_missing_files_and_registry(tmp_path: Path):
    ac_ids, registry, errors = MImplLedgerMixin._taskgraph_docs(
        tmp_path / "missing-acc.md", tmp_path / "missing-if.md"
    )
    assert ac_ids == [] and registry == set()
    assert any("acceptance.md missing" in e for e in errors)
    assert any("interfaces.md missing" in e for e in errors)

    iface = tmp_path / "if.md"
    iface.write_text("no registry here", encoding="utf-8")
    _ac_ids, registry, errors = MImplLedgerMixin._taskgraph_docs(tmp_path / "missing-acc.md", iface)
    assert registry == set()
    assert any("IF Registry" in e for e in errors)


def test_task_payload_carries_split_refs(tmp_path: Path):
    payload = MImplLedgerMixin._task_payload(
        _task(
            unit_refs=("tests/unit/a.py::t1",),
            acceptance_refs=("tests/integration/b.py::t2",),
            integration=True,
            schema=2,
        )
    )
    assert payload["unit_refs"] == ["tests/unit/a.py::t1"]
    assert payload["acceptance_refs"] == ["tests/integration/b.py::t2"]
    assert payload["integration"] is True
    assert payload["schema"] == 2


def test_task_payloads_equivalent_ignores_debt():
    old = {"task_id": "T", "debt": [{"x": 1}]}
    new = {"task_id": "T", "debt": []}
    assert MImplLedgerMixin._task_payloads_equivalent(old, new) is True
    assert MImplLedgerMixin._task_payloads_equivalent({"task_id": "T"}, {"task_id": "U"}) is False


def test_compute_retained_completions(tmp_path: Path):
    host = _Host(tmp_path)
    old = [{"task_id": "T-1", "issue_number": 1}]
    new = [{"task_id": "T-1", "issue_number": 1}]
    assert host._compute_retained_completions(old, new, {"T-1"}) == ["T-1"]
    assert host._compute_retained_completions(old, new, {"T-2"}) == []
    new_changed = [{"task_id": "T-1", "issue_number": 2}]
    assert host._compute_retained_completions(old, new_changed, {"T-1"}) == []


# ---------------------------------------------------------------------------
# task selection / lease recovery
# ---------------------------------------------------------------------------


def test_do_select_task_guards(tmp_path: Path):
    host = _Host(tmp_path)
    host._do_select_task(_cmd(), State(current_task_id="T-1"), "T", False)
    assert host.emitted == []
    host._start_task = lambda *a: host.emitted.append(("started", {}, {}))
    host._emit_no_task_failure = lambda cmd, state, reason: host.emitted.append(
        ("no-task", {"reason": reason}, {})
    )
    host._do_select_task(_cmd(), State(task_refs=[]), "T", False)
    assert host.emitted[0][1]["reason"] == "no tasks available"


def test_do_select_task_dev_scenario_refreezes_on_branch_advance(tmp_path: Path):
    host = _Host(tmp_path)
    host._freeze_scenario_head = lambda state: "NEW"
    host._baseline_frozen_payload = lambda state: {"status": "current", "digest": "NEW"}
    host.store = _Store(
        [
            _ev(1, "baseline.frozen", {"scenario_branch_head": "OLD"}),
        ]
    )
    state = State(run_id="RUN", hotfix_issue=7, hotfix_scenario="dev", task_refs=[{"task_id": "T-1"}])
    host._do_select_task(_cmd("C-3"), state, "T", False)
    assert host.emitted == [
        ("baseline.frozen", {"status": "current", "digest": "NEW"}, {"command_id": "C-3"})
    ]


def test_do_select_task_started_lease_and_ready_selection(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_no_task_failure = lambda cmd, state, reason: host.emitted.append(
        ("no-task", {"reason": reason}, {})
    )
    state = State(
        run_id="RUN",
        task_refs=[{"task_id": "T-1", "scope_boundary": "a", "depends_on": []}],
        current_task_metadata=None,
    )
    host.store = _Store(
        [
            _ev(1, "stage.entered", {"stage": "M-IMPL"}),
            _ev(2, "task.started", {"task_id": "T-1"}),
        ]
    )
    host._do_select_task(_cmd(), state, "T", False)
    assert host.emitted == []  # in-flight start this cycle blocks a double select

    host.store = _Store([_ev(1, "stage.entered", {"stage": "M-IMPL"})])
    host._recover_task_lease = lambda cmd, events, completed: host.emitted.append(("lease", {}, {}))
    host._do_select_task(_cmd(), State(run_id="RUN", task_refs=[{"task_id": "T-1"}], writelock_held=True), "T", False)
    assert host.emitted[0][0] == "lease"

    host2 = _Host(tmp_path)
    host2._emit_no_task_failure = lambda cmd, state, reason: host2.emitted.append(
        ("no-task", {"reason": reason}, {})
    )
    host2.store = _Store([_ev(1, "stage.entered", {"stage": "M-IMPL"})])
    host2._do_select_task(
        _cmd(),
        State(run_id="RUN", task_refs=[{"task_id": "T-1", "depends_on": ["T-9"], "scope_boundary": "a"}]),
        "T",
        False,
    )
    assert host2.emitted[0][1]["reason"] == "no ready tasks"

    host3 = _Host(tmp_path)
    started: list = []
    host3._start_task = lambda cmd, chosen, manifest: started.append((cmd, chosen, manifest))
    host3._task_manifest = lambda task, state: {"task_id": task.task_id}
    host3.store = _Store([_ev(1, "stage.entered", {"stage": "M-IMPL"})])
    host3._do_select_task(
        _cmd("C-4"),
        State(run_id="RUN", task_refs=[{"task_id": "T-1", "scope_boundary": "a", "depends_on": []}]),
        "T",
        False,
    )
    assert started and started[0][1].task_id == "T-1"


def test_effective_completed_task_ids_combines_sources(tmp_path: Path):
    host = _Host(tmp_path)
    events = [
        _ev(1, "taskgraph.committed", {}),
        _ev(2, "task.completed", {"task_id": "T-1"}),
        _ev(3, "known_issue.registered", {"task_id": "T-2"}),
        _ev(4, "known_issue.registered", {"task_id": "T-3", "fixed": True}),
    ]
    state = State(run_id="RUN", retained_completed_task_ids=["T-0"])
    assert host._effective_completed_task_ids(events, state) == {"T-0", "T-1", "T-2"}


def test_recover_task_lease_no_lease_and_stale_generation(tmp_path: Path):
    host = _Host(tmp_path)
    host._recover_task_lease(_cmd(), [], set())
    assert host.emitted == []
    host._recover_task_lease(_cmd(), [_ev(1, "writelock.granted", {"task_id": "T-1"})], set())
    assert host.emitted == []


def test_recover_task_lease_stale_graph_paths(tmp_path: Path):
    host = _Host(tmp_path)
    events = [
        _ev(1, "writelock.granted", {"task_id": "T-1"}),
        _ev(2, "taskgraph.committed", {}),
    ]
    host._recover_task_lease(_cmd("C-5"), events, set())
    assert host.emitted[0][0] == "writelock.released"
    assert host.emitted[0][1]["reason"] == "taskgraph_replaced"

    host2 = _Host(tmp_path)
    host2._recover_task_lease(
        _cmd(),
        events + [_ev(3, "writelock.released", {"task_id": "T-1"})],
        set(),
    )
    assert host2.emitted == []


def test_recover_task_lease_completed_same_generation(tmp_path: Path):
    host = _Host(tmp_path)
    events = [
        _ev(1, "taskgraph.committed", {}),
        _ev(2, "writelock.granted", {"task_id": "T-1"}),
    ]
    host._recover_task_lease(_cmd("C-6"), events, {"T-1"})
    assert [e[0] for e in host.emitted] == ["writelock.released"]
    host2 = _Host(tmp_path)
    host2._recover_task_lease(
        _cmd(), events + [_ev(3, "writelock.released", {"task_id": "T-1"})], {"T-1"}
    )
    assert host2.emitted == []


def test_recover_task_lease_reemits_task_started_with_manifest(tmp_path: Path):
    host = _Host(tmp_path)
    payload = {"task_id": "T-1", "task": {"task_id": "T-1"}, "manifest": {"allowed_paths": []}}
    events = [
        _ev(1, "taskgraph.committed", {}),
        _ev(2, "writelock.granted", payload),
    ]
    host._recover_task_lease(_cmd("C-7"), events, set())
    assert [e[0] for e in host.emitted] == ["task.started"]
    assert host.emitted[0][1]["task_id"] == "T-1"


def test_start_task_emits_writelock_and_task_started(tmp_path: Path):
    host = _Host(tmp_path)
    host._task_allowed_paths = lambda task: ["tracks/app.py"]
    host._start_task(_cmd("C-8"), _task(), {"forbidden_paths": [".tracks/**"]})
    assert [e[0] for e in host.emitted] == ["writelock.granted", "task.started"]
    assert host.emitted[0][1]["allowed_paths"] == ["tracks/app.py"]
    assert host.emitted[0][1]["forbidden_paths"] == [".tracks/**"]
    assert host.projection_rebuilds == 1


def test_emit_no_task_failure_payload(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_no_task_failure(_cmd("C-9"), State(current_attempt=0), "no tasks available")
    event, payload, kwargs = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["check"] == "island"
    assert payload["attempt"] == 1
    assert kwargs == {"command_id": "C-9"}


def test_do_complete_task_branches(tmp_path: Path):
    host = _Host(tmp_path)
    host._transition_full_ledger = lambda *a: True
    state = State(run_id="RUN", full_chain_round="FULL_F", writelock_held=True)
    host._do_complete_task(_cmd("C-1"), state, "T-1", False)
    assert [e[0] for e in host.emitted] == ["writelock.released"]
    assert host.projection_rebuilds == 1

    host2 = _Host(tmp_path)
    host2.store = _Store(
        [
            _ev(1, "taskgraph.committed", {}),
            _ev(2, "task.completed", {"task_id": "T-1"}),
        ]
    )
    host2._do_complete_task(_cmd("C-2", task_id="T-1"), State(run_id="RUN"), "T-1", False)
    assert [e[0] for e in host2.emitted] == ["writelock.released"]

    host3 = _Host(tmp_path)
    host3.store = _Store([_ev(1, "taskgraph.committed", {})])
    host3._do_complete_task(_cmd("C-3", task_id="T-1"), State(run_id="RUN"), "T-1", False)
    assert [e[0] for e in host3.emitted] == ["task.completed", "writelock.released"]
    assert host3.projection_rebuilds == 1


def test_do_complete_task_retained_completion_releases(tmp_path: Path):
    host = _Host(tmp_path)
    host.store = _Store([])
    state = State(run_id="RUN", retained_completed_task_ids=["T-1"])
    host._do_complete_task(_cmd("C-4", task_id="T-1"), state, "T-1", False)
    assert [e[0] for e in host.emitted] == ["writelock.released"]
