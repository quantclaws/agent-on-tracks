"""Integration: canonical quality guard registry single source (IF-GUARD-001).

AC-FR0258-01@v0.7 registry single source, eight categories,
AC-FR0258-04@v0.7 registry migration no silent gap,
AC-FR0259-01@v0.7 Prism REVISE routes back to Archer.

Assertions land on `load_guard_registry`/`validate_guard_registry`
(IF-GUARD-001, interfaces §1e/§1k) public outlets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.guard_registry import (
    GUARD_CATEGORIES,
    GuardRegistry,
    load_guard_registry,
    validate_guard_registry,
)

# The architecture.md machine registry block is the canonical source (§1k).


def _latest_architecture(root):
    """2026-09-23 (island-2 sweep): meta-tests pinning config digests must
    validate the LIVE version's registry -- a frozen v0.7 registry's digest
    pins only held at that release's tree; the repo's lint config legitimately
    evolves per each version's design (v0.9's M-DESIGN updated pyproject).
    Historical registries stay in git history."""
    import re as _re

    projects = root / ".tracks" / "projects"
    versions = sorted(
        (p.name for p in projects.iterdir() if _re.fullmatch(r"v\d+\.\d+", p.name)),
        key=lambda v: tuple(int(x) for x in v[1:].split(".")),
    )
    return projects / versions[-1] / "architecture.md"


REPO_ROOT = Path(__file__).resolve().parents[2]
ARCH = _latest_architecture(REPO_ROOT)

pytestmark = pytest.mark.integration



# AC-FR0258-01@v0.7 TRACKS-TRACE registry single source, eight categories
def test_registry_single_source_eight_categories():
    """AC-FR0258-01: canonical registry declares exactly the eight categories."""
    assert len(GUARD_CATEGORIES) == 8, "guard category set must contain exactly 8"
    registry = load_guard_registry(ARCH)
    assert isinstance(registry, GuardRegistry)
    # Each of the eight categories must appear exactly once.
    seen = [entry.category for entry in registry.entries]
    assert sorted(seen) == sorted(GUARD_CATEGORIES), (
        f"registry categories {sorted(seen)} != required {sorted(GUARD_CATEGORIES)}"
    )
    # No duplicate category (one canonical entry per category).
    assert len(seen) == len(set(seen)), "duplicate guard category in registry"
    # A non-empty registry digest must be present (parity anchor).
    assert registry.digest.startswith("sha256:")


# AC-FR0258-04@v0.7 TRACKS-TRACE registry migration no silent gap
def test_registry_migration_no_silent_gap():
    """AC-FR0258-04: validate surfaces any missing/malformed entry (no silent gap)."""
    registry = load_guard_registry(ARCH)
    # Against the canonical repo, a valid registry yields zero validation errors.
    errors = validate_guard_registry(registry, Path(__file__).resolve().parents[2])
    assert errors == (), f"canonical registry must validate clean: {errors}"
    # A registry missing a category must NOT silently pass: validate must report.
    short = GuardRegistry(
        version=registry.version,
        host=registry.host,
        entries=registry.entries[:7],  # drop one category
        digest=registry.digest,
    )
    missing_errors = validate_guard_registry(short, Path(__file__).resolve().parents[2])
    assert missing_errors, "missing category must be reported, not silently ignored"


# AC-FR0259-01@v0.7 TRACKS-TRACE Prism REVISE routes back to Archer
def test_prism_revise_routes_back_to_archer():
    """AC-FR0259-01: a registry missing a guard category fails validation
    (the REVISE trigger condition); the failing reason routes back to Archer,
    not to a later stage. We assert the validation outlet surfaces the gap so
    that Prism REVISE has a programmatic basis."""
    registry = load_guard_registry(ARCH)
    # Drop the lint_format category to model a registry Prism must REVISE.
    revised = GuardRegistry(
        version=registry.version,
        host=registry.host,
        entries=tuple(e for e in registry.entries if e.category != "lint_format"),
        digest=registry.digest,
    )
    errors = validate_guard_registry(revised, Path(__file__).resolve().parents[2])
    assert errors, "REVISE trigger: missing category must surface validation errors"
