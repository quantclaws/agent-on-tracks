"""IF-PHASE-003 Phase 0 seal manifest (T-005 RED unit).

Pins the seal manifest contract from interfaces.md §1d *before* the GREEN
implementation lands in the ``executor/phase0.py`` facade:

- ``build_seal_manifest`` constructs a ``SealManifest`` from the given
  baseline version, document digests, frozen test digests, marks, and
  environment contract digest (AC-FR0257-04: stable seal_id/readonly freeze;
  AC-FR0257-05: unrecoverable -> BLOCKED).
- The ``seal_id`` is computed as ``sha256(canonical_json(remaining fields))``.

Each assertion fails today because ``build_seal_manifest`` is a
``NotImplementedError("IF-PHASE-003")`` stub in
``tracks/executor/phase0.py`` -- the M-IMPL RED on the contract.
"""

from __future__ import annotations

from tracks.executor.phase0 import SealManifest, build_seal_manifest

# AC-FR0257-04@v0.7 TRACKS-TRACE seal manifest construction

def test_build_seal_manifest_returns_seal_manifest():
    """build_seal_manifest returns a SealManifest with the correct fields."""
    result = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "a" * 64},
        frozen_test_digests={"test_integration_a.py": "b" * 64},
        marks=("unit", "integration", "e2e"),
        environment_contract_digest="c" * 64,
    )
    assert isinstance(result, SealManifest)
    assert result.baseline_version == "v0.6"
    assert result.document_digests == {"spec.md": "a" * 64}
    assert result.frozen_test_digests == {"test_integration_a.py": "b" * 64}
    assert result.marks == ("unit", "integration", "e2e")
    assert result.environment_contract_digest == "c" * 64


def test_build_seal_manifest_computes_seal_id():
    """seal_id is sha256(canonical_json of the other fields)."""
    result = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "a" * 64},
        frozen_test_digests={"test_integration_a.py": "b" * 64},
        marks=("unit", "integration", "e2e"),
        environment_contract_digest="c" * 64,
    )
    assert isinstance(result.seal_id, str)
    assert len(result.seal_id) == 64  # sha256 hex digest


# AC-FR0257-04@v0.7 TRACKS-TRACE seal_id is deterministic

def test_build_seal_manifest_deterministic_seal_id():
    """Same inputs produce the same seal_id."""
    kwargs = {
        "baseline_version": "v0.6",
        "document_digests": {"spec.md": "a" * 64},
        "frozen_test_digests": {"test_integration_a.py": "b" * 64},
        "marks": ("unit", "integration", "e2e"),
        "environment_contract_digest": "c" * 64,
    }
    r1 = build_seal_manifest(**kwargs)
    r2 = build_seal_manifest(**kwargs)
    assert r1.seal_id == r2.seal_id


# AC-FR0257-04@v0.7 TRACKS-TRACE different inputs produce different seal_id

def test_build_seal_manifest_different_version_different_seal():
    """Different baseline_version produces a different seal_id."""
    r1 = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={"spec.md": "a" * 64},
        frozen_test_digests={},
        marks=(),
        environment_contract_digest="c" * 64,
    )
    r2 = build_seal_manifest(
        baseline_version="v0.7",
        document_digests={"spec.md": "a" * 64},
        frozen_test_digests={},
        marks=(),
        environment_contract_digest="c" * 64,
    )
    assert r1.seal_id != r2.seal_id


# AC-FR0257-04@v0.7 TRACKS-TRACE empty digests / marks are valid

def test_build_seal_manifest_accepts_empty_digests():
    """Empty document and frozen test digests are acceptable (no prior docs)."""
    result = build_seal_manifest(
        baseline_version="v0.6",
        document_digests={},
        frozen_test_digests={},
        marks=(),
        environment_contract_digest="d" * 64,
    )
    assert isinstance(result, SealManifest)
    assert result.document_digests == {}
    assert result.frozen_test_digests == {}
    assert result.marks == ()

