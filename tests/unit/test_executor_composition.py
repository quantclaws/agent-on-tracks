"""IF-MUTATION-001 executor package composition (T-010 RED unit).

Pins the executor package composition contract: the helper functions
``build_manifest`` and ``validate_manifest`` must be accessible via
``tracks.executor`` (not just ``tracks.executor.mutation_manifest``).

The current implementation only imports the module, not the individual
functions, so these tests fail.
"""

from __future__ import annotations

import pytest

import tracks.executor as executor_mod


def test_executor_exposes_build_manifest():
    """build_manifest must be accessible via tracks.executor."""
    assert hasattr(executor_mod, "build_manifest"), (
        "build_manifest not exposed via tracks.executor"
    )


def test_executor_exposes_validate_manifest():
    """validate_manifest must be accessible via tracks.executor."""
    assert hasattr(executor_mod, "validate_manifest"), (
        "validate_manifest not exposed via tracks.executor"
    )


def test_executor_build_manifest_returns_manifest():
    """tracks.executor.build_manifest returns a MutationManifest (re-pinned)."""
    fn = getattr(executor_mod, "build_manifest", None)
    if fn is None:
        pytest.skip("build_manifest not exposed via tracks.executor")
    from tracks.executor.mutation import MUTATION_PROTOCOL_VERSION, MutationManifest

    result = fn(
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


def test_executor_validate_manifest_returns_manifest():
    """tracks.executor.validate_manifest returns a MutationManifest (re-pinned)."""
    fn = getattr(executor_mod, "validate_manifest", None)
    if fn is None:
        pytest.skip("validate_manifest not exposed via tracks.executor")

    with pytest.raises(ValueError, match="no-op"):
        fn(
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

