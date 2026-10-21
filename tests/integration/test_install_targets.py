"""Install target compatibility: empty dir + existing project (IF-INSTALL-001,
IF-PROJLAYOUT-001).

AC-FR0351-02: an empty directory becomes a fully working tracks project and
an in-progress git project is installed into without clearing or migrating
it. Assertions land on the install CLI, the receipt/launcher/layout files
and the user-file bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.v012_install import (
    CURRENT_VERSION,
    file_snapshot,
    make_existing_project,
    reason_of,
    receipt,
)

pytestmark = pytest.mark.integration


def _success_line(proc, target: Path) -> str:
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# AC-FR0351-02@v0.12 TRACKS-TRACE empty dir and existing project install
# AC-FR0352-01@v0.12 TRACKS-TRACE user owned config files stay byte-identical
def test_empty_dir_and_existing_project(v012_global, tmp_path):
    """AC-FR0351-02: ``trac install <path>`` works on an empty directory
    (git init + scaffold + managed files + receipt + launcher) and on an
    existing git project (install without touching the user's files).

    AC-FR0352-01 (ownership half): the receipt splits the tracks-owned files
    it overwrote (``managed_files``) from the ones it only seeded when the
    target lacked them (``seeded_files``), the runtime env is git-ignored
    with ``*``, and the user's own root ``opencode.json`` / user
    ``.opencode/opencode.json`` are never overwritten (interfaces §1b.1,
    §1b.2, §1b.4).
    """
    empty = tmp_path / "empty-project"
    empty.mkdir()
    proc = v012_global.run("install", str(empty), cwd=tmp_path)
    assert _success_line(proc, empty) == (
        f"installed tracks {CURRENT_VERSION} at {empty}"
    )
    assert (empty / ".git").is_dir(), "an empty target is initialised with git"
    installed = receipt(empty)
    assert installed["version"] == CURRENT_VERSION
    assert installed["schema_version"] == 1
    assert installed["source"] in ("running-distribution", "wheel")
    assert installed["site_packages"] == f".tracks/env/{CURRENT_VERSION}/site-packages"
    assert installed["launcher"] == ".tracks/bin/trac.py"
    assert (empty / ".tracks" / "bin" / "trac.py").is_file()
    site_init = empty / installed["site_packages"] / "tracks" / "__init__.py"
    assert site_init.is_file()
    assert f'__version__ = "{CURRENT_VERSION}"' in site_init.read_text(encoding="utf-8")
    for rel in (".opencode/agents", ".opencode/skills", ".opencode/templates"):
        directory = empty / rel
        assert directory.is_dir() and any(directory.iterdir()), f"{rel} must be populated"
    assert (empty / "opencode.json").is_file(), "the default harness config is seeded"
    assert not (empty / ".gitignore").exists(), (
        "the installer must not create a host root .gitignore (§1b.4)"
    )
    # §1b.1: the receipt carries the whole documented field set, and splits
    # the overwritten tracks-owned files from the seeded-only ones.
    assert set(installed) == {
        "schema_version",
        "version",
        "installed_at",
        "python",
        "site_packages",
        "launcher",
        "installer_version",
        "source",
        "managed_files",
        "seeded_files",
    }, f"the receipt schema is closed (§1b.1): {sorted(installed)}"
    managed = installed["managed_files"]
    assert managed, "the receipt lists the tracks-owned files the install wrote"
    for rel in managed:
        assert rel.startswith(
            (".opencode/agents/", ".opencode/skills/", ".opencode/templates/")
        ) or rel in (".tracks/bin/trac.py", ".tracks/install.json"), (
            f"only the documented tracks-owned paths are managed files: {rel}"
        )
        assert (empty / rel).is_file(), f"managed file {rel} must exist in the target"
    assert ".tracks/bin/trac.py" in managed, (
        "the launcher is written by the installer and belongs to the managed set "
        "(§1b.3)"
    )
    assert "opencode.json" not in managed, (
        "the root harness config is seeded-only, never a managed file (§1b.4)"
    )
    assert installed["seeded_files"] == ["opencode.json"], (
        f"the root harness config is the only seeded file: {installed['seeded_files']}"
    )
    # §1b.2: the runtime env never enters git.
    assert (empty / ".tracks" / "env" / ".gitignore").read_text(
        encoding="utf-8"
    ).strip() == "*", "the env directory is git-ignored with '*' (§1b.2)"
    status = v012_global.run("status", cwd=empty)
    assert status.returncode == 0, (
        f"the installed project must be usable: {status.stderr!r}"
    )

    existing = make_existing_project(tmp_path / "existing-project")
    # Only the user's files are part of the byte-identity contract; the
    # installer's own path-scoped commit legitimately advances .git/ metadata.
    before = {
        rel: digest
        for rel, digest in file_snapshot(existing).items()
        if not rel.startswith(".git/")
    }
    proc = v012_global.run("install", str(existing), cwd=tmp_path)
    assert _success_line(proc, existing) == (
        f"installed tracks {CURRENT_VERSION} at {existing}"
    )
    after = {
        rel: digest
        for rel, digest in file_snapshot(existing).items()
        if not rel.startswith(".git/")
    }
    for rel, digest in before.items():
        assert after.get(rel) == digest, f"user file {rel} must stay byte-identical"
    installed_existing = receipt(existing)
    assert installed_existing["version"] == CURRENT_VERSION
    # §1b.4: the root opencode.json is seeded-only — a user-owned one is
    # neither overwritten nor listed as a managed file.
    assert installed_existing["seeded_files"] == [], (
        "an existing root opencode.json is not seeded again: "
        f"{installed_existing['seeded_files']}"
    )
    assert "opencode.json" not in installed_existing["managed_files"], (
        "the root opencode.json is never a managed file"
    )
    # "Introduced by install" is a file-set delta: the fixture's pre-existing
    # user-owned scratch.txt is in ``before`` and therefore not a new path.
    new_paths = set(after) - set(before)
    unexpected = [
        rel for rel in sorted(new_paths)
        if not rel.startswith((".tracks/", ".opencode/", "opencode.json"))
    ]
    assert not unexpected, f"install must not introduce unrelated changes: {unexpected}"
    assert reason_of(proc) == ""
