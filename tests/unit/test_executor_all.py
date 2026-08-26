"""IF-MUTATION-001 executor package __all__ export (T-010 RED unit).

Pins the executor package composition contract: ``__all__`` must include
``build_manifest``, ``validate_manifest``, and ``mutation_manifest`` so
that the helper is stably discoverable via ``from tracks.executor import *``.
"""

from __future__ import annotations

import tracks.executor as executor_mod


def test_all_includes_build_manifest():
    """__all__ must include build_manifest."""
    assert "build_manifest" in executor_mod.__all__, (
        "build_manifest not in tracks.executor.__all__"
    )


def test_all_includes_validate_manifest():
    """__all__ must include validate_manifest."""
    assert "validate_manifest" in executor_mod.__all__, (
        "validate_manifest not in tracks.executor.__all__"
    )


def test_all_includes_mutation_manifest():
    """__all__ must include mutation_manifest."""
    assert "mutation_manifest" in executor_mod.__all__, (
        "mutation_manifest not in tracks.executor.__all__"
    )
