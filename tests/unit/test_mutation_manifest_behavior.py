"""IF-MUTATION-001 mutation manifest helper REAL behavior (T-010 RED unit).

Pins the REAL behavior of the mutation_manifest helper functions. The current
implementation raises NotImplementedError, so these tests fail.

The task description requires extracting the already-delivered IF-MUTATION-001
manifest behavior into the mutation_manifest helper module.  The helper must
implement the same contract as the facade in ``mutation.py``:

- ``build_manifest`` constructs a ``MutationManifest`` with the minimal
  field set (AC-FR0262-01).
- ``validate_manifest`` validates a blob dict and rejects no-op patches
  (AC-FR0262-02) and tests-scope mutations (AC-FR0262-03).
"""

from __future__ import annotations

import pytest

from tracks.executor.mutation import MUTATION_PROTOCOL_VERSION, MutationManifest
from tracks.executor.mutation_manifest import build_manifest, validate_manifest

# AC-FR0262-01@v0.7 TRACKS-TRACE build_manifest real behavior

def test_build_manifest_returns_mutation_manifest():
    """build_manifest returns a MutationManifest with the given fields."""
    result = build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("tests/unit/test_a.py::test_x",),
        control_nodes=("tests/unit/test_a.py::test_control",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/executor/mutation_manifest.py",),
    )
    assert isinstance(result, MutationManifest)
    assert result.ac == "AC-FR0262-01@v0.7"
    assert result.protocol_version == MUTATION_PROTOCOL_VERSION
    assert result.expected_result == {"target": "killed", "controls": "green"}


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
        patch_paths=("tracks/executor/mutation_manifest.py",),
    )
    assert isinstance(result, MutationManifest)
    assert result.ac == "AC-FR0262-01@v0.7"


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

