"""Focused deterministic Fake Devon phase tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tracks.effects.fake import FakeBackend


def _assignment(phase: str = "red") -> dict:
    value = {
        "task_id": "T-001",
        "phase": phase,
        "if_ids": ["IF-IMPL-001"],
        "ac_refs": ["AC-FR0001-01"],
        "test_refs": ["tests/unit/test_demo.py::test_demo"],
        "commands": [".venv/bin/python -m pytest -n 4 tests/unit/test_demo.py"],
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


def _apply_check(tmp_path: Path, patch: str) -> None:
    repo = tmp_path / "patch-repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "apply", "--check", "-"],
        input=patch,
        text=True,
        cwd=repo,
        check=True,
    )


@pytest.mark.parametrize("phase", ["red", "green", "refactor"])
def test_missing_devon_contract_fails_closed(tmp_path, phase):
    assignment = _assignment(phase)
    del assignment["task_id"]
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        phase.upper(),
        None,
        None,
        assignment,
    )
    assert result["status"] == "failed"
    assert result["failure_class"] == "contract_error"
    assert "diff_ref" not in result


def test_red_patch_is_behavioral_and_non_mutating(tmp_path):
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "RED",
        None,
        None,
        _assignment("red"),
    )
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after
    assert result["status"] == "done"
    assert result["changed_paths"] == ["tests/unit/test_demo.py"]
    assert "NotImplementedError" not in result["diff_ref"]
    assert "assert namespace.get" in result["diff_ref"]
    _apply_check(tmp_path, result["diff_ref"])


def test_green_patch_is_production_only_and_keeps_r_identity(tmp_path):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        _assignment("green"),
    )
    assert result["status"] == "done"
    assert result["changed_paths"] == ["tracks/impl/demo.py"]
    assert result["r_identity"] == "r-tree-1"
    assert "tests/" not in result["diff_ref"]
    _apply_check(tmp_path, result["diff_ref"])


def test_red_then_green_patches_make_the_behavioral_test_pass(tmp_path):
    red = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "RED",
        None,
        None,
        _assignment("red"),
    )
    green = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        _assignment("green"),
    )
    repo = tmp_path / "combined-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for patch in (red["diff_ref"], green["diff_ref"]):
        subprocess.run(
            ["git", "apply", "-"],
            input=patch,
            text=True,
            cwd=repo,
            check=True,
        )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/unit/test_demo.py"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_refactor_returns_stable_no_change_evidence(tmp_path):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "REFACTOR",
        None,
        None,
        _assignment("refactor"),
    )
    assert result["status"] == "done"
    assert result["changed_paths"] == []
    assert result["no_change_reason"]
    assert "diff_ref" not in result
    assert result["r_identity"] == "r-tree-1"


def test_evidence_and_patch_are_byte_deterministic(tmp_path):
    assignment = _assignment("green")
    first = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        assignment,
    )
    second = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        assignment,
    )
    assert first == second
    assert first["commands"][0]["cmd"].startswith(".venv/bin/python")
    assert "-n 4" in first["commands"][0]["cmd"]


def test_stub_token_is_a_classifiable_red_patch_and_retry_is_legal(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "devon:RED=stub_token_failure|ok")
    backend = FakeBackend(tmp_path, "v0.5")
    first = backend.act("devon", "RED", None, None, _assignment("red"))
    second = backend.act("devon", "RED", None, None, _assignment("red"))
    assert first["status"] == second["status"] == "done"
    assert first["verdict"] == first["failure_class"] == "stub_token_failure"
    assert 'NotImplementedError("IF-IMPL-001")' in first["diff_ref"]
    assert second["verdict"] == "assertion_failure"
    _apply_check(tmp_path, first["diff_ref"])
    _apply_check(tmp_path, second["diff_ref"])


# -- audit_evidence structured-object contract (worktree/dispatch consumers) --

_REQUIRED_AUDIT_KEYS = (
    "phase",
    "changed_paths",
    "commands",
    "results",
    "manifest_compliance",
    "pre_identity",
    "post_identity",
    "implemented_if_ids",
    "result_identity",
)


@pytest.mark.parametrize("phase", ["red", "green", "refactor"])
def test_audit_evidence_is_a_structured_dict_for_done_outcomes(tmp_path, phase):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        phase.upper(),
        None,
        None,
        _assignment(phase),
    )
    assert result["status"] == "done"
    audit = result["audit_evidence"]
    assert isinstance(audit, dict)
    for key in _REQUIRED_AUDIT_KEYS:
        assert key in audit, f"audit_evidence missing {key}"
    assert audit["phase"] == phase
    assert audit["changed_paths"] == result["changed_paths"]
    assert audit["manifest_compliance"] is True
    assert audit["pre_identity"] == result["pre_identity"]
    assert audit["post_identity"] == result["post_identity"]
    assert audit["implemented_if_ids"] == result["implemented_if_ids"]
    assert audit["implemented_if_ids"] == ["IF-IMPL-001"]
    assert audit["result_identity"] == "result-1"
    assert isinstance(audit["commands"], list) and audit["commands"]
    assert isinstance(audit["results"], list) and audit["results"]
    assert audit["commands"][0]["cmd"].startswith(".venv/bin/python")
    assert audit["results"][0].get("classification")


@pytest.mark.parametrize("phase", ["green", "refactor"])
def test_audit_evidence_carries_r_identity_for_green_and_refactor(tmp_path, phase):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        phase.upper(),
        None,
        None,
        _assignment(phase),
    )
    audit = result["audit_evidence"]
    assert audit["r_identity"] == "r-tree-1"
    assert audit["r_identity"] == result["r_identity"]


def test_audit_evidence_red_omits_r_identity(tmp_path):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "RED",
        None,
        None,
        _assignment("red"),
    )
    assert "r_identity" not in result["audit_evidence"]
    assert "no_change_reason" not in result["audit_evidence"]


def test_audit_evidence_refactor_carries_no_change_reason(tmp_path):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "REFACTOR",
        None,
        None,
        _assignment("refactor"),
    )
    audit = result["audit_evidence"]
    assert audit["no_change_reason"] == result["no_change_reason"]
    assert audit["changed_paths"] == []


def test_audit_evidence_changed_paths_excludes_integration_and_e2e(tmp_path):
    for phase in ("red", "green", "refactor"):
        result = FakeBackend(tmp_path, "v0.5").act(
            "devon",
            phase.upper(),
            None,
            None,
            _assignment(phase),
        )
        for path in result["audit_evidence"]["changed_paths"]:
            assert not path.startswith(("tests/integration/", "tests/e2e/"))


def test_audit_evidence_keeps_top_level_runtime_fields_unchanged(tmp_path):
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        _assignment("green"),
    )
    for key in (
        "phase",
        "changed_paths",
        "commands",
        "results",
        "manifest_compliance",
        "pre_identity",
        "post_identity",
        "implemented_if_ids",
        "result_identity",
        "r_identity",
    ):
        assert key in result
    audit = result["audit_evidence"]
    assert audit["changed_paths"] == result["changed_paths"]
    assert audit["commands"] is not result["commands"]
    assert audit["results"] is not result["results"]
    assert audit["commands"][0] is not result["commands"][0]


def test_audit_evidence_is_byte_deterministic(tmp_path):
    assignment = _assignment("green")
    first = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        assignment,
    )
    second = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        assignment,
    )
    assert first["audit_evidence"] == second["audit_evidence"]


@pytest.mark.parametrize("token", ["fail", "over_reach"])
def test_token_failure_outcome_carries_structured_audit_evidence(
    tmp_path,
    monkeypatch,
    token,
):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", f"devon:GREEN={token}")
    result = FakeBackend(tmp_path, "v0.5").act(
        "devon",
        "GREEN",
        None,
        None,
        _assignment("green"),
    )
    assert result["status"] == "failed"
    assert result["failure_class"]
    audit = result["audit_evidence"]
    assert isinstance(audit, dict)
    assert audit["phase"] == "green"
    assert audit["changed_paths"] == result["changed_paths"] == []
    assert audit["manifest_compliance"] is True
    assert audit["result_identity"] == "result-1"
    assert audit["r_identity"] == "r-tree-1"
