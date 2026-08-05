"""Runtime root resolution (D-15).

The `.tracks/` root is resolved from the runtime cwd and may be overridden by
the TRACKS_HOME environment variable. It is NEVER hardcoded to the tracks
project root, so tests running in a tmp git repo stay isolated.
"""
from __future__ import annotations

import os
from pathlib import Path


def tracks_home(cwd: Path | str | None = None) -> Path:
    env = os.environ.get("TRACKS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    base = Path(cwd) if cwd is not None else Path.cwd()
    return (base / ".tracks").resolve()


def projects_dir(home: Path) -> Path:
    return home / "projects"


def version_dir(home: Path, version: str) -> Path:
    return projects_dir(home) / version


def runtime_dir(home: Path) -> Path:
    return home / "runtime"


def project_dir(home: Path) -> Path:
    return home / "project"


def project_toml_path(home: Path) -> Path:
    return project_dir(home) / "project.toml"


def wiki_dir(home: Path) -> Path:
    return home / "wiki"


def db_path(home: Path) -> Path:
    return runtime_dir(home) / "tracks.db"


def blobs_dir(home: Path) -> Path:
    return runtime_dir(home) / "blobs"


def lock_path(home: Path) -> Path:
    return runtime_dir(home) / "lock"
