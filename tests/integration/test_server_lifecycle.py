"""v0.12 project server lifecycle (IF-SERVERLIFE-001, IF-PROJECTCLI-001).

AC-FR0349-02 / AC-FR0350-02: outside an installed project, serve refuses
with ``not_installed`` (no global-version fallback, no listener).
AC-FR0350-01: background serves the project's locked version and stays
connected after the starting shell exits until ``--stop``.
AC-NFR0159-01: POSIX background persistence / explicit stop; Windows
``--background`` is refused and foreground lifetime equals the process.

Assertions land on CLI stdout/stderr/exit, ``/healthz`` and the pidfile
outlet only.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests._support.v012_install import (
    free_port,
    git_init,
    parse_serving_line,
    receipt,
    wait_healthz,
    wait_unreachable,
)
from tests._support.v09_web import (
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration

def _install_project(global_cli, target: Path, cwd: Path):
    proc = global_cli.run("install", str(target), cwd=cwd)
    assert proc.returncode == 0, proc.stderr


def _start_background(global_cli, project: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = global_cli.env({"TRAC_SERVE_PASSWORD": "v012-test-password"})
    return subprocess.run(
        ["sh", "-c", f'"{global_cli.executable}" serve --repo "{project}" --background'],
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


def _port_closed(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _pidfile(project: Path) -> Path:
    """The background-service pidfile outlet (interfaces §1b.5 / §3 row 8)."""
    return project / ".tracks" / "service" / "serve.pid"


def _write_pidfile(project: Path, pid: int) -> Path:
    """Write a pidfile in the documented shape (§1b.5)."""
    path = _pidfile(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "host": "127.0.0.1",
                "port": 8000,
                "version": "0.12.0",
                "repo": str(project),
                "started_at": "2026-10-19T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def _windows_driver() -> str:
    """A CLI driver with the platform injected as Windows (§1e.5, §6.3)."""
    return (
        "import os, platform, sys\n"
        "platform.system = lambda: 'Windows'\n"
        "sys.platform = 'win32'\n"
        "os.name = 'nt'\n"
        # import after the platform injection so a module-level platform
        # constant in the serve entry also sees Windows (§6.3).
        "from tracks.cli.main import main\n"
        "raise SystemExit(main(sys.argv[1:]))\n"
    )


# AC-FR0349-02@v0.12 TRACKS-TRACE serve outside a project is refused
# AC-FR0350-02@v0.12 TRACKS-TRACE no global version fallback for serve
def test_serve_outside_project_refused(v012_global, tmp_path):
    """AC-FR0349-02 / AC-FR0350-02: ``trac serve`` in a directory without a
    receipt refuses with ``not_installed`` (pointing at ``trac install``),
    exits non-zero, listens on nothing and creates no project state."""
    nonproject = tmp_path / "plain-dir"
    nonproject.mkdir()
    port = free_port()
    env = v012_global.env({"TRAC_SERVE_PASSWORD": "v012-test-password"})
    proc = subprocess.Popen(
        [
            str(v012_global.executable),
            "serve",
            "--repo",
            str(nonproject),
            "--port",
            str(port),
        ],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise AssertionError(
                "serve started outside an installed project instead of refusing"
            ) from None
        assert proc.returncode != 0, f"serve must refuse, stdout={stdout!r}"
        assert "not_installed" in stderr, stderr
        assert "trac install" in stderr, "the refusal must point at the install step"
        assert not (nonproject / ".tracks" / "install.json").exists()
        assert _port_closed(port), "a refused serve must not listen"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# AC-FR0350-01@v0.12 TRACKS-TRACE background serves the locked project version
def test_background_survives_shell_and_reports_locked_version(
    v012_global, v012_wheels, tmp_path
):
    """AC-FR0350-01: a background start prints url/pid/version, healthz and
    ``--status`` report the project's locked version, and the process keeps
    answering after the starting shell exits until ``--stop``."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    line = _serving_line(started.stdout)
    assert "background" in line
    parsed = parse_serving_line(line)
    locked = receipt(project)["version"]
    assert parsed.get("version") == locked
    payload = wait_healthz(parsed["url"])
    assert payload["version"] == locked, (
        f"healthz must carry the project version: {payload!r}"
    )
    status = v012_global.run(
        "serve", "--repo", str(project), "--status", cwd=tmp_path, timeout=30
    )
    assert status.returncode == 0, status.stderr
    assert "running" in status.stdout
    assert f"pid={parsed['pid']}" in status.stdout, status.stdout
    assert f"version={locked}" in status.stdout
    assert "url=" in status.stdout, status.stdout
    assert parsed["url"].rsplit(":", 1)[1] in status.stdout, (
        f"the status line must report the running service's url: {status.stdout!r}"
    )
    stop = v012_global.run(
        "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
    )
    assert stop.returncode == 0, stop.stderr
    assert "stopped" in stop.stdout
    wait_unreachable(parsed["url"])


# AC-NFR0159-01@v0.12 TRACKS-TRACE shell exit keeps server stop makes unreachable
def test_background_survives_shell_exit_and_stop_unreachable(
    v012_global, v012_wheels, tmp_path
):
    """AC-NFR0159-01 (POSIX half): the detached service survives the shell
    that started it exiting, and an explicit ``--stop`` makes it
    unreachable."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    url = parse_serving_line(_serving_line(started.stdout))["url"]
    # the spawning shell already exited (subprocess.run returned); the
    # service must still answer.
    assert wait_healthz(url)["status"] == "ok"
    stop = v012_global.run(
        "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
    )
    assert stop.returncode == 0, stop.stderr
    wait_unreachable(url)


# AC-NFR0159-01@v0.12 TRACKS-TRACE windows background refused foreground honest
def test_windows_foreground_branch(v012_global, tmp_path):
    """AC-NFR0159-01 (Windows half): ``--background`` is refused with
    ``unsupported_platform`` and writes no pidfile; the foreground process's
    lifetime is the service lifetime (no pidfile, unreachable once it ends)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    driver = _windows_driver()
    refused = subprocess.run(
        [sys.executable, "-c", driver, "serve", "--repo", str(project), "--background"],
        cwd=str(tmp_path),
        env=v012_global.env({"TRAC_SERVE_PASSWORD": "v012-test-password"}),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert refused.returncode != 0
    assert "unsupported_platform" in refused.stderr, refused.stderr
    assert not (project / ".tracks" / "service" / "serve.pid").exists()

    home = tmp_path / "foreground-home"
    home.mkdir()
    proc = start_serve(home, project, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        assert wait_healthz(base)["status"] == "ok"
        assert not (project / ".tracks" / "service" / "serve.pid").exists(), (
            "foreground service must not write a pidfile"
        )
    finally:
        stop_serve(proc)
    wait_unreachable(base)


# AC-NFR0159-01@v0.12 TRACKS-TRACE stop refuses a foreign pid and cleans a stale pidfile
def test_stop_refuses_foreign_pid_and_cleans_stale_pidfile(v012_global, tmp_path):
    """AC-NFR0159-01: ``--stop`` on a pidfile that names a live process
    outside this project refuses with ``stop_refused`` and leaves that
    process untouched; a stale pidfile is cleaned up and reported as not
    running (§1e.3, §10 #18)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)

    foreign = subprocess.Popen(["sleep", "120"])
    try:
        pidfile = _write_pidfile(project, foreign.pid)
        refused = v012_global.run(
            "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
        )
        assert refused.returncode != 0, (
            f"a foreign pid must not be stopped: {refused.stdout!r}"
        )
        assert "stop_refused" in refused.stderr, refused.stderr
        assert foreign.poll() is None, "the foreign process must survive"
        assert pidfile.exists(), "a refused stop must not consume the pidfile"
    finally:
        foreign.terminate()
        foreign.wait(timeout=10)

    stale = subprocess.Popen(["sleep", "0.01"])
    stale.wait(timeout=10)
    pidfile = _write_pidfile(project, stale.pid)
    stopped = v012_global.run(
        "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
    )
    assert stopped.returncode == 0, stopped.stderr
    assert "not running" in stopped.stdout, stopped.stdout
    assert "stale pidfile" in stopped.stdout, stopped.stdout
    assert not pidfile.exists(), "a stale pidfile must be cleaned up"


# AC-FR0350-01@v0.12 TRACKS-TRACE a second background start reports already running
def test_second_background_start_reports_already_running(v012_global, tmp_path):
    """AC-FR0350-01: a second ``--background`` on a live project reports
    ``already running pid=<pid> url=<url>`` and exits 0 without starting a
    second service (§1e.2)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    first = parse_serving_line(_serving_line(started.stdout))
    try:
        again = _start_background(v012_global, project, tmp_path)
        assert again.returncode == 0, again.stderr
        assert "already running" in again.stdout, again.stdout
        assert f"pid={first['pid']}" in again.stdout, again.stdout
        assert "url=" in again.stdout, again.stdout
    finally:
        v012_global.run(
            "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
        )


# AC-FR0350-01@v0.12 TRACKS-TRACE a taken port fails the background start
def test_background_on_taken_port_fails_without_pidfile(v012_global, tmp_path):
    """AC-FR0350-01: a background start whose port is already taken fails
    with ``background_failed`` and writes no pidfile — the half-started
    service is not advertised as running (§1e.2, §10 #17)."""
    holder = git_init(tmp_path / "holder", {"README.md": "h\n"})
    _install_project(v012_global, holder, tmp_path)
    port = free_port()
    env = v012_global.env({"TRAC_SERVE_PASSWORD": "v012-test-password"})
    running = subprocess.run(
        [
            "sh",
            "-c",
            (
                f'"{v012_global.executable}" serve --repo "{holder}" '
                f"--port {port} --background"
            ),
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert running.returncode == 0, running.stderr
    try:
        taken = git_init(tmp_path / "taken", {"README.md": "t\n"})
        _install_project(v012_global, taken, tmp_path)
        failed = subprocess.run(
            [
                "sh",
                "-c",
                (
                    f'"{v012_global.executable}" serve --repo "{taken}" '
                    f"--port {port} --background"
                ),
            ],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        assert failed.returncode != 0, (
            f"a taken port must fail the background start: {failed.stdout!r}"
        )
        assert "background_failed" in failed.stderr, failed.stderr
        assert not _pidfile(taken).exists(), (
            "a failed background start must not leave a pidfile"
        )
    finally:
        v012_global.run(
            "serve", "--repo", str(holder), "--stop", cwd=tmp_path, timeout=30
        )


# AC-FR0350-01@v0.12 TRACKS-TRACE background writes the documented pidfile and log
def test_background_writes_documented_pidfile_and_log(v012_global, tmp_path):
    """AC-FR0350-01: a background start records the documented pidfile
    (``pid``/``host``/``port``/``version``/``repo``/``started_at``) that agrees
    with the announced serving line and the project's locked version, and
    appends the background child's output to the service log
    (§1b.5, §1e.2, §3 rows 8/9)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    started = _start_background(v012_global, project, tmp_path)
    assert started.returncode == 0, started.stderr
    parsed = parse_serving_line(_serving_line(started.stdout))
    try:
        pidfile = _pidfile(project)
        assert pidfile.is_file(), "the background start must write the pidfile"
        record = json.loads(pidfile.read_text(encoding="utf-8"))
        assert record["pid"] == int(parsed["pid"]), record
        assert record["port"] == int(parsed["url"].rsplit(":", 1)[1]), record
        assert record["version"] == receipt(project)["version"], record
        assert record["host"], record
        assert project.name in record["repo"], record
        assert record["started_at"], record
    finally:
        v012_global.run(
            "serve", "--repo", str(project), "--stop", cwd=tmp_path, timeout=30
        )
    log = project / ".tracks" / "service" / "serve.log"
    assert log.is_file(), "the background start must append to the service log"
    body = log.read_text(encoding="utf-8")
    assert body.strip(), "the service log must carry the background child's output"
    assert parsed["url"].rsplit(":", 1)[1] in body, (
        f"the service log must carry the announced serving line: {body!r}"
    )


# AC-FR0350-01@v0.12 TRACKS-TRACE status reports a stale pidfile as stopped
def test_status_reports_stale_pidfile_as_stopped(v012_global, tmp_path):
    """AC-FR0350-01: ``--status`` on a stale pidfile reports
    ``stopped (stale pidfile)`` and exits 0 (§1e.4)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    stale = subprocess.Popen(["sleep", "0.01"])
    stale.wait(timeout=10)
    _write_pidfile(project, stale.pid)
    status = v012_global.run(
        "serve", "--repo", str(project), "--status", cwd=tmp_path, timeout=30
    )
    assert status.returncode == 0, status.stderr
    assert "stopped" in status.stdout, status.stdout
    assert "stale pidfile" in status.stdout, status.stdout


# AC-NFR0159-01@v0.12 TRACKS-TRACE windows stop refused
def test_windows_stop_refused(v012_global, tmp_path):
    """AC-NFR0159-01 (Windows half): ``--stop`` is refused with
    ``unsupported_platform`` and leaves the pidfile alone (§1e.5)."""
    project = git_init(tmp_path / "project", {"README.md": "p\n"})
    _install_project(v012_global, project, tmp_path)
    pidfile = _write_pidfile(project, 4242)
    refused = subprocess.run(
        [sys.executable, "-c", _windows_driver(), "serve", "--repo", str(project), "--stop"],
        cwd=str(tmp_path),
        env=v012_global.env({"TRAC_SERVE_PASSWORD": "v012-test-password"}),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert refused.returncode != 0
    assert "unsupported_platform" in refused.stderr, refused.stderr
    assert pidfile.exists(), "a refused stop must not consume the pidfile"
