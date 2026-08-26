"""IF-MUTATION-002 mutation manifest validation (T-011 RED unit).

Pins the manifest validation contract from interfaces.md §1g and the
failure-matrix fail-closed guard (AC-FR0263-04) *before* the GREEN
implementation lands:

- ``validate_manifest`` must reject **empty target nodes** — an experiment
  with no target node is malformed (AC-FR0263-04: malformed-result class).
- ``validate_manifest`` must reject **empty control nodes** — an experiment
  with no control node is malformed (no controls-green baseline).
- ``validate_manifest`` must reject **overlapping target/control nodes** — a
  node cannot be both target and control (invalid manifest).
- ``validate_manifest`` must reject manifests missing required fields
  (``ac``, ``if_ref``, ``candidate_digest``, ``patch_digest``,
  ``runner_identity``, ``allowed_change_scope``).

Each assertion fails today: ``validate_manifest`` in ``mutation_manifest.py``
parses the dict directly without checking for these structural validity
conditions.
"""

from __future__ import annotations

import pytest

from tracks.executor.mutation import validate_manifest

_VALID = {
    "protocol_version": 1,
    "ac": "AC-FR0263-01@v0.7",
    "if_ref": "IF-MUTATION-002",
    "candidate_digest": "sha256:c",
    "patch_digest": "sha256:p",
    "target_nodes": ("t1",),
    "control_nodes": ("c1",),
    "runner_identity": "runtime:mutation-v1",
    "allowed_change_scope": ("tracks/",),
    "expected_result": {"target": "killed", "controls": "green"},
}


# AC-FR0263-04@v0.7 TRACKS-TRACE failure matrix: malformed manifest blocked

def test_validate_manifest_rejects_empty_target_nodes():
    """Empty target_nodes must be rejected (malformed)."""
    manifest = dict(_VALID, target_nodes=())
    with pytest.raises((ValueError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_empty_control_nodes():
    """Empty control_nodes must be rejected (malformed)."""
    manifest = dict(_VALID, control_nodes=())
    with pytest.raises((ValueError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_overlapping_nodes():
    """Overlapping target/control nodes must be rejected (invalid)."""
    manifest = dict(_VALID, target_nodes=("t1",), control_nodes=("t1",))
    with pytest.raises((ValueError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_missing_ac():
    """Missing ac field must be rejected."""
    manifest = {k: v for k, v in _VALID.items() if k != "ac"}
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_missing_if_ref():
    """Missing if_ref field must be rejected."""
    manifest = {k: v for k, v in _VALID.items() if k != "if_ref"}
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_missing_runner_identity():
    """Missing runner_identity field must be rejected."""
    manifest = {k: v for k, v in _VALID.items() if k != "runner_identity"}
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_rejects_missing_candidate_digest():
    """Missing candidate_digest field must be rejected."""
    manifest = {k: v for k, v in _VALID.items() if k != "candidate_digest"}
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_manifest(manifest, ["patch.diff"])


def test_validate_manifest_accepts_valid_manifest():
    """A valid manifest must pass validation (sanity anchor)."""
    result = validate_manifest(_VALID, ["patch.diff"])
    assert result.ac == "AC-FR0263-01@v0.7"
    assert result.target_nodes == ("t1",)
    assert result.control_nodes == ("c1",)
