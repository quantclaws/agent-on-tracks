"""Install and multi-version journey (test-plan §9.1/§9.2).

AC-FR0348-01: the official wheel installs a fully capable project.
AC-FR0349-01: the global console entry works from any directory.
AC-FR0350-01: a project background service serves its locked version and
can be stopped.
AC-FR0351-01: an explicit old version installs while the global version is
new. AC-FR0353-01: upgrading the global prefix leaves the locked project on
its old version.

Happy path only; every assertion lands on CLI output/exit, persisted files
or ``/healthz``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests._support.v012_install import (
    CURRENT_VERSION,
    OLD_VERSION,
    git_init,
    install_global,
    parse_serving_line,
    receipt,
    wait_healthz,
    wait_unreachable,
    wheel_members,
)

pytestmark = pytest.mark.e2e


def _install(cli, target: Path, cwd: Path, *extra: str):
    return cli.run("install", str(target), *extra, cwd=cwd)


def _start_background(cli, project: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = cli.env({"TRAC_SERVE_PASSWORD": "v012-journey-password"})
    return subprocess.run(
        ["sh", "-c", f'"{cli.executable}" serve --repo "{project}" --background'],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def _serving_line(stdout: str) -> str:
    for line in stdout.splitlines():
        if "serving on http://" in line:
            return line
    raise AssertionError(f"no serving line in stdout={stdout!r}")


def _assert_capability_resources(project: Path, wheel: Path) -> None:
    members = wheel_members(wheel)
    locked_site = project / receipt(project)["site_packages"]
    for name, content in members.items():
        if name.startswith(("tracks/agents/", "tracks/skills/", "tracks/templates/")):
            assert (locked_site / name).read_bytes() == content, (
                f"install must retain runtime resources in the locked package: {name}"
            )
    assert (project / "opencode.json").read_bytes() == members["tracks/assets/opencode.json"], (
        "the default harness config must be the wheel's asset"
    )
    assert (project / ".opencode" / "agents" / "Shield.md").is_file()
    assert (project / ".opencode" / "skills" / "tracks-shield" / "SKILL.md").is_file()
    assert (project / ".opencode" / "templates" / "story.md").is_file()
    assert (project / ".tracks" / "bin" / "trac.py").is_file()
    assert receipt(project)["version"] == CURRENT_VERSION


# AC-FR0349-01@v0.12 TRACKS-TRACE global trac available anywhere
def test_global_trac_available_anywhere(v012_global, tmp_path):
    """AC-FR0349-01 (journey): after installing the wheel globally, ``trac
    --help`` works from a directory that is not a project."""
    outside = tmp_path / "not-a-project"
    outside.mkdir()
    proc = v012_global.run("--help", cwd=outside)
    assert proc.returncode == 0, proc.stderr
    assert "usage: trac" in proc.stdout
    for token in ("install", "version", "serve"):
        assert token in proc.stdout
    assert not (outside / ".tracks").exists()


# AC-FR0348-01@v0.12 TRACKS-TRACE install from the official wheel
def test_install_from_official_wheel(v012_global, v012_wheels, tmp_path):
    """AC-FR0348-01 (journey): installing the official wheel into an empty
    directory delivers the complete capability set and a usable project."""
    project = tmp_path / "journey-project"
    project.mkdir()
    proc = _install(v012_global, project, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        f"installed tracks {CURRENT_VERSION} at {project}"
    )
    _assert_capability_resources(project, v012_wheels["current"])
    status = v012_global.run("status", cwd=project)
    assert status.returncode == 0, status.stderr


# AC-FR0348-01@v0.12 TRACKS-TRACE fresh installed project pins the full workflow at v0.1
def test_fresh_project_pins_full_workflow(v012_global, tmp_path):
    project = tmp_path / "first-product"
    installed = _install(v012_global, project, tmp_path)
    assert installed.returncode == 0, installed.stderr
    started = v012_global.run("start", "v0.1", cwd=project, stdin="实现一个整数加法函数。")
    assert started.returncode == 0, started.stderr
    run_id = started.stdout.split("run ", 1)[1].split(" started", 1)[0]
    replay = v012_global.run("replay", run_id, cwd=project)
    assert replay.returncode == 0, replay.stderr
    assert '"workflow_profile": "full-v1"' in replay.stdout
    assert (project / ".tracks" / "projects" / "v0.1" / "story.md").is_file()
    assert not (project / ".tracks" / "projects" / "full-v1").exists()


# AC-FR0348-01@v0.12 TRACKS-TRACE installed v0.1 product traverses the full release workflow
def test_installed_product_full_release_journey(v012_global, host_repo, ci_echo_standin):
    """Use the installed CLI; Agent and external-channel fixtures remain deterministic."""
    from tests.e2e.helpers import walk_to_awaiting_release

    installed = v012_global.run("install", str(host_repo), cwd=host_repo.parent)
    assert installed.returncode == 0, installed.stderr

    def installed_trac(*args, stdin=None, simulate=None):
        env = {"TRAC_FAKE_SIMULATE": simulate} if simulate else None
        return v012_global.run(*args, cwd=host_repo, stdin=stdin, env=env, timeout=300)

    installed_trac.repo = host_repo
    run_id = walk_to_awaiting_release(installed_trac, version="v0.1")
    released = installed_trac("release", "--action", "release")
    assert released.returncode == 0, released.stderr
    driven = installed_trac("run", "--no-watch")
    assert driven.returncode == 0, driven.stderr
    status = installed_trac("status")
    assert "terminal=released" in status.stdout, status.stdout
    replay = installed_trac("replay", run_id)
    for stage in ("M-IMPL", "M-VERIFY", "M-SECURITY", "M-PUBLISH", "M-MILESTONE"):
        assert f'"stage": "{stage}"' in replay.stdout


# AC-FR0350-01@v0.12 TRACKS-TRACE project background server roundtrip
def test_project_server_background_roundtrip(v012_global, v012_wheels, tmp_path):
    """AC-FR0350-01 (journey): background start reports url/pid/version,
    healthz serves the locked version, the shell exits, status stays
    running, and stop makes the service unreachable."""
    project = tmp_path / "journey-project"
    project.mkdir()
    assert _install(v012_global, project, tmp_path).returncode == 0
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    line = _serving_line(started.stdout)
    assert "background" in line
    parsed = parse_serving_line(line)
    locked = receipt(project)["version"]
    assert parsed.get("version") == locked
    assert wait_healthz(parsed["url"])["version"] == locked
    status = v012_global.run(
        "serve", "--repo", str(project), "--status", cwd=tmp_path, timeout=30
    )
    assert status.returncode == 0 and "running" in status.stdout
    stop = v012_global.run(
        "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
    )
    assert stop.returncode == 0 and "stopped" in stop.stdout
    wait_unreachable(parsed["url"])


# AC-FR0351-01@v0.12 TRACKS-TRACE install old while global is new
def test_install_old_version_while_global_new(v012_global, v012_wheels, tmp_path):
    """AC-FR0351-01 (journey): the target installs the explicit old version
    while the global CLI reports the new version; both scopes are observable."""
    project = tmp_path / "old-project"
    project.mkdir()
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
    assert receipt(project)["version"] == OLD_VERSION
    outside = tmp_path / "outside"
    outside.mkdir()
    global_payload = json.loads(
        v012_global.run("version", "--json", cwd=outside).stdout
    )
    assert global_payload == {"scope": "global", "version": CURRENT_VERSION}
    project_payload = json.loads(
        v012_global.run("version", "--json", cwd=project).stdout
    )
    assert project_payload["scope"] == "project"
    assert project_payload["version"] == OLD_VERSION
    assert Path(project_payload["project_root"]).resolve() == project.resolve()


# AC-FR0353-01@v0.12 TRACKS-TRACE global upgrade keeps the old project
def test_upgrade_global_project_stays_old(v012_wheels, tmp_path):
    """AC-FR0353-01 (journey): a project installed by the old global prefix
    keeps working at the old version after the global entry is upgraded."""
    old_prefix = tmp_path / "global-old"
    old_global = install_global(old_prefix, v012_wheels["old"])
    project = git_init(tmp_path / "locked-project", {"README.md": "locked\n"})
    installed = _install(old_global, project, tmp_path, "--version", OLD_VERSION)
    assert installed.returncode == 0, installed.stderr

    upgraded_prefix = tmp_path / "global-new"
    upgraded = install_global(upgraded_prefix, v012_wheels["current"])
    payload = json.loads(upgraded.run("version", "--json", cwd=project).stdout)
    assert payload["scope"] == "project"
    assert payload["version"] == OLD_VERSION
    status = upgraded.run("status", cwd=project)
    assert status.returncode == 0, (
        f"the locked project must keep working after the upgrade: {status.stderr!r}"
    )
    assert receipt(project)["version"] == OLD_VERSION
