"""Install target compatibility: empty dir + existing project (IF-INSTALL-001,
IF-PROJLAYOUT-001).

AC-FR0351-02: an empty directory becomes a fully working tracks project and
an in-progress git project is installed into without clearing or migrating
it. Assertions land on the install CLI, the receipt/launcher/layout files
and the user-file bytes.
"""

from __future__ import annotations

import subprocess
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
def test_empty_dir_and_existing_project(v012_global, tmp_path):
    """AC-FR0351-02: ``trac install <path>`` works on an empty directory
    (git init + scaffold + managed files + receipt + launcher) and on an
    existing git project (install without touching the user's files)."""
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
    after = file_snapshot(existing)
    for rel, digest in before.items():
        assert after.get(rel) == digest, f"user file {rel} must stay byte-identical"
    assert receipt(existing)["version"] == CURRENT_VERSION
    changed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=existing,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    unexpected = [
        line for line in changed
        if not line[3:].startswith((".tracks/", ".opencode/", "opencode.json"))
    ]
    assert not unexpected, f"install must not introduce unrelated changes: {unexpected}"
    assert reason_of(proc) == ""
