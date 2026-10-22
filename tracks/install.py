"""Project installer for ``trac install`` (IF-INSTALL-001 / IF-INSTALLPKG-001).

The installer consumes an official wheel (or the running distribution),
validates the closed resource set, stages the new version, materializes the
managed files, writes the receipt and launcher, runs a post-install health
check and commits only the tracks-owned paths.  A failure before the commit
restores the previous managed trees and writes no receipt (interfaces §1c.6).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import Parser
from importlib import metadata
from pathlib import Path, PurePosixPath

import tracks
from tracks import project_runtime
from tracks.project_runtime import (
    ENV_GITIGNORE,
    LAUNCHER_REL,
    RECEIPT_REL,
    SITE_PACKAGES_TEMPLATE,
    scaffold_tracks_dirs,
    write_launcher,
    write_receipt,
)
from tracks.store import new_ulid

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_INIT_VERSION_RE = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE
)
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

_AGENT_MEMBER = re.compile(r"tracks/agents/[^/]+\.md")
_SKILL_MEMBER = re.compile(r"tracks/skills/[^/]+/SKILL\.md")
_OPENCODE_MEMBER = "tracks/assets/opencode.json"
_PACKAGE_INIT = "tracks/__init__.py"
_RUNNING_DIST_NAME = "agent-on-tracks"

INSTALL_USAGE = (
    "usage: trac install <path> [--version <X.Y.Z>] [--wheel <path>]"
)
CLI_INSTALL_USAGE = (
    "usage: trac init|install <path> [--version <v>] [--wheel <path>]"
)

# Installer-owned paths committed by install (interfaces §1b.4/§1.0.5).
# Only the files this install wrote are listed — never a scaffold directory
# wholesale (an existing project may carry user docs under .tracks/projects).
_COMMIT_PATHS = (
    RECEIPT_REL,
    LAUNCHER_REL,
    ".tracks/.gitignore",
    ".tracks/projects/.gitignore",
    ".tracks/projects/.gitkeep",
    ".tracks/runtime/.gitignore",
    ".tracks/wiki/.gitkeep",
    ".tracks/env/.gitignore",
    ".opencode/agents",
    ".opencode/skills",
    ".opencode/templates",
)


class InstallError(Exception):
    """Closed-reason install failure (interfaces §1c.5)."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class InstalledProject:
    version: str
    target: Path
    source: str


@dataclass(frozen=True)
class _Source:
    kind: str
    version: str
    requires: tuple[str, ...]
    wheel: Path | None
    package_dir: Path | None


@dataclass(frozen=True)
class _WheelInfo:
    path: Path
    version: str
    requires: tuple[str, ...]
    opencode_json: str
    init_version: str | None
    names: tuple[str, ...]


def _option_value(args: tuple[str, ...], index: int) -> str:
    if index + 1 >= len(args):
        raise InstallError("usage", f"{args[index]} requires a value")
    return args[index + 1]


def _parse_install_options(
    args: tuple[str, ...],
) -> tuple[str | None, str | None, str | None]:
    target: str | None = None
    version: str | None = None
    wheel: str | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in ("--version", "--wheel"):
            value = _option_value(args, index)
            if argument == "--version":
                version = value
            else:
                wheel = value
            index += 2
            continue
        if argument.startswith("-"):
            raise InstallError("usage", f"unknown option {argument}")
        if target is not None:
            raise InstallError("usage", "expected exactly one <path>")
        target = argument
        index += 1
    return target, version, wheel


def _as_repo_path(repo: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(repo) / path


def parse_install_args(
    repo: Path, args: tuple[str, ...]
) -> tuple[Path, str | None, Path | None]:
    target, version, wheel = _parse_install_options(args)
    if target is None:
        raise InstallError("usage", INSTALL_USAGE)
    target_path = _as_repo_path(repo, target)
    assert target_path is not None
    return target_path, version, _as_repo_path(repo, wheel)


def normalize_version(raw: str) -> str:
    value = raw.strip()
    if value[:1] in ("v", "V"):
        value = value[1:]
    if not _VERSION_RE.match(value):
        raise InstallError("version_invalid", raw)
    return value


def resolve_target(raw: Path) -> Path:
    """Validate the target path without writing to it (interfaces §1c.2)."""
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()
    if target.exists() and not target.is_dir():
        raise InstallError("target_not_directory", str(target))
    if not target.parent.is_dir():
        raise InstallError("target_parent_missing", str(target.parent))
    if target.exists() and any(target.iterdir()) and not (target / ".git").exists():
        raise InstallError(
            "not_a_git_repo", "target is not empty and is not a git repository"
        )
    return target


def install_project(
    target: Path, version: str | None = None, wheel: Path | None = None
) -> InstalledProject:
    """Install (or converge) one project to *version*."""
    display = Path(target).expanduser()
    if not display.is_absolute():
        display = Path.cwd() / display
    try:
        target = resolve_target(target)
        requested = normalize_version(version) if version else tracks.__version__
        source = _resolve_source(requested, wheel)
        _ensure_git(target)
    except InstallError:
        raise
    except OSError as exc:
        raise InstallError("install_failed", str(exc)) from None
    tracks_existed = (target / ".tracks").exists()
    staging = target / ".tracks" / f".install-staging-{new_ulid()}"
    try:
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError("install_failed", str(exc)) from None
    rollback = _Rollback(target, staging / "backup", tracks_existed)
    try:
        scaffold_tracks_dirs(target)
        staged_tracks = _stage_site(staging / "site-packages", source)
        seeded = _materialize(target, staged_tracks, requested, rollback)
        receipt = _receipt(target, requested, source.kind, seeded)
        rollback.save_file(target / RECEIPT_REL)
        write_receipt(target, receipt)
        _health_check(target)
    except InstallError:
        rollback.run()
        raise
    except OSError as exc:
        rollback.run()
        raise InstallError("install_failed", str(exc)) from None
    except Exception as exc:
        rollback.run()
        raise InstallError("install_failed", str(exc)) from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    try:
        try:
            _commit(target, requested, seeded)
        except OSError as exc:
            raise InstallError("git_commit_failed", str(exc)) from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return InstalledProject(version=requested, target=display, source=source.kind)


def _ensure_git(target: Path) -> None:
    if (target / ".git").exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise InstallError("install_failed", _tail(proc.stderr))


def _resolve_source(requested: str, wheel: Path | None) -> _Source:
    if wheel is not None:
        info = _inspect_wheel(wheel)
        if info.version != requested:
            raise InstallError(
                "version_mismatch",
                f"wheel version {info.version} != requested {requested}",
            )
        _validate_members(info.names)
        _validate_opencode(info.opencode_json)
        if info.init_version != info.version:
            raise InstallError(
                "version_mismatch",
                f"wheel package version {info.init_version or 'missing'}"
                f" != METADATA {info.version}",
            )
        _validate_dependencies(info.requires)
        return _Source(
            kind="wheel",
            version=info.version,
            requires=info.requires,
            wheel=info.path,
            package_dir=None,
        )
    if requested != tracks.__version__:
        raise InstallError(
            "version_unavailable",
            f"version {requested} is not running; download the official wheel"
            " and pass it with --wheel",
        )
    package_dir = _running_package_dir()
    _validate_running_resources(package_dir)
    requires = _running_requires()
    _validate_dependencies(requires)
    return _Source(
        kind="running-distribution",
        version=requested,
        requires=requires,
        wheel=None,
        package_dir=package_dir,
    )


def _inspect_wheel(wheel: Path) -> _WheelInfo:
    path = Path(wheel).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise InstallError(
            "version_unavailable",
            f"wheel not found: {path}; download the official release artifact",
        )
    try:
        with zipfile.ZipFile(path) as archive:
            names = tuple(archive.namelist())
            member = next(
                (name for name in names if name.endswith(".dist-info/METADATA")),
                None,
            )
            if member is None:
                raise InstallError("package_incomplete", "wheel has no METADATA")
            message = Parser().parsestr(
                archive.read(member).decode("utf-8", "replace")
            )
            version = (message.get("Version") or "").strip()
            requires = tuple(message.get_all("Requires-Dist") or ())
            opencode = _wheel_text(archive, _OPENCODE_MEMBER)
            init_text = _wheel_text(archive, _PACKAGE_INIT)
    except zipfile.BadZipFile:
        raise InstallError("package_incomplete", f"not a valid wheel: {path}") from None
    if not version:
        raise InstallError("version_mismatch", "wheel METADATA has no Version")
    return _WheelInfo(
        path=path,
        version=version,
        requires=requires,
        opencode_json=opencode or "",
        init_version=_version_from_init(init_text),
        names=names,
    )


def _wheel_text(archive: zipfile.ZipFile, member: str) -> str | None:
    if member not in archive.namelist():
        return None
    return archive.read(member).decode("utf-8", "replace")


def _version_from_init(text: str | None) -> str | None:
    if text is None:
        return None
    match = _INIT_VERSION_RE.search(text)
    return match.group(1) if match is not None else None


def _validate_members(names: tuple[str, ...]) -> None:
    available = set(names)
    missing = []
    if not any(_AGENT_MEMBER.fullmatch(name) for name in available):
        missing.append("agents")
    if not any(_SKILL_MEMBER.fullmatch(name) for name in available):
        missing.append("skills")
    if not any(
        name.startswith("tracks/templates/") and not name.endswith("/")
        for name in available
    ):
        missing.append("templates")
    if _OPENCODE_MEMBER not in available:
        missing.append("assets/opencode.json")
    if _PACKAGE_INIT not in available:
        missing.append("tracks/__init__.py")
    if missing:
        raise InstallError("package_incomplete", "missing " + ", ".join(missing))


def _validate_opencode(text: str) -> None:
    try:
        data = json.loads(text)
    except ValueError:
        raise InstallError(
            "package_incomplete", "assets/opencode.json is not valid JSON"
        ) from None
    if not isinstance(data, dict):
        raise InstallError(
            "package_incomplete", "assets/opencode.json is not an object"
        )


def _running_package_dir() -> Path:
    return Path(tracks.__file__).resolve().parent


def _validate_running_resources(package_dir: Path) -> None:
    missing = []
    agents = package_dir / "agents"
    skills = package_dir / "skills"
    templates = package_dir / "templates"
    if not agents.is_dir() or not any(agents.glob("*.md")):
        missing.append("agents")
    if not skills.is_dir() or not any(skills.glob("*/SKILL.md")):
        missing.append("skills")
    if not templates.is_dir() or not any(
        path.is_file() for path in templates.rglob("*")
    ):
        missing.append("templates")
    asset = package_dir / "assets" / "opencode.json"
    if not asset.is_file():
        missing.append("assets/opencode.json")
    if missing:
        raise InstallError("package_incomplete", "missing " + ", ".join(missing))
    _validate_opencode(asset.read_text(encoding="utf-8"))


def _running_requires() -> tuple[str, ...]:
    try:
        distribution = metadata.distribution(_RUNNING_DIST_NAME)
    except metadata.PackageNotFoundError:
        return ()
    return tuple(distribution.requires or ())


def _validate_dependencies(requires: tuple[str, ...]) -> None:
    missing = set()
    for requirement in requires:
        if "extra ==" in requirement:
            continue
        match = _REQUIREMENT_NAME.match(requirement)
        if match is None:
            continue
        name = match.group(1)
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.add(name)
    if missing:
        raise InstallError(
            "dependency_missing",
            "not installed in the target interpreter: " + ", ".join(sorted(missing)),
        )


def _stage_site(staging_site: Path, source: _Source) -> Path:
    staging_site.mkdir(parents=True, exist_ok=True)
    if source.kind == "wheel":
        assert source.wheel is not None
        _extract_wheel(source.wheel, staging_site)
    else:
        assert source.package_dir is not None
        _copy_running_distribution(source, staging_site)
    return staging_site / "tracks"


def _extract_wheel(wheel: Path, destination: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = PurePosixPath(info.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise InstallError(
                    "package_incomplete", f"unsafe wheel member {info.filename}"
                )
            if not (
                info.filename.startswith("tracks/")
                or ".dist-info/" in info.filename
            ):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)


def _copy_running_distribution(source: _Source, destination: Path) -> None:
    assert source.package_dir is not None
    shutil.copytree(
        source.package_dir,
        destination / "tracks",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    dist_info = destination / f"agent_on_tracks-{source.version}.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    lines = [
        "Metadata-Version: 2.1",
        "Name: agent-on-tracks",
        f"Version: {source.version}",
    ]
    lines.extend(f"Requires-Dist: {item}" for item in source.requires)
    (dist_info / "METADATA").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _materialize(
    target: Path,
    staged_tracks: Path,
    version: str,
    rollback: _Rollback,
) -> bool:
    opencode_dir = target / ".opencode"
    if not opencode_dir.exists():
        rollback.defer_remove_empty_dir(opencode_dir)
    for name in ("agents", "skills", "templates"):
        rollback.replace_dir(opencode_dir / name, staged_tracks / name)
    seeded = _seed_opencode(
        target, staged_tracks / "assets" / "opencode.json", rollback
    )
    env_dir = target / ".tracks" / "env"
    rollback.defer_remove_tree(env_dir)
    env_dir.mkdir(parents=True, exist_ok=True)
    (env_dir / ".gitignore").write_text(ENV_GITIGNORE, encoding="utf-8")
    version_dir = env_dir / version
    version_dir.mkdir(parents=True, exist_ok=True)
    rollback.replace_dir(
        version_dir / "site-packages", staged_tracks.parent
    )
    rollback.save_file(target / LAUNCHER_REL)
    write_launcher(target)
    return seeded


def _seed_opencode(target: Path, asset: Path, rollback: _Rollback) -> bool:
    destination = target / "opencode.json"
    if destination.exists():
        return False
    rollback.save_file(destination)
    shutil.copy2(asset, destination)
    return True


def _previous_receipt(target: Path) -> dict | None:
    """The receipt already on disk, when parseable (idempotence basis)."""
    try:
        data = json.loads(
            (target / RECEIPT_REL).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _receipt(
    target: Path, version: str, source: str, seeded: bool
) -> dict:
    previous = _previous_receipt(target) or {}
    installed_at = datetime.now(timezone.utc).isoformat()
    if previous.get("version") == version and isinstance(
        previous.get("installed_at"), str
    ):
        # Same-version reinstall converges to the same receipt (no churn).
        installed_at = previous["installed_at"]
    seeded_files = ["opencode.json"] if seeded else []
    if (
        not seeded_files
        and "opencode.json" in (previous.get("seeded_files") or [])
        and (target / "opencode.json").exists()
    ):
        seeded_files = ["opencode.json"]
    return {
        "schema_version": 1,
        "version": version,
        "installed_at": installed_at,
        "python": sys.executable,
        "site_packages": SITE_PACKAGES_TEMPLATE.format(version=version),
        "launcher": LAUNCHER_REL,
        "installer_version": tracks.__version__,
        "source": source,
        "managed_files": _managed_files(target),
        "seeded_files": seeded_files,
    }


def _managed_files(target: Path) -> list[str]:
    managed = []
    for name in ("agents", "skills", "templates"):
        root = target / ".opencode" / name
        if root.is_dir():
            managed.extend(
                path.relative_to(target).as_posix()
                for path in root.rglob("*")
                if path.is_file()
            )
    managed.extend((LAUNCHER_REL, RECEIPT_REL))
    return sorted(set(managed))


def _health_check(target: Path) -> None:
    try:
        receipt = project_runtime.read_receipt(target)
    except project_runtime.ProjectCorrupt as exc:
        raise InstallError("health_check_failed", str(exc)) from None
    try:
        proc = subprocess.run(
            [receipt["python"], str(target / receipt["launcher"]), "--help"],
            cwd=target,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError("health_check_failed", str(exc)) from None
    if proc.returncode != 0:
        detail = _tail(proc.stderr) or _tail(proc.stdout) or "launcher --help failed"
        raise InstallError("health_check_failed", detail)


def _commit(target: Path, version: str, seeded: bool) -> None:
    paths = [
        relative
        for relative in _COMMIT_PATHS + (("opencode.json",) if seeded else ())
        if (target / relative).exists()
    ]
    added = subprocess.run(
        ["git", "add", "-f", "--", *paths],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        raise InstallError("git_commit_failed", _tail(added.stderr))
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *paths],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if diff.returncode == 0:
        return
    committed = subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"trac install: tracks {version}",
            "--only",
            "--",
            *paths,
        ],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if committed.returncode != 0:
        raise InstallError("git_commit_failed", _tail(committed.stderr))


def _tail(stderr: str) -> str:
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


class _Rollback:
    """Restore the pre-install managed trees when the transaction fails."""

    def __init__(self, target: Path, backup_root: Path, tracks_existed: bool):
        self.target = target
        self.backup_root = backup_root
        self.tracks_existed = tracks_existed
        self._ops: list = []
        self._backup_index = 0

    def _next_backup(self, name: str) -> Path:
        self._backup_index += 1
        return self.backup_root / f"{self._backup_index:02d}-{name}"

    def replace_dir(self, destination: Path, staged: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if destination.exists():
            self.backup_root.mkdir(parents=True, exist_ok=True)
            backup = self._next_backup(destination.name)
            destination.rename(backup)
        staged.rename(destination)

        def undo() -> None:
            if destination.is_dir():
                shutil.rmtree(destination, ignore_errors=True)
            elif destination.exists():
                destination.unlink()
            if backup is not None and backup.exists():
                backup.rename(destination)

        self._ops.append(undo)

    def save_file(self, path: Path) -> None:
        existed = path.exists()
        data = path.read_bytes() if existed else None

        def undo() -> None:
            if existed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data or b"")
            else:
                path.unlink(missing_ok=True)

        self._ops.append(undo)

    def defer_remove_empty_dir(self, path: Path) -> None:
        def undo() -> None:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

        self._ops.append(undo)

    def defer_remove_tree(self, path: Path) -> None:
        existed = path.exists()

        def undo() -> None:
            if not existed:
                shutil.rmtree(path, ignore_errors=True)

        self._ops.append(undo)

    def run(self) -> None:
        for op in reversed(self._ops):
            try:
                op()
            except OSError:
                continue
        if not self.tracks_existed:
            shutil.rmtree(self.target / ".tracks", ignore_errors=True)
