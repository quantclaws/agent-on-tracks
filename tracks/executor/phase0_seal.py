"""Phase 0 seal manifest implementation (IF-PHASE-003).

Implements ``build_seal_manifest`` declared in interfaces.md §1d.  The
``seal_id`` is computed as ``sha256(canonical_json(remaining fields))``
(AC-FR0257-04: stable seal_id / readonly freeze).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256

from tracks.executor.phase0 import SealManifest


def _canonical_json(
    baseline_version: str,
    document_digests: Mapping[str, str],
    frozen_test_digests: Mapping[str, str],
    marks: Sequence[str],
    environment_contract_digest: str,
) -> str:
    """Deterministic JSON serialization of the seal fields (excluding seal_id).

    Uses ``sort_keys=True`` and compact separators so that the same logical
    content always produces the same digest.
    """
    payload = {
        "baseline_version": baseline_version,
        "document_digests": dict(sorted(document_digests.items())),
        "frozen_test_digests": dict(sorted(frozen_test_digests.items())),
        "marks": sorted(marks) if marks else [],
        "environment_contract_digest": environment_contract_digest,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_seal_manifest(
    baseline_version: str,
    document_digests: Mapping[str, str],
    frozen_test_digests: Mapping[str, str],
    marks: Sequence[str],
    environment_contract_digest: str,
) -> SealManifest:
    """Construct a ``SealManifest`` with a deterministic ``seal_id``.

    The ``seal_id`` is ``sha256(canonical_json(baseline_version,
    document_digests, frozen_test_digests, marks,
    environment_contract_digest))`` -- the hex digest of the canonical JSON
    representation of the other fields (interfaces.md §1d).
    """
    canonical = _canonical_json(
        baseline_version, document_digests, frozen_test_digests,
        marks, environment_contract_digest,
    )
    seal_id = sha256(canonical.encode("utf-8")).hexdigest()
    return SealManifest(
        baseline_version=baseline_version,
        document_digests=dict(document_digests),
        frozen_test_digests=dict(frozen_test_digests),
        marks=tuple(marks),
        environment_contract_digest=environment_contract_digest,
        seal_id=seal_id,
    )

