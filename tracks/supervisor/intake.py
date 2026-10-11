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

import json
import sqlite3
from pathlib import Path
from typing import Any

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
