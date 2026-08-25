"""Mutation manifest helper (IF-MUTATION-001).

Extracts the manifest construction/validation logic declared in interfaces.md
§1g.  The facade in ``tracks/executor/mutation.py`` delegates to this module
(via T-011 wiring).

The functions are stubs that raise ``NotImplementedError("IF-MUTATION-001")``
until T-011 wires the final delegation.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

# Register this module with the parent package so that the R test
# ``hasattr(tracks.executor, "mutation_manifest")`` can find it.
import tracks.executor as _executor_pkg
from tracks.executor.mutation import MutationManifest

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
) -> MutationManifest:
    """Construct a mutation manifest (IF-MUTATION-001, AC-FR0262-01)."""
    raise NotImplementedError("IF-MUTATION-001")


def validate_manifest(
    manifest: Mapping, patch_paths: Sequence[str]
) -> MutationManifest:
    """Validate a manifest blob and return a ``MutationManifest``."""
    raise NotImplementedError("IF-MUTATION-001")

