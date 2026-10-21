"""Install fail-closed paths (IF-INSTALL-001, IF-PROJECTCLI-001, IF-VERSIONVIS-001).

AC-FR0354-01: a malformed install invocation is refused with the closed
``usage`` reason, exits non-zero and writes nothing into the target.
AC-FR0353-01 / AC-FR0350-01: a project whose receipt is gone (or whose
site-packages version contradicts the receipt) is ``install_corrupt`` — the
global version is never reported as a fallback.

Contracts: interfaces §1c.5 (closed reason set, ``trac install: <reason>:
<detail>`` on stderr), §1d.2/§1d.3 (project discovery, receipt + site-packages
validation, no global fallback), §1d.5 (version attribution), test-plan §10
rows 1 and 14.

Assertions land on the CLI outlet (stdout/stderr/exit code) and on the
project's own files — never on installer internals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._support.v012_install import git_init, receipt

pytestmark = pytest.mark.integration


def _install(cli, target: Path, cwd: Path, *extra: str):
    return cli.run("install", str(target), *extra, cwd=cwd)


def _assert_installed(cli, target: Path, cwd: Path) -> None:
    proc = _install(cli, target, cwd)
    assert proc.returncode == 0, proc.stderr


# AC-FR0354-01@v0.12 TRACKS-TRACE install usage failure writes nothing
def test_install_usage_failure_writes_nothing(v012_global, tmp_path):
    """AC-FR0354-01: a missing target argument or an unknown flag is refused
    with the closed ``usage`` reason, exits non-zero, prints no success line
    and leaves the target directory untouched (§1c.5, §10 #1)."""
    missing_arg = v012_global.run("install", cwd=tmp_path)
    assert missing_arg.returncode != 0, (
        f"a usage failure must exit non-zero: {missing_arg.stdout!r}"
    )
    assert "installed tracks" not in missing_arg.stdout
    assert missing_arg.stderr.startswith("trac install: usage:"), missing_arg.stderr
    assert not (tmp_path / ".tracks").exists(), (
        "a refused invocation must not create project state"
    )

    target = tmp_path / "unknown-flag"
    target.mkdir()
    unknown_flag = v012_global.run(
        "install", str(target), "--not-a-flag", cwd=tmp_path
    )
    assert unknown_flag.returncode != 0, (
        f"a usage failure must exit non-zero: {unknown_flag.stdout!r}"
    )
    assert "installed tracks" not in unknown_flag.stdout
    assert unknown_flag.stderr.startswith("trac install: usage:"), unknown_flag.stderr
    for relative in (".tracks", ".opencode", "opencode.json"):
        assert not (target / relative).exists(), (
            f"a refused invocation must not write {relative}"
        )


# AC-FR0353-01@v0.12 TRACKS-TRACE missing receipt is install corrupt
# AC-FR0350-01@v0.12 TRACKS-TRACE corrupt project never reports the global version
def test_missing_receipt_is_install_corrupt(v012_global, tmp_path):
    """AC-FR0353-01 / AC-FR0350-01: a project whose receipt is gone while its
    launcher and site-packages remain is ``install_corrupt`` — the global
    version is never reported in its place (§1d.2, §1d.3, §10 #14)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _assert_installed(v012_global, project, tmp_path)
    (project / ".tracks" / "install.json").unlink()

    outside = tmp_path / "outside"
    outside.mkdir()
    control = v012_global.run("version", cwd=outside)
    assert control.returncode == 0, (
        f"outside a project the global version must answer: {control.stderr!r}"
    )
    assert "(global)" in control.stdout, control.stdout

    broken = v012_global.run("version", cwd=project)
    assert broken.returncode != 0, (
        f"a corrupt project must not exit 0: {broken.stdout!r}"
    )
    assert "install_corrupt" in broken.stderr, broken.stderr
    assert "(global)" not in broken.stdout, (
        f"the global version must not take over a corrupt project: {broken.stdout!r}"
    )


# AC-FR0350-01@v0.12 TRACKS-TRACE receipt and site-packages version mismatch
def test_receipt_version_mismatch_is_install_corrupt(v012_global, tmp_path):
    """AC-FR0350-01: when the receipt version and the installed
    site-packages ``tracks.__version__`` disagree, the project is
    ``install_corrupt`` instead of reporting a version (§1d.3, §1d.5)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _assert_installed(v012_global, project, tmp_path)
    record = receipt(project)
    site_init = project / record["site_packages"] / "tracks" / "__init__.py"
    body = site_init.read_text(encoding="utf-8")
    assert re.search(r'__version__\s*=\s*"[^"]+"', body), (
        "the installed site-packages carries a version declaration"
    )
    site_init.write_text(
        re.sub(r'__version__\s*=\s*"[^"]+"', '__version__ = "9.9.9"', body, count=1),
        encoding="utf-8",
    )

    broken = v012_global.run("version", cwd=project)
    assert broken.returncode != 0, (
        f"a version mismatch must not exit 0: {broken.stdout!r}"
    )
    assert "install_corrupt" in broken.stderr, broken.stderr
    assert "(global)" not in broken.stdout, (
        f"the global version must not take over a corrupt project: {broken.stdout!r}"
    )
