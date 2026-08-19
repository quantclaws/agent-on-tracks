"""Hotfix live credential probe (IF-HOTFIX-003 L3, test-plan §6.3 L3).

Probes ``GITHUB_TOKEN`` + ``TRAC_GITHUB_REPO`` + ``TRAC_LIVE_ISSUE`` (real
bug issue).  Missing → stdout exactly ``LIVE_SKIPPED: missing <NAME>`` + exit 0
skip, no evidence.  Present → ``GithubBackend.fetch_issue`` real read +
``precheck_hotfix`` rules on the real issue — from the INSTALLED current
candidate wheel, never the source tree (test-plan §2.5 isolation: import path
not in source tree, no ``TRAC_FAKE_SIMULATE``, no ``--assignment-overlay``).

The probe is read-only: it creates no run and writes nothing to a tracks DB.
``GithubBackend.fetch_issue`` / ``precheck_hotfix`` are IF-HOTFIX-003 stubs
until Devon wires the live channel — the credential-present test is therefore
a forward-looking Red (same status as the v0.5 live evidence probes): it fails
on the stub contract token until the real backend read lands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.e2e_live.harness import (
    clean_env,
    require_current_virtualenv,
)

_HOTFIX_LIVE_ENV = ("GITHUB_TOKEN", "TRAC_GITHUB_REPO", "TRAC_LIVE_ISSUE")

# The probe process runs from the isolated installation (cwd = a disposable
# git host OUTSIDE the source tree). It reads the real GitHub issue through
# the installed live channel and runs the deterministic PRECHECK rules; it
# never builds a run and never touches a tracks DB.
_HOTFIX_LIVE_PROBE = r'''
import json
import os
import re
import sqlite3
from pathlib import Path

import tracks

PROJECT_ROOT = Path(os.environ["TRAC_PROJECT_ROOT"])
tracks_file = Path(tracks.__file__).resolve()
venv_lib = Path(os.environ["TRAC_ISOLATED_VENV"]).resolve() / "lib"
assert tracks_file.is_relative_to(venv_lib), tracks_file
assert not tracks_file.is_relative_to(PROJECT_ROOT), tracks_file
assert not os.environ.get("TRAC_FAKE_SIMULATE"), "probe must not set TRAC_FAKE_SIMULATE"

from tracks.effects.github import GithubBackend
from tracks.executor.hotfix import precheck_hotfix

host = Path(os.environ["TRAC_LIVE_HOST"]).resolve()
token = os.environ["GITHUB_TOKEN"]
gh_repo = os.environ["TRAC_GITHUB_REPO"]
issue_number = int(os.environ["TRAC_LIVE_ISSUE"])
assert re.fullmatch(r"[^/]+/[^/]+", gh_repo), gh_repo

backend = GithubBackend(host, version="v0.6")
issue = backend.fetch_issue(issue_number)
assert issue is not None, f"issue {issue_number} not found in {gh_repo}"
assert issue.number == issue_number
assert issue.is_bug, f"TRAC_LIVE_ISSUE must carry the 'bug' label: {issue.title}"

projects_dir = host / ".tracks" / "projects"
report = precheck_hotfix(
    issue=issue,
    scenario="post-release",
    repo_branches=["main"],
    active_run_branch=None,
    projects_dir=projects_dir,
    approved_versions={"v0.5"},
)
assert report.status in ("pass", "rejected"), report
if report.status == "pass":
    assert report.target_version, "pass must locate a target version"

# Read-only probe: no run DB is created anywhere under the host.
assert not (host / ".tracks" / "runtime" / "tracks.db").exists()
print(json.dumps({"issue": issue.number, "title": issue.title,
                  "is_bug": issue.is_bug, "precheck": report.status}))
'''


@pytest.mark.e2e_live
# AC-FR0240-02@v0.6 TRACKS-TRACE live probe: missing credentials reports LIVE_SKIPPED without evidence
def test_hotfix_live_missing_credentials_reports_live_skipped():
    """Verify that missing credentials produce a precise skip and no evidence.

    Asserts no success evidence is produced BEFORE the skip (the skip must
    not swallow the no-evidence assertion), then emits the exact
    ``LIVE_SKIPPED: missing <NAME>`` reason (test-plan §2.3/§6.3 L3).
    """
    missing = [key for key in _HOTFIX_LIVE_ENV if not os.environ.get(key, "").strip()]
    if not missing:
        return  # All credentials present — the credential-less case is not applicable
    assert _no_hotfix_evidence(Path.cwd()), (
        "found satisfaction evidence despite missing credentials"
    )
    pytest.skip(
        f"LIVE_SKIPPED: missing {', '.join(missing)}; "
        "set GITHUB_TOKEN, TRAC_GITHUB_REPO, TRAC_LIVE_ISSUE to enable"
    )


@pytest.mark.e2e_live
# AC-FR0240-02@v0.6 TRACKS-TRACE live probe: real backend read from installed wheel
def test_hotfix_live_real_backend_read_from_installed_wheel():
    """Read the real GitHub issue + run PRECHECK from the current candidate
    wheel installed into an isolated fresh venv (test-plan §2.5 isolation),
    with cwd outside the source tree.

    Forward-looking Red: ``GithubBackend.fetch_issue`` / ``precheck_hotfix``
    are IF-HOTFOT handler stubs; until Devon wires the live read the probe
    fails at the stub contract token. On wiring it asserts the real API read
    + ``bug`` label convention + PRECHECK rules, with no run/DB write.
    """
    require_current_virtualenv()
    missing = [key for key in _HOTFIX_LIVE_ENV if not os.environ.get(key, "").strip()]
    if missing:
        pytest.skip(
            f"LIVE_SKIPPED: missing {', '.join(missing)}; "
            "set GITHUB_TOKEN, TRAC_GITHUB_REPO, TRAC_LIVE_ISSUE to enable"
        )

    wheel = _build_wheel_once()
    with tempfile.TemporaryDirectory(prefix="hotfix_live_") as td:
        temp_root = Path(td)
        host = _setup_host(temp_root)
        isolated_venv = host / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(isolated_venv)],
            check=True, capture_output=True,
        )
        isolated_python = isolated_venv / "bin" / "python"
        install = subprocess.run(
            [str(isolated_python), "-m", "pip", "install", str(wheel), "--no-deps"],
            capture_output=True, text=True, timeout=600,
        )
        assert install.returncode == 0, f"wheel install failed: {install.stderr}"

        # Live env for the probe: real credentials only, no simulation, no
        # overlay. cwd = the disposable host OUTSIDE the source tree.
        env = clean_env()
        env.update(
            {
                "GITHUB_TOKEN": os.environ["GITHUB_TOKEN"],
                "TRAC_GITHUB_REPO": os.environ["TRAC_GITHUB_REPO"],
                "TRAC_LIVE_ISSUE": os.environ["TRAC_LIVE_ISSUE"],
                "TRAC_PROJECT_ROOT": str(Path(__file__).resolve().parents[2]),
                "TRAC_ISOLATED_VENV": str(isolated_venv),
                "TRAC_LIVE_HOST": str(host),
            }
        )
        env.pop("TRAC_FAKE_SIMULATE", None)
        probe = subprocess.run(
            [str(isolated_python), "-c", _HOTFIX_LIVE_PROBE],
            cwd=host, env=env, capture_output=True, text=True, timeout=120,
        )
        assert probe.returncode == 0, (
            f"hotfix live probe failed: {probe.stderr.strip()}\n{probe.stdout.strip()}"
        )
        data = json.loads(probe.stdout)
        assert data["is_bug"] is True
        assert data["precheck"] in ("pass", "rejected")


_WHEEL_CACHE: dict[str, str] = {}


def _build_wheel_once() -> str:
    """Build the current project wheel once per session; return its path."""
    project_root = Path(__file__).resolve().parents[2]
    cached = _WHEEL_CACHE.get("wheel")
    if cached and Path(cached).is_file():
        return cached
    wheelhouse = project_root / "dist"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(project_root),
         "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheelhouse)],
        cwd=project_root, capture_output=True, text=True, timeout=600,
    )
    assert build.returncode == 0, f"wheel build failed: {build.stderr}"
    wheels = sorted(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    _WHEEL_CACHE["wheel"] = str(wheels[0])
    return _WHEEL_CACHE["wheel"]


def _setup_host(temp_root: Path) -> Path:
    """Create a minimal disposable git host at temp_root (probe cwd)."""
    host = temp_root / "host"
    host.mkdir()
    for cmd in [
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test Human"],
    ]:
        subprocess.run(cmd, cwd=host, check=True, capture_output=True)
    (host / "README.md").write_text("hotfix live probe host\n", encoding="utf-8")
    (host / ".gitignore").write_text(".venv\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", ".gitignore"], cwd=host, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=host, check=True, capture_output=True)
    return host


def _no_hotfix_evidence(path: Path) -> bool:
    """Return True if no hotfix run evidence exists under ``path``."""
    db = path / ".tracks" / "runtime" / "tracks.db"
    if not db.exists():
        return True
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT 1 FROM events WHERE type LIKE 'hotfix.%' LIMIT 1"
        ).fetchall()
    finally:
        conn.close()
    return len(rows) == 0
