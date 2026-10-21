"""Builders shared by the T-001 install RED unit tests.

These helpers construct the *external inputs* the install contract consumes:
candidate wheels (safe local artifacts, interfaces §1a) and seeded installed
project layouts (receipt + site-packages + launcher, interfaces §1b).  No
part of the installer, CLI, or project runtime is mocked — tests drive the
real ``tracks.cli.main.main`` entry point.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

AGENTS = ("Archer", "Devon", "Lex", "Prism", "Sage", "Scribe", "Shield")

SKILLS = (
    "tracks-archer-design",
    "tracks-archer-planning",
    "tracks-devon-rgr",
    "tracks-discuz",
    "tracks-envelope",
    "tracks-prism-design",
    "tracks-prism-impl",
    "tracks-prism-test",
    "tracks-quality-guards",
    "tracks-shield",
)

TEMPLATES = (
    "acceptance.md",
    "architecture.md",
    "bug-fix.md",
    "interfaces.md",
    "issues.md",
    "prd.md",
    "security-checklist.md",
    "spec.md",
    "story.md",
    "task-log.md",
    "task-plan.md",
    "test-plan.md",
    "pre-commit/README.md",
    "pre-commit/base.yaml",
    "pre-commit/ci-snippet.yml",
    "pre-commit/go.yaml",
    "pre-commit/java.yaml",
    "pre-commit/node.yaml",
    "pre-commit/python.yaml",
    "pre-commit/rust.yaml",
)

# The package-entry stub shipped inside a candidate wheel: the post-install
# health check runs the project launcher against this module.
_CLI_MAIN = (
    "def main(argv=None):\n"
    "    print('usage: trac')\n"
    "    return 0\n"
)

MARKER_LAUNCHER = """\
import json
import os
import sys
from pathlib import Path

payload = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "active": os.environ.get("TRAC_PROJECT_ACTIVE"),
}
Path(os.environ["TRAC_DELEGATION_MARKER"]).write_text(
    json.dumps(payload), encoding="utf-8"
)
raise SystemExit(7)
"""


@pytest.fixture(autouse=True)
def hermetic_git_identity(monkeypatch):
    """Deterministic commit identity for installer-driven git operations."""
    for key, value in {
        "GIT_AUTHOR_NAME": "Tracks Test",
        "GIT_AUTHOR_EMAIL": "tracks-test@example.com",
        "GIT_COMMITTER_NAME": "Tracks Test",
        "GIT_COMMITTER_EMAIL": "tracks-test@example.com",
    }.items():
        monkeypatch.setenv(key, value)


def default_opencode_config() -> dict:
    """The interfaces §1a.2 default harness config (per-agent permissions)."""
    permission = {"*": "deny", "{env:TMPDIR}tracks/*": "allow"}
    return {
        "agent": {
            role: {"permission": {"external_directory": dict(permission)}}
            for role in AGENTS
        }
    }


def _wheel_entries(version: str, agent_body: str | None) -> dict[str, str]:
    entries = {
        "tracks/__init__.py": f'__version__ = "{version}"\n',
        "tracks/cli/__init__.py": "",
        "tracks/cli/main.py": _CLI_MAIN,
        "tracks/assets/opencode.json": json.dumps(
            default_opencode_config(), indent=2
        )
        + "\n",
    }
    for role in AGENTS:
        body = agent_body if role == "Archer" and agent_body else f"# {role}\n"
        entries[f"tracks/agents/{role}.md"] = body
    for skill in SKILLS:
        entries[f"tracks/skills/{skill}/SKILL.md"] = f"# {skill}\n"
    for template in TEMPLATES:
        entries[f"tracks/templates/{template}"] = f"template {template}\n"
    return entries


def build_wheel(
    path: Path,
    version: str = "9.9.9",
    *,
    omit: str | None = None,
    requires: str | None = None,
    agent_body: str | None = None,
) -> Path:
    """Build a minimal PEP 427 wheel carrying the closed resource set."""
    entries = _wheel_entries(version, agent_body)
    if omit is not None:
        entries.pop(omit, None)
    metadata = [
        "Metadata-Version: 2.1",
        "Name: agent-on-tracks",
        f"Version: {version}",
    ]
    if requires is not None:
        metadata.append(f"Requires-Dist: {requires}")
    dist_info = f"agent_on_tracks-{version}.dist-info"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
        archive.writestr(f"{dist_info}/METADATA", "\n".join(metadata) + "\n")
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\n")
    return path


def wheel_bytes(wheel: Path, member: str) -> bytes:
    with zipfile.ZipFile(wheel) as archive:
        return archive.read(member)


def init_git_project(root: Path) -> None:
    """Create an existing user project with user-owned files + Git history."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-b", "main")
    (root / "README.md").write_text("user readme\n", encoding="utf-8")
    (root / ".gitignore").write_text("user-ignore\n", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "notes.md").write_text("user notes\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "user baseline")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def seed_installed_project(
    root: Path,
    version: str = "9.9.9",
    *,
    site_version: str | None = None,
    launcher: str | None = None,
) -> Path:
    """Seed a project receipt + site-packages (+ optional launcher)."""
    site_packages = f".tracks/env/{version}/site-packages"
    package_dir = root / site_packages / "tracks"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        f'__version__ = "{site_version or version}"\n', encoding="utf-8"
    )
    receipt = {
        "schema_version": 1,
        "version": version,
        "installed_at": "2026-10-19T00:00:00+00:00",
        "python": sys.executable,
        "site_packages": site_packages,
        "launcher": ".tracks/bin/trac.py",
        "installer_version": "0.12.0",
        "source": "wheel",
        "managed_files": [".tracks/bin/trac.py"],
        "seeded_files": [],
    }
    (root / ".tracks").mkdir(parents=True, exist_ok=True)
    (root / ".tracks" / "install.json").write_text(
        json.dumps(receipt, indent=2), encoding="utf-8"
    )
    launcher_path = root / ".tracks" / "bin" / "trac.py"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(launcher if launcher is not None else "", encoding="utf-8")
    return root


def read_receipt(root: Path) -> dict:
    return json.loads((root / ".tracks" / "install.json").read_text(encoding="utf-8"))


def assert_success_line(out: str, version: str, target: Path) -> None:
    """The interfaces §1c.4 success outlet: exactly one line, exact shape."""
    lines = out.splitlines()
    assert len(lines) == 1, f"expected one stdout line, got {lines!r}"
    prefix = f"installed tracks {version} at "
    assert lines[0].startswith(prefix), lines[0]
    announced = Path(lines[0][len(prefix):])
    assert announced.resolve() == target.resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
