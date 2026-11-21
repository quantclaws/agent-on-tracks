"""Hotfix registry inheritance respects explicit delta policy declarations."""

import pytest

from tracks.executor.guard_registry import (
    GUARD_CATEGORIES,
    GuardEntry,
    GuardRegistry,
    load_guard_registry,
    validate_guard_registry,
)
from tracks.executor.registry_gate import registry_architecture


def _architectures(tmp_path):
    home = tmp_path / ".tracks"
    base = home / "projects/v0.12/architecture.md"
    delta = home / "projects/v0.12-hotfix-262/architecture.md"
    base.parent.mkdir(parents=True)
    delta.parent.mkdir(parents=True)
    base.write_text("base registry authority\n")
    delta.write_text("### 4.2 CI / pre-commit / release\n\nInherit v0.12 registry.\n")
    return home, base, delta


# AC-FR0269-01@v0.8 TRACKS-TRACE hotfix quality gate uses declared base policy
def test_delta_without_registry_inherits_only_its_base(tmp_path):
    home, base, _ = _architectures(tmp_path)
    assert registry_architecture(home, "v0.12-hotfix-262") == base
    assert registry_architecture(home, "v0.12") == base
    assert registry_architecture(home, "v0.13") == home / "projects/v0.13/architecture.md"


# AC-FR0258-01@v0.7 TRACKS-TRACE explicit malformed overrides never fall back
@pytest.mark.parametrize("declaration", [
    "### 4.2 Canonical quality guard registry\n",
    "[quality_registry]\nversion = 1\n",
    "[[quality_guard]]\n",
    "[quality_registr_typo]\n",
])
def test_explicit_delta_registry_must_validate_as_written(tmp_path, declaration):
    home, _, delta = _architectures(tmp_path)
    delta.write_text(declaration)
    selected = registry_architecture(home, "v0.12-hotfix-262")
    assert selected == delta
    with pytest.raises(ValueError):
        load_guard_registry(selected)


def test_inherited_hotfix_registry_allows_config_digest_drift_but_requires_file(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nversion = '0.12.2'\n")
    entries = tuple(
        GuardEntry(
            guard_id=f"g-{cat}",
            category=cat,
            tool="ruff",
            tool_version="0.16.0",
            command=("ruff", "check"),
            config_paths=("pyproject.toml",),
            config_sections=("tool.ruff",),
            config_digest="sha256:" + "0" * 64,
            scope=("tracks/",),
            threshold="0 errors",
            timeout_seconds=60,
            failure_policy="fail_closed",
            execution_points=("pre_commit",),
            required_check="lint",
        )
        for cat in GUARD_CATEGORIES
    )
    registry = GuardRegistry(version=1, host="python", entries=entries, digest="sha256:abc")
    # Default strict mode rejects byte drift.
    assert any("config digest drift" in e for e in validate_guard_registry(registry, tmp_path))
    # Inherited hotfix mode allows legitimate config edits while still enforcing file presence.
    assert validate_guard_registry(registry, tmp_path, check_config_digest=False) == ()
    (tmp_path / "pyproject.toml").unlink()
    assert any(
        "config file missing" in e
        for e in validate_guard_registry(registry, tmp_path, check_config_digest=False)
    )
