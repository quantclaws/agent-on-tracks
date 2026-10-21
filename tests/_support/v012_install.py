"""v0.12 install/version test infrastructure (IF-INSTALLPKG-001 / IF-INSTALL-001).

Builds candidate wheels from the tree under test (the current version and
extra versions/recipes in a throw-away copy), installs one wheel into an
isolated global prefix, and runs it exactly like the test-plan §2.5
contract describes: ``PYTHONPATH=<prefix>/lib/.../site-packages
<prefix>/bin/trac`` from a cwd outside the source tree. Nothing here
imports a not-yet-delivered product module; the install/version assertions
land on CLI output, files and HTTP only.

Unavoidable external constraints (test-plan §6.1): no package index — the
only package inputs are wheels built locally from this tree; no mocked
framework internals — the wheel is the real build product and the prefix
is a real isolated install target.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)
CURRENT_VERSION = "0.12.0"
OLD_VERSION = "0.11.0"

_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    ".tracks",
    ".opencode",
    ".pytest_cache",
    "tests",
    "dist",
    "build",
    "tracks.db",
    "__pycache__",
    "*.pyc",
    "*.egg-info",
)


def _wheel_path_for(version: str) -> str:
    return f"agent_on_tracks-{version}-py3-none-any.whl"


def build_wheel(
    workdir: Path,
    *,
    version: str | None = None,
    omit: tuple[str, ...] = (),
    extra_requires: tuple[str, ...] = (),
) -> Path:
    """Build one wheel into ``workdir/dist``.

    ``version`` rewrites the temp source copy (never this tree); ``omit``
    removes package-data paths from the copy so the wheel is genuinely
    incomplete; ``extra_requires`` appends a Requires-Dist the running
    interpreter cannot satisfy.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "dist"
    out.mkdir(parents=True, exist_ok=True)
    source = workdir / "src"
    if source.exists():
        shutil.rmtree(source)
    shutil.copytree(PROJECT_ROOT, source, ignore=_IGNORE)
    if version is not None:
        _rewrite_version(source, version)
    for rel in omit:
        _drop(source / rel)
    if extra_requires:
        _add_requires(source / "pyproject.toml", extra_requires)
    proc = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "-w",
            str(out),
            str(source),
        ],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, f"wheel build failed: {proc.stderr[-2000:]}"
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels!r}"
    return wheels[0]


def _rewrite_version(source: Path, version: str) -> None:
    pyproject = source / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', text, count=1)
    pyproject.write_text(text, encoding="utf-8")
    init = source / "tracks" / "__init__.py"
    body = init.read_text(encoding="utf-8")
    body = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', body, count=1)
    init.write_text(body, encoding="utf-8")


def _drop(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _add_requires(pyproject: Path, requires: tuple[str, ...]) -> None:
    text = pyproject.read_text(encoding="utf-8")
    marker = "dependencies = ["
    assert marker in text, "pyproject dependencies table missing"
    added = "".join(f'\n    "{item}",' for item in requires)
    text = text.replace(marker, marker + added, 1)
    pyproject.write_text(text, encoding="utf-8")


def _site_dir(prefix: Path) -> Path:
    rel = Path("lib") / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    return prefix / rel


@dataclass(frozen=True)
class GlobalCli:
    """An isolated global install: prefix + site-packages + console entry."""

    prefix: Path
    site: Path
    executable: Path

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE", "PYTHONPATH", "VIRTUAL_ENV")
        }
        env["PYTHONPATH"] = str(self.site)
        env.setdefault("TRAC_AGENT_BACKEND", "fake")
        env.setdefault("TRAC_INFRA_RETRY_LIMIT", "2")
        if extra:
            env.update(extra)
        return env

    def run(
        self,
        *args: str,
        cwd: Path,
        timeout: float = 120.0,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.executable), *args],
            cwd=str(cwd),
            env=self.env(env),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )


def install_global(prefix: Path, wheel: Path) -> GlobalCli:
    """Install a wheel into ``prefix`` and expose a ``bin/trac`` entry.

    ``pip install --target`` gives the isolated tree the test-plan asks
    for; the console entry is materialised in ``bin/trac`` (the wheel's
    declared entry point is ``tracks.cli.main:main``) so the global
    invocation is ``PYTHONPATH=<site> bin/trac ...``.
    """
    site = _site_dir(prefix)
    site.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "pip",
            "install",
            "--no-cache-dir",
            "--no-deps",
            "--target",
            str(site),
            str(wheel),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, f"global install failed: {proc.stderr[-2000:]}"
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    entry = bin_dir / "trac"
    entry.write_text(
        f"#!{PYTHON}\n"
        "import sys\n"
        "from tracks.cli.main import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)
    return GlobalCli(prefix=prefix, site=site, executable=entry)


def git_init(path: Path, files: dict[str, str] | None = None) -> Path:
    """Initialise a deterministic git project (target premise, §2.5)."""
    path.mkdir(parents=True, exist_ok=True)
    base = {"README.md": "project readme\n"}
    base.update(files or {})
    for name, body in base.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "install@example.com"),
        ("config", "user.name", "Install Human"),
        ("add", "-A"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(
            ["git", *args],
            cwd=str(path),
            check=True,
            capture_output=True,
            text=True,
        )
    return path


def make_existing_project(path: Path) -> Path:
    """An in-progress git project with tracked and untracked user files.

    The user owns the root ``opencode.json`` and its own
    ``.opencode/opencode.json``: interfaces §1b.4 makes the root config
    seeded-only and forbids touching the user's own ``.opencode`` config.
    """
    git_init(
        path,
        {
            "src/app.py": "def main():\n    return 1\n",
            "docs/notes.md": "user notes\n",
            "opencode.json": '{"user": "own harness config"}\n',
            ".opencode/opencode.json": '{"user": "own opencode config"}\n',
        },
    )
    (path / "scratch.txt").write_text("untracked user scratch\n", encoding="utf-8")
    return path


def receipt(project: Path) -> dict[str, Any]:
    payload = json.loads((project / ".tracks" / "install.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def project_launcher(project: Path) -> Path:
    return project / ".tracks" / "bin" / "trac.py"


def project_cli(
    project: Path,
    *args: str,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the installed project's own launcher (the locked version)."""
    launcher = project_launcher(project)
    assert launcher.is_file(), f"project launcher missing at {launcher}"
    base = {
        key: value
        for key, value in os.environ.items()
        if key not in ("TRACKS_HOME", "TRAC_FAKE_SIMULATE", "PYTHONPATH", "VIRTUAL_ENV")
    }
    base.setdefault("TRAC_AGENT_BACKEND", "fake")
    base.setdefault("TRAC_INFRA_RETRY_LIMIT", "2")
    if env:
        base.update(env)
    return subprocess.run(
        [str(PYTHON), str(launcher), *args],
        cwd=str(project),
        env=base,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def wheel_members(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def wheel_metadata_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as archive:
        name = next(
            entry for entry in archive.namelist()
            if entry.endswith(".dist-info/METADATA")
        )
        for line in archive.read(name).decode("utf-8").splitlines():
            if line.startswith("Version: "):
                return line.split(": ", 1)[1].strip()
    raise AssertionError("wheel METADATA carries no Version")


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot(root: Path) -> dict[str, str]:
    """Relative path -> sha256 for every regular file under ``root``."""
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def reason_of(proc: subprocess.CompletedProcess) -> str:
    match = re.search(r"trac install: ([a-z_]+):", proc.stderr or "")
    return match.group(1) if match else ""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def parse_serving_line(line: str) -> dict[str, str]:
    match = re.search(
        r"serving on (?P<url>http://\S+) \(pid (?P<pid>\d+)(?:, version "
        r"(?P<version>[^,)]+))?(?:, background)?\)",
        line,
    )
    assert match, f"no serving line: {line!r}"
    return {key: value for key, value in match.groupdict().items() if value}


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def wait_healthz(base_url: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: tuple[int, bytes] = (0, b"")
    while time.time() < deadline:
        try:
            status, _headers, body = http_request("GET", base_url + "/healthz")
        except OSError:
            time.sleep(0.2)
            continue
        last = (status, body)
        if status == 200:
            return json.loads(body)
        time.sleep(0.2)
    raise AssertionError(f"healthz never 200; last={last!r}")


def wait_unreachable(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_request("GET", base_url + "/healthz", timeout=0.5)
        except OSError:
            return
        time.sleep(0.2)
    raise AssertionError(f"{base_url} is still reachable after stop")


def stop_background(cli: GlobalCli, project: Path, cwd: Path) -> None:
    proc = cli.run("serve", "--repo", str(project), "--stop", cwd=cwd, timeout=30)
    if proc.returncode != 0:
        pidfile = project / ".tracks" / "service" / "serve.pid"
        if pidfile.is_file():
            try:
                pid = int(json.loads(pidfile.read_text(encoding="utf-8"))["pid"])
                os.kill(pid, 15)
            except (OSError, ValueError, KeyError):
                pass
