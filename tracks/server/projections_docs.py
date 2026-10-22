"""Project-domain docs/version read models (§1n, §1p) for IF-QUERY-001.

Version tree ordering/titles, docs tree and doc resolution, and the discussion
thread projection. Public names are re-exported by ``tracks.server.projections``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracks.baseline import revision_digest
from tracks.discuss.model import iter_comments
from tracks.discuss.parser import parse_threads

from .projections_core import _TERMINAL_STATUSES, _project_from_home, _project_runs

# Project-domain doc read model (§1n.4): the six-piece closed set with
# `design` kept as the architecture alias; shared with the write face.
DOC_FILES = {"story": "story.md", "spec": "spec.md", "acceptance": "acceptance.md",
    "architecture": "architecture.md", "interfaces": "interfaces.md", "test-plan": "test-plan.md",
    "design": "architecture.md",}
_DOC_ORDER = ("story", "spec", "acceptance", "architecture", "interfaces", "test-plan")
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)$")
_HOTFIX_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)-hotfix-(\d+)$")


# -- project-domain docs read models (§1n, §1p) ------------------------------


def _version_sort_key(name: str) -> tuple[int, int, int, int, str] | None:
    """Numeric-descending order for version directories (§1n.1).

    ``v<M>.<m>`` sorts by numeric components (never lexicographically); a
    ``v<M>.<m>-hotfix-<n>`` directory ranks right after its baseline version,
    newer hotfix numbers after older ones. None for non-version names.
    """
    match = _HOTFIX_VERSION_RE.match(name)
    if match is not None:
        return (-int(match.group(1)), -int(match.group(2)), 1, -int(match.group(3)), name)
    match = _VERSION_RE.match(name)
    if match is None:
        return None
    return (-int(match.group(1)), -int(match.group(2)), 0, 0, name)


def _projects_root(project: dict) -> Path:
    return Path(project["repo_path"]) / ".tracks" / "projects"


def _project_versions(project: dict) -> list[str]:
    root = _projects_root(project)
    if not root.is_dir():
        return []
    keyed = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        key = _version_sort_key(child.name)
        if key is not None:
            keyed.append((key, child.name))
    keyed.sort()
    return [name for _key, name in keyed]


def _version_path(project: dict, version: str) -> Path:
    return _projects_root(project) / version


_STORY_TITLE_RE = re.compile(r"^title:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _version_story_title(project: dict, version: str) -> str | None:
    """The story.md frontmatter ``title:`` of one version (human label, #224).

    Searched in the file head only — the frontmatter block leads the document
    by construction; a missing/unreadable story or a missing field degrades to
    ``None`` and callers fall back to the run id.
    """
    story = _version_path(project, version) / DOC_FILES["story"]
    try:
        head = story.read_text(encoding="utf-8")[:2048]
    except (OSError, UnicodeDecodeError):
        return None
    match = _STORY_TITLE_RE.search(head)
    return match.group(1) if match else None


def _revision_or_none(vdir: Path) -> str | None:
    """The version directory's revision token for the docs read faces.

    The inherited (v0.10) construction is the trio digest
    (``baseline.revision_digest``) — the token the run-domain edit face #17
    binds, and the value the docs tree/read has always presented for dirs
    whose trio is derivable. §1n.1 globalizes ``docs_revision`` (six-piece,
    missing-aware) so that the docs-centre edit face #35 stays bindable in
    ANY review round: when the trio digest is structurally unavailable
    (spec/acceptance not yet written — the M-STORY window), the read faces
    fall back to the docs_revision instead of answering a null revision that
    no edit face could bind. Complete dirs keep the v0.10 value byte for
    byte (pure extension, §1n.1)."""
    try:
        return revision_digest(vdir)
    except (OSError, UnicodeDecodeError):
        pass
    try:
        from tracks.supervisor.service import docs_revision  # cycle-safe

        return docs_revision(vdir)
    except (OSError, UnicodeDecodeError):
        return None


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _editable_run_id(runs: list[dict], version: str) -> str | None:
    """The newest non-terminal run of ``version``, or None (§1n.1)."""
    active = [
        run for run in runs if run[
            "version"
        ] == version and run["status"] not in _TERMINAL_STATUSES]
    if not active:
        return None
    return max(active, key=lambda run: run["updated_ts"] or "")["run_id"]


def project_docs_tree(home: Path, project_id: str) -> dict | None:
    """Project docs tree: version-descending dirs + six-piece docs (§1n.1)."""
    project = _project_from_home(home, project_id)
    if project is None:
        return None
    return docs_tree_for_project(project)


def docs_tree_for_project(project: dict) -> dict:
    """Docs tree read model for one resolved project row (§1n.1).

    Split out of ``project_docs_tree`` so the single-user host fallback
    (api_query) can compute the same tree for the serve's own repo without a
    registered projects row.
    """
    runs = _project_runs(project["repo_path"])
    versions = []
    for version in _project_versions(project):
        vdir = _version_path(project, version)
        revision = _revision_or_none(vdir)
        docs = []
        for doc in _DOC_ORDER:
            path = vdir / DOC_FILES[doc]
            if path.is_file():
                docs.append({"doc": doc, "revision": revision, "updated_at": _mtime_iso(path)})
        versions.append(
            {
                "version": version, "title": _version_story_title(project, version), "docs": docs,
                "editable_run_id": _editable_run_id(runs, version),
            })
    return {"versions": versions}


def resolve_project_doc(
    home: Path, project_id: str, version: str, doc: str
) -> tuple[dict, Path, Path, str | None] | None:
    """(project, version dir, doc file, revision) for a project-domain doc.

    None when the project, the version directory name, or the doc member is
    unknown; handlers fail closed on that state (§2b #32-34).
    """
    project = _project_from_home(home, project_id)
    if project is None:
        return None
    return resolve_doc_in_project(project, version, doc)


def resolve_doc_in_project(
    project: dict, version: str, doc: str
) -> tuple[dict, Path, Path, str | None] | None:
    """``resolve_project_doc`` over an already-resolved project row (§2b #32)."""
    name = DOC_FILES.get(doc)
    if name is None or _version_sort_key(version) is None:
        return None
    vdir = _version_path(project, version)
    doc_file = vdir / name
    if not doc_file.is_file():
        return None
    return project, vdir, doc_file, _revision_or_none(vdir)


def project_doc_discussions(
    home: Path, project_id: str, version: str, doc: str
) -> dict | None:
    """Server-side tracks/discuss parse projected to the UI read model (§1p)."""
    resolved = resolve_project_doc(home, project_id, version, doc)
    if resolved is None:
        return None
    _project, _vdir, doc_file, _revision = resolved
    threads = parse_threads(doc_file.read_text(encoding="utf-8"))
    return {"threads": [_thread_json(thread) for thread in threads]}


def _thread_json(thread: Any) -> dict:
    return {"thread_id": thread.thread_id, "status": thread.status, "initiator": thread.initiator,
        "anchor_line": thread.anchor_line, "summary": thread.snippet,
        "entry_line": thread.root_line, "awaiting": _thread_awaiting(thread),}


def _thread_awaiting(thread: Any) -> str | None:
    """The party a thread currently awaits, or None (§1p.1).

    A comment whose @mention requests an answer (the tracks/discuss
    "@request someone to answer" semantic) and that still has no child reply
    is an open request; the latest such comment names the awaited party when
    it carries exactly one distinct mention.
    """
    pending = [
        comment for comment in iter_comments(
            thread.root
        ) if not comment.children and comment.mentions]
    if not pending:
        return None
    latest = max(pending, key=lambda comment: comment.line)
    unique = list(dict.fromkeys(latest.mentions))
    return unique[0] if len(unique) == 1 else None
