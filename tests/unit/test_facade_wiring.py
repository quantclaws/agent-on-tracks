"""IF-MUTATION-002 facade delegation wiring (T-011 RED unit).

Pins the facade delegation contract: the public facade in ``mutation.py``
must delegate ``build_manifest``/``validate_manifest`` to the T-010 helper
and ``run_mutation_experiment`` to the T-011 helper.

The current implementation has the helper re-export from the facade
(wrong direction) — these tests fail because the functions are owned
by the facade module, not the helper modules.
"""

from __future__ import annotations

import tracks.executor.mutation as facade
import tracks.executor.mutation_experiment as experiment_helper
import tracks.executor.mutation_manifest as manifest_helper


def test_build_manifest_owned_by_mutation_manifest():
    """build_manifest must be defined in mutation_manifest (T-010 helper)."""
    assert facade.build_manifest.__module__ == "tracks.executor.mutation_manifest", (
        f"build_manifest is owned by {facade.build_manifest.__module__}, "
        f"expected tracks.executor.mutation_manifest"
    )


def test_validate_manifest_owned_by_mutation_manifest():
    """validate_manifest must be defined in mutation_manifest (T-010 helper)."""
    assert facade.validate_manifest.__module__ == "tracks.executor.mutation_manifest", (
        f"validate_manifest is owned by {facade.validate_manifest.__module__}, "
        f"expected tracks.executor.mutation_manifest"
    )


def test_run_mutation_experiment_owned_by_mutation_experiment():
    """run_mutation_experiment must be defined in mutation_experiment (T-011 helper)."""
    assert facade.run_mutation_experiment.__module__ == "tracks.executor.mutation_experiment", (
        f"run_mutation_experiment is owned by {facade.run_mutation_experiment.__module__}, "
        f"expected tracks.executor.mutation_experiment"
    )


def test_facade_delegates_build_manifest_to_helper():
    """facade.build_manifest must be the same object as mutation_manifest.build_manifest."""
    assert facade.build_manifest is manifest_helper.build_manifest, (
        "facade.build_manifest is not delegated to mutation_manifest"
    )


def test_facade_delegates_validate_manifest_to_helper():
    """facade.validate_manifest must be the same object as mutation_manifest.validate_manifest."""
    assert facade.validate_manifest is manifest_helper.validate_manifest, (
        "facade.validate_manifest is not delegated to mutation_manifest"
    )


def test_facade_delegates_run_mutation_experiment_to_helper():
    """facade.run_mutation_experiment must be the same as mutation_experiment's."""
    assert facade.run_mutation_experiment is experiment_helper.run_mutation_experiment, (
        "facade.run_mutation_experiment is not delegated to mutation_experiment"
    )

