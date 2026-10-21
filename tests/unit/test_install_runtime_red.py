"""T-001 RED: project discovery, version observation and CLI delegation
(IF-PROJECTCLI-001 / IF-VERSIONVIS-001 / IF-GLOBALCLI-001).

Devon-owned unit RED for the project-runtime slice:

- ``trac version`` reports the global scope outside a project and the
  receipt/site-packages scope inside one (interfaces §1d.3);
- a receipt-less ``.tracks/env`` or a receipt/site-packages version
  mismatch is ``install_corrupt`` — never a silent global fallback;
- non-install/version commands inside a project are delegated to the
  project launcher with ``TRAC_PROJECT_ACTIVE=1`` and the project root as
  cwd, while an already-active project does not re-delegate.

All nodes drive the real ``tracks.cli.main.main`` entry point over seeded
project layouts; the launcher is a recorded marker process, so delegation
is observed without mocking the CLI.  On the RED tree every node fails
with an ``AssertionError`` — no stub tokens, no collection errors.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracks import __version__ as RUNNING_VERSION
from tracks.cli.main import main

from .install_red_support import (
    MARKER_LAUNCHER,
    hermetic_git_identity,
    seed_installed_project,
)

__all__ = ["hermetic_git_identity"]


def _project_root_from_line(line: str, version: str) -> Path:
    prefix = f"trac {version} (project "
    assert line.startswith(prefix), line
    assert line.endswith(")")
    return Path(line[len(prefix):-1])


# AC-FR0349-01@v0.12 TRACKS-TRACE IF-GLOBALCLI-001 global version scope
def test_ac_0349_01_global_version_scope_outside_project(
    tmp_path, monkeypatch, capsys
):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    rc = main(["version"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == ""
    assert out.strip() == f"trac {RUNNING_VERSION} (global)"

    rc = main(["version", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == ""
    assert json.loads(out) == {
        "scope": "global",
        "version": RUNNING_VERSION,
    }


# AC-FR0353-01@v0.12 TRACKS-TRACE IF-VERSIONVIS-001 project scope from receipt
def test_ac_0353_01_project_version_reports_receipt_scope(
    tmp_path, monkeypatch, capsys
):
    root = seed_installed_project(
        tmp_path / "proj", "9.9.9", launcher=MARKER_LAUNCHER
    )
    marker = tmp_path / "delegated.json"
    monkeypatch.setenv("TRAC_DELEGATION_MARKER", str(marker))
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    rc = main(["version"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == ""
    line = out.strip()
    assert line.endswith(")")
    assert _project_root_from_line(line, "9.9.9").resolve() == root.resolve()
    assert not marker.exists()

    rc = main(["version", "--json"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["scope"] == "project"
    assert payload["version"] == "9.9.9"
    assert Path(payload["project_root"]).resolve() == root.resolve()
    candidate = Path(payload["site_packages"])
    if not candidate.is_absolute():
        candidate = root / candidate
    expected = root / ".tracks" / "env" / "9.9.9" / "site-packages"
    assert candidate.resolve() == expected.resolve()


# AC-FR0353-02@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 corrupt project fail-closed
def test_ac_0353_02_missing_receipt_with_env_is_corrupt(
    tmp_path, monkeypatch, capsys
):
    root = tmp_path / "broken"
    (root / ".tracks" / "env" / "9.9.9" / "site-packages" / "tracks").mkdir(
        parents=True
    )
    monkeypatch.chdir(root)

    rc = main(["version"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "install_corrupt" in err
    assert "global" not in out


# AC-FR0353-02@v0.12 TRACKS-TRACE IF-VERSIONVIS-001 receipt/site mismatch
def test_ac_0353_02_site_version_mismatch_is_corrupt(
    tmp_path, monkeypatch, capsys
):
    root = seed_installed_project(
        tmp_path / "proj", "9.9.9", site_version="8.8.8"
    )
    monkeypatch.chdir(root)

    rc = main(["version"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "install_corrupt" in err
    assert "global" not in out


# AC-FR0353-01@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 project launcher delegation
# AC-FR0349-01@v0.12 TRACKS-TRACE IF-GLOBALCLI-001 global entry delegates
def test_ac_0353_01_project_command_delegates_to_locked_launcher(
    tmp_path, monkeypatch, capsys
):
    root = seed_installed_project(
        tmp_path / "proj", "9.9.9", launcher=MARKER_LAUNCHER
    )
    marker = tmp_path / "delegated.json"
    monkeypatch.setenv("TRAC_DELEGATION_MARKER", str(marker))
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    nested = root / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)

    rc = main(["status"])
    capsys.readouterr()
    assert rc == 7
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["argv"] == ["status"]
    assert Path(payload["cwd"]).resolve() == root.resolve()
    assert payload["active"] == "1"


# AC-FR0353-02@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 corrupt project blocks run
def test_ac_0353_02_corrupt_project_blocks_delegation(
    tmp_path, monkeypatch, capsys
):
    root = seed_installed_project(
        tmp_path / "proj",
        "9.9.9",
        site_version="8.8.8",
        launcher=MARKER_LAUNCHER,
    )
    marker = tmp_path / "delegated.json"
    monkeypatch.setenv("TRAC_DELEGATION_MARKER", str(marker))
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    monkeypatch.chdir(root)

    rc = main(["status"])
    out, err = capsys.readouterr()
    assert rc == 1
    assert "install_corrupt" in err
    assert not marker.exists()


# AC-FR0353-02@v0.12 TRACKS-TRACE IF-PROJECTCLI-001 no recursive delegation
def test_ac_0353_02_active_project_does_not_delegate(tmp_path, monkeypatch, capsys):
    root = seed_installed_project(
        tmp_path / "proj", "9.9.9", launcher=MARKER_LAUNCHER
    )
    marker = tmp_path / "delegated.json"
    monkeypatch.setenv("TRAC_DELEGATION_MARKER", str(marker))
    monkeypatch.chdir(root)

    # First call (no marker env) delegates to the project launcher...
    monkeypatch.delenv("TRAC_PROJECT_ACTIVE", raising=False)
    rc = main(["status"])
    capsys.readouterr()
    assert rc == 7
    assert marker.exists()

    # ...and the launcher's own TRAC_PROJECT_ACTIVE=1 run must not re-delegate.
    marker.unlink()
    monkeypatch.setenv("TRAC_PROJECT_ACTIVE", "1")
    rc = main(["status"])
    out, err = capsys.readouterr()
    assert rc == 0, err
    assert "no runs yet" in out
    assert not marker.exists()
