"""Project registration shared by HTTP and installed-project service startup."""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import tomllib


def repo_version(repo: Path) -> str:
    """Read the host product version; the Tracks package version is unrelated."""
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        version = str(data.get("project", {}).get("version", "")).strip()
    except (OSError, tomllib.TOMLDecodeError):
        version = ""
    return version or "unknown"


def register_project(home: Path, repo_path: str, version: str, actor: str) -> dict:
    """Insert or return the service's project row without changing its identity."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        row = conn.execute(
            "SELECT project_id, repo_path, version FROM projects WHERE repo_path = ?",
            (repo_path,),
        ).fetchone()
        if row is not None:
            return {"project_id": row[0], "repo_path": row[1], "version": row[2], "fresh": False}
        project_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, repo_path, version, actor, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return {"project_id": project_id, "repo_path": repo_path, "version": version, "fresh": True}
    finally:
        conn.close()
