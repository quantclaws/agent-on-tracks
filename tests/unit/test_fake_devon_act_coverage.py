"""Behavior coverage for the Fake Devon RGR actuator (``fake_devon_act``).

Drives assignment contract validation (required keys, value types, manifest
path rules, schema-2 unit/acceptance refs), failure-token classification,
red/green path derivation errors and the refactor lie/change simulation
tokens through ``FakeBackend.act`` (FR-0210, B50/#65).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.effects.fake import FakeBackend
from tracks.effects.fake_devon_act import FakeDevonActMixin


@pytest.fixture(autouse=True)
def _clear_simulation(monkeypatch):
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)


def _assignment(phase: str = "red") -> dict:
    value = {
        "task_id": "T-001",
        "phase": phase,
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": ["tests/unit/test_demo.py::test_demo"],
        "commands": [".venv/bin/python -m pytest tests/unit/test_demo.py"],
        "manifest": {
            "allowed_paths": ["tracks/impl/demo.py", "tests/unit/test_demo.py"],
            "forbidden_paths": ["tests/integration/**"],
        },
        "pre_dirty_snapshot": {},
        "result_identity": "result-1",
    }
    if phase in ("green", "refactor"):
        value["r_tree_identity"] = "r-tree-1"
    return value


def _act(repo: Path, phase: str, assignment: dict) -> dict:
    return FakeBackend(repo, "v0.5").act("devon", phase.upper(), None, None, assignment)


def _assert_contract_error(result: dict, fragment: str) -> None:
    assert result["status"] == "failed"
    assert result["failure_class"] == "contract_error"
    assert fragment in result["audit_evidence"]


# ---------------------------------------------------------------------------
# required / value / manifest validation
# ---------------------------------------------------------------------------


def test_phase_must_be_known(tmp_path):
    assignment = _assignment()
    assignment["phase"] = "bogus"
    _assert_contract_error(_act(tmp_path, "RED", assignment), "phase")


def test_task_id_must_be_nonempty_string(tmp_path):
    assignment = _assignment()
    assignment["task_id"] = "  "
    _assert_contract_error(_act(tmp_path, "RED", assignment), "task_id must be a non-empty string")


def test_commands_must_contain_a_command(tmp_path):
    assignment = _assignment()
    assignment["commands"] = []
    _assert_contract_error(_act(tmp_path, "RED", assignment), "commands must contain at least one command")


def test_pre_dirty_snapshot_type_validation(tmp_path):
    for bad in (3, "", [], ()):
        assignment = _assignment()
        assignment["pre_dirty_snapshot"] = bad
        _assert_contract_error(
            _act(tmp_path, "RED", assignment), "pre_dirty_snapshot has an invalid type"
        )


def test_result_identity_validation(tmp_path):
    assignment = _assignment()
    assignment["result_identity"] = " "
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "result_identity must be a non-empty string"
    )


def test_green_r_tree_identity_validation(tmp_path):
    assignment = _assignment("green")
    assignment["r_tree_identity"] = ""
    _assert_contract_error(_act(tmp_path, "GREEN", assignment), "r_tree_identity")


def test_manifest_must_be_object(tmp_path):
    assignment = _assignment()
    assignment["manifest"] = []
    _assert_contract_error(_act(tmp_path, "RED", assignment), "manifest must be an object")


def test_manifest_paths_must_be_valid_lists(tmp_path):
    assignment = _assignment()
    assignment["manifest"]["allowed_paths"] = [""]
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "manifest.allowed_paths must be a non-empty path list"
    )


def test_schema2_unit_refs_validation(tmp_path):
    assignment = _assignment()
    assignment["unit_refs"] = ["tracks/impl/demo.py"]
    assignment["acceptance_refs"] = ["tests/integration/test_demo.py"]
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "unit_refs must target repo-relative tests/unit paths"
    )


def test_schema2_acceptance_refs_validation(tmp_path):
    assignment = _assignment()
    assignment["unit_refs"] = []
    assignment["acceptance_refs"] = ["tests/unit/test_demo.py"]
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "acceptance_refs must target repo-relative tests/integration paths"
    )


def test_legacy_test_refs_validation(tmp_path):
    assignment = _assignment()
    assignment["test_refs"] = ["tracks/impl/demo.py"]
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "test_refs must target repo-relative tests/unit paths"
    )


def test_ac_refs_validation(tmp_path):
    assignment = _assignment()
    assignment["ac_refs"] = []
    _assert_contract_error(_act(tmp_path, "RED", assignment), "ac_refs must be a non-empty string list")


def test_r_tree_identity_wrong_type_value_error(tmp_path):
    assignment = _assignment("green")
    assignment["r_tree_identity"] = 5
    _assert_contract_error(
        _act(tmp_path, "GREEN", assignment), "r_tree_identity must be a non-empty string"
    )


def test_commands_valid_shapes():
    mixin = FakeDevonActMixin
    assert mixin._devon_commands_valid("pytest") is True
    assert mixin._devon_commands_valid({"a": "pytest", "b": ["x"]}) is True
    assert mixin._devon_commands_valid({"a": ""}) is False
    assert mixin._devon_commands_valid(5) is False
    assert mixin._devon_commands_valid(["ok", ["nested"]]) is True
    assert mixin._devon_commands_valid([]) is False


def test_manifest_path_and_test_ref_helpers():
    mixin = FakeDevonActMixin
    assert mixin._devon_manifest_path_valid(5) is False
    assert mixin._devon_manifest_path_valid("tracks/x.py") is True
    assert mixin._devon_test_refs_valid("tests/unit/x.py") is False
    assert mixin._devon_test_refs_valid(["tests/unit/x.py::t"]) is True
    assert mixin._devon_manifest_paths_valid(("tracks/x.py",)) is True


# ---------------------------------------------------------------------------
# failure tokens / path errors
# ---------------------------------------------------------------------------


def test_hang_token_maps_to_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:RED=hang")
    result = _act(tmp_path, "RED", _assignment())
    assert result["status"] == "failed"
    assert result["failure_class"] == "timeout"
    assert result["self_report"] == "simulated Devon red failure: hang"


def test_red_without_allowed_unit_path_fails_closed(tmp_path):
    assignment = _assignment()
    assignment["manifest"]["allowed_paths"] = ["tracks/impl/demo.py"]
    assignment["test_refs"] = ["tests/unit/other.py"]
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "manifest has no allowed tests/unit path"
    )


def test_red_test_path_not_regular_file(tmp_path):
    assignment = _assignment()
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "unit" / "test_demo.py").symlink_to(tmp_path / "outside.py")
    _assert_contract_error(
        _act(tmp_path, "RED", assignment), "target path is not a regular file"
    )


def test_red_production_path_not_regular_file(tmp_path):
    assignment = _assignment()
    production = tmp_path / "tracks" / "impl"
    production.mkdir(parents=True)
    (production / "demo.py").mkdir()
    result = _act(tmp_path, "RED", assignment)
    _assert_contract_error(result, "target path is not a regular file")


def test_green_without_allowed_production_path(tmp_path):
    assignment = _assignment("green")
    assignment["manifest"]["allowed_paths"] = ["tests/unit/test_demo.py"]
    _assert_contract_error(
        _act(tmp_path, "GREEN", assignment), "manifest has no allowed production path"
    )


def test_green_production_path_not_regular_file(tmp_path):
    assignment = _assignment("green")
    production = tmp_path / "tracks" / "impl"
    production.mkdir(parents=True)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    (production / "demo.py").symlink_to(tmp_path / "outside.py")
    _assert_contract_error(
        _act(tmp_path, "GREEN", assignment), "target path is not a regular file"
    )


# ---------------------------------------------------------------------------
# success phases
# ---------------------------------------------------------------------------


def test_red_success_creates_assertion_test_patch(tmp_path):
    result = _act(tmp_path, "RED", _assignment())
    assert result["status"] == "done"
    assert result["verdict"] == "assertion_failure"
    assert result["changed_paths"] == ["tests/unit/test_demo.py"]
    assert "assert namespace.get" in result["diff_ref"]
    assert not (tmp_path / "tests" / "unit" / "test_demo.py").exists()


def test_red_stub_token_creates_stub_test(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:RED=stub_token_failure")
    result = _act(tmp_path, "RED", _assignment())
    assert result["status"] == "done"
    assert result["failure_class"] == "stub_token_failure"
    assert result["verdict"] == "stub_token_failure"
    assert "NotImplementedError" in result["diff_ref"]


def test_green_success_creates_implementation_patch(tmp_path):
    result = _act(tmp_path, "GREEN", _assignment("green"))
    assert result["status"] == "done"
    assert result["changed_paths"] == ["tracks/impl/demo.py"]
    assert result["r_identity"] == "r-tree-1"
    assert "IMPLEMENTED_IF" in result["diff_ref"]


# ---------------------------------------------------------------------------
# refactor simulation tokens
# ---------------------------------------------------------------------------


def test_refactor_default_is_no_change(tmp_path):
    result = _act(tmp_path, "REFACTOR", _assignment("refactor"))
    assert result["status"] == "done"
    assert "no authorized behavior-preserving refactor" in result["self_report"]
    assert "diff_ref" not in result
    assert not (tmp_path / "tracks" / "impl" / "demo.py").exists()


def test_refactor_lie_production_path_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=lie_no_change")
    assignment = _assignment("refactor")
    assignment["manifest"]["allowed_paths"] = ["tests/unit/test_demo.py"]
    _assert_contract_error(
        _act(tmp_path, "REFACTOR", assignment), "manifest has no allowed production path"
    )


def test_refactor_lie_existing_text_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=lie_no_change")
    production = tmp_path / "tracks" / "impl"
    production.mkdir(parents=True)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    (production / "demo.py").symlink_to(tmp_path / "outside.py")
    _assert_contract_error(
        _act(tmp_path, "REFACTOR", _assignment("refactor")),
        "target path is not a regular file",
    )


def test_refactor_change_production_path_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=change")
    assignment = _assignment("refactor")
    assignment["manifest"]["allowed_paths"] = ["tests/unit/test_demo.py"]
    _assert_contract_error(
        _act(tmp_path, "REFACTOR", assignment), "manifest has no allowed production path"
    )


def test_refactor_change_existing_text_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=change")
    production = tmp_path / "tracks" / "impl"
    production.mkdir(parents=True)
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    (production / "demo.py").symlink_to(tmp_path / "outside.py")
    _assert_contract_error(
        _act(tmp_path, "REFACTOR", _assignment("refactor")),
        "target path is not a regular file",
    )


def test_refactor_lie_no_change_writes_undeclared_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=lie_no_change")
    result = _act(tmp_path, "REFACTOR", _assignment("refactor"))
    assert result["status"] == "done"
    assert result["self_report"] == "fake Devon lied about an undeclared refactor change"
    assert "diff_ref" not in result
    written = (tmp_path / "tracks" / "impl" / "demo.py").read_text(encoding="utf-8")
    assert "# undeclared refactor drift" in written


def test_refactor_change_emits_patch_and_writes_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:REFACTOR=change")
    result = _act(tmp_path, "REFACTOR", _assignment("refactor"))
    assert result["status"] == "done"
    assert result["self_report"] == "fake Devon refactor changed implementation shape"
    assert "diff_ref" in result
    written = (tmp_path / "tracks" / "impl" / "demo.py").read_text(encoding="utf-8")
    assert "# behavior-preserving refactor" in written
