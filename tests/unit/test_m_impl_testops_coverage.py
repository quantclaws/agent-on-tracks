"""Behavior coverage for the M-IMPL test-operations mixin (``MImplTestOpsMixin``).

Drives manifest construction, path-scope guards, gate-worktree candidate
selection, RED/acceptance ref resolution, lint scope, layer inventory
collection, waivers/drift and snapshots through a bare mixin host with only
the external process/contract seams stubbed (B50/B89/B94/FR-0286).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.executor import m_impl_testops as testops
from tracks.executor.m_impl_testops import MImplTestOpsMixin
from tracks.executor.taskgraph_parse import TaskNode
from tracks.kernel.events import Command
from tracks.kernel.machine import State


class _Store:
    def __init__(self, state=None, events=()):
        self._state = state or State(run_id="RUN")
        self._events = list(events)

    def state(self, run_id):
        return self._state

    def events(self, run_id):
        return list(self._events)

    def write_audit_blob(self, payload):
        return "BLOB"


class _Host(MImplTestOpsMixin):
    def __init__(self, tmp_path: Path, *, state=None, events=()):
        self.repo = tmp_path / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_id = "RUN"
        self.store = _Store(state, events)
        self.emitted: list[tuple] = []
        self._vdir_path = tmp_path / "vdir"
        self._vdir_path.mkdir(parents=True, exist_ok=True)
        self.frozen: list[str] = []
        self.dirty: dict = {}
        self.unit_run = "pytest tests/unit"
        self.layout: dict[str, list[str]] = {"devon": [], "shield": []}

    def _emit(self, event, payload=None, **kwargs):
        self.emitted.append((event, payload, kwargs))

    def _vdir(self) -> Path:
        return self._vdir_path

    def _frozen_test_paths(self) -> list[str]:
        return list(self.frozen)

    def _dirty_snapshot(self) -> dict:
        return dict(self.dirty)

    def _last_baseline_digest(self) -> str:
        return "BASELINE"

    def _path_identity(self, path: Path) -> str:
        return f"id:{path}"

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

    def _contract_unit_run_hook(self) -> str:
        return self.unit_run


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


def _cmd(command_id: str = "C-1", **params) -> Command:
    return Command(kind="impl_cmd", params=dict(params), command_id=command_id)


# ---------------------------------------------------------------------------
# contract / command construction
# ---------------------------------------------------------------------------


def test_contract_unit_run_missing_and_present(tmp_path: Path, monkeypatch):
    def _boom(repo):
        raise testops.ContractError("missing")

    monkeypatch.setattr(testops, "load_contract", _boom)
    assert testops._contract_unit_run(tmp_path) == ""
    monkeypatch.setattr(
        testops, "load_contract", lambda repo: SimpleNamespace(unit=SimpleNamespace(run="pytest"))
    )
    assert testops._contract_unit_run(tmp_path) == "pytest"


def test_parse_allowed_paths_normalizes_and_drops_unsafe():
    assert MImplTestOpsMixin._parse_allowed_paths(" tracks/app.py ,src/\n/abs,../x,b/") == [
        "b",
        "src",
        "tracks/app.py",
    ]


def test_forbidden_paths_merges_layout_and_frozen(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(testops, "layout_paths", lambda repo, role: ["tests/", ".venv"])
    host.frozen = ["tests/frozen"]
    forbidden = host._forbidden_paths()
    assert ".tracks/projects/**" in forbidden
    assert "tests/" in forbidden and "tests/**" in forbidden
    assert ".venv/**" in forbidden
    assert "tests/frozen/**" in forbidden


def test_test_commands_contract_error_and_sections(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        testops, "load_contract", lambda repo: (_ for _ in ()).throw(testops.ContractError("no"))
    )
    assert host._test_commands() == {}
    contract = SimpleNamespace(
        integration=SimpleNamespace(run="pytest tests/integration"),
        e2e=None,
    )
    monkeypatch.setattr(testops, "load_contract", lambda repo: contract)
    assert host._test_commands() == {"integration": "pytest tests/integration"}


def test_guard_commands_uses_declared_interpreter(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(testops, "declared_install_interpreter", lambda repo: "PY")
    assert host._guard_commands() == [
        "PY -m ruff check",
        "PY -m flake8 --select=CCR001",
        "PY -m pylint --disable=all --enable=R0801,C0302,R0915,R0914",
    ]


def test_unit_commands_and_red_test_dirs(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(testops, "layout_paths", lambda repo, role: ["src", "tests/unit", "testkit"])
    assert host._devon_red_test_dirs() == ["tests/unit", "testkit"]
    monkeypatch.setattr(testops, "_contract_unit_run", lambda repo: host.unit_run)
    assert host._unit_commands() == [host.unit_run]
    monkeypatch.setattr(testops, "layout_paths", lambda repo, role: ["src"])
    assert host._unit_commands() == []


# ---------------------------------------------------------------------------
# manifest / allowed paths
# ---------------------------------------------------------------------------


def test_task_manifest_uses_integration_union_when_declared(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    task = _task(integration=True)
    state = State(run_id="RUN", task_refs=[{"task_id": "T-1"}], current_attempt=2)
    monkeypatch.setattr(testops, "layout_paths", lambda repo, role: [])
    monkeypatch.setattr(testops, "declared_install_interpreter", lambda repo: "PY")
    monkeypatch.setattr(testops, "_contract_unit_run", lambda repo: "pytest")
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_allowed_paths", lambda nodes: ["union/"]
    )
    host._task_allowed_paths = lambda task_arg: ["own/"]
    host._result_identity = lambda *a: "RESULT"
    manifest = host._task_manifest(task, state)
    assert manifest["allowed_paths"] == ["union/"]
    assert manifest["issue_number"] == 1
    assert manifest["budget"] == 3
    assert manifest["result_identity"] == "RESULT"
    assert manifest["red_test_paths"] == []


def test_task_manifest_integration_exception_falls_back(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    task = _task(integration=True)
    state = State(run_id="RUN", task_refs=[{"task_id": "T-1"}])
    monkeypatch.setattr(testops, "layout_paths", lambda repo, role: [])
    monkeypatch.setattr(testops, "declared_install_interpreter", lambda repo: "PY")
    monkeypatch.setattr(testops, "_contract_unit_run", lambda repo: "pytest")
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_allowed_paths",
        lambda nodes: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    host._task_allowed_paths = lambda task_arg: ["own/"]
    host._result_identity = lambda *a: "RESULT"
    assert host._task_manifest(task, state)["allowed_paths"] == ["own/"]


def test_task_allowed_paths_scope_and_unit_refs(tmp_path: Path):
    host = _Host(tmp_path)
    task = _task(unit_refs=("tests/unit/test_a.py::test_a", "tests/integration/test_b.py"))
    allowed = host._task_allowed_paths(task)
    assert "tracks/app.py" in allowed
    assert "tests/unit/test_a.py" in allowed
    assert "tests/integration/test_b.py" not in allowed


def test_task_allowed_paths_integration_union_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    task = _task(integration=True, task_id="T-2")
    state = State(run_id="RUN", task_refs=[{"task_id": "T-1"}])
    host.store = _Store(state)
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_allowed_paths", lambda nodes: ["union/"]
    )
    assert host._task_allowed_paths(task) == ["union/"]

    host.store = _Store(State(run_id="RUN", task_refs=[{"task_id": "T-2"}]))
    assert host._task_allowed_paths(task) == ["union/"]

    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_allowed_paths",
        lambda nodes: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    host.store = _Store(State(run_id="RUN", task_refs=[]))
    assert host._task_allowed_paths(task) == ["tracks/app.py"]


def test_result_identity_is_input_sensitive(tmp_path: Path):
    host = _Host(tmp_path)
    first = host._result_identity("T-1", "manifest", 1, {"a": "b"}, "BASE")
    assert first == host._result_identity("T-1", "manifest", 1, {"a": "b"}, "BASE")
    assert first != host._result_identity("T-1", "manifest", 2, {"a": "b"}, "BASE")


def test_unit_commands_from_manifest_prefers_valid_manifest(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._current_manifest = lambda: {"unit_commands": ["pytest -q"]}
    assert host._unit_commands_from_manifest() == ["pytest -q"]
    host._current_manifest = lambda: {"unit_commands": [""]}
    monkeypatch.setattr(testops, "_contract_unit_run", lambda repo: "pytest tests/unit")
    assert host._unit_commands_from_manifest() == ["pytest tests/unit"]
    monkeypatch.setattr(testops, "_contract_unit_run", lambda repo: "")
    assert host._unit_commands_from_manifest() == []


# ---------------------------------------------------------------------------
# gate worktrees
# ---------------------------------------------------------------------------


def test_existing_gate_handle_detects_directory(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(testops, "ensure_runtime_assets", lambda repo, gate: None)
    assert host._existing_gate_handle("T-1") is None
    gate = host.repo / ".tracks" / "worktrees" / "RUN" / "T-1" / "gate"
    gate.mkdir(parents=True)
    handle = host._existing_gate_handle("T-1")
    assert handle is not None
    assert handle.path == str(gate)
    assert handle.kind == "gate"
    assert host._existing_gate_handle("") is None


def test_usable_tree_identity_rejects_bogus_shas():
    assert MImplTestOpsMixin._usable_tree_identity("abc") is True
    assert MImplTestOpsMixin._usable_tree_identity("") is False
    assert MImplTestOpsMixin._usable_tree_identity("0" * 40) is False


def test_refactor_gate_base_reads_green_commit(tmp_path: Path):
    green = SimpleNamespace(type="green.committed", payload={"task_id": "T-1", "g_sha": "G"})
    host = _Host(tmp_path, events=[green])
    assert host._refactor_gate_base("T-1") == "G"
    host.store = _Store(events=[SimpleNamespace(type="green.committed", payload={"task_id": "T-1"})])
    assert host._refactor_gate_base("T-1") is None


def test_latest_phase_diff_ref_reads_outcome(tmp_path: Path):
    events = [
        SimpleNamespace(type="outcome.received", payload={"phase": "green", "diff_ref": "D1"}),
        SimpleNamespace(type="outcome.received", payload={"phase": "green", "diff_ref": "D2"}),
    ]
    host = _Host(tmp_path, events=events)
    assert host._latest_phase_diff_ref("green") == "D2"
    assert host._latest_phase_diff_ref("red") is None


def test_gate_candidate_inputs_branches(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    state = State(run_id="RUN", r_tree_identity="0" * 40)
    assert host._gate_candidate_inputs(state, "T", "green") is None

    state = State(run_id="RUN", r_tree_identity="R")
    monkeypatch.setattr(testops, "red_base_sha", lambda repo, sha: None)
    assert host._gate_candidate_inputs(state, "T", "green") is None

    monkeypatch.setattr(testops, "red_base_sha", lambda repo, sha: "B")
    host._r_tests_in_working_tree = lambda sha: True
    assert host._gate_candidate_inputs(state, "T", "green") is None

    host._r_tests_in_working_tree = lambda sha: False
    host._latest_phase_diff_ref = lambda phase: None
    assert host._gate_candidate_inputs(state, "T", "green") is None

    host._latest_phase_diff_ref = lambda phase: "DIFF"
    assert host._gate_candidate_inputs(state, "T", "green") == ("R", "B", "DIFF")

    host._refactor_gate_base = lambda task_id: None
    assert host._gate_candidate_inputs(state, "T", "refactor") is None
    host._refactor_gate_base = lambda task_id: "G"
    host._latest_phase_diff_ref = lambda phase: None
    assert host._gate_candidate_inputs(state, "T", "refactor") == ("R", "G", "")


def test_ensure_gate_worktree_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    state = State(run_id="RUN", current_task_id="T-1")
    existing = SimpleNamespace(path="/gate", base_sha="", kind="gate")
    monkeypatch.setattr(
        MImplTestOpsMixin, "_existing_gate_handle", lambda self, task_id: existing
    )
    assert host._ensure_gate_worktree(state) == ("/gate", existing)

    monkeypatch.setattr(MImplTestOpsMixin, "_existing_gate_handle", lambda self, task_id: None)
    host._gate_candidate_inputs = lambda *a: None
    assert host._ensure_gate_worktree(state) == (str(host.repo), None)

    host._gate_candidate_inputs = lambda *a: ("R", "B", "D")
    monkeypatch.setattr(
        testops,
        "create_gate_worktree",
        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert host._ensure_gate_worktree(state) == (str(host.repo), None)

    handle = SimpleNamespace(path="/new-gate", kind="gate")
    monkeypatch.setattr(testops, "create_gate_worktree", lambda *a: handle)
    assert host._ensure_gate_worktree(state) == ("/new-gate", handle)


def test_r_tests_in_working_tree_branches(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        testops, "git", lambda repo, *a, check=False: SimpleNamespace(returncode=1, stdout="")
    )
    assert host._r_tests_in_working_tree("R") is False

    monkeypatch.setattr(
        testops,
        "git",
        lambda repo, *a, check=False: SimpleNamespace(returncode=0, stdout="src/app.py\n"),
    )
    assert host._r_tests_in_working_tree("R") is False

    monkeypatch.setattr(
        testops,
        "git",
        lambda repo, *a, check=False: SimpleNamespace(returncode=0, stdout="tests/unit/test_a.py\n"),
    )
    assert host._r_tests_in_working_tree("R") is False
    (host.repo / "tests" / "unit").mkdir(parents=True)
    (host.repo / "tests" / "unit" / "test_a.py").write_text("", encoding="utf-8")
    assert host._r_tests_in_working_tree("R") is True


def test_path_in_tree_and_parse_diff_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr(
        testops, "git", lambda repo, *a, check=False: SimpleNamespace(returncode=0, stdout="")
    )
    assert host._path_in_tree("R", "a.py") is True
    monkeypatch.setattr(
        testops, "git", lambda repo, *a, check=False: SimpleNamespace(returncode=128, stdout="")
    )
    assert host._path_in_tree("R", "a.py") is False
    diff = (
        "diff --git a/tests/unit/test_a.py b/tests/unit/test_a.py\n"
        "--- a/tests/unit/test_a.py\n"
        "diff --git a/src/app.py b/src/app.py\n"
    )
    assert MImplTestOpsMixin._parse_diff_paths(diff) == {"tests/unit/test_a.py", "src/app.py"}


def test_green_regression_tests_unions_claimed_and_captured(tmp_path: Path):
    host = _Host(tmp_path)
    host._last_devon_outcome = lambda: {"changed_paths": ["tests/unit/test_claimed.py", "src/app.py"]}
    host._validated_diff = lambda phase, state: ("argv", "diff --git a/tests/unit/test_captured.py b/tests/unit/test_captured.py\n")
    host._path_in_tree = lambda sha, path: path.startswith("tests/unit/test_claimed")
    assert host._green_regression_tests("R", State()) == ["tests/unit/test_claimed.py"]


def test_gate_evidence_includes_log_ref(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    obs = SimpleNamespace(argv=["pytest"], cwd="/x", exit_code=1, stdout="FAIL", stderr="")
    monkeypatch.setattr(testops, "failed_summary_lines", lambda stdout: ["FAIL"])
    monkeypatch.setattr(testops, "observation_evidence", lambda obs, extra: {"extra": extra})
    host.store = _Store()
    host.store.write_audit_blob = lambda payload: "ref1"
    assert host._gate_evidence(obs)["extra"]["log_ref"] == ".tracks/runtime/blobs/ref1"
    host.store.write_audit_blob = lambda payload: None
    assert "log_ref" not in host._gate_evidence(obs)["extra"]


def test_emit_gate_failure_detects_evidence_shape_error(tmp_path: Path):
    host = _Host(tmp_path)
    host._emit_gate_failure(
        _cmd("C-3"),
        check="green_gate",
        reason="Devon evidence missing: outcomes_ref",
        task_id="T-1",
        attempt=2,
        evidence="",
    )
    event, payload, kwargs = host.emitted[0]
    assert event == "verdict.failed"
    assert payload["failure_class"] == "evidence_malformed"
    assert payload["check"] == "evidence_malformed"
    assert kwargs == {"command_id": "C-3", "task_id": "T-1"}


def test_lint_targets_scopes_by_phase(tmp_path: Path):
    host = _Host(tmp_path)
    for rel in ("tests/unit/test_a.py", "src/app.py", "notes.txt"):
        path = host.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    host._last_devon_outcome = lambda: {
        "changed_paths": ["tests/unit/test_a.py", "src/app.py", "notes.txt", "src/missing.py"]
    }
    assert host._lint_targets("red") == ["tests/unit/test_a.py"]
    assert host._lint_targets("green") == ["src/app.py"]


def test_run_declared_lint_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setattr("tracks.project.lint_check_command", lambda repo: None)
    assert host._run_declared_lint(["src/app.py"]) == (None, "")
    monkeypatch.setattr("tracks.project.lint_check_command", lambda repo: "ruff check")
    assert host._run_declared_lint([]) == (None, "")

    def _raise(*a, **k):
        raise OSError("missing ruff")

    monkeypatch.setattr(testops.subprocess, "run", _raise)
    _findings, note = host._run_declared_lint(["src/app.py"])
    assert note.startswith("lint skipped:")

    monkeypatch.setattr(
        testops.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="E501", stderr=""),
    )
    assert host._run_declared_lint(["src/app.py"]) == ("E501", "")
    monkeypatch.setattr(
        testops.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert host._run_declared_lint(["src/app.py"]) == (None, "")


# ---------------------------------------------------------------------------
# ref resolution
# ---------------------------------------------------------------------------


def test_expand_unit_refs_file_and_node_expansion():
    inventory = ["tests/unit/test_a.py::test_1", "tests/unit/test_a.py::test_2", "tests/unit/test_b.py::test_3"]
    refs = ["tests/unit/test_a.py", "tests/unit/test_b.py::test_3"]
    assert MImplTestOpsMixin._expand_unit_refs(refs, inventory) == [
        "tests/unit/test_a.py::test_1",
        "tests/unit/test_a.py::test_2",
        "tests/unit/test_b.py::test_3",
    ]
    with pytest.raises(testops.TestSelectError, match="absent"):
        MImplTestOpsMixin._expand_unit_refs(["tests/unit/test_missing.py"], inventory)
    with pytest.raises(testops.TestSelectError, match="absent"):
        MImplTestOpsMixin._expand_unit_refs(["tests/unit/test_a.py::test_9"], inventory)


def test_resolve_one_acceptance_ref_file_and_node():
    by_path = {"tests/integration/test_a.py": ["tests/integration/test_a.py::t1", "tests/integration/test_a.py::t2"]}
    resolved: set = set()
    MImplTestOpsMixin._resolve_one_acceptance_ref("tests/integration/test_a.py", by_path, resolved)
    assert resolved == set(by_path["tests/integration/test_a.py"])
    resolved = set()
    MImplTestOpsMixin._resolve_one_acceptance_ref("tests/integration/test_a.py::t1", by_path, resolved)
    assert resolved == {"tests/integration/test_a.py::t1"}
    with pytest.raises(testops.TestSelectError, match="absent"):
        MImplTestOpsMixin._resolve_one_acceptance_ref("tests/integration/test_x.py", by_path, set())


def test_acceptance_anchors_schema1_skips_plan_crosscheck():
    inventory = ["tests/integration/test_a.py::t1"]
    host = MImplTestOpsMixin()
    assert host._acceptance_anchors(
        ["tests/integration/test_a.py"], inventory, "", 1
    ) == ["tests/integration/test_a.py::t1"]


def test_acceptance_anchors_schema2_requires_planned_rows():
    inventory = ["tests/integration/test_a.py::t1"]
    plan = (
        "# Plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_a.py | IF-1 |\n"
    )
    host = MImplTestOpsMixin()
    assert host._acceptance_anchors(
        ["tests/integration/test_a.py"], inventory, plan, 2
    ) == ["tests/integration/test_a.py::t1"]
    with pytest.raises(testops.TestSelectError, match="§8"):
        host._acceptance_anchors(
            ["tests/integration/test_other.py"], ["tests/integration/test_other.py::t9"], plan, 2
        )


def test_anchor_is_planned_symmetric_file_node():
    planned = {"tests/integration/test_a.py", "tests/integration/test_b.py::t9"}
    assert MImplTestOpsMixin._anchor_is_planned("tests/integration/test_a.py", planned) is True
    assert MImplTestOpsMixin._anchor_is_planned("tests/integration/test_b.py::t9", planned) is True
    assert MImplTestOpsMixin._anchor_is_planned("tests/integration/test_b.py::t8", {"tests/integration/test_b.py"}) is True
    assert MImplTestOpsMixin._anchor_is_planned("tests/integration/test_c.py", planned) is False


def test_row_has_integration_layer_and_targets():
    assert MImplTestOpsMixin._row_has_integration_layer("integration", "tests/x.py") is True
    assert MImplTestOpsMixin._row_has_integration_layer("integration+e2e", "tests/x.py") is True
    assert MImplTestOpsMixin._row_has_integration_layer("unit", "tests/x.py") is False
    assert MImplTestOpsMixin._row_has_integration_layer("integration", None) is False
    assert MImplTestOpsMixin._integration_row_targets("tests/integration/test_a.py::t1") == [
        ("tests/integration/test_a.py", "tests/integration/test_a.py::t1")
    ]


# ---------------------------------------------------------------------------
# inventories / artifacts
# ---------------------------------------------------------------------------


def test_collect_layer_inventories_retries_quiet_listing(tmp_path: Path, monkeypatch):
    """D4: pytest>=9 quiet collect (per-file counts, no ``::`` ids) is
    re-collected with the verbosity pin instead of yielding an empty layer."""
    host = _Host(tmp_path)
    sections = {
        name: SimpleNamespace(collect=f"collect {name} -q", cwd=".")
        for name in ("unit", "integration", "e2e")
    }
    contract = SimpleNamespace(**sections)
    calls: list[str] = []

    def fake_execute(command, cwd, label):
        calls.append(command)
        if command.endswith("-o verbosity_test_cases=-1"):
            return SimpleNamespace(
                exit_code=0, stdout=f"tests/{label}.py::test_one\n", stderr=""
            )
        return SimpleNamespace(
            exit_code=0, stdout=f"tests/{label}.py: 1\n", stderr=""
        )

    monkeypatch.setattr(testops, "execute_gate_command", fake_execute)
    inventories = host._collect_layer_inventories(contract, str(host.repo))
    assert inventories == {
        "unit": ["tests/unit-collect.py::test_one"],
        "integration": ["tests/integration-collect.py::test_one"],
        "e2e": ["tests/e2e-collect.py::test_one"],
    }
    assert calls == [
        "collect unit -q",
        "collect unit -q -o verbosity_test_cases=-1",
        "collect integration -q",
        "collect integration -q -o verbosity_test_cases=-1",
        "collect e2e -q",
        "collect e2e -q -o verbosity_test_cases=-1",
    ]


def test_collect_layer_inventories_outcomes(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    unit = SimpleNamespace(collect="collect unit", cwd=".")
    integration = SimpleNamespace(collect="collect int", cwd="tests")
    e2e = SimpleNamespace(collect="collect e2e", cwd=".")
    contract = SimpleNamespace(unit=unit, integration=integration, e2e=e2e)
    observations = {
        "unit-collect": SimpleNamespace(exit_code=5, stdout="", stderr=""),
        "integration-collect": SimpleNamespace(exit_code=0, stdout="n1\n", stderr=""),
        "e2e-collect": SimpleNamespace(exit_code=2, stdout="", stderr="missing"),
    }
    monkeypatch.setattr(
        testops, "execute_gate_command", lambda command, cwd, label: observations[label]
    )
    monkeypatch.setattr(testops, "parse_collected_nodes", lambda stdout: ["parsed"] if stdout else [])
    inventories = host._collect_layer_inventories(contract, str(host.repo))
    assert inventories == {"unit": [], "integration": ["parsed"], "e2e": []}

    observations["integration-collect"] = SimpleNamespace(exit_code=2, stdout="", stderr="boom")
    with pytest.raises(testops.TestSelectError, match="integration.*collect failed"):
        host._collect_layer_inventories(contract, str(host.repo))


def test_task_r_family_shas_and_r_artifacts(tmp_path: Path):
    events = [
        SimpleNamespace(
            type="red.checkpointed", payload={"task_id": "T-1", "r_sha": "R1"}
        ),
        SimpleNamespace(
            type="red.checkpointed", payload={"task_id": "T-1", "r_sha": "R1"}
        ),
        SimpleNamespace(
            type="red.checkpointed", payload={"task_id": "T-2", "r_sha": "R2"}
        ),
    ]
    host = _Host(tmp_path, events=events)
    assert host._task_r_family_shas("T-1") == ["R1"]
    host._path_in_tree = lambda sha, path: sha == "R1" and "test_a" in path
    host._require_r_artifacts(["tests/unit/test_a.py::t"], "R0", "T-1")
    with pytest.raises(testops.TestSelectError, match="absent"):
        host._require_r_artifacts(["tests/unit/test_b.py::t"], "R0", "T-1")


def test_effective_refs_for_gate_paths(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    task = _task(integration=True, acceptance_refs=("tests/integration/test_a.py::t1",))
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_hard_refs", lambda task_arg, nodes: ["hard"]
    )
    host.store = _Store(State(run_id="RUN", task_refs=[]))
    assert host._effective_refs_for_gate(task) == ["hard"]

    monkeypatch.setattr(
        "tracks.executor.deferred_gate.integration_hard_refs",
        lambda task_arg, nodes: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert host._effective_refs_for_gate(task) == ["tests/integration/test_a.py::t1"]

    non_integration = _task(deferred_refs=("tests/e2e/test_e.py::t1",))
    assert host._effective_refs_for_gate(non_integration) == ["tests/e2e/test_e.py::t1"]


def test_resolve_e2e_nodes(tmp_path: Path):
    host = _Host(tmp_path)
    inventories = {"e2e": ["tests/e2e/test_e.py::t1", "tests/e2e/test_e.py::t2"]}
    assert host._resolve_e2e_nodes([], inventories) == set()
    assert host._resolve_e2e_nodes(["tests/e2e/test_e.py::t1"], inventories) == {
        "tests/e2e/test_e.py::t1"
    }


def test_collect_task_gate_nodes_and_empty_select(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    contract = SimpleNamespace(unit=SimpleNamespace(), integration=SimpleNamespace(), e2e=None)
    monkeypatch.setattr(testops, "load_contract", lambda repo: contract)
    host._collect_layer_inventories = lambda contract_arg, cwd: {"unit": [], "integration": []}
    host._expand_unit_refs = lambda refs, inventory: ["tests/unit/test_a.py::t1"]
    host._require_r_artifacts = lambda nodes, sha, task_id: None
    host._devon_unit_touched = lambda: []
    host._gate_acceptance_nodes = lambda task, inventories: []
    monkeypatch.setattr(testops, "select_task", lambda *a: ["tests/unit/test_a.py::t1"])
    task = _task(unit_refs=("tests/unit/test_a.py::t1",))
    assert host._collect_task_gate_nodes(str(host.repo), task) == (contract, ["tests/unit/test_a.py::t1"])
    monkeypatch.setattr(testops, "select_task", lambda *a: [])
    with pytest.raises(testops.TestSelectError, match="empty SELECT_TASK"):
        host._collect_task_gate_nodes(str(host.repo), task)


def test_devon_unit_touched_and_gate_acceptance_nodes(tmp_path: Path):
    host = _Host(tmp_path)
    host._last_devon_outcome = lambda: {"changed_paths": ["tests/unit/test_a.py", "src/app.py"]}
    assert host._devon_unit_touched() == ["tests/unit/test_a.py"]
    plan = host._vdir() / "test-plan.md"
    plan.write_text("plan", encoding="utf-8")
    task = _task(acceptance_refs=("tests/integration/test_a.py", "tests/e2e/test_e.py::t1"))
    host._acceptance_anchors = lambda refs, inv, text, schema: ["anchor"]
    host._resolve_e2e_nodes = lambda refs, inventories: {"tests/e2e/test_e.py::t1"}
    nodes = host._gate_acceptance_nodes(task, {"integration": [], "e2e": []})
    assert nodes == ["anchor", "tests/e2e/test_e.py::t1"]


# ---------------------------------------------------------------------------
# drift / snapshots / helpers
# ---------------------------------------------------------------------------


def test_check_drift_breaker_emits_unexpected_and_swallows_errors(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    host._check_drift_breaker(_cmd(), _task(), [])
    assert host.emitted == []
    failed = [{"node": "tests/integration/test_x.py::t1"}]
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.legal_red_refs", lambda nodes, completed: {"legal"}
    )
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.unexpected_reds", lambda failed_ids, legal: {"u1"}
    )
    host._all_task_nodes = lambda: []
    host._completed_ids = lambda: set()
    host._failed_ids = lambda nodes: ["tests/integration/test_x.py::t1"]
    host._check_drift_breaker(_cmd("C-5"), _task(), failed)
    assert host.emitted[0][0] == "drift_breaker"
    assert host.emitted[0][1]["unexpected"] == {"u1"}

    host2 = _Host(tmp_path)
    monkeypatch.setattr(
        "tracks.executor.deferred_gate.legal_red_refs",
        lambda nodes, completed: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    host2._check_drift_breaker(_cmd(), _task(), failed)
    assert host2.emitted == []


def test_gate_environment_identity_and_selection_identity(tmp_path: Path, monkeypatch):
    host = _Host(tmp_path)
    monkeypatch.setenv("TRAC_TEST_MARKER", "1")
    identity = host._gate_environment_identity()
    assert len(identity) == 64
    commands = {"unit": ("pytest", "x"), "integration": ("pytest", "y")}
    encoded = MImplTestOpsMixin._selection_command_identity(commands)
    assert len(encoded) == 2
    assert "integration" in encoded[0]


def test_task_content_snapshot_and_digest(tmp_path: Path):
    host = _Host(tmp_path)
    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a", encoding="utf-8")
    (root / "empty").mkdir()
    (root / "file.txt").write_text("f", encoding="utf-8")
    entries = host._task_content_snapshot(root, ["src/**", "empty/**", "file.txt"])
    assert entries["src/a.py"] == f"id:{root / 'src' / 'a.py'}"
    assert entries["empty"] == "empty-dir"
    assert entries["file.txt"] == f"id:{root / 'file.txt'}"
    assert len(host._snapshot_digest(entries)) == 64


def test_all_task_nodes_completed_ids_failed_ids(tmp_path: Path):
    store = _Store(
        State(run_id="RUN", task_refs=[{"task_id": "T-1"}], retained_completed_task_ids=["T-0"]),
        events=[
            SimpleNamespace(type="task.completed", payload={"task_id": "T-2"}),
            SimpleNamespace(type="other", payload={"task_id": "T-3"}),
        ],
    )
    host = _Host(tmp_path)
    host.store = store
    assert [n.task_id for n in host._all_task_nodes()] == ["T-1"]
    assert host._completed_ids() == {"T-0", "T-2"}
    assert host._failed_ids([{"node": "n1"}]) == ["n1"]
    assert host._failed_ids(["n2"]) == ["n2"]
    assert host._failed_ids([]) == []
