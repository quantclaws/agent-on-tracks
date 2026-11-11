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
import zipfile
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


# AC-FR0348-02@v0.12 TRACKS-TRACE partially missing runtime resources are rejected before install
def test_individual_required_resource_missing_is_rejected(tmp_path, capsys):
    for index, missing in enumerate((
        "tracks/templates/story.md", "tracks/templates/test-plan.md",
        "tracks/agents/Shield.md", "tracks/skills/tracks-devon-rgr/SKILL.md",
    )):
        target = tmp_path / f"missing-resource-{index}"
        wheel = _wheel(tmp_path / str(index), "9.9.9", omit=missing)
        rc = main(["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)])
        assert rc == 1
        _reason(capsys, "package_incomplete")
        assert not (target / ".tracks" / "install.json").exists()


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


# AC-FR0348-01@v0.12 TRACKS-TRACE installed runtime metadata cannot dirty a release candidate
def test_runtime_and_service_state_are_ignored_but_product_files_are_visible(tmp_path):
    target = tmp_path / "proj"
    wheel = _wheel(tmp_path)
    assert main(["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]) == 0
    for relative in ("runtime/sessions/Scribe.json", "runtime/future-cache/data", "service/service.db"):
        file = target / ".tracks" / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("runtime state\n")
    assert git(target, "status", "--porcelain", "--untracked-files=all").stdout == ""
    (target / "product.py").write_text("value = 1\n")
    assert git(target, "status", "--porcelain").stdout.strip() == "?? product.py"
    assert not (target / ".gitignore").exists()


# AC-FR0352-01@v0.12 TRACKS-TRACE reinstall migrates managed rules without editing host ignores
def test_reinstall_migrates_old_runtime_ignore_rules(tmp_path):
    target = tmp_path / "proj"
    init_git_project(target)
    wheel = _wheel(tmp_path)
    args = ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    assert main(args) == 0
    managed = target / ".tracks/.gitignore"
    old = "# custom Tracks ignore\ncustom-cache/\nreport/\n*.lock\n"
    managed.write_text(old)
    git(target, "add", "-f", ".tracks/.gitignore")
    git(target, "commit", "-m", "legacy scaffold")
    original_host_ignore = (target / ".gitignore").read_bytes()
    assert main(args) == 0
    assert managed.read_text().startswith(old)
    assert (target / ".gitignore").read_bytes() == original_host_ignore
    for relative in ("runtime/sessions/Scribe.json", "service/service.db"):
        file = target / ".tracks" / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("runtime state\n")
    assert git(target, "status", "--porcelain", "--untracked-files=all").stdout == ""


# AC-FR0354-01@v0.12 TRACKS-TRACE failed health check restores migrated ignore bytes
def test_failed_reinstall_restores_legacy_ignore_rules(tmp_path):
    target = tmp_path / "proj"
    wheel = _wheel(tmp_path)
    args = ["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]
    assert main(args) == 0
    managed = target / ".tracks/.gitignore"
    old = b"# custom legacy rules\nreport/\n*.lock\n"
    managed.write_bytes(old)
    broken = tmp_path / "broken.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(broken, "w") as output:
        for member in source.namelist():
            body = source.read(member)
            if member == "tracks/cli/main.py":
                body = b"def main(argv=None):\n    raise RuntimeError('health failure')\n"
            output.writestr(member, body)
    assert main(["install", str(target), "--version", "9.9.9", "--wheel", str(broken)]) == 1
    assert managed.read_bytes() == old


# AC-FR0352-01@v0.12 TRACKS-TRACE a linked managed ignore cannot modify the host ignore
def test_managed_ignore_symlink_does_not_write_the_host_file(tmp_path):
    target = tmp_path / "proj"
    init_git_project(target)
    (target / ".tracks").mkdir()
    linked = target / ".tracks/.gitignore"
    linked.symlink_to("../.gitignore")
    host_ignore = target / ".gitignore"
    before = (host_ignore.read_bytes(), host_ignore.stat().st_mtime_ns)
    head = git(target, "rev-parse", "HEAD").stdout
    wheel = _wheel(tmp_path)
    assert main(["install", str(target), "--version", "9.9.9", "--wheel", str(wheel)]) == 1
    assert linked.is_symlink()
    assert (host_ignore.read_bytes(), host_ignore.stat().st_mtime_ns) == before
    assert git(target, "rev-parse", "HEAD").stdout == head
