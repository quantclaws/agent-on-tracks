"""Overwrite install and file ownership (IF-INSTALL-001, IF-PROJLAYOUT-001).

AC-FR0352-01: a repeated install replaces every tracks-owned file with the
installed version's bytes while user files stay byte-identical. AC-FR0352-02:
a user-modified tracks-owned file is overwritten by the installed version
without a recovery flow. AC-FR0354-01 (idempotence half): re-installing the
same version converges to a clean state with no staging residue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support.v012_install import (
    CURRENT_VERSION,
    OLD_VERSION,
    file_snapshot,
    git_init,
    receipt,
    wheel_members,
)

pytestmark = pytest.mark.integration

_USER_FILES = {
    "src/app.py": "def main():\n    return 1\n",
    "docs/notes.md": "user notes\n",
}


def _install(cli, project: Path, cwd: Path, *, version: str, wheel: Path):
    proc = cli.run(
        "install",
        str(project),
        "--version",
        version,
        "--wheel",
        str(wheel),
        cwd=cwd,
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _assert_managed_bytes(project: Path, wheel: Path) -> None:
    members = wheel_members(wheel)
    mapping = {
        "tracks/agents/": ".opencode/agents/",
        "tracks/skills/": ".opencode/skills/",
        "tracks/templates/": ".opencode/templates/",
    }
    copied = 0
    for name, payload in members.items():
        for source, target in mapping.items():
            if not name.startswith(source) or name.endswith("/"):
                continue
            rel = name[len(source):]
            path = project / target / rel
            assert path.is_file(), f"managed file {path} is missing"
            assert path.read_bytes() == payload, (
                f"managed file {path} must carry the installed version bytes"
            )
            copied += 1
    assert copied > 0, "the wheel carries managed resources"


# AC-FR0352-01@v0.12 TRACKS-TRACE managed files replaced user files byte-identical
def test_managed_files_replaced_user_files_byte_identical(
    v012_global, v012_wheels, tmp_path
):
    """AC-FR0352-01: after installing the old version then the current
    version, every tracks-owned file carries the current version's bytes and
    the user's own files (tracked and untracked) are untouched."""
    project = git_init(tmp_path / "project", _USER_FILES)
    (project / "scratch.txt").write_text("untracked user scratch\n", encoding="utf-8")
    _install(
        v012_global,
        project,
        tmp_path,
        version=OLD_VERSION,
        wheel=v012_wheels["old"],
    )
    before = file_snapshot(project)
    _install(
        v012_global,
        project,
        tmp_path,
        version=CURRENT_VERSION,
        wheel=v012_wheels["current"],
    )
    assert receipt(project)["version"] == CURRENT_VERSION
    _assert_managed_bytes(project, v012_wheels["current"])
    after = file_snapshot(project)
    for rel in {*_USER_FILES, "scratch.txt"}:
        assert after.get(rel) == before.get(rel), (
            f"user file {rel} must stay byte-identical across installs"
        )
    assert not any(
        path.parent.name == "conflict" for path in project.rglob("*")
    )


# AC-FR0352-02@v0.12 TRACKS-TRACE user-modified managed file overwritten
def test_user_modified_managed_file_overwritten(v012_global, v012_wheels, tmp_path):
    """AC-FR0352-02: editing a tracks-owned file and re-installing restores
    the installed version's bytes; no recovery prompt is involved."""
    project = git_init(tmp_path / "project", _USER_FILES)
    _install(
        v012_global,
        project,
        tmp_path,
        version=CURRENT_VERSION,
        wheel=v012_wheels["current"],
    )
    skill = project / ".opencode" / "skills" / "tracks-discuz" / "SKILL.md"
    skill.write_text("user changed this managed file\n", encoding="utf-8")
    _install(
        v012_global,
        project,
        tmp_path,
        version=CURRENT_VERSION,
        wheel=v012_wheels["current"],
    )
    expected = wheel_members(v012_wheels["current"])["tracks/skills/tracks-discuz/SKILL.md"]
    assert skill.read_bytes() == expected, (
        "the managed file must be restored to the installed version bytes"
    )
    for rel, body in _USER_FILES.items():
        assert (project / rel).read_text(encoding="utf-8") == body


# AC-FR0354-01@v0.12 TRACKS-TRACE same-version reinstall converges clean
def test_reinstall_idempotent(v012_global, v012_wheels, tmp_path):
    """AC-FR0354-01 (idempotence): installing the same version twice succeeds,
    leaves the same managed file set and no staging directories behind."""
    project = git_init(tmp_path / "project", _USER_FILES)
    _install(
        v012_global,
        project,
        tmp_path,
        version=CURRENT_VERSION,
        wheel=v012_wheels["current"],
    )
    first = file_snapshot(project)
    _install(
        v012_global,
        project,
        tmp_path,
        version=CURRENT_VERSION,
        wheel=v012_wheels["current"],
    )
    second = file_snapshot(project)
    modified = {rel for rel in first if second.get(rel) != first[rel]}
    assert not any(
        rel.startswith((".opencode/", "opencode.json")) for rel in modified
    ), f"managed/user content must be stable on reinstall: {sorted(modified)}"
    assert receipt(project)["version"] == CURRENT_VERSION
    staging = list((project / ".tracks").glob(".install-staging-*"))
    assert not staging, f"staging directories must be cleaned: {staging}"
