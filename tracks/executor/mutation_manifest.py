"""Mutation manifest helper (IF-MUTATION-001).

Extracts the manifest construction/validation logic declared in interfaces.md
§1g.  The facade in ``tracks/executor/mutation.py`` owns the implementation;
this helper re-exports the same functions so the executor package composition
can expose them (T-011 wires the facade-side delegation).
"""

from __future__ import annotations

import sys

# Register this module with the parent package so that the R test
# ``hasattr(tracks.executor, "mutation_manifest")`` can find it.
import tracks.executor as _executor_pkg
from tracks.executor.mutation import (
    MUTATION_PROTOCOL_VERSION,
    MutationManifest,
    build_manifest,
    validate_manifest,
)

_executor_pkg.mutation_manifest = sys.modules[__name__]

__all__ = [
    "MUTATION_PROTOCOL_VERSION",
    "MutationManifest",
    "build_manifest",
    "validate_manifest",
]
