"""Reusable test-only harness code for the installed-wheel live channel."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from tests._support.runtime_resources import RUNTIME_RESOURCE_PATHS
from tracks.effects.opencode import AGENT_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CURRENT_PYTHON = Path(sys.executable).resolve()
CURRENT_VENV_BIN = Path(sys.prefix).resolve() / "bin"
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
DEFAULT_LIVE_ROOT = Path("/tmp/tracks/live-e2e")


def live_root() -> Path:
    """Return the fixed root used for live hosts, baselines, and artifacts."""
    configured = os.environ.get("TRAC_LIVE_ROOT", "").strip()
    return Path(configured) if configured else DEFAULT_LIVE_ROOT


LIVE_ENV = (
    "TRAC_LIVE_PROVIDER",
    "TRAC_LIVE_MODEL",
    "TRAC_LIVE_BASE_URL",
    "TRAC_LIVE_API_KEY",
)
LIVE_SKIPPED_PREFIX = "LIVE_SKIPPED: missing "
AGENT_NAMES = tuple(sorted(AGENT_NAME.values()))
REQUIRED_SCENARIOS = {
    "triage",
    "scribe-story-draft",
    "sage-story-finding",
    "scribe-story-respond",
    "sage-story-resolve",
    "sage-spec-draft",
    "lex-spec-review",
    "sage-spec-respond",
    "lex-spec-resolve",
    "sage-acceptance-draft",
    "lex-acceptance-review",
    "approval-final",
    "archer-design-draft",
    "prism-design-review",
    "archer-design-respond",
    "shield-test-draft",
    "shield-test-respond",
    "prism-test-review",
}


@dataclass(frozen=True)
class Scenario:
    path: Path
    data: dict
    console_input: str

    @property
    def start_requirement(self) -> str:
        return str(self.data.get("start_requirement", ""))


@dataclass(frozen=True)
class LiveInstall:
    artifact_dir: Path
    isolated_venv: Path
    isolated_python: Path
    isolated_bin: Path
    tools_bin: Path
    opencode: Path
    wheel: Path
    install_log: Path
    resources: dict[str, bytes]
    probe: dict


# tracks/kernel/machine.py `_consume_attempt`: a failed outcome consumes one
# attempt and only at the 3rd failure escalates to awaiting=escalation. The
# harness must mirror that budget instead of failing on the first flake.
ESCALATION_FAILURES = 3


@dataclass
class _Bounds:
    stage_counts: dict[str, int] = field(default_factory=dict)
    review_counts: dict[tuple[str, int], int] = field(default_factory=dict)
    review_outcomes: dict[tuple[str, str], int] = field(default_factory=dict)
    dispatch_by_command: dict = field(default_factory=dict)
    failed_outcomes: dict[tuple[str, str], int] = field(default_factory=dict)


def require_current_virtualenv() -> None:
    if not CURRENT_PYTHON.is_file():
        raise AssertionError(f"current Python is not executable: {CURRENT_PYTHON}")
    if sys.prefix == sys.base_prefix:
        raise AssertionError(f"live E2E must run inside a virtualenv: prefix={sys.prefix!r}")


def clean_env(source: dict[str, str] | None = None) -> dict[str, str]:
    """Remove import hooks that could make an isolated command load workspace code."""
    env = dict(os.environ if source is None else source)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    return env


def filtered_system_path(source: str | None = None) -> str:
    """Remove the current venv bin while retaining the host's system tools."""
    raw = os.environ.get("PATH", "") if source is None else source
    current_bin = CURRENT_VENV_BIN.resolve()
    kept = []
    for part in raw.split(os.pathsep):
        if not part:
            continue
        if Path(part).expanduser().resolve() == current_bin:
            continue
        kept.append(part)
    return os.pathsep.join(kept)


def live_path(isolated_bin: Path, tools_bin: Path, source: str | None = None) -> str:
    parts = [str(isolated_bin), str(tools_bin), filtered_system_path(source)]
    return os.pathsep.join(part for part in parts if part)


def installed_trac_command(isolated_bin: Path, *args: str) -> list[str]:
    return [str(isolated_bin / "trac"), *args]


def live_env(install: LiveInstall, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = clean_env()
    env["PATH"] = live_path(install.isolated_bin, install.tools_bin)
    env["TRAC_AGENT_BACKEND"] = "opencode"
    env.pop("TRAC_FAKE_SIMULATE", None)
    if extra:
        env.update(extra)
    backend = env["TRAC_AGENT_BACKEND"].strip().lower()
    if backend == "fake":
        env.pop("TRAC_AGENT_MODEL", None)
    elif backend == "opencode":
        model = env.get("TRAC_LIVE_MODEL", "").strip()
        if model:
            if "/" not in model:
                provider = env.get("TRAC_LIVE_PROVIDER", "").strip()
                if provider:
                    model = f"{provider}/{model}"
            env["TRAC_AGENT_MODEL"] = model
    return env


def _live_provenance(install: LiveInstall) -> dict[str, str]:
    """Return launch facts required by the IF-LIVE-001 evidence contract."""
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "TRAC_LIVE_CANDIDATE_SHA": candidate_sha,
        "TRAC_LIVE_ARTIFACT_SHA256": hashlib.sha256(install.wheel.read_bytes()).hexdigest(),
    }


def timeout_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value


def resolve_github_repo(monkeypatch, live_root: Path) -> str:
    """Validate TRACKS_E2E_GITHUB_REPO + auth, set the env vars the issue
    backend needs, and return the ``owner/name`` slug. Shared by the
    ``live_github_repo`` fixture (conftest) and the resume test's deferred
    GitHub setup so the R0801 duplicate-code gate stays green."""
    value = os.environ.get("TRACKS_E2E_GITHUB_REPO", "").strip()
    if not value:
        raise AssertionError(f"full live journey requires TRACKS_E2E_GITHUB_REPO; host={live_root}")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        try:
            auth = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, timeout=30
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            auth = None
        token = auth.stdout.strip() if auth is not None and auth.returncode == 0 else ""
    if not token:
        raise AssertionError(
            "full live journey requires GitHub auth via GITHUB_TOKEN or `gh auth token`; "
            f"host={live_root}"
        )
    slug = value.removesuffix(".git").rstrip("/")
    if "github.com" in slug:
        slug = slug.split("github.com", maxsplit=1)[1].lstrip("/:")
    if not re.fullmatch(r"[^/]+/[^/]+", slug):
        raise AssertionError(f"TRACKS_E2E_GITHUB_REPO must be owner/name, got {value!r}")
    monkeypatch.setenv("TRAC_GITHUB_REPO", slug)
    monkeypatch.setenv("GITHUB_TOKEN", token)
    if (
        os.environ.get("SSL_CERT_FILE") is None
        and ssl.get_default_verify_paths().cafile is None
        and Path("/etc/ssl/cert.pem").exists()
    ):
        monkeypatch.setenv("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    return slug


def append_log(log_path: Path, text: str) -> None:
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(text)


def _kill_descendants(pid: int) -> None:
    """Recursively SIGKILL all descendant processes of ``pid`` (best-effort).

    ``pgrep -P`` finds children by parent PID even when they have started
    their own process group via ``setsid`` (as ``opencode`` does), so this
    catches orphans that a plain ``killpg`` would miss. Best-effort: if
    ``pgrep`` is unavailable the caller's ``killpg`` remains the safety net.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            start_new_session=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    for token in result.stdout.split():
        if not token.strip().isdigit():
            continue
        child_pid = int(token.strip())
        _kill_descendants(child_pid)
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(child_pid, signal.SIGKILL)


def _kill_process_tree(pid: int) -> None:
    """Kill a process, its process group, and all descendants (best-effort).

    ``start_new_session=True`` puts the direct child in its own process group,
    but a grandchild that calls ``setsid`` (e.g. ``opencode``) escapes
    ``killpg``. Walk the tree with ``pgrep`` to catch those orphans first,
    then kill the whole group (live run049: ``opencode`` kept running for
    minutes after the harness killed ``trac``)."""
    _kill_descendants(pid)
    with suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pid, signal.SIGKILL)


def run_logged(
    command: list[str], cwd: Path, env: dict[str, str], log_path: Path, timeout: int = 300
) -> subprocess.CompletedProcess:
    append_log(log_path, f"\n$ {shlex.join(command)}\n")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process.pid)
        process.communicate()
        append_log(log_path, f"timeout after {timeout}s: {exc}\n")
        raise RuntimeError(f"command timed out after {timeout}s: {shlex.join(command)}") from exc
    append_log(log_path, stdout or "")
    append_log(log_path, stderr or "")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def select_wheel(wheelhouse: Path) -> Path:
    wheels = sorted(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {wheelhouse}, found {wheels}")
    return wheels[0]


def wheel_resources(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        forbidden = {
            name
            for name in names
            if set(PurePosixPath(name).parts) & {"tests", "e2e_live", "scenarios"}
        }
        if forbidden:
            raise RuntimeError(f"test-only files leaked into wheel: {sorted(forbidden)}")
        missing = {f"tracks/{path}" for path in RUNTIME_RESOURCE_PATHS} - names
        if missing:
            raise RuntimeError(f"runtime resources missing from wheel: {sorted(missing)}")
        return {path: archive.read(f"tracks/{path}") for path in RUNTIME_RESOURCE_PATHS}


def find_external_opencode() -> Path:
    """Find opencode without accepting a copy from the current Python venv."""
    candidate = shutil.which("opencode")
    current_bin = CURRENT_VENV_BIN.resolve()
    if candidate and Path(candidate).resolve().parent != current_bin:
        return Path(candidate).resolve()
    candidate = shutil.which("opencode", path=filtered_system_path())
    if candidate is None:
        raise RuntimeError("external opencode executable not found on filtered PATH")
    resolved = Path(candidate).resolve()
    if resolved.parent == current_bin:
        raise RuntimeError(f"opencode resolves to current venv: {resolved}")
    return resolved


def install_opencode_shim(artifact_dir: Path, source: Path) -> tuple[Path, Path]:
    tools_bin = artifact_dir / "tools" / "bin"
    tools_bin.mkdir(parents=True, exist_ok=True)
    shim = tools_bin / "opencode"
    try:
        shim.symlink_to(source)
    except OSError:
        shutil.copy2(source, shim)
        shim.chmod(shim.stat().st_mode | 0o111)
    return tools_bin, shim


_PROBE_SCRIPT = f"""
import json
import os
import sys
from importlib.resources import files
from pathlib import Path

import tracks
from tracks.effects.opencode import OpencodeBackend

resource_paths = {RUNTIME_RESOURCE_PATHS!r}
workspace = Path(os.environ["WORKSPACE_ROOT"]).resolve()
venv = Path(sys.prefix).resolve()
tracks_file = Path(tracks.__file__).resolve()
assert tracks_file.is_relative_to(venv), tracks_file
assert not tracks_file.is_relative_to(workspace), tracks_file
package = files("tracks")
for resource_path in resource_paths:
    resource = package.joinpath(*resource_path.split("/"))
    assert resource.is_file(), resource_path
    assert resource.read_bytes(), resource_path

probe_host = Path(os.environ["PROBE_HOST"])
probe_host.mkdir(parents=True, exist_ok=True)
backend = OpencodeBackend(probe_host, "installed-resource-probe")
materialized = []
for agent_name in {AGENT_NAMES!r}:
    info = backend._materialize(agent_name)
    destination = Path(info["dest"])
    expected = package.joinpath("agents", agent_name + ".md").read_bytes()
    assert destination.read_bytes() == expected, agent_name
    materialized.append(str(destination))
    backend._cleanup(info)
    assert not destination.exists(), destination

print(json.dumps({{
    "tracks_file": str(tracks_file),
    "venv": str(venv),
    "resources": sorted(resource_paths),
    "materialized": materialized,
}}))
"""


_BACKEND_SCRIPT = """
import json
import os
from importlib.resources import files
from pathlib import Path

from tracks.effects.opencode import AGENT_NAME, OpencodeBackend

request = json.loads(os.environ["TRAC_LIVE_BACKEND_REQUEST"])
repo = Path(request["repo"])
backend = OpencodeBackend(
    repo,
    request["version"],
    model=request["model"],
)
agent_name = AGENT_NAME[request["role"]]
expected = files("tracks").joinpath("agents", agent_name + ".md").read_bytes()
evidence = {"agent": agent_name, "materialized": False, "cleaned": False}
original_materialize = backend._materialize
original_cleanup = backend._cleanup

def checked_materialize(name):
    info = original_materialize(name)
    assert Path(info["dest"]).read_bytes() == expected
    evidence["materialized"] = True
    return info

def checked_cleanup(info):
    original_cleanup(info)
    destination = Path(info["dest"])
    evidence["cleaned"] = not destination.exists()
    assert evidence["cleaned"] or destination.read_bytes() == expected

backend._materialize = checked_materialize
backend._cleanup = checked_cleanup
doc_path = Path(request["doc_path"]) if request.get("doc_path") else None
result = backend.act(
    request["role"],
    request["substate"],
    request.get("doc"),
    doc_path,
    assignment=request.get("assignment"),
)
result["materialization_evidence"] = evidence
print(json.dumps(result, ensure_ascii=False))
"""


def run_bounded(
    command: list[str], cwd: Path, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process.pid)
        stdout, stderr = process.communicate()
        raise RuntimeError(
            f"command timed out after {timeout}s: {shlex.join(command)}\n"
            f"stdout={stdout or exc.stdout or ''}\nstderr={stderr or exc.stderr or ''}"
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def assert_host_agents(install: LiveInstall, host: Path) -> None:
    for agent_name in AGENT_NAMES:
        destination = host / ".opencode" / "agents" / f"{agent_name}.md"
        if destination.exists():
            expected = install.resources[f"agents/{agent_name}.md"]
            assert destination.read_bytes() == expected, (
                f"materialized {destination} differs from installed resource"
            )


def load_scenarios() -> dict[str, Scenario]:
    scenario_dir = SCENARIO_DIR.resolve()
    scenarios = {}
    for path in sorted(scenario_dir.glob("*.json")):
        if not path.resolve().is_relative_to(scenario_dir):
            raise AssertionError(f"scenario escaped fixture directory: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("scenario_id"):
            raise AssertionError(f"invalid scenario context: {path}")
        console_file = data.get("console_file")
        console_input = ""
        if console_file:
            console_path = (scenario_dir / str(console_file)).resolve()
            if not console_path.is_relative_to(scenario_dir):
                raise AssertionError(f"console transcript escaped fixture directory: {path}")
            console_input = console_path.read_text(encoding="utf-8")
            if not console_input.startswith("tracks-live-console/v1\n"):
                raise AssertionError(f"console transcript is not finite protocol input: {path}")
        scenarios[path.stem] = Scenario(path, data, console_input)
    missing = REQUIRED_SCENARIOS - scenarios.keys()
    if missing:
        raise AssertionError(f"required live scenarios missing: {sorted(missing)}")
    return scenarios


def prepare_live_install(live_root: Path) -> LiveInstall:
    """Build/install/probe a wheel and retain every artifact for diagnostics."""
    require_current_virtualenv()
    artifact_dir = live_root.parent / f"{live_root.name}-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    wheelhouse = artifact_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    install_log = artifact_dir / "install.log"
    install_log.write_text(
        f"workspace={PROJECT_ROOT}\nhost={live_root}\ncurrent_python={CURRENT_PYTHON}\n",
        encoding="utf-8",
    )
    isolated_venv = artifact_dir / "isolated-venv"
    isolated_python = isolated_venv / "bin" / "python"
    isolated_bin = isolated_venv / "bin"
    install_timeout = timeout_env("TRAC_LIVE_INSTALL_TIMEOUT", 300)

    def failed(message: str):
        print(f"LIVE_E2E_HOST={live_root}", flush=True)
        print(f"LIVE_E2E_INSTALL_LOG={install_log}", flush=True)
        raise AssertionError(
            f"{message}; host={live_root}; install_log={install_log}; isolated_venv={isolated_venv}"
        )

    try:
        opencode_source = find_external_opencode()
        tools_bin, opencode = install_opencode_shim(artifact_dir, opencode_source)
        append_log(install_log, f"external_opencode={opencode_source}\nshim={opencode}\n")
        clean = clean_env()
        build = run_logged(
            [
                str(CURRENT_PYTHON),
                "-m",
                "pip",
                "wheel",
                str(PROJECT_ROOT),
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheelhouse),
            ],
            PROJECT_ROOT,
            clean,
            install_log,
            timeout=install_timeout,
        )
        if build.returncode != 0:
            failed(f"wheel build failed: {build.stderr.strip()}")
        wheel = select_wheel(wheelhouse)
        resources = wheel_resources(wheel)
        create = run_logged(
            [str(CURRENT_PYTHON), "-m", "venv", str(isolated_venv)],
            artifact_dir,
            clean,
            install_log,
            timeout=install_timeout,
        )
        if create.returncode != 0:
            failed(f"isolated venv creation failed: {create.stderr.strip()}")
        install = run_logged(
            [str(isolated_python), "-m", "pip", "install", "--no-deps", str(wheel)],
            artifact_dir,
            clean,
            install_log,
            timeout=install_timeout,
        )
        if install.returncode != 0:
            failed(f"wheel installation failed: {install.stderr.strip()}")
        if not (isolated_bin / "trac").is_file():
            failed(f"installed trac executable missing: {isolated_bin / 'trac'}")
        live_path_value = live_path(isolated_bin, tools_bin)
        resolved_trac = shutil.which("trac", path=live_path_value)
        resolved_opencode = shutil.which("opencode", path=live_path_value)
        assert resolved_trac and Path(resolved_trac).resolve() == isolated_bin.resolve() / "trac"
        assert resolved_opencode and Path(resolved_opencode).parent == tools_bin
        probe_host = artifact_dir / "probe-host"
        probe_env = clean_env()
        probe_env.update({"PROBE_HOST": str(probe_host), "WORKSPACE_ROOT": str(PROJECT_ROOT)})
        probe = run_logged(
            [str(isolated_python), "-c", _PROBE_SCRIPT],
            artifact_dir,
            probe_env,
            install_log,
            timeout=install_timeout,
        )
        if probe.returncode != 0:
            failed(f"installed wheel probe failed: {probe.stderr.strip()}")
        probe_data = json.loads(probe.stdout)
        tracks_file = Path(probe_data["tracks_file"]).resolve()
        assert tracks_file.is_relative_to(isolated_venv.resolve() / "lib")
        assert not tracks_file.is_relative_to(PROJECT_ROOT.resolve())
        assert probe_data["resources"] == sorted(RUNTIME_RESOURCE_PATHS)
    except (OSError, RuntimeError, AssertionError, json.JSONDecodeError) as exc:
        failed(f"wheel/install/probe setup failed: {exc}")

    info = LiveInstall(
        artifact_dir=artifact_dir,
        isolated_venv=isolated_venv,
        isolated_python=isolated_python,
        isolated_bin=isolated_bin,
        tools_bin=tools_bin,
        opencode=opencode,
        wheel=wheel,
        install_log=install_log,
        resources=resources,
        probe=probe_data,
    )
    print(f"LIVE_E2E_ISOLATED_VENV={isolated_venv}", flush=True)
    print(f"LIVE_E2E_WHEEL={wheel}", flush=True)
    print(f"LIVE_E2E_INSTALL_LOG={install_log}", flush=True)
    wheel_command = shlex.join(
        [
            str(CURRENT_PYTHON),
            "-m",
            "pip",
            "wheel",
            str(PROJECT_ROOT),
            "--no-deps",
            "--no-build-isolation",
        ]
    )
    print(f"LIVE_E2E_WHEEL_COMMAND={wheel_command}", flush=True)
    return info


class InstalledBackend:
    """Test-only subprocess adapter for direct live-agent plumbing tests."""

    def __init__(self, host: Path, install: LiveInstall, model: str, timeout: int):
        self.host = host
        self.install = install
        self.model = model
        self.timeout = timeout

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
    ) -> dict:
        assert_host_agents(self.install, self.host)
        env = live_env(
            self.install,
            {
                "TRAC_AGENT_TIMEOUT": str(self.timeout),
                "TRAC_LIVE_BACKEND_REQUEST": json.dumps(
                    {
                        "repo": str(self.host),
                        "version": "v0.2",
                        "model": self.model,
                        "role": role,
                        "substate": substate,
                        "doc": doc,
                        "doc_path": str(doc_path) if doc_path else None,
                        "assignment": assignment,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        command = [str(self.install.isolated_python), "-c", _BACKEND_SCRIPT]
        process = run_bounded(command, self.host, env, self.timeout + 60)
        if process.returncode != 0:
            raise AssertionError(
                f"installed backend failed: {process.stderr.strip()}\n"
                f"host={self.host}; install_log={self.install.install_log}"
            )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"installed backend returned invalid JSON: {process.stdout!r}; "
                f"host={self.host}; install_log={self.install.install_log}"
            ) from exc
        assert_host_agents(self.install, self.host)
        return result


_OPENCODE_LOG_PATH = Path.home() / ".local" / "share" / "opencode" / "log" / "opencode.log"
_OPENCODE_LOG_TAIL_BYTES = 20 * 1024 * 1024  # 20MB - read the tail, not the whole file
_OPENCODE_LOG_TAIL_LINES = 80


def capture_opencode_log_tail(
    host_path: Path,
    report_dir: Path,
    log_path: Path | None = None,
    tail_bytes: int = _OPENCODE_LOG_TAIL_BYTES,
    tail_lines: int = _OPENCODE_LOG_TAIL_LINES,
) -> Path | None:
    """Best-effort: extract the last ~80 opencode log lines whose
    ``directory=`` matches ``host_path`` and write them to
    ``<report_dir>/opencode-session-tail.log``.

    The opencode CLI logs to ``~/.local/share/opencode/log/opencode.log``
    (NDJSON-ish lines with ``directory=<host path>``). The events DB only
    shows outcomes after the fact; operators need log-level visibility when
    a dispatch hangs or dies silently. Best-effort only - never raises.

    Returns the output path, or ``None`` if no matching lines or any error.
    """
    try:
        src = log_path or _OPENCODE_LOG_PATH
        if not src.is_file():
            return None
        host_resolved = str(Path(host_path).resolve())
        host_str = str(host_path)
        with src.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - tail_bytes))
            chunk = stream.read().decode("utf-8", errors="replace")
        matching = [
            line
            for line in chunk.splitlines()
            if f"directory={host_resolved}" in line
            or f"directory={host_str}" in line
            or f'"directory":"{host_resolved}"' in line
            or f'"directory":"{host_str}"' in line
        ]
        if not matching:
            return None
        out_dir = Path(report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "opencode-session-tail.log"
        out.write_text("\n".join(matching[-tail_lines:]) + "\n", encoding="utf-8")
        return out
    except (OSError, PermissionError, ValueError):
        return None


def prepare_host_venv(host: Path, install: LiveInstall) -> Path:
    """Create host ``.venv`` for M-TEST (§3.2) by symlinking to the current
    virtualenv, mirroring ``tests/conftest.py``'s ``host_repo`` fixture.
    No pip/network: the current venv already has pytest 9.1.1."""
    host_venv = host / ".venv"
    if host_venv.is_symlink() or host_venv.exists():
        return host_venv
    target = Path(sys.prefix).resolve()
    try:
        import pytest  # noqa: F401
    except ImportError as exc:
        raise AssertionError(f"no importable pytest in venv {sys.prefix!r}: {exc}") from exc
    host_venv.symlink_to(target, target_is_directory=True)
    append_log(install.install_log, f"host_venv=symlink:{target}\n")
    return host_venv


class LiveTracDriver:
    """Bounded installed-CLI driver used by the full-journey fixture."""

    def __init__(
        self,
        live_root: Path,
        install: LiveInstall,
        scenarios: dict[str, Scenario],
        agent_timeout: int,
        command_timeout: int,
        total_timeout: int,
        max_commands: int,
        max_stage_dispatches: int,
        max_review_dispatches: int,
        max_review_rounds: int,
    ):
        self.live_root = live_root
        self.install = install
        self.scenarios = scenarios
        self.agent_timeout = agent_timeout
        self.command_timeout = command_timeout
        self.deadline = time.monotonic() + total_timeout
        self.max_commands = max_commands
        self.max_stage_dispatches = max_stage_dispatches
        self.max_review_dispatches = max_review_dispatches
        self.max_review_rounds = max_review_rounds
        self.command_count = 0
        self.run_id: str | None = None

    # Bounded retry for transient SQLite read errors during ``events()``.
    # ``sqlite3.OperationalError("database is locked")`` and the occasional
    # ``sqlite3.DatabaseError("database disk image is malformed")`` have been
    # observed in the live harness when the Runtime writer is shutting down
    # and the WAL is mid-checkpoint. These are transient: the writer's
    # shutdown completes within a few hundred ms and the read succeeds on
    # retry. The retry is bounded (5 x 100ms) so a *persistent* corruption
    # (e.g. a real on-disk page tear) still fails loudly, and on final
    # failure we run ``PRAGMA integrity_check`` to surface whether the
    # corruption is genuine (it returns a non-"ok" string) or just a flake.
    EVENT_READ_RETRIES = 5
    EVENT_READ_RETRY_DELAY = 0.100

    def events(self) -> list[dict]:
        database = self.live_root / ".tracks" / "runtime" / "tracks.db"
        if not database.is_file():
            return []
        last_error: Exception | None = None
        for attempt in range(self.EVENT_READ_RETRIES):
            try:
                with sqlite3.connect(database, timeout=1.0) as connection:
                    rows = connection.execute(
                        "SELECT seq, type, command_id, payload FROM events ORDER BY seq"
                    ).fetchall()
                return [
                    {
                        "seq": seq,
                        "type": kind,
                        "command_id": command_id,
                        "payload": json.loads(payload),
                    }
                    for seq, kind, command_id, payload in rows
                ]
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
                last_error = exc
                if attempt + 1 < self.EVENT_READ_RETRIES:
                    time.sleep(self.EVENT_READ_RETRY_DELAY)
        # Final failure: run integrity_check if possible and include its
        # result + the original error so operators can tell a flake (locked)
        # from a genuine page tear (corrupt).
        integrity = "unavailable"
        with suppress(sqlite3.Error), sqlite3.connect(database, timeout=1.0) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        raise AssertionError(
            f"LiveTracDriver.events() failed after {self.EVENT_READ_RETRIES} retries: "
            f"{last_error!r}; integrity_check={integrity!r}; db={database}"
        ) from last_error

    def preserve_report(self) -> Path | None:
        if not self.run_id:
            return None
        report_dir = self.live_root / "report"
        env = live_env(self.install, {"TRAC_AGENT_TIMEOUT": str(self.agent_timeout)})
        env.pop("TRAC_AGENT_CONSOLE_INPUT", None)
        report_cmd = installed_trac_command(
            self.install.isolated_bin,
            "report",
            "--run-id",
            self.run_id,
            "--output",
            str(report_dir),
        )
        with suppress(OSError, RuntimeError):
            run_bounded(report_cmd, self.live_root, env, min(60, self.command_timeout))
        return report_dir.resolve()

    def metadata(self) -> tuple[str, str]:
        remote = (
            subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=self.live_root,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "-"
        )
        branch = (
            subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=self.live_root,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            or "-"
        )
        return remote, branch

    def fail(self, message: str):
        report_dir = self.preserve_report()
        log_tail = capture_opencode_log_tail(
            self.live_root, report_dir or (self.live_root / "report")
        )
        report_value = report_dir if report_dir and report_dir.exists() else "not-generated"
        remote, branch = self.metadata()
        print(f"LIVE_E2E_HOST={self.live_root.resolve()}", flush=True)
        print(f"LIVE_E2E_INSTALL_LOG={self.install.install_log}", flush=True)
        print(f"LIVE_E2E_WHEEL={self.install.wheel}", flush=True)
        print(f"LIVE_E2E_ISOLATED_VENV={self.install.isolated_venv}", flush=True)
        raise AssertionError(
            f"{message}; host={self.live_root.resolve()}; report={report_value}; "
            f"install_log={self.install.install_log}; remote={remote}; branch={branch}"
            + (f"; opencode_session_tail={log_tail}" if log_tail else "")
        )

    def _observe_dispatch(self, event: dict, bounds: _Bounds) -> None:
        command = event["payload"].get("command", {})
        if command.get("kind") != "dispatch_agent":
            return
        params = command.get("params", {})
        stage = str(params.get("stage", "unknown"))
        bounds.stage_counts[stage] = bounds.stage_counts.get(stage, 0) + 1
        bounds.dispatch_by_command[event["command_id"]] = params
        if not str(params.get("substate") or "").endswith("_REVIEW"):
            return
        round_id = int(params.get("review_round", 0))
        key = (stage, round_id)
        bounds.review_counts[key] = bounds.review_counts.get(key, 0) + 1

    def _observe_failed_outcome(self, event: dict, bounds: _Bounds) -> None:
        # The runtime retries a failed dispatch outcome: each failure consumes
        # one attempt, carries failure evidence into the re-dispatch prompt, and
        # only at the 3rd failure escalates to awaiting=escalation (the journey
        # is genuinely dead). Count per (stage, substate) and fail only there.
        params = bounds.dispatch_by_command.get(event["command_id"], {})
        key = (str(params.get("stage", "unknown")), str(params.get("substate") or "unknown"))
        bounds.failed_outcomes[key] = bounds.failed_outcomes.get(key, 0) + 1
        if bounds.failed_outcomes[key] >= ESCALATION_FAILURES:
            self.fail(
                f"live provider/backend outcome failed {bounds.failed_outcomes[key]} times "
                f"for stage={key[0]} substate={key[1]}; the runtime escalates to "
                f"awaiting=escalation at the 3rd failure: {event['payload']}"
            )

    def _observe_event(self, event: dict, bounds: _Bounds) -> None:
        if event["type"] == "run.interrupted":
            self.fail(f"Runtime reported interrupted activity: {event['payload']}")
        if event["type"] == "outcome.received" and event["payload"].get("status") == "failed":
            self._observe_failed_outcome(event, bounds)
        if event["type"] == "command.issued":
            self._observe_dispatch(event, bounds)
        if not event["type"].endswith(".verdict"):
            return
        params = bounds.dispatch_by_command.get(event["command_id"], {})
        key = (str(params.get("stage", "unknown")), event["type"])
        bounds.review_outcomes[key] = bounds.review_outcomes.get(key, 0) + 1

    def _check_counts(self, bounds: _Bounds) -> None:
        if any(value > self.max_stage_dispatches for value in bounds.stage_counts.values()):
            self.fail(f"stage dispatch bound exceeded: {bounds.stage_counts}")
        if any(value > self.max_review_dispatches for value in bounds.review_counts.values()):
            self.fail(f"review dispatch bound exceeded: {bounds.review_counts}")
        if any(value > self.max_review_rounds for value in bounds.review_outcomes.values()):
            self.fail(f"review round bound exceeded: {bounds.review_outcomes}")

    def check_bounds(self) -> None:
        bounds = _Bounds()
        for event in self.events():
            self._observe_event(event, bounds)
        self._check_counts(bounds)

    def finalize(self) -> None:
        report_dir = self.preserve_report()
        remote, branch = self.metadata()
        print(f"LIVE_E2E_HOST={self.live_root.resolve()}", flush=True)
        print(f"LIVE_E2E_ISOLATED_VENV={self.install.isolated_venv}", flush=True)
        print(f"LIVE_E2E_WHEEL={self.install.wheel}", flush=True)
        print(f"LIVE_E2E_INSTALL_LOG={self.install.install_log}", flush=True)
        print(f"LIVE_E2E_REPORT_DIR={report_dir if report_dir else 'not-generated'}", flush=True)
        print(f"LIVE_E2E_REMOTE={remote}", flush=True)
        print(f"LIVE_E2E_BRANCH={branch}", flush=True)

    @staticmethod
    def _dispatch_budget(scenario: Scenario) -> int:
        # Author/WRITE/reviewer steps may flake; the runtime retries a failed
        # dispatch internally and escalates at the 3rd attempt, so they get a
        # 3-dispatch budget. Successful paths consume only one. Human-gate steps
        # (no kind) keep one dispatch; the global bounds remain the outer net.
        # S4: TRAC_LIVE_DEV_BUDGET=1 is a dev-only opt-in that reduces the retry
        # budget to 1 for fast failure exposure; prod (budget=3) is unchanged.
        kind = scenario.data.get("kind") or ""
        if kind in ("DRAFT", "RESPOND", "WRITE") or kind.endswith("_REVIEW"):
            return 1 if os.environ.get("TRAC_LIVE_DEV_BUDGET") else 3
        return 1

    def _scenario_budget(self, scenario: str | None) -> int:
        """The dispatch budget for the given scenario (mirrors --max-dispatches)."""
        selected = self.scenarios.get(scenario or "triage") if self.scenarios else None
        return self._dispatch_budget(selected) if selected is not None else 1

    def _effective_command_timeout(
        self,
        *,
        timeout: int | None,
        agent_timeout: int | None,
        max_dispatches: int | None,
        scenario: str | None,
    ) -> int:
        """Compute the outer command timeout for a ``trac run`` invocation.

        When a per-call ``agent_timeout`` overrides the default, the outer
        command timeout must cover ALL dispatch attempts a single ``trac run``
        may consume (``--max-dispatches`` from the scenario budget; each attempt
        takes up to ``agent_timeout + pre/post overhead``), unless the caller
        passed an explicit ``timeout``. Without this, live run049 attempt2 was
        killed mid-flight at 1500s while ~90% through a 3-attempt run because
        the old formula only budgeted for one attempt (``agent_timeout + 300``).
        """
        effective = timeout or self.command_timeout
        if agent_timeout and not timeout:
            budget = (
                max_dispatches if max_dispatches is not None else self._scenario_budget(scenario)
            )
            effective = max(effective, budget * (agent_timeout + 300))
        return effective

    def _command_args(
        self,
        args: tuple[str, ...],
        scenario: str | None,
        console_input: str | None,
        max_dispatches: int | None = None,
        no_overlay: bool = False,
    ) -> tuple[list[str], str]:
        command_args = list(args)
        if not command_args or command_args[0] != "run":
            return command_args, console_input or ""
        selected = self.scenarios.get(scenario or "triage")
        if selected is None:
            self.fail(f"unknown live scenario {scenario!r}")
        budget = (
            max_dispatches
            if max_dispatches is not None
            else self._dispatch_budget(selected)
        )
        if no_overlay:
            command_args.extend(["--max-dispatches", str(budget)])
            return command_args, ""
        command_args.extend(
            [
                "--assignment-overlay",
                str(selected.path),
                "--max-dispatches",
                str(budget),
            ]
        )
        return command_args, selected.console_input if console_input is None else console_input

    def _communicate(self, process, command_args: list[str], stdin, timeout: float):
        try:
            return process.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(process.pid)
            stdout, stderr = process.communicate()
            self.fail(
                f"trac {' '.join(command_args)} timed out; "
                f"stdout={stdout or exc.stdout or ''}; stderr={stderr or exc.stderr or ''}"
            )

    def run(
        self,
        *args,
        stdin=None,
        console_input=None,
        scenario=None,
        timeout=None,
        agent_timeout=None,
        max_dispatches=None,
        backend: str = "opencode",
        no_overlay: bool = False,
    ):
        self.command_count += 1
        if self.command_count > self.max_commands:
            self.fail(f"live command bound exceeded ({self.max_commands})")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.fail("live journey total timeout exceeded")
        command_args, selected_console = self._command_args(
            args, scenario, console_input, max_dispatches, no_overlay=no_overlay
        )
        effective_agent_timeout = agent_timeout or self.agent_timeout
        env = live_env(
            self.install,
            {
                "TRAC_AGENT_BACKEND": backend,
                "TRAC_AGENT_TIMEOUT": str(effective_agent_timeout),
            },
        )
        if no_overlay and backend.strip().lower() == "opencode":
            env.update(_live_provenance(self.install))
        if backend.strip().lower() == "fake":
            env.pop("TRAC_AGENT_CONSOLE_INPUT", None)
        else:
            env["TRAC_AGENT_CONSOLE_INPUT"] = selected_console
        assert_host_agents(self.install, self.live_root)
        command = installed_trac_command(self.install.isolated_bin, *command_args)
        process = subprocess.Popen(
            command,
            cwd=self.live_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        effective_command_timeout = self._effective_command_timeout(
            timeout=timeout,
            agent_timeout=agent_timeout,
            max_dispatches=max_dispatches,
            scenario=scenario,
        )
        stdout, stderr = self._communicate(
            process,
            command_args,
            stdin,
            min(effective_command_timeout, remaining),
        )
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if result.returncode != 0:
            self.fail(f"trac {' '.join(command_args)} failed: {result.stderr.strip()}")
        if args and args[0] == "start":
            match = re.search(r"run (\S+) started", result.stdout)
            if match:
                self.run_id = match.group(1)
        assert_host_agents(self.install, self.live_root)
        self.check_bounds()
        return result
