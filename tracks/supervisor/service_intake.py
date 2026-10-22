"""Intake/backlog command faces of the persistent command service.

Owns the v0.11 unified-entry admission preflights and the synchronous
decision writes (``submit_intake`` / ``decide_intake`` / ``dispose_backlog``).
Split out of ``tracks.supervisor.service`` to keep the C0302 module-line
budget (architecture NFR-06b) without changing the service import face.

Tokens: IF-INTAKE-001, IF-CMDSVC-001.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tracks.store import new_ulid
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.intake import (
    _append_backlog_event,
    _backlog_entry_exists,
    _entry_disposed,
    _issue_number,
    _next_release_version,
    _now_iso,
    _preflight_develop_gates,
)

# v0.11 closed enums (interfaces §1b #14-18 params columns).
_INTAKE_SOURCES = frozenset({"text", "issue", "backlog"})
_INTAKE_ACTIONS = frozenset({"backlog", "archive", "remove_backlog", "develop"})
_BACKLOG_DISPOSITIONS = frozenset({"removed", "promoted"})

# deny(reason, detail) audits command.rejected and raises Rejection
# (SM-01.5): a binding failure leaves the command unpersisted/unexecuted.
RejectFn = Callable[..., None]


def create_run_id(params: dict) -> str:
    """Stable run identity of a create_run acceptance (interfaces §1b#3, §2b#8)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return uuid.uuid5(uuid.NAMESPACE_OID, canonical).hex


class IntakeCommands:
    """Intake/backlog command faces mixed into ``CommandService``."""

    def _preflight_submit_intake(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #14: the unified-entry admission — registered project, closed IF-HOTFIX-011"""
        source = str(request.get("source") or "")
        if source not in _INTAKE_SOURCES:
            deny("validation_failed",
                f"unknown source {source!r}; closed set is text|issue|backlog")
        project = store.get_project(request.get("project_id"))
        if project is None:
            deny("not_found", f"project {request.get('project_id')!r} is not registered")
        self._check_intake_source_shape(Path(str(project["repo_path"])), request, source, deny)

    def _check_intake_source_shape(
        self, repo: Path, request: dict, source: str, deny: RejectFn
    ) -> None:
        """The per-source shape rules of §1b #14"""
        if source == "text":
            if not str(request.get("content") or "").strip():
                deny("validation_failed", "source 'text' requires a non-empty content param")
        elif source == "backlog":
            origin = str(request.get("origin_entry") or "")
            if not origin:
                deny("validation_failed", "source 'backlog' requires origin_entry")
            if not _backlog_entry_exists(repo, origin):
                deny("not_found", f"backlog entry {origin!r} does not exist")
            if _entry_disposed(repo, origin):
                deny("validation_failed", f"backlog entry {origin!r} is already dispositioned")
        elif source == "issue":
            number = _issue_number(request.get("issue"))
            if number is None:
                deny("validation_failed", "source 'issue' requires an integer issue param")
            issue = self._fetch_issue_or_deny(repo, number, deny)
            if issue is not None and not str(request.get("content") or "").strip():
                request["content"] = f"#{number}: {issue.title}\n\n{issue.body}"

    def _fetch_issue_or_deny(self, repo: Path, number: int, deny: RejectFn) -> Any:
        """Issue fetch with the IF-HOTFIX-011 classification mapping AC-FR0338-01 SM-01.5"""
        from tracks.effects.github import GithubIssuesError, select_issue_backend

        try:
            issue = select_issue_backend(repo, "").fetch_issue(number)
        except GithubIssuesError as exc:
            classification = getattr(exc, "classification", "") or "network"
            deny("issue_fetch_failed",
                f"issue {number} could not be fetched ({classification}): {exc}")
        if issue is None:
            deny("issue_fetch_failed",
                f"issue {number} could not be fetched (not_found):"
                    " the host confirms no such issue")
        return issue

    def _preflight_decide_intake(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #15: the decision admission — evaluated-only readiness, SM-01.5"""
        action = str(request.get("action") or "")
        if action not in _INTAKE_ACTIONS:
            deny("validation_failed",
                f"unknown action {action!r}; closed set is backlog|archive|remove_backlog|develop")
        intake = store.get_intake(request.get("intake_id"))
        if intake is None or intake.get("status") != "evaluated":
            deny("intake_not_ready",
                f"intake {request.get('intake_id')!r} is not in the evaluated state")
        if action == "remove_backlog" and str(intake.get("source") or "") != "backlog":
            deny("validation_failed",
                "remove_backlog is only legal for a backlog-sourced intake (SM-01.5)")
        if action == "develop":
            _preflight_develop_gates(store, intake, request, deny)

    def _preflight_dispose_backlog(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #16: the entry must exist and be undisposed; a promotion needs"""
        disposition = str(request.get("disposition") or "")
        if disposition not in _BACKLOG_DISPOSITIONS:
            deny("validation_failed",
                f"unknown disposition {disposition!r}; closed set is removed|promoted")
        project = store.get_project(request.get("project_id"))
        if project is None:
            deny("not_found", f"project {request.get('project_id')!r} is not registered")
        repo = Path(str(project["repo_path"]))
        entry = str(request.get("entry_id") or "")
        if not _backlog_entry_exists(repo, entry):
            deny("not_found", f"backlog entry {entry!r} does not exist")
        if _entry_disposed(repo, entry):
            deny("validation_failed", f"backlog entry {entry!r} is already dispositioned")
        if disposition == "promoted" and not str(request.get("to") or "").strip():
            deny("validation_failed", "disposition 'promoted' requires a non-empty to param")

    def _apply_intake_decided(
        self, store: ServiceDB, request: dict, actor: str, command_id: str
    ) -> dict:
        """§1f.3 short writes: actions ①②③ are terminals (backlog event / §1g.1"""
        intake = store.get_intake(request.get("intake_id"))
        if intake is None:
            return {}
        action = str(request.get("action") or "")
        project = store.get_project(intake.get("project_id"))
        repo = Path(str(project["repo_path"])) if project is not None else None
        content = str(intake.get("content") or "")
        source = intake.get("source")
        outcome: Any
        if action == "backlog" and repo is not None:
            entry = new_ulid()
            _append_backlog_event(repo, str(project.get("version") or "v0.1"), "backlog.recorded",
                {
                    "source": source, "content": content, "intake_id": intake.get("intake_id")
                }, entry)
            outcome = {"backlog_entry_id": entry}
        elif action == "remove_backlog" and repo is not None:
            entry = str(intake.get("origin_entry") or "")
            _append_backlog_event(repo, str(project.get("version") or "v0.1"),
                "backlog.dispositioned", {"entry": entry, "disposition": "removed", "to": None},
                new_ulid())
            outcome = {"backlog_entry_id": entry, "disposition": "removed"}
        elif action == "develop":
            outcome = self._apply_develop(store, intake, project, actor)
        else:
            outcome = "archive"
        store.update_intake(str(intake.get("intake_id")), status="decided", decision=action,
            decided_at=_now_iso())
        store.append_event("intake.decided",
            {
                "intake_id": intake.get("intake_id"), "action": action, "outcome": outcome,
                "actor": actor, "command_id": command_id,
            },
            project_id=intake.get("project_id"), command_id=command_id)
        return outcome if isinstance(outcome, dict) else {"outcome": outcome}

    def _apply_develop(
        self, store: ServiceDB, intake: dict, project: dict | None, actor: str
    ) -> Any:
        """The develop action's §1g.1 execution §1f.3/ §1d.3"""
        if project is None:
            return {"outcome": "develop_failed", "reason": "project not registered"}
        repo = Path(str(project["repo_path"]))
        content = str(intake.get("content") or "")
        schedule = store.get_schedule()
        active = schedule.get("active_run") if schedule is not None else None
        if active:
            # T-008 (AC-FR0339-02, §1g.2): the active-run exit lands its
            # backlog.recorded ON the active run's stream; the entry identity
            # is that stream id (§1d.1), not the run-less entry stream.
            _append_backlog_event(repo, str(project.get("version") or "v0.1"), "backlog.recorded",
                {
                    "reason": "active_run", "source": intake.get("source"), "content": content,
                    "intake_id": intake.get("intake_id"),
                },
                active)
            return {"outcome": "backlog_recorded", "active_run": active, "backlog_entry_id": active}
        report: dict = {}
        with contextlib.suppress(TypeError, ValueError):
            report = json.loads(intake.get("report_json") or "") or {}
        from tracks import templating  # cycle-safe: templating stays leaf-side

        params = {"project_id": intake.get("project_id"), "journey": "feature",
            "version": _next_release_version(repo), "story": content,
            "title": str(report.get("title") or ""),
            "story_form": templating.classify_story_input(content), "issue": None, "target": None,
            "preempt": False,}
        self.accept("create_run", params, actor=actor, actor_class="human", surface="cli",
            idempotency_key=f"intake-develop-{intake.get('intake_id')}")
        # T-008 (AC-FR0338/FR-0339, §1f.3/§1d.3): a successful develop
        # promotes the origin entry — the SAME backlog.dispositioned event
        # dispose_backlog lands (合同唯一); a denied create_run raises above,
        # so no half-promoted residue (single-disposition invariant).
        origin_entry = str(intake.get("origin_entry") or "")
        if str(intake.get("source") or "") == "backlog" and origin_entry:
            _append_backlog_event(repo, str(project.get("version") or "v0.1"),
                "backlog.dispositioned",
                { "entry": origin_entry, "disposition": "promoted", "to": params["version"], },
                new_ulid())
        return {"outcome": "develop", "run_id": create_run_id(params), "version": params["version"],
            "branch": f"releases/{params['version']}",}
