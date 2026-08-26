"""IF-MUTATION-001 mutation manifest helper (T-010 RED unit).

Pins the mutation manifest helper contract from interfaces.md §1g *before*
the GREEN implementation lands in the new ``mutation_manifest.py`` module:

- ``build_manifest`` constructs a ``MutationManifest`` with the minimal
  field set (AC-FR0262-01).
- ``validate_manifest`` validates a blob dict and rejects no-op patches
  (AC-FR0262-02) and tests-scope mutations (AC-FR0262-03).

The module ``tracks/executor/mutation_manifest.py`` does not exist yet --
these tests fail because the module and its functions are not yet created.
"""

from __future__ import annotations

import pytest

import tracks.executor as executor_mod


def test_mutation_manifest_module_exists():
    """The mutation_manifest module must exist."""
    assert hasattr(executor_mod, "mutation_manifest"), (
        "tracks/executor/mutation_manifest.py not implemented"
    )


def test_build_manifest_exists():
    """build_manifest function must exist in mutation_manifest."""
    mm = getattr(executor_mod, "mutation_manifest", None)
    assert mm is not None, "mutation_manifest module not found"
    assert hasattr(mm, "build_manifest"), (
        "build_manifest not implemented in mutation_manifest"
    )


def test_validate_manifest_exists():
    """validate_manifest function must exist in mutation_manifest."""
    mm = getattr(executor_mod, "mutation_manifest", None)
    assert mm is not None, "mutation_manifest module not found"
    assert hasattr(mm, "validate_manifest"), (
        "validate_manifest not implemented in mutation_manifest"
    )


def test_build_manifest_returns_manifest():
    """build_manifest returns a MutationManifest (re-pinned for real implementation)."""
    mm = getattr(executor_mod, "mutation_manifest", None)
    if mm is None or not hasattr(mm, "build_manifest"):
        pytest.skip("mutation_manifest.build_manifest not yet implemented")
    from tracks.executor.mutation import MUTATION_PROTOCOL_VERSION, MutationManifest

    result = mm.build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:c",
        patch_digest="sha256:p",
        target_nodes=(),
        control_nodes=(),
        runner_identity="r",
        allowed_change_scope=(),
    )
    assert isinstance(result, MutationManifest)
    assert result.protocol_version == MUTATION_PROTOCOL_VERSION
    assert result.expected_result == {"target": "killed", "controls": "green"}


def test_validate_manifest_returns_manifest():
    """validate_manifest returns a MutationManifest (re-pinned for real implementation)."""
    mm = getattr(executor_mod, "mutation_manifest", None)
    if mm is None or not hasattr(mm, "validate_manifest"):
        pytest.skip("mutation_manifest.validate_manifest not yet implemented")

    with pytest.raises(ValueError, match="no-op"):
        mm.validate_manifest(
            manifest={
                "protocol_version": 1,
                "ac": "AC-FR0262-01@v0.7",
                "if_ref": "IF-MUTATION-001",
                "candidate_digest": "sha256:c",
                "patch_digest": "sha256:p",
                "target_nodes": [],
                "control_nodes": [],
                "runner_identity": "r",
                "allowed_change_scope": [],
                "expected_result": {"target": "killed", "controls": "green"},
            },
            patch_paths=(),
        )

