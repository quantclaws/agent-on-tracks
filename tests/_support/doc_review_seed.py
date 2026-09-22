"""Shared seeding for the IF-DOCREV-001 material-review anchors.

The doc read path (``GET /api/runs/{run_id}/docs/{doc}``, interfaces §2b #15)
and the edit/approval revision bindings (§1b #5/#6) address the registered
project's version directory: the revision is the approval-bound trio digest
(``baseline.revision_digest``). This seeder arranges the documented storage
contract for real — the service-plane ``projects`` row (interfaces §1c table
2), the run's tracks.db through the real ``Store``, and the material trio
under ``.tracks/projects/<version>/`` in a real git repo (the Runtime commits
material revisions there) — so the frozen anchors exercise the genuine seam
instead of an unseeded run.
"""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks.baseline import revision_digest
from tracks.paths import tracks_home
from tracks.server import api_query
from tracks.server.redaction import SecretRedactor
from tracks.store import Store
from tracks.supervisor import db as sdb

_VERSION = "v0.9"
_ACTOR = "local-user"
_TS = "2026-09-22T00:00:00+00:00"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def seed_doc_review_run(tmp_path: Path, run_id: str) -> tuple[Path, Path, str]:
    """A registered run whose material trio is its current revision.

    Returns ``(home, repo, current_revision)``; the anchors approve (or edit)
    against the trio digest the reviewer would have signed.
    """
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text("story body\n", encoding="utf-8")
    (vdir / "spec.md").write_text("spec body\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text("acceptance body\n", encoding="utf-8")
    (vdir / "architecture.md").write_text("design body\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-f", ".tracks")
    _git(repo, "commit", "-qm", "material trio")
    current = revision_digest(vdir)

    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-REQ-APPROVAL"})
    finally:
        store.close()

    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), _VERSION, _ACTOR, _TS),
        )
        conn.commit()
    finally:
        conn.close()
    return home, repo, current


def read_doc(home: Path, run_id: str, doc: str) -> tuple[Any, Any]:
    """``GET /api/runs/{run_id}/docs/{doc}`` through the handler seam (§2b #15)."""
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(home=home, redactor=SecretRedactor({}))
        ),
        path_params={"run_id": run_id, "doc": doc},
        query_params={},
    )
    result = asyncio.run(api_query.read_doc(request))
    return getattr(result, "status_code", None), getattr(result, "payload", None)


def material_edits(home: Path, run_id: str, doc: str) -> list[dict]:
    """The audited ``material.edited`` payloads for a run/doc (§1a #22)."""
    return [
        event["payload"]
        for event in sdb.ServiceDB(home).read_events(run_id=run_id)
        if event["type"] == "material.edited" and (event["payload"] or {}).get("doc") == doc
    ]
