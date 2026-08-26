"""IF-MUTATION-001 mutation manifest facade (T-010 RED unit).

Pins the mutation manifest contract from interfaces.md §1g *before* the GREEN
implementation lands in ``tracks/executor/mutation.py``:

- ``build_manifest`` constructs a ``MutationManifest`` with the minimal
  field set: AC/IF identity, target/control nodes, change scope, and
  ``expected_result = {"target": "killed", "controls": "green"}``
  (AC-FR0262-01: minimal language-neutral field set).
- ``validate_manifest`` validates a blob dict and rejects no-op patches
  (AC-FR0262-02: no batch endorsement) and tests-scope mutations
  (AC-FR0262-03: tests scope mutation blocked).

Each assertion fails today because ``build_manifest`` and
``validate_manifest`` are ``NotImplementedError("IF-MUTATION-001")`` stubs
-- the M-IMPL RED on the contract.
"""

from __future__ import annotations

import pytest

from tracks.executor.mutation import (
    MUTATION_PROTOCOL_VERSION,
    MutationManifest,
    build_manifest,
    validate_manifest,
)

# AC-FR0262-01@v0.7 TRACKS-TRACE build_manifest minimal field set

def test_build_manifest_returns_mutation_manifest():
    """build_manifest constructs a MutationManifest with the given fields."""
    result = build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("tests/unit/test_a.py::test_x",),
        control_nodes=("tests/unit/test_a.py::test_control",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/executor/mutation.py",),
    )
    assert isinstance(result, MutationManifest)
    assert result.ac == "AC-FR0262-01@v0.7"
    assert result.if_ref == "IF-MUTATION-001"
    assert result.candidate_digest == "sha256:candidate"
    assert result.patch_digest == "sha256:patch"
    assert result.target_nodes == ("tests/unit/test_a.py::test_x",)
    assert result.control_nodes == ("tests/unit/test_a.py::test_control",)
    assert result.runner_identity == "runtime:mutation-v1"
    assert result.allowed_change_scope == ("tracks/executor/mutation.py",)


def test_build_manifest_sets_protocol_version():
    """build_manifest sets protocol_version to MUTATION_PROTOCOL_VERSION."""
    result = build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=(),
        control_nodes=(),
        runner_identity="r",
        allowed_change_scope=(),
    )
    assert result.protocol_version == MUTATION_PROTOCOL_VERSION


def test_build_manifest_sets_expected_result():
    """build_manifest sets expected_result for target kill and controls green."""
    result = build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=("t1",),
        control_nodes=("c1",),
        runner_identity="r",
        allowed_change_scope=(),
    )
    assert result.expected_result == {"target": "killed", "controls": "green"}


# AC-FR0262-02@v0.7 TRACKS-TRACE validate_manifest rejects no-op

def test_validate_manifest_returns_mutation_manifest():
    """validate_manifest parses a valid manifest blob."""
    result = validate_manifest(
        manifest={
            "protocol_version": 1,
            "ac": "AC-FR0262-01@v0.7",
            "if_ref": "IF-MUTATION-001",
            "candidate_digest": "sha256:c",
            "patch_digest": "sha256:p",
            "target_nodes": ["t1"],
            "control_nodes": ["c1"],
            "runner_identity": "r",
            "allowed_change_scope": ["tracks/"],
            "expected_result": {"target": "killed", "controls": "green"},
        },
        patch_paths=("tracks/executor/mutation.py",),
    )
    assert isinstance(result, MutationManifest)
    assert result.ac == "AC-FR0262-01@v0.7"
    assert result.if_ref == "IF-MUTATION-001"


# AC-FR0262-03@v0.7 TRACKS-TRACE tests scope mutation blocked

def test_validate_manifest_rejects_tests_scope():
    """validate_manifest blocks when patch paths include test directories."""
    with pytest.raises(ValueError, match="test scope"):
        validate_manifest(
            manifest={
                "protocol_version": 1,
                "ac": "AC-FR0262-01@v0.7",
                "if_ref": "IF-MUTATION-001",
                "candidate_digest": "sha256:c",
                "patch_digest": "sha256:p",
                "target_nodes": ["t1"],
                "control_nodes": ["c1"],
                "runner_identity": "r",
                "allowed_change_scope": ["tracks/"],
                "expected_result": {"target": "killed", "controls": "green"},
            },
            patch_paths=("tests/unit/test_x.py",),
        )


# AC-FR0262-02@v0.7 TRACKS-TRACE no batch endorsement

def test_validate_manifest_rejects_noop_patch():
    """validate_manifest blocks no-op patches (empty / no change)."""
    with pytest.raises(ValueError, match="no-op"):
        validate_manifest(
            manifest={
                "protocol_version": 1,
                "ac": "AC-FR0262-01@v0.7",
                "if_ref": "IF-MUTATION-001",
                "candidate_digest": "sha256:c",
                "patch_digest": "sha256:p",
                "target_nodes": ["t1"],
                "control_nodes": ["c1"],
                "runner_identity": "r",
                "allowed_change_scope": ["tracks/"],
                "expected_result": {"target": "killed", "controls": "green"},
            },
            patch_paths=(),
        )

