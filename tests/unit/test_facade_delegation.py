"""IF-MUTATION-001 facade delegation (T-010 RED unit).

Pins the facade delegation contract: the facade in ``mutation.py`` must
delegate to the helper in ``mutation_manifest.py`` (T-011 wiring).

The current facade has its own inline implementation, so these tests fail.
"""

from __future__ import annotations

import tracks.executor.mutation as facade
import tracks.executor.mutation_manifest as helper


def test_facade_build_manifest_delegates_to_helper():
    """facade.build_manifest must delegate to mutation_manifest.build_manifest."""
    assert facade.build_manifest is helper.build_manifest, (
        "facade.build_manifest does not delegate to mutation_manifest.build_manifest"
    )


def test_facade_validate_manifest_delegates_to_helper():
    """facade.validate_manifest must delegate to mutation_manifest.validate_manifest."""
    assert facade.validate_manifest is helper.validate_manifest, (
        "facade.validate_manifest does not delegate to mutation_manifest.validate_manifest"
    )
