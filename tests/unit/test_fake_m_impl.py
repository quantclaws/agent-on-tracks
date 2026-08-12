"""Focused unit tests for the bounded M-IMPL FakeBackend Archer PLANNING.

Covers: tasks.json artifact derivation (acceptance.md AC ids + interfaces.md
IF registry), structural validity against the Runtime taskgraph validators,
the scope whitelist shape (production + tests/unit), ``archer:PLANNING``
failure tokens failing closed without writing a valid artifact, missing-doc
fail-closed inputs, and byte-identical deterministic replay (NFR-01/NFR-02).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks import paths
from tracks.effects.fake import FakeBackend
from tracks.scaffold import _scaffold_declared_paths

_ACC = """# v0.5 — 验收标准

## FR-0030 Task graph commit

### AC-FR0030-01

  - tasks.json is the machine truth; Runtime parses it to drive DAG scheduling.

## FR-0020 Baseline recalculation

### AC-FR0020-01

  - baseline.frozen carries digest and frozen test paths.
"""

_IFC = """# v0.5 — 接口与类型化 Schema

## 5. IF Registry

### IF-IMPL-002 M-IMPL executor handler 合同

- **合同**：M-IMPL executor handler 合同.

### IF-IMPL-003 task graph 解析与校验合同

- **合同**：parse_tasks_json / validate_dag / validate_scope /
           validate_ac_coverage / validate_issue_numbers.
"""


def _vdir(tmp_path: Path, version: str = "v0.5") -> Path:
    return paths.version_dir(paths.tracks_home(tmp_path), version)


def _docs(tmp_path: Path, version: str = "v0.5") -> Path:
    vdir = _vdir(tmp_path, version)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(_ACC, encoding="utf-8")
    (vdir / "interfaces.md").write_text(_IFC, encoding="utf-8")
    return vdir


def _plan(tmp_path: Path, version: str = "v0.5",
          assignment: dict | None = None) -> dict:
    return FakeBackend(tmp_path, version).act(
        "archer", "PLANNING", None, None,
        assignment if assignment is not None else {"kind": "PLANNING"},
    )


def _tasks(tmp_path: Path) -> dict:
    raw = (_vdir(tmp_path) / "tasks.json").read_text(encoding="utf-8")
    return json.loads(raw)


# -- success path ------------------------------------------------------------

def test_planning_writes_tasks_json_artifact(tmp_path):
    _docs(tmp_path)
    result = _plan(tmp_path)

    assert result["status"] == "done"
    assert result["artifact_ref"] == str(_vdir(tmp_path) / "tasks.json")
    assert result["self_report"]
    assert result["audit_evidence"]
    assert (_vdir(tmp_path) / "tasks.json").exists()


def test_planning_tasks_are_deterministic_vertical_slices(tmp_path):
    _docs(tmp_path)
    _plan(tmp_path)
    tasks = _tasks(tmp_path)["tasks"]

    assert len(tasks) == 2
    for index, task in enumerate(tasks):
        assert task["task_id"] == f"T-{index + 1:03d}"
        assert isinstance(task["issue_number"], int) and task["issue_number"] > 0
        assert task["description"]
        assert task["ac_refs"] and all(x.startswith("AC-") for x in task["ac_refs"])
        assert task["fr_refs"] and all(x.startswith(("FR-", "NFR-")) for x in task["fr_refs"])
        assert task["if_ids"] and all(x.startswith("IF-") for x in task["if_ids"])
        assert task["test_refs"] and all(x.startswith("tests/unit/") for x in task["test_refs"])
        assert task["depends_on"] == []
        assert task["batch"]
        assert task["parallel"] in (True, False)
        assert isinstance(task["budget"], int) and task["budget"] > 0
        # scope whitelist carries an allowed production path and a tests/unit path
        scope = task["scope_boundary"].replace("\n", ",").split(",")
        assert any(p.strip().startswith("tests/unit/") for p in scope)
        assert any(p.strip() and not p.strip().startswith("tests/")
                   for p in scope)


def test_planning_covers_required_acs_with_registered_if_ids(tmp_path):
    _docs(tmp_path)
    _plan(tmp_path)
    tasks = _tasks(tmp_path)["tasks"]

    covered = {ac for task in tasks for ac in task["ac_refs"]}
    assert covered == {"AC-FR0020-01", "AC-FR0030-01"}
    registry = {"IF-IMPL-002", "IF-IMPL-003"}
    for task in tasks:
        assert set(task["if_ids"]) <= registry
    assert {if_id for task in tasks for if_id in task["if_ids"]} == registry


def test_planning_taskgraph_passes_runtime_validators(tmp_path):
    _docs(tmp_path)
    _plan(tmp_path)

    from tracks.executor.taskgraph import (
        parse_tasks_json,
        validate_ac_coverage,
        validate_dag,
        validate_issue_numbers,
        validate_scope,
        validate_task_structure,
    )

    raw = (_vdir(tmp_path) / "tasks.json").read_text(encoding="utf-8")
    nodes, err = parse_tasks_json(raw)
    assert err is None
    assert not validate_task_structure(nodes)
    ok, _ = validate_dag(nodes)
    assert ok
    ok, _ = validate_scope(nodes)
    assert ok
    ok, _ = validate_ac_coverage(
        nodes, ["AC-FR0020-01", "AC-FR0030-01"], {"IF-IMPL-002", "IF-IMPL-003"})
    assert ok
    ok, _ = validate_issue_numbers(nodes)
    assert ok


# -- failure tokens fail closed without a valid artifact ---------------------

@pytest.mark.parametrize("token", ["fail", "timeout", "over_reach", "no_target_diff"])
def test_planning_failure_tokens_do_not_write_tasks_json(
    tmp_path, monkeypatch, token,
):
    _docs(tmp_path)
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", f"archer:PLANNING={token}")
    result = _plan(tmp_path)

    assert result["status"] == "failed"
    assert result["artifact_ref"] is None
    assert result["failure_class"]
    assert result["audit_evidence"]
    assert not (_vdir(tmp_path) / "tasks.json").exists()


# -- missing/empty doc inputs fail closed ------------------------------------

def test_planning_missing_acceptance_fails_closed(tmp_path):
    vdir = _vdir(tmp_path)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "interfaces.md").write_text(_IFC, encoding="utf-8")

    result = _plan(tmp_path)

    assert result["status"] == "failed"
    assert result["failure_class"] == "invalid_taskgraph"
    assert "acceptance.md" in result["audit_evidence"]
    assert not (vdir / "tasks.json").exists()


def test_planning_missing_interfaces_fails_closed(tmp_path):
    vdir = _vdir(tmp_path)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(_ACC, encoding="utf-8")

    result = _plan(tmp_path)

    assert result["status"] == "failed"
    assert "interfaces.md" in result["audit_evidence"]
    assert not (vdir / "tasks.json").exists()


def test_planning_empty_if_registry_fails_closed(tmp_path):
    vdir = _vdir(tmp_path)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(_ACC, encoding="utf-8")
    (vdir / "interfaces.md").write_text(
        "# Interfaces\n\n## 5. IF Registry\n\n", encoding="utf-8")

    result = _plan(tmp_path)

    assert result["status"] == "failed"
    assert "IF Registry" in result["audit_evidence"]
    assert not (vdir / "tasks.json").exists()


# -- deterministic replay ----------------------------------------------------

def test_planning_replay_is_byte_identical(tmp_path):
    _docs(tmp_path)
    first = _plan(tmp_path)
    first_raw = (_vdir(tmp_path) / "tasks.json").read_bytes()

    second = FakeBackend(tmp_path, "v0.5").act(
        "archer", "PLANNING", None, None, {"kind": "PLANNING"})
    second_raw = (_vdir(tmp_path) / "tasks.json").read_bytes()

    assert first_raw == second_raw
    assert first["self_report"] == second["self_report"]
    assert first["audit_evidence"] == second["audit_evidence"]


@pytest.mark.parametrize(
    "substate", ["PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE"]
)
@pytest.mark.parametrize("verdict", ["pass", "revise", "fail"])
def test_m_impl_prism_result_metadata_matches_simulation(
    tmp_path, monkeypatch, substate, verdict
):
    criteria_pack = {"name": "tracks-prism-impl", "version": "0.1"}
    monkeypatch.setenv(
        "TRAC_FAKE_SIMULATE",
        f"prism:{substate}={verdict};"
        "prism:defect_classification=impl_defect",
    )

    result = FakeBackend(tmp_path, "v0.5").act(
        "prism", substate, None, None, {"criteria_pack": criteria_pack}
    )

    assert result["status"] == "done"
    assert result["verdict"] == verdict
    assert result["criteria_pack"] == criteria_pack
    if verdict == "pass":
        assert "defect_classification" not in result
    else:
        assert result["defect_classification"] == "impl_defect"


# -- v0.5-plus capability matrix: Shield contracts + reach entrypoints --------

# The M-IMPL capability is version-gated (IF-IMPL-001/IF-SHIELD-001): every
# flow version at or above v0.5 gets the behavioral Fake machinery (Shield
# tests that fail legit-Red and pass once tracks/impl/{ac_slug}.py carries the
# matching IMPLEMENTED_IF; reach-entrypoint artifacts for check_reach), while
# v0.4 (legacy M-TEST boundary) and malformed versions fail closed with the
# legacy stub and no reach entries.
_M_IMPL_CAPABILITY_VERSIONS = ("v0.5", "v0.6", "v0.10")
_LEGACY_FAIL_CLOSED_VERSIONS = ("v0.4", "v0.5.1", "0.5", "v0", "bogus")
_SHIELD_TASKS = [{"ac_id": "AC-FR0010-01", "layers": ["integration"],
                  "if_ids": ["IF-MTEST-001"]}]


def _shield_body(tmp_path: Path, version: str) -> str:
    """Public capability behavior: the generated default Fake Shield test body
    for ``version`` (deterministic FakeBackend setup, no env injection)."""
    FakeBackend(tmp_path, version).act(
        "shield", "WRITE", None, None, {"test_tasks": _SHIELD_TASKS})
    return (tmp_path / "tests" / "integration"
            / "test_ac_fr0010_01.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("version", _M_IMPL_CAPABILITY_VERSIONS)
def test_m_impl_versions_get_behavioral_shield_tests(tmp_path, version):
    """v0.5/v0.6/v0.10 use the deterministic behavioral Shield contract over
    ``tracks/impl/{ac_slug}.py`` — never the legacy NotImplementedError
    M-TEST stub token."""
    body = _shield_body(tmp_path, version)

    assert "NotImplementedError" not in body
    assert "IMPLEMENTED_IF" in body
    assert "import runpy" in body
    assert "tracks/impl/ac_fr0010_01.py" in body
    assert "'IF-MTEST-001'" in body


@pytest.mark.parametrize("version", _M_IMPL_CAPABILITY_VERSIONS)
def test_m_impl_versions_materialize_reach_entries(tmp_path, version):
    """v0.5/v0.6/v0.10 Fake M-DESIGN declare and materialize the reach-entries
    artifact (``.tracks/reach-entries.txt``) the M-IMPL Green/check_reach path
    needs."""
    vdir = _docs(tmp_path, version)
    result = FakeBackend(tmp_path, version).act(
        "archer", "DRAFT", None, None, {"kind": "DRAFT"})
    assert result["status"] == "done"

    reach_path = tmp_path / ".tracks" / "reach-entries.txt"
    assert reach_path.is_file(), (
        f"{version} Fake M-DESIGN must materialize reach entrypoints")
    assert reach_path.read_text(encoding="utf-8").splitlines() == [
        "tracks.impl.ac_fr0020_01",
        "tracks.impl.ac_fr0030_01",
    ]
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert ".tracks/reach-entries.txt" in _scaffold_declared_paths(architecture)


@pytest.mark.parametrize("version", _LEGACY_FAIL_CLOSED_VERSIONS)
def test_pre_v05_and_malformed_versions_fail_closed(tmp_path, version):
    """v0.4 (legacy M-TEST boundary) and malformed versions fail closed: the
    Shield keeps the legacy legit stub, and the Fake M-DESIGN writes no
    reach-order artifact — never referencing a not-yet-created
    ``tracks/impl`` production symbol."""
    body = _shield_body(tmp_path, version)
    assert 'raise NotImplementedError("IF-MTEST-001")' in body
    assert "IMPLEMENTED_IF" not in body
    assert "runpy" not in body

    vdir = _docs(tmp_path, version)
    result = FakeBackend(tmp_path, version).act(
        "archer", "DRAFT", None, None, {"kind": "DRAFT"})
    assert result["status"] == "done"
    assert not (tmp_path / ".tracks" / "reach-entries.txt").exists()
    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    assert ".tracks/reach-entries.txt" not in _scaffold_declared_paths(architecture)
