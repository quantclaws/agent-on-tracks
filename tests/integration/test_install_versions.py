"""Explicit versions, global/project split and failure semantics
(IF-INSTALL-001, IF-PROJECTCLI-001, IF-VERSIONVIS-001).

AC-FR0351-01: an explicit version installs that version while the global
version stays different. AC-FR0353-01/02: upgrading the global prefix does
not touch a locked project's receipt, site-packages or observable version.
AC-FR0354-01: failures exit non-zero with a closed-set reason, write no
receipt and can be retried to a clean usable state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._support.v012_install import (
    CURRENT_VERSION,
    OLD_VERSION,
    build_wheel,
    file_snapshot,
    git_init,
    install_global,
    reason_of,
    receipt,
)

pytestmark = pytest.mark.integration

_USER_FILES = {"src/app.py": "def main():\n    return 1\n"}


@pytest.fixture(scope="module")
def old_global(v012_wheels, tmp_path_factory):
    prefix = tmp_path_factory.mktemp("v012-global-old")
    return install_global(prefix, v012_wheels["old"])


@pytest.fixture(scope="module")
def dependency_wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp("v012-dependency-wheel")
    return build_wheel(
        root,
        extra_requires=("tracks-missing-dependency-xyz>=9.9",),
    )


def _install(cli, target: Path, cwd: Path, *extra: str):
    return cli.run("install", str(target), *extra, cwd=cwd)


def _assert_version(payload: dict, *, scope: str, version: str, project: Path | None = None):
    assert payload["scope"] == scope
    assert payload["version"] == version
    if project is not None:
        assert Path(payload["project_root"]).resolve() == project.resolve()


# AC-FR0351-01@v0.12 TRACKS-TRACE explicit version installs old while global is new
def test_explicit_version_and_global_project_split(v012_global, v012_wheels, tmp_path):
    """AC-FR0351-01: ``--version <old> --wheel <old>`` installs the old
    version into the target; the global CLI keeps reporting the new version
    and the project launcher reports the locked old version."""
    project = git_init(tmp_path / "project", _USER_FILES)
    proc = _install(
        v012_global,
        project,
        tmp_path,
        "--version",
        OLD_VERSION,
        "--wheel",
        str(v012_wheels["old"]),
    )
    assert proc.returncode == 0, proc.stderr
    installed = receipt(project)
    assert installed["version"] == OLD_VERSION
    assert installed["site_packages"] == f".tracks/env/{OLD_VERSION}/site-packages"
    site_init = project / installed["site_packages"] / "tracks" / "__init__.py"
    assert f'__version__ = "{OLD_VERSION}"' in site_init.read_text(encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    global_version = v012_global.run("version", "--json", cwd=outside)
    assert global_version.returncode == 0, global_version.stderr
    _assert_version(json.loads(global_version.stdout), scope="global", version=CURRENT_VERSION)

    project_version = v012_global.run("version", "--json", cwd=project)
    assert project_version.returncode == 0, project_version.stderr
    _assert_version(
        json.loads(project_version.stdout),
        scope="project",
        version=OLD_VERSION,
        project=project,
    )
    text = v012_global.run("version", cwd=project)
    assert text.returncode == 0
    assert f"trac {OLD_VERSION} (project {project})" in text.stdout


# AC-FR0353-01@v0.12 TRACKS-TRACE global upgrade keeps the locked project working
def test_global_upgrade_keeps_locked_project(old_global, v012_global, tmp_path):
    """AC-FR0353-01: a project installed by the old global CLI keeps serving
    the old version after the global entry is upgraded to the new one; its
    normal commands still return usable results."""
    project = git_init(tmp_path / "project", _USER_FILES)
    proc = _install(
        old_global,
        project,
        tmp_path,
        "--version",
        OLD_VERSION,
    )
    assert proc.returncode == 0, proc.stderr
    assert receipt(project)["version"] == OLD_VERSION

    upgraded = v012_global.run("version", "--json", cwd=project)
    assert upgraded.returncode == 0, upgraded.stderr
    _assert_version(
        json.loads(upgraded.stdout),
        scope="project",
        version=OLD_VERSION,
        project=project,
    )
    status = v012_global.run("status", cwd=project)
    assert status.returncode == 0, (
        f"the locked project must stay usable after the global upgrade: {status.stderr!r}"
    )


# AC-FR0353-02@v0.12 TRACKS-TRACE no auto upgrade no lock change
def test_no_auto_upgrade_or_lock_change(old_global, v012_global, tmp_path):
    """AC-FR0353-02: after the global upgrade, a locked project's receipt and
    site-packages bytes are unchanged, no new version directory appears and
    no migration write happens."""
    project = git_init(tmp_path / "project", _USER_FILES)
    proc = _install(old_global, project, tmp_path, "--version", OLD_VERSION)
    assert proc.returncode == 0, proc.stderr
    receipt_before = (project / ".tracks" / "install.json").read_bytes()
    env_before = file_snapshot(project / ".tracks" / "env")

    for args in (("version",), ("status",)):
        run = v012_global.run(*args, cwd=project)
        assert run.returncode == 0, run.stderr

    assert (project / ".tracks" / "install.json").read_bytes() == receipt_before
    assert file_snapshot(project / ".tracks" / "env") == env_before
    env_versions = sorted(
        path.name for path in (project / ".tracks" / "env").iterdir() if path.is_dir()
    )
    assert env_versions == [OLD_VERSION], (
        f"only the locked version may exist: {env_versions}"
    )


# AC-FR0354-01@v0.12 TRACKS-TRACE failure reasons and retry convergence
def test_failure_semantics_and_retry(
    v012_global, v012_wheels, dependency_wheel, tmp_path
):
    """AC-FR0354-01: each failure class exits non-zero with its closed-set
    reason and leaves no receipt; the fixed target then installs cleanly and
    a same-version retry stays successful."""
    def empty_target(name: str) -> Path:
        """A fresh target per failure class (no failure-order coupling)."""
        target = tmp_path / name
        target.mkdir()
        return target

    target_file = tmp_path / "target-file"
    target_file.write_text("not a directory\n", encoding="utf-8")
    cases: list[tuple[str, list[str], Path]] = [
        (
            "target_not_directory",
            [str(target_file)],
            target_file,
        ),
    ]
    non_git = tmp_path / "non-git"
    non_git.mkdir()
    (non_git / "user.txt").write_text("user\n", encoding="utf-8")
    cases.append(("not_a_git_repo", [str(non_git)], non_git))
    invalid = empty_target("version-invalid")
    cases.append(("version_invalid", [str(invalid), "--version", "1.2"], invalid))
    unavailable = empty_target("version-unavailable")
    cases.append(
        ("version_unavailable", [str(unavailable), "--version", OLD_VERSION], unavailable)
    )
    mismatch = empty_target("version-mismatch")
    cases.append(
        (
            "version_mismatch",
            [
                str(mismatch),
                "--version",
                CURRENT_VERSION,
                "--wheel",
                str(v012_wheels["old"]),
            ],
            mismatch,
        )
    )
    missing_dependency = empty_target("dependency-missing")
    cases.append(
        (
            "dependency_missing",
            [str(missing_dependency), "--wheel", str(dependency_wheel)],
            missing_dependency,
        )
    )
    missing_parent = tmp_path / "missing-parent" / "child"
    cases.append(("target_parent_missing", [str(missing_parent)], missing_parent))

    for reason, args, target in cases:
        proc = v012_global.run("install", *args, cwd=tmp_path)
        assert proc.returncode != 0, f"{reason}: failure must exit non-zero"
        assert "installed tracks" not in proc.stdout
        assert reason_of(proc) == reason, (
            f"expected {reason}, got {proc.stderr!r}"
        )
        assert proc.stderr.startswith("trac install: "), proc.stderr
        if target.is_dir():
            assert not (target / ".tracks" / "install.json").exists()

    # the fix + retry converges, and a same-version retry is idempotent
    fixed = tmp_path / "fixed"
    fixed.mkdir()
    for _ in range(2):
        proc = v012_global.run("install", str(fixed), cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
    assert receipt(fixed)["version"] == CURRENT_VERSION
    assert not list((fixed / ".tracks").glob(".install-staging-*"))
