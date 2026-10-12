"""Scribe intake evaluation worker (IF-INTAKE-001, interfaces §1f.2/§3 #2).

Owns the evaluation half of the unified intake entry: the command service
accepts ``submit_intake`` and persists the ``submitted`` row; this worker
drives the row through SM-01's ``submitted -> evaluating -> evaluated``
window (``failed`` is the retryable dead end, re-dispatch budget <= 3):

- ``materialize_assignment`` writes the Scribe dispatch brief under
  ``<service_home>/intakes/<intake_id>/assignment.md`` (input body, source
  and the §1f.2 report schema the reply must carry — interfaces §3 #2);
- ``run_intake_evaluation`` executes one evaluation attempt through the
  shared agent-effects seam (``TRAC_AGENT_BACKEND`` selects the fake channel
  for deterministic runs and the live channel in production), lands the
  parsed report on the intakes row and appends the ``intake.evaluated``
  service event (actor ``scribe-evaluator``);
- ``reclaim_stale_evaluating`` recovers rows stranded in ``evaluating``
  (crashed worker): past the age threshold the row returns to ``submitted``
  for a re-dispatch until the budget is exhausted, then settles ``failed``.

The worker never talks to HTTP clients and never writes the run plane; the
decision half (``decide_intake``) stays with the command service.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.store import Store, new_ulid
from tracks.supervisor.db import ServiceDB

# SM-01.1 re-dispatch budget (interfaces §1f.1: 失败态 failed 可重派，≤3).
EVALUATION_ATTEMPT_BUDGET = 3
_REPORT_SCHEMA_HINT = (
    "{\n"
    '  "feasibility": {"verdict": "feasible|blocked", "blockers": [...]},\n'
    '  "duplicates": {"found": bool, "items": [{"ref": "FR-XXXX@vN",'
    ' "summary": str, "suggestion": "no_go|enhance"}]},\n'
    '  "conflicts": {"found": bool, "pairs": [{"new_item": str,'
    ' "existing_ref": str}]},\n'
    '  "title": str,  # non-empty suggested title\n'
    '  "input_form": "raw|complete",\n'
    '  "summary": str\n'
    "}"
)
_REQUIRED_REPORT_KEYS = ("feasibility", "duplicates", "conflicts", "title", "summary")


def assignment_path(home: Path, intake_id: str) -> Path:
    """The §3 #2 dispatch brief location of one intake."""
    return Path(home) / "intakes" / str(intake_id) / "assignment.md"


def materialize_assignment(home: Path, intake: dict) -> Path:
    """Write the Scribe evaluation brief (输入正文/来源/报告 schema 要求)."""
    path = assignment_path(home, str(intake.get("intake_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# Scribe intake evaluation",
        "",
        f"- intake_id: {intake.get('intake_id')}",
        f"- project_id: {intake.get('project_id')}",
        f"- source: {intake.get('source')}",
        "",
        "## 输入正文（逐字）",
        "",
        "> " + str(intake.get("content") or "").replace("\n", "\n> "),
        "",
        "## 评估要求",
        "",
        "可行性 / 重复 / 冲突建议，按 intake report schema 返回 JSON：",
        "",
        "```json",
        _REPORT_SCHEMA_HINT,
        "```",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def load_intake(home: Path, intake_id: str) -> dict | None:
    """One intakes row as a dict; None when the id is unknown."""
    return ServiceDB(Path(home)).get_intake(intake_id)


def run_intake_evaluation(home: Path, intake_id: str, *, repo: Path | None = None) -> dict:
    """Drive one intake through the evaluation window (one attempt).

    ``submitted``/``failed`` rows enter ``evaluating``; the Scribe dispatch
    rides the shared agent-effects seam; a conforming report lands the row
    at ``evaluated`` plus the ``intake.evaluated`` audit, anything else
    settles ``failed`` with the reason recorded in report_json. Returns the
    terminal row.
    """
    db = ServiceDB(Path(home))
    intake = db.get_intake(intake_id)
    if intake is None:
        raise ValueError(f"unknown intake {intake_id!r}")
    status = intake.get("status")
    if status not in ("submitted", "failed", "evaluating"):
        return intake  # evaluated/decided: nothing to do (idempotent)
    attempts = _attempt_count(db, intake_id) + 1
    db.update_intake(intake_id, status="evaluating")
    materialize_assignment(Path(home), intake)
    report, failure = _dispatch_scribe(home, intake, repo=repo)
    if report is None:
        if attempts >= EVALUATION_ATTEMPT_BUDGET:
            db.update_intake(
                intake_id, status="failed",
                report_json=json.dumps({"evaluation_error": failure}, ensure_ascii=False),
            )
        else:
            db.update_intake(intake_id, status="submitted")
        return db.get_intake(intake_id) or {}
    db.update_intake(
        intake_id, status="evaluated",
        report_json=json.dumps(report, ensure_ascii=False),
    )
    db.append_event(
        "intake.evaluated",
        {"intake_id": intake_id, "report": report, "attempt": attempts,
         "actor": "scribe-evaluator"},
        project_id=intake.get("project_id"),
    )
    return db.get_intake(intake_id) or {}


def reclaim_stale_evaluating(
    home: Path, *, older_than_s: float, now_ts: float | None = None
) -> list[str]:
    """Recover rows stranded in ``evaluating`` (crashed worker).

    A row whose ``updated_at`` is older than ``older_than_s`` returns to
    ``submitted`` for a re-dispatch until the attempt budget is exhausted;
    past the budget it settles ``failed``. Returns the recovered intake ids
    (re-dispatched first, then the budget-exhausted ones in order).
    """
    import time

    now = now_ts if now_ts is not None else time.time()
    db = ServiceDB(Path(home))
    conn = _connect(Path(home))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT intake_id, updated_at FROM intakes WHERE status = 'evaluating'"
        ).fetchall()
    finally:
        conn.close()
    redispatch: list[str] = []
    exhausted: list[str] = []
    for row in rows:
        updated = _parse_ts(row["updated_at"])
        if updated is None or (now - updated) < older_than_s:
            continue
        intake_id = str(row["intake_id"])
        if _attempt_count(db, intake_id) >= EVALUATION_ATTEMPT_BUDGET:
            db.update_intake(intake_id, status="failed")
            exhausted.append(intake_id)
        else:
            db.update_intake(intake_id, status="submitted")
            redispatch.append(intake_id)
    return redispatch + exhausted


def _dispatch_scribe(
    home: Path, intake: dict, *, repo: Path | None = None
) -> tuple[dict | None, str | None]:
    """One Scribe evaluation through the agent-effects seam.

    The backend is selected exactly like every other dispatch
    (``TRAC_AGENT_BACKEND``); the outcome's ``intake_report`` payload is the
    §1f.2 report. A missing/non-conforming reply is a failed attempt, never
    a fabricated report.
    """
    try:
        from tracks.effects import select_backend

        project_repo = Path(repo) if repo is not None else _project_repo(home, intake)
        backend = select_backend(project_repo, "intake")
        outcome = backend.act(
            "scribe",
            "INTAKE_EVAL",
            doc=None,
            doc_path=assignment_path(home, str(intake.get("intake_id") or "")),
        )
    except Exception as exc:  # the agent channel failing is a failed attempt
        return None, f"dispatch error: {exc}"
    report = _coerce_report(outcome)
    if report is None:
        return None, "outcome carried no conforming intake report"
    return report, None


def _coerce_report(outcome: Any) -> dict | None:
    """The §1f.2 report out of a Scribe outcome, or None when absent or
    non-conforming (required keys present, title non-empty)."""
    if not isinstance(outcome, dict):
        return None
    report = outcome.get("intake_report")
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            return None
    if not isinstance(report, dict):
        return None
    if any(key not in report for key in _REQUIRED_REPORT_KEYS):
        return None
    if not str(report.get("title") or "").strip():
        return None
    return report


def _project_repo(home: Path, intake: dict) -> Path:
    """The registered project's repo of an intake (§1c projects table)."""
    conn = _connect(Path(home))
    try:
        row = conn.execute(
            "SELECT repo_path FROM projects WHERE project_id = ?",
            (str(intake.get("project_id") or ""),),
        ).fetchone()
    finally:
        conn.close()
    return Path(str(row[0])) if row else Path.cwd()


def _attempt_count(db: ServiceDB, intake_id: str) -> int:
    """Attempts already spent: the intake.evaluated audits of this intake."""
    spent = 0
    for event in db.read_events():
        if event.get("type") != "intake.evaluated":
            continue
        payload = event.get("payload") or {}
        if payload.get("intake_id") == intake_id:
            spent += 1
    return spent


def _connect(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(Path(home) / "service.db")


def _parse_ts(raw: Any) -> float | None:
    """An ISO timestamp as epoch seconds; None when unparseable."""
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return None


# -- T-010 D1 migration: unified-entry acceptance helpers (from service.py) --
# The M-START gate + backlog + hotfix-precheck mirror and the intake/backlog
# short-write halves moved here for C0302 line governance (round 19; behavior
# and the service import face unchanged — service re-imports these names).
# Contracts: IF-MSTART-001, IF-BACKLOG-001, IF-HOTFIX-003, IF-INTAKE-001.

def _now_iso() -> str:
    """UTC now in the store's ISO convention (intake row timestamps)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# -- hotfix journey precheck mirror (IF-CMDSVC-001 / FR-0292) -----------------
# create_run hotfix journeys mirror the v0.8 entry precheck at acceptance
# (§1b#3 "hotfix_* 复用 v0.8 hotfix 前检"): rules run before anything
# persists (FR-0292 / AC-FR0292-02); pure rules stay in executor/hotfix.py.

# create_run journey -> v0.8 precheck scenario (IF-HOTFIX-003 P-2 vocabulary).
_HOTFIX_SCENARIOS = {"hotfix_post": "post-release", "hotfix_dev": "dev"}


def _issue_number(value: Any) -> int | None:
    """The create_run ``issue`` param as an int; None when absent/uncoercible."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _git_branch_lines(repo: Path) -> list[str]:
    """``git branch --list`` lines; [] when the repo cannot be probed."""
    try:
        proc = subprocess.run(["git", "branch", "--list"], cwd=str(repo), capture_output=True,
            text=True, check=False)
    except OSError:
        return []
    return proc.stdout.splitlines() if proc.returncode == 0 else []


# -- v0.11 M-START gate + backlog helpers (IF-MSTART-001 / IF-BACKLOG-001) ----

_RELEASE_VERSION_RE = re.compile(r"v(\d+)\.(\d+)$")


def _worktree_dirty(repo: Path) -> str | None:
    """``git status --porcelain`` output §1g.1.b"""
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo), capture_output=True,
            text=True, check=False)
    except OSError:
        return "worktree not probeable"
    if proc.returncode != 0:
        return (proc.stderr or "worktree not probeable").strip()
    significant = [
        line for line in proc.stdout.splitlines() if line.strip() and not _is_runtime_storage(line)]
    return "\n".join(significant).strip() or None


def _is_runtime_storage(porcelain_line: str) -> bool:
    """True when one ``git status --porcelain`` line targets ``.tracks``"""
    path = porcelain_line[3:].strip() if len(porcelain_line) > 3 else ""
    if " -> " in path:
        path = path.split(" -> ")[0]
    path = path.strip().strip('"')
    return path == ".tracks" or path.startswith(".tracks/")


def _release_versions(repo: Path) -> set[tuple[int, int]]:
    """The project's existing release versions as ``(major, minor)`` pairs §1g.1.d"""
    versions: set[tuple[int, int]] = set()
    for line in _git_branch_lines(repo):
        name = line.strip().lstrip("*+").strip()
        match = _RELEASE_VERSION_RE.search(name)
        if match and name.startswith("releases/"):
            versions.add((int(match.group(1)), int(match.group(2))))
    projects = paths.projects_dir(paths.tracks_home(repo))
    if projects.is_dir():
        for entry in projects.iterdir():
            match = _RELEASE_VERSION_RE.search(entry.name)
            if entry.is_dir() and match:
                versions.add((int(match.group(1)), int(match.group(2))))
    return versions


def _next_release_version(repo: Path) -> str:
    """The next release version (§1g.1.d), monotone over existing releases."""
    versions = _release_versions(repo)
    if not versions:
        return "v0.1"
    major = max(number for number, _ in versions)
    if major > 0:
        return f"v{major + 1}.0"
    return f"v0.{max(minor for _, minor in versions) + 1}"


def _unmerged_release_branches(repo: Path) -> list[str]:
    """``releases/*`` branches not fully merged into main (§1g.1.c)."""
    try:
        proc = subprocess.run(["git", "branch", "--list", "releases/*", "--no-merged", "main"],
            cwd=str(repo), capture_output=True, text=True, check=False)
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip().lstrip("*+").strip() for line in proc.stdout.splitlines() if line.strip()]


def _backlog_events(repo: Path, event_type: str) -> list[tuple[str, dict]]:
    """(run_id, payload) of one backlog event type in the project's tracks.db."""
    db_path = paths.db_path(paths.tracks_home(repo))
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT run_id, payload FROM events WHERE type = ?", (event_type,)
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out: list[tuple[str, dict]] = []
    for run_id, payload in rows:
        try:
            out.append((str(run_id), json.loads(payload)))
        except (TypeError, ValueError):
            continue
    return out


def _backlog_entry_exists(repo: Path, entry_id: str) -> bool:
    """A backlog entry exists iff a backlog.recorded event rides its ULID."""
    return any(run_id == entry_id for run_id, _payload in _backlog_events(repo, "backlog.recorded"))


def _entry_disposed(repo: Path, entry_id: str) -> bool:
    """A backlog entry is disposed iff a backlog.dispositioned points at it."""
    return any(payload.get("entry") == entry_id for _run_id,
        payload in _backlog_events(repo, "backlog.dispositioned"))


def _append_backlog_event(
    repo: Path, version: str, event_type: str, payload: dict, run_id: str
) -> None:
    """Append one run-plane backlog event under the writer discipline of the §1d"""
    store = Store(paths.tracks_home(repo))
    try:
        store.append(run_id, version, event_type, payload)
    finally:
        store.close()


def _hotfix_issue(repo: Path, issue_number: int | None) -> Any:
    """Host issue snapshot for the precheck (IF-HOTFIX-003)"""
    if issue_number is None:
        return None
    from tracks.effects.github import GithubIssuesError, select_issue_backend

    try:
        return select_issue_backend(repo, "").fetch_issue(issue_number)
    except GithubIssuesError:
        return None


def _hotfix_precheck(repo: Path, issue_number: int | None, scenario: str) -> Any:
    """The v0.8 deterministic hotfix precheck over a project repo (FR-0292)."""
    from tracks.executor.hotfix import precheck_hotfix  # cycle-safe imports
    from tracks.executor.hotfix_face import precheck_hotfix_report

    tracks_home = paths.tracks_home(repo)
    if paths.db_path(tracks_home).exists():
        store = Store(tracks_home)
        try:
            report, _issue = precheck_hotfix_report(repo, store, issue_number, scenario)
        finally:
            store.close()
        return report
    return precheck_hotfix(_hotfix_issue(repo, issue_number), scenario, _git_branch_lines(repo),
        None, paths.projects_dir(tracks_home), set())


def _preflight_develop_gates(
    store: ServiceDB, intake: dict, request: dict, deny: Callable[..., None]
) -> None:
    """The §1g.1 M-START order for the develop action."""
    project = store.get_project(intake.get("project_id"))
    if project is None:
        deny("not_found", f"project {intake.get('project_id')!r} is not registered")
    repo = Path(str(project["repo_path"]))
    dirty = _worktree_dirty(repo)
    if dirty:
        deny("dirty_tree", f"the worktree is not clean: {dirty}")
    schedule = store.get_schedule()
    if schedule is not None and schedule.get("active_run"):
        return  # (a) legal terminal: recorded into backlog at apply time
    unmerged = _unmerged_release_branches(repo)
    if unmerged and not request.get("confirm"):
        deny("unmerged_release_branches",
                "local release branches are not fully merged into main:"
                    f" {', '.join(unmerged)}; confirm=true continues, cancelling"
                " leaves nothing behind")


def _apply_intake_submitted(store: ServiceDB, request: dict, actor: str, command_id: str
) -> dict:
    """§1b #14 short write"""
    intake_id = new_ulid()
    now = _now_iso()
    row = {"intake_id": intake_id, "project_id": request.get("project_id"),
        "source": request.get("source"), "content": request.get("content"),
        "issue": _issue_number(request.get("issue")),
        "origin_entry": request.get("origin_entry"), "status": "submitted", "created_at": now,
        "updated_at": now,}
    store.insert_intake(row)
    content = str(request.get("content") or "")
    store.append_event("intake.submitted",
        {
            "intake_id": intake_id, "project_id": request.get("project_id"),
            "source": request.get("source"),
            "content_sha": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "issue": row["issue"], "origin_entry": request.get("origin_entry"), "actor": actor,
            "command_id": command_id,
        },
        project_id=request.get("project_id"), command_id=command_id)
    return row


def _apply_backlog_disposed(store: ServiceDB, request: dict, actor: str, command_id: str
) -> None:
    """§1b #16 / §1d.3 short write"""
    project = store.get_project(request.get("project_id"))
    if project is None:
        return
    repo = Path(str(project["repo_path"]))
    _append_backlog_event(repo, str(project.get("version") or "v0.1"), "backlog.dispositioned",
        {
            "entry": str(request.get("entry_id") or ""),
            "disposition": request.get("disposition"), "to": request.get("to") or None,
            "reason": request.get("reason"),
        },
        new_ulid())
