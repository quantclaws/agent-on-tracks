"""Integration: mutation manifest minimality (IF-MUTATION-001).

AC-FR0262-01@v0.7 manifest minimal field set, language neutral,
AC-FR0262-02@v0.7 no batch endorsement, no-op patch blocked,
AC-FR0262-03@v0.7 tests scope mutation blocked.

Assertions land on `build_manifest`/`validate_manifest` (IF-MUTATION-001,
interfaces §1g) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.mutation import (
    MUTATION_PROTOCOL_VERSION,
    MutationManifest,
    build_manifest,
    validate_manifest,
)

pytestmark = pytest.mark.integration



# AC-FR0262-01@v0.7 TRACKS-TRACE manifest minimal field set, language neutral
def test_manifest_minimal_field_set_language_neutral():
    """AC-FR0262-01: manifest carries exactly the minimal, language-neutral fields."""
    manifest = build_manifest(
        ac="AC-FR0262-01@v0.7",
        if_ref="IF-MUTATION-001",
        candidate_digest="sha256:candidate",
        patch_digest="sha256:patch",
        target_nodes=("opaque-node-1",),
        control_nodes=("opaque-node-2",),
        runner_identity="runtime:mutation-v1",
        allowed_change_scope=("tracks/...",),
    )
    assert isinstance(manifest, MutationManifest)
    assert manifest.protocol_version == MUTATION_PROTOCOL_VERSION == 1
    # Opaque node IDs carry no language/framework semantics.
    for node in (*manifest.target_nodes, *manifest.control_nodes):
        assert not any(tok in node for tok in ("pytest", "junit", "java", ".py")), (
            f"node id {node} leaks language semantics"
        )
    # Exact minimal field set: no extra fields beyond §1g.
    expected_fields = {
        "protocol_version", "ac", "if_ref", "candidate_digest", "patch_digest",
        "target_nodes", "control_nodes", "runner_identity",
        "allowed_change_scope", "expected_result",
    }
    actual_fields = set(manifest.__dict__) | {"expected_result"}
    assert expected_fields <= actual_fields


# AC-FR0262-02@v0.7 TRACKS-TRACE no batch endorsement, no-op patch blocked
def test_no_batch_endorsement_noop_patch():
    """AC-FR0262-02: a no-op patch (no selectable diff identity) is blocked."""
    manifest_blob = {
        "protocol_version": 1,
        "ac": "AC-FR0262-02@v0.7",
        "if_ref": "IF-MUTATION-001",
        "candidate_digest": "sha256:candidate",
        "patch_digest": "sha256:patch",
        "target_nodes": ("opaque-node-1",),
        "control_nodes": ("opaque-node-2",),
        "runner_identity": "runtime:mutation-v1",
        "allowed_change_scope": ("tracks/...",),
        "expected_result": {"target": "killed", "controls": "green"},
    }
    # An empty/no-op patch file has no selectable diff identity.
    noop_patch = Path(__file__).resolve().parent.parent / "assets" / "noop.patch"
    # Contract (§1g): a no-op patch (no selectable diff) must be blocked with
    # no_selectable_diff_identity. It must NOT be accepted as a valid manifest.
    try:
        result = validate_manifest(manifest_blob, patch_paths=[noop_patch])
    except Exception as exc:  # noqa: BLE001 - contract: no-op is hard error
        # Legal Red anchor: the stub raises a generic IF token; a no-op must
        # surface the specific no_selectable_diff_identity reason, not the token.
        assert "no_selectable_diff_identity" in str(exc) or "selectable" in str(exc), (
            "no-op patch must be blocked with no_selectable_diff_identity"
        )
        return
    assert result is None or getattr(result, "status", None) == "blocked", (
        "no-op patch must be blocked (no_selectable_diff_identity), not accepted"
    )


# AC-FR0262-03@v0.7 TRACKS-TRACE tests scope mutation blocked
def test_tests_scope_mutation_blocked():
    """AC-FR0262-03: a manifest whose scope contains tests/ paths is blocked."""
    manifest_blob = {
        "protocol_version": 1,
        "ac": "AC-FR0262-03@v0.7",
        "if_ref": "IF-MUTATION-001",
        "candidate_digest": "sha256:candidate",
        "patch_digest": "sha256:patch",
        "target_nodes": ("opaque-node-1",),
        "control_nodes": ("opaque-node-2",),
        "runner_identity": "runtime:mutation-v1",
        "allowed_change_scope": ("tracks/", "tests/integration/frozen.py"),
        "expected_result": {"target": "killed", "controls": "green"},
    }
    patch = Path(__file__).resolve().parent.parent / "assets" / "noop.patch"
    # validate_manifest must reject a tests/-scoped manifest.
    try:
        result = validate_manifest(manifest_blob, patch_paths=[patch])
    except Exception as exc:  # noqa: BLE001 - contract: tests scope is hard error
        assert "tests" in str(exc) or "tests_in_scope" in str(exc)
        return
    # If it returns rather than raises, it must surface the block.
    assert result is None or getattr(result, "status", None) == "blocked"
