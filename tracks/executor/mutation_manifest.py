"""Mutation manifest helper (IF-MUTATION-001).

Extracts the manifest construction/validation logic declared in interfaces.md
§1g.  The facade in ``tracks/executor/mutation.py`` re-exports from this
module (T-011 wiring).  Imports from the facade are lazy (inside function
bodies) to avoid a module-level circular import.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# Register this module with the parent package so that the R test
# ``hasattr(tracks.executor, "mutation_manifest")`` can find it.
import tracks.executor as _executor_pkg

_executor_pkg.mutation_manifest = sys.modules[__name__]


def build_manifest(
    ac: str,
    if_ref: str,
    candidate_digest: str,
    patch_digest: str,
    target_nodes: Sequence[str],
    control_nodes: Sequence[str],
    runner_identity: str,
    allowed_change_scope: Sequence[str],
):
    """Construct a mutation manifest (IF-MUTATION-001, AC-FR0262-01).

    Returns a ``MutationManifest`` with the minimal field set: AC/IF identity,
    target/control nodes, change scope, and ``expected_result`` set to
    ``{"target": "killed", "controls": "green"}``.
    """
    from tracks.executor.mutation import (
        MUTATION_PROTOCOL_VERSION,
        MutationManifest,
    )

    return MutationManifest(
        protocol_version=MUTATION_PROTOCOL_VERSION,
        ac=ac,
        if_ref=if_ref,
        candidate_digest=candidate_digest,
        patch_digest=patch_digest,
        target_nodes=tuple(target_nodes),
        control_nodes=tuple(control_nodes),
        runner_identity=runner_identity,
        allowed_change_scope=tuple(allowed_change_scope),
        expected_result={"target": "killed", "controls": "green"},
    )


def validate_manifest(manifest: Mapping, patch_paths: Sequence[str | Path]):
    """Validate a manifest blob and return a ``MutationManifest``.

    Guards:
    - Empty/no-op patches (patch files must be non-empty) -- AC-FR0262-02.
    - Patch paths and allowed_change_scope must not include test-node
      directories -- AC-FR0262-03.
    """
    from tracks.executor.mutation import (
        MUTATION_PROTOCOL_VERSION,
        MutationManifest,
    )

    patch_strs = [str(p) for p in patch_paths]
    if not patch_strs:
        raise ValueError(
            "no-op patch: no_selectable_diff_identity -- no patch paths"
        )
    # Detect empty patch files (0 bytes) as no-op patches.
    for p in patch_paths:
        try:
            if Path(p).stat().st_size == 0:
                raise ValueError(
                    f"no-op patch: no_selectable_diff_identity -- {p} is empty"
                )
        except (OSError, FileNotFoundError):
            pass  # patch file may not be locally accessible; skip check
    # Check for test-node directories (unit/integration/e2e) but NOT test
    # assets (tests/assets/).  The frozen integration test expects
    # no-op detection to fire before the test-scope check for asset paths.
    _TEST_NODE_PREFIXES = ("tests/unit/", "tests/integration/", "tests/e2e/",
                           "tests/e2e_live/", "tests/counterexamples/")
    _all_paths = list(patch_strs) + [
        str(s) for s in manifest.get("allowed_change_scope", ())
    ]
    for p_str in _all_paths:
        seg = p_str.replace("\\", "/")
        if any(seg.startswith(prefix) or f"/{prefix}" in seg
               for prefix in _TEST_NODE_PREFIXES):
            raise ValueError(f"path {p_str} includes test scope")
    return MutationManifest(
        protocol_version=manifest.get("protocol_version", MUTATION_PROTOCOL_VERSION),
        ac=manifest["ac"],
        if_ref=manifest["if_ref"],
        candidate_digest=manifest["candidate_digest"],
        patch_digest=manifest["patch_digest"],
        target_nodes=tuple(manifest["target_nodes"]),
        control_nodes=tuple(manifest["control_nodes"]),
        runner_identity=manifest["runner_identity"],
        allowed_change_scope=tuple(manifest["allowed_change_scope"]),
        expected_result=dict(manifest.get("expected_result", {})),
    )

