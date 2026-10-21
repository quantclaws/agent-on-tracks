"""T-001 RED: ``trac install`` command face (IF-INSTALL-001 /
IF-INSTALLPKG-001 / IF-PROJLAYOUT-001).

Devon-owned unit RED for the install slice: target guards, version
resolution, closed failure reasons (interfaces §1c.5), wheel resource
completeness, staging→materialize→receipt convergence, managed-file
overwrite semantics, and byte-identical user-file preservation.

All nodes drive the real ``tracks.cli.main.main`` entry point; nothing in
the installer is mocked.  Candidate wheels are built locally with
``zipfile`` (the artifact is an external input, not the system under test).
On the RED tree ``install`` is unregistered, so every node fails with an
``AssertionError`` against the missing behavior — no stub tokens, no
collection errors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tracks.cli.main import main

from .install_red_support import (
    assert_success_line,
    build_wheel,
    git,
    hermetic_git_identity,
    init_git_project,
    read_receipt,
    sha256,
    wheel_bytes,
)

__all__ = ["hermetic_git_identity"]


def _wheel(tmp_path: Path, version: str = "9.9.9", **kwargs) -> Path:
    return build_wheel(
        tmp_path / "dist" / f"agent_on_tracks-{version}-py3-none-any.whl",
        version,
        **kwargs,
    )


def _reason(capsys, reason: str) -> str:
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith(f"trac install: {reason}:"), err
    return err


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 usage is a closed reason
def test_ac_0354_01_usage_errors_are_closed(tmp_path, capsys):
    rc = main(["install"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.startswith("trac install: usage:"), err

    target = tmp_path / "proj"
    target.mkdir()
    rc = main(["install", str(target), "--bogus"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.startswith("trac install: usage:"), err
    assert not (target / ".tracks").exists()


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 target kind guards
def test_ac_0354_01_target_guards_fail_closed(tmp_path, capsys):
    a_file = tmp_path / "a-file.txt"
    a_file.write_text("user content\n", encoding="utf-8")
    rc = main(["install", str(a_file)])
    _reason(capsys, "target_not_directory")
    assert rc == 1

    missing = tmp_path / "absent-parent" / "proj"
    rc = main(["install", str(missing)])
    _reason(capsys, "target_parent_missing")
    assert rc == 1
    assert not missing.exists()


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 non-git non-empty target
def test_ac_0354_01_non_git_nonempty_target_refused(tmp_path, capsys):
    target = tmp_path / "not-a-repo"
    target.mkdir()
    keep = target / "keep.txt"
    keep.write_text("user content\n", encoding="utf-8")

    rc = main(["install", str(target)])
    _reason(capsys, "not_a_git_repo")
    assert rc == 1
    assert keep.read_text(encoding="utf-8") == "user content\n"
    assert not (target / ".tracks").exists()


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 version reason closed set
def test_ac_0354_01_version_guards_fail_closed(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()

    rc = main(["install", str(target), "--version", "0.12"])
    _reason(capsys, "version_invalid")
    assert rc == 1

    rc = main(["install", str(target), "--version", "9.9.9"])
    err = _reason(capsys, "version_unavailable")
    assert rc == 1
    assert "wheel" in err.lower()
    assert not (target / ".tracks" / "install.json").exists()


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 wheel metadata must match
def test_ac_0354_01_wheel_version_mismatch_rejected(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel = _wheel(tmp_path, "9.9.9")

    rc = main(
        [
            "install",
            str(target),
            "--version",
            "8.8.8",
            "--wheel",
            str(wheel),
        ]
    )
    _reason(capsys, "version_mismatch")
    assert rc == 1
    assert not (target / ".tracks" / "install.json").exists()


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 dependency preconditions
def test_ac_0354_01_dependency_missing_rejected(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel = _wheel(
        tmp_path,
        "9.9.9",
        requires="tracks-definitely-missing-dependency>=1",
    )

    rc = main(
        ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    )
    _reason(capsys, "dependency_missing")
    assert rc == 1
    assert not (target / ".tracks" / "install.json").exists()
    assert not (target / ".tracks" / "env" / "9.9.9").exists()


# AC-FR0348-02@v0.12 TRACKS-TRACE IF-INSTALLPKG-001 closed resource list
def test_ac_0348_02_incomplete_wheel_rejected(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel = _wheel(
        tmp_path, "9.9.9", omit="tracks/assets/opencode.json"
    )

    rc = main(
        ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    )
    _reason(capsys, "package_incomplete")
    assert rc == 1
    assert not (target / ".tracks" / "install.json").exists()
    assert not (target / ".tracks" / "env" / "9.9.9").exists()


# AC-FR0348-01@v0.12 TRACKS-TRACE IF-INSTALLPKG-001 full resources installed
# AC-FR0351-02@v0.12 TRACKS-TRACE IF-PROJLAYOUT-001 empty dir becomes project
# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 success means usable project
def test_ac_0351_02_empty_directory_becomes_working_project(tmp_path, capsys):
    target = tmp_path / "empty"
    target.mkdir()
    wheel = _wheel(tmp_path, "9.9.9")

    rc = main(
        ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    )
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert err == ""
    assert_success_line(out, "9.9.9", target)

    receipt = read_receipt(target)
    assert receipt["schema_version"] == 1
    assert receipt["version"] == "9.9.9"
    assert receipt["site_packages"] == ".tracks/env/9.9.9/site-packages"
    assert receipt["launcher"] == ".tracks/bin/trac.py"
    assert receipt["source"] == "wheel"
    assert Path(receipt["python"]).resolve() == Path(sys.executable).resolve()
    assert ".tracks/bin/trac.py" in receipt["managed_files"]
    assert ".opencode/agents/Archer.md" in receipt["managed_files"]
    assert "opencode.json" in receipt["seeded_files"]

    installed = target / receipt["site_packages"]
    version_file = installed / "tracks" / "__init__.py"
    assert '__version__ = "9.9.9"' in version_file.read_text(encoding="utf-8")

    for member in (
        ".opencode/agents/Archer.md",
        ".opencode/skills/tracks-devon-rgr/SKILL.md",
        ".opencode/templates/story.md",
    ):
        source = "tracks/" + member.removeprefix(".opencode/")
        assert (target / member).read_bytes() == wheel_bytes(wheel, source)
    assert (target / "opencode.json").read_bytes() == wheel_bytes(
        wheel, "tracks/assets/opencode.json"
    )

    assert (target / ".tracks" / "env" / ".gitignore").read_text(
        encoding="utf-8"
    ).strip() == "*"
    for directory in ("projects", "runtime", "wiki"):
        assert (target / ".tracks" / directory).is_dir()
    assert not (target / ".gitignore").exists()
    assert not list((target / ".tracks").glob(".install-staging-*"))

    git(target, "rev-parse", "--verify", "HEAD")
    tracked = git(target, "ls-files", "--", ".tracks/install.json")
    assert tracked.stdout.strip() == ".tracks/install.json"


# AC-FR0352-01@v0.12 TRACKS-TRACE IF-PROJLAYOUT-001 user files preserved
# AC-FR0351-02@v0.12 TRACKS-TRACE IF-INSTALL-001 existing project install
def test_ac_0351_02_existing_project_keeps_user_bytes(tmp_path, capsys):
    target = tmp_path / "existing"
    init_git_project(target)
    custom_config = '{"user": "harness"}\n'
    (target / "opencode.json").write_text(custom_config, encoding="utf-8")
    user_files = (
        target / "README.md",
        target / ".gitignore",
        target / "docs" / "notes.md",
        target / "opencode.json",
    )
    before = {path: sha256(path) for path in user_files}
    wheel = _wheel(tmp_path, "9.9.9")

    rc = main(
        ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    )
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert_success_line(out, "9.9.9", target)

    for path, digest in before.items():
        assert sha256(path) == digest, path
    assert (target / ".gitignore").read_text(encoding="utf-8") == "user-ignore\n"
    assert (target / "opencode.json").read_text(encoding="utf-8") == custom_config
    assert (target / ".opencode" / "agents" / "Archer.md").is_file()
    assert read_receipt(target)["seeded_files"] == []


# AC-FR0352-01@v0.12 TRACKS-TRACE IF-PROJLAYOUT-001 managed replaced, user kept
def test_ac_0352_01_reinstall_replaces_managed_and_preserves_user(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel_a = _wheel(tmp_path / "a", "9.9.9")
    rc = main(
        ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel_a)]
    )
    _, err = capsys.readouterr()
    assert rc == 0, err

    user_file = target / "user-code.py"
    user_file.write_text("print('user')\n", encoding="utf-8")
    user_digest = sha256(user_file)
    custom_config = '{"user": "custom"}\n'
    (target / "opencode.json").write_text(custom_config, encoding="utf-8")

    wheel_b = _wheel(
        tmp_path / "b",
        "8.8.8",
        agent_body="# Archer from 8.8.8\n",
    )
    rc = main(
        ["install", str(target), "--version", "8.8.8", "--wheel", str(wheel_b)]
    )
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert_success_line(out, "8.8.8", target)

    assert read_receipt(target)["version"] == "8.8.8"
    assert (target / ".opencode" / "agents" / "Archer.md").read_bytes() == (
        wheel_bytes(wheel_b, "tracks/agents/Archer.md")
    )
    assert sha256(user_file) == user_digest
    assert (target / "opencode.json").read_text(encoding="utf-8") == custom_config


# AC-FR0352-02@v0.12 TRACKS-TRACE IF-PROJLAYOUT-001 managed files overwrite
def test_ac_0352_02_user_modified_managed_file_overwritten(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel = _wheel(tmp_path, "9.9.9")
    install = [
        "install",
        str(target),
        "--version",
        "9.9.9",
        "--wheel",
        str(wheel),
    ]
    rc = main(install)
    _, err = capsys.readouterr()
    assert rc == 0, err

    managed = target / ".opencode" / "skills" / "tracks-devon-rgr" / "SKILL.md"
    managed.write_text("user hand edit\n", encoding="utf-8")

    rc = main(install)
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert_success_line(out, "9.9.9", target)
    assert managed.read_bytes() == wheel_bytes(
        wheel, "tracks/skills/tracks-devon-rgr/SKILL.md"
    )


# AC-FR0354-01@v0.12 TRACKS-TRACE IF-INSTALL-001 same-version convergence
def test_ac_0354_01_same_version_reinstall_converges(tmp_path, capsys):
    target = tmp_path / "proj"
    target.mkdir()
    wheel = _wheel(tmp_path, "9.9.9")
    install = [
        "install",
        str(target),
        "--version",
        "9.9.9",
        "--wheel",
        str(wheel),
    ]
    rc = main(install)
    _, err = capsys.readouterr()
    assert rc == 0, err
    first_files = sorted(
        str(path.relative_to(target))
        for path in (target / ".opencode").rglob("*")
        if path.is_file()
    )

    rc = main(install)
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert_success_line(out, "9.9.9", target)
    second_files = sorted(
        str(path.relative_to(target))
        for path in (target / ".opencode").rglob("*")
        if path.is_file()
    )
    assert second_files == first_files
    assert not list((target / ".tracks").glob(".install-staging-*"))
    assert read_receipt(target)["version"] == "9.9.9"
    assert json.loads((target / "opencode.json").read_text(encoding="utf-8"))[
        "agent"
    ].keys() >= {"Archer", "Devon", "Shield"}
