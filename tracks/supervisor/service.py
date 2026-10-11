"""Persistent command service shared by CLI and Web (IF-CMDSVC-001).

Single accept path for the closed ServiceCommandKind set (interfaces §1b):
guard validation -> idempotency dedup -> persist (SM-01.1) -> hand to a
supervisor worker (HTTP) or execute inline (CLI); registration admits only
repos inside the serve permitted scope (IF-PROJ-001/IF-SECRECY-001).
Tokens: IF-CMDSVC-001, IF-WEBGATE-001, IF-WEBAUTH-001, IF-PAUSE-001,
IF-SCHED-001, IF-PROJ-001, IF-SECRECY-001.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import re
import sqlite3
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracks import paths
from tracks.baseline import revision_digest
from tracks.cli.gate_cmd import _retry_gate_error, _return_gate
from tracks.cli.release_cmd import (
    _already_executed_ops,
    _establish_escape_barrier,
    _stale_downstream_evidence,
)
from tracks.discuss import writer
from tracks.discuss.gate import adjudication_owner
from tracks.discuss.locate import token_for
from tracks.discuss.model import Thread, speaker_key
from tracks.discuss.parser import parse_tag, parse_threads
from tracks.executor.release_authorization import assess_release, validate_authorization
from tracks.frontmatter import doc_body_sha
from tracks.kernel.events import EventEnvelope
from tracks.store import Store, new_ulid
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.intake import (
    _HOTFIX_SCENARIOS,
    _append_backlog_event,
    _apply_backlog_disposed,
    _apply_intake_submitted,
    _backlog_entry_exists,
    _entry_disposed,
    _hotfix_precheck,
    _issue_number,
    _next_release_version,
    _now_iso,
    _preflight_develop_gates,
)

# Closed command-kind set (interfaces §1b). v0.11 append (#14-18): the intake
# entry/decision + backlog disposition + gate records; short writes execute
# synchronously at acceptance (edit_material precedent, §1b 执行形态分界),
# the gate kinds persist for the supervisor's worker claim (§1j.3).
SERVICE_COMMAND_KINDS = (
    "register_project", "check_readiness", "create_run", "submit_clarification",
    "edit_material", "record_stage_approval", "record_release_decision",
    "pause_run", "resume_run", "abandon_run", "return_stage", "retry_run",
    "drive_run", "submit_intake", "decide_intake", "dispose_backlog",
    "record_triage", "record_review",
)

# actor_class partition (interfaces §1f.5).
HUMAN_ONLY_KINDS = frozenset(k for k in SERVICE_COMMAND_KINDS if k != "drive_run")
SYSTEM_ONLY_KINDS = frozenset({"drive_run"})

# create_run journey enum (interfaces §1b #3).
_CREATE_RUN_JOURNEYS = frozenset({"feature", "hotfix_post", "hotfix_dev"})

# v0.11 closed enums (interfaces §1b #14-18 params columns).
_INTAKE_SOURCES = frozenset({"text", "issue", "backlog"})
_INTAKE_ACTIONS = frozenset({"backlog", "archive", "remove_backlog", "develop"})
_BACKLOG_DISPOSITIONS = frozenset({"removed", "promoted"})
_TRIAGE_DECISIONS = frozenset({"go", "no_go", "park"})
_REVIEW_ACTIONS = frozenset({"comment", "no_comment"})
_GATE_AWAITING = {
    "record_triage": frozenset({"triage"}),
    "record_review": frozenset({"review", "review_pending_threads"}),
}

# Required params per kind (interfaces §1b params column). v0.11: edit
# ``base_revision``/clarification ``thread_token`` left the required tier —
# null edits are the §1n.2 pre-approval window; the clarification takes
# ``thread_token`` XOR ``anchor_line`` (§1n.3).
_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "register_project": ("repo_path",), "check_readiness": ("project_id",),
    "create_run": ("project_id", "journey", "version"),
    "submit_clarification": ("run_id", "doc", "body"),
    "edit_material": ("run_id", "doc", "content"),
    "record_stage_approval": ("run_id", "object", "expected_revision", "decision"),
    "record_release_decision": ("run_id", "action", "preview_digest"), "pause_run": ("run_id",),
    "resume_run": ("run_id",), "abandon_run": ("run_id", "reason"),
    # v0.11 (v0.9 §1b#11): confirm is conditional — the preflight demands
    # it only when the return crosses already-executed irreversible ops.
    "return_stage": ("run_id", "to", "reason"), "retry_run": ("run_id",), "drive_run": ("run_id",),
    "submit_intake": ("project_id", "source"), "decide_intake": ("intake_id", "action"),
    "dispose_backlog": ("project_id", "entry_id", "disposition"),
    "record_triage": ("run_id", "decision"), "record_review": ("run_id", "action"),
}


def _params_digest(params: dict) -> str:
    """Idempotency digest: sha256 over the canonical JSON of params (§1b.2)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_run_id(params: dict) -> str:
    """Stable run identity of a create_run acceptance (interfaces §1b#3, §2b#8)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return uuid.uuid5(uuid.NAMESPACE_OID, canonical).hex


def _permitted_repo_roots(config: Any) -> list[Path]:
    """The serve ``--repo`` permitted scope (interfaces §1g.2) from config."""
    if isinstance(config, dict):
        raw = config.get("permitted_repos")
    elif config is None:
        raw = None
    else:
        raw = getattr(config, "permitted_repos", None)
    if not raw:
        return []
    return [Path(root) for root in raw]


# -- web-gate accept-time binding (IF-WEBGATE-001 §1b.1) ----------------------
# Decisions bind their object at acceptance (SM-01.1): edit binds its base
# revision (FR-0294.3), approval the reviewed revision (FR-0308), release
# decision the live preview digest (FR-0309), retry the legal repair/escape
# position (FR-0311); shared validators, the service appends no events.

_WEBGATE_KINDS = frozenset(
    {"record_stage_approval", "edit_material", "record_release_decision", "retry_run"})

# deny(reason, detail) audits command.rejected and raises Rejection
# (SM-01.5): a binding failure leaves the command unpersisted/unexecuted.
RejectFn = Callable[..., None]


def _run_plane(repo_path: str, run_id: str) -> tuple[list[EventEnvelope], Any]:
    """(events, state) for a run, read from its tracks.db."""
    store = Store(paths.tracks_home(Path(repo_path)))
    try:
        return list(store.events(run_id)), store.state(run_id)
    finally:
        store.close()


def _current_revision(repo: Path, version: str | None) -> str | None:
    """Material trio digest the reviewer would have signed — the same content"""
    if not version:
        return None
    try:
        return revision_digest(paths.version_dir(paths.tracks_home(repo), version))
    except (OSError, UnicodeDecodeError):
        return None


# docs-centre revision identity (§1o.1a): six-piece set in canonical order
# plus the `design` alias; the trio subset is the approval-bound set.
_DOCS_MATERIALS = (("story", "story.md"), ("spec", "spec.md"), ("acc", "acceptance.md"),
    ("architecture", "architecture.md"), ("interfaces", "interfaces.md"),
    ("test_plan", "test-plan.md"),)
_DOCS_MATERIAL_FILES = dict(_DOCS_MATERIALS, design="architecture.md")
_TRIO_MATERIAL_FILES = {"story": "story.md", "spec": "spec.md", "acceptance": "acceptance.md"}


def _docs_piece(vdir: Path, name: str) -> str:
    doc = vdir / name
    if not doc.is_file():
        return "missing"
    return doc_body_sha(doc)


def docs_revision(vdir: Path) -> str:
    """Six-piece docs-centre revision identity (interfaces §1o.1a) §1n.1 FR-0308"""
    joined = "\n".join(f"{label}:{_docs_piece(vdir, name)}" for label, name in _DOCS_MATERIALS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _docs_revision_or_none(repo: Path, version: str | None) -> str | None:
    """docs_revision of a version dir §1n.1"""
    if not version:
        return None
    vdir = paths.version_dir(paths.tracks_home(repo), str(version))
    if not vdir.is_dir():
        return None
    try:
        return docs_revision(vdir)
    except (OSError, UnicodeDecodeError):
        return None


def _material_files(kind: str) -> dict[str, str]:
    return _DOCS_MATERIAL_FILES if kind == "docs" else _TRIO_MATERIAL_FILES


def _material_file(
    repo: Path, version: str | None, doc: Any, kind: str = "trio"
) -> Path | None:
    """The version-dir document a material edit targets, or None."""
    name = _material_files(kind).get(str(doc))
    if name is None or not version:
        return None
    return paths.version_dir(paths.tracks_home(repo), str(version)) / name


def _material_version(project: dict, state: Any) -> str | None:
    if state is not None and state.version:
        return str(state.version)
    version = project.get("version")
    return str(version) if version else None


def _write_material(doc_file: Path, content: str) -> None:
    """Atomic document write (tmp + replace): no torn file is ever visible."""
    tmp = doc_file.with_name(doc_file.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(doc_file)


def _commit_material(repo: Path, doc_file: Path, doc: str, actor: str) -> None:
    """Record the material revision in the run repo (interfaces §1b #5)."""
    try:
        relative = doc_file.relative_to(repo).as_posix()
    except ValueError:
        return
    added = subprocess.run(["git", "add", "--", relative], cwd=str(repo), capture_output=True,
        text=True, check=False,)
    if added.returncode != 0:
        return
    subprocess.run(
        ["git", "commit", "--only", "-m", f"material edit ({doc}) by {actor}", "--", relative],
        cwd=str(repo), capture_output=True, text=True, check=False,)


def _run_material_version(repo: Path, run_id: str, project: dict) -> str | None:
    _events, state = _run_plane(str(repo), run_id)
    return _material_version(project, state)


def _require_reviewed_revision(
    repo: Path, version: str | None, submitted: Any, deny: RejectFn, label: str,
    current_fn: Callable[..., str | None] = _current_revision,
) -> None:
    """Reject unless ``submitted`` is the live face revision token. FR-0308 SM-01.5 §1o.1b"""
    current = current_fn(repo, version)
    if current is None or submitted != current:
        deny("stale_revision",
            f"{label} rejected: the reviewed revision is stale; "
                f"current revision is {current or 'unavailable'}",)


def _bind_approval(
    repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict
) -> None:
    """FR-0308: the approval binds the revision the user actually reviewed."""
    version = _run_material_version(repo, run_id, project)
    _require_reviewed_revision(repo, version, request.get("expected_revision"), deny, "approval")


def _edit_revision_kind(request: dict, deny: RejectFn) -> str:
    """The closed ``revision_kind`` enum (interfaces §1o.1b); default trio."""
    kind = str(request.get("revision_kind") or "trio")
    if kind not in ("trio", "docs"):
        deny("validation_failed", f"unknown revision_kind {kind!r}; closed enum is trio|docs")
    return kind


def _bind_edit(repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict) -> None:
    """The edit binds its face's revision token (§1o.1b) SM-01.5 §1n.2"""
    kind = _edit_revision_kind(request, deny)
    version = _run_material_version(repo, run_id, project)
    doc_file = _material_file(repo, version, request.get("doc"), kind)
    if doc_file is None:
        deny("not_found", f"material {request.get('doc')!r} has no editable document")
    current_fn = _docs_revision_or_none if kind == "docs" else _current_revision
    if request.get("base_revision") is None:
        if doc_file.is_file():
            deny("stale_revision",
                "edit rejected: base_revision is null but the document already"
                    " exists and must be bound (the pre-approval window covers" " creation only)",)
        current = current_fn(repo, version)
        if current is not None:
            deny("stale_revision",
                f"edit rejected: base_revision is null but the face revision"
                    f" {current} is derivable and must be bound",)
        return  # creation window open: the apply step writes the document
    _require_reviewed_revision(repo, version, request.get("base_revision"), deny, "edit",
        current_fn)


def _resolve_anchor(text: str, anchor: Any) -> int | None:
    """§1n.3 dual anchor form"""
    if isinstance(anchor, bool):
        return None
    if isinstance(anchor, int):
        return anchor
    value = str(anchor).strip()
    if value.isdigit():
        return int(value)
    for idx, line in enumerate(text.splitlines(), start=1):
        if line.strip() == value:
            return idx
    return None


def _bind_clarification(request: dict, deny: RejectFn) -> None:
    """v0.11 §1n.3: the clarification takes ``thread_token`` XOR"""
    token = request.get("thread_token")
    anchor = request.get("anchor_line")
    if (token is None) == (anchor is None):
        deny("validation_failed",
            "submit_clarification takes thread_token XOR anchor_line:"
                " exactly one is required (interfaces §1n.3)",)


def _latest_preview(events: list[EventEnvelope]) -> EventEnvelope | None:
    return next((event for event in reversed(events) if event.type == "release.previewed"), None,)


def _deny_stale_preview(deny: RejectFn, detail: str) -> None:
    """Close the release decision path on a stale/unverifiable preview FR-0309"""
    deny("stale_preview", detail)


def _bind_release(
    repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict
) -> None:
    """FR-0309: the decision binds the live preview digest; stale rejects."""
    events, state = _run_plane(str(repo), run_id)
    version = _material_version(project, state) or ""
    preview_event = _latest_preview(events)
    if preview_event is None:
        _deny_stale_preview(deny,
            "release decision rejected: no release preview is bound to this run")
    preview = preview_event.payload if isinstance(preview_event.payload, dict) else {}
    if request.get("preview_digest") != preview.get("preview_digest"):
        _deny_stale_preview(deny,
            "release decision rejected: the submitted digest does not match "
                "the bound release preview",)
    try:
        authorization = assess_release(repo, paths.tracks_home(repo), events, preview_event,
            version)
    except (OSError, ValueError) as error:
        _deny_stale_preview(deny,
            f"release decision rejected: release preview could not be verified: {error}",)
    if authorization.preview_stale:
        _deny_stale_preview(deny,
            "release decision rejected: release preview is stale: "
                f"{authorization.stale_reason}",)
    ok, err = validate_authorization(str(request.get("action")), authorization, preview)
    if not ok:
        deny("validation_failed", f"release decision rejected: {err}")


def _bind_retry(
    repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict
) -> None:
    """FR-0311: retry only from the legal position (v0.8 repair/escape gate)."""
    del project  # the retry position is a run-plane fact only
    _events, state = _run_plane(str(repo), run_id)
    gate_error = _retry_gate_error(state, bool(request.get("clear_evidence")))
    if gate_error is not None:
        deny("validation_failed", f"retry rejected: {gate_error}")


_BINDINGS = {"record_stage_approval": _bind_approval, "edit_material": _bind_edit,
    "record_release_decision": _bind_release, "retry_run": _bind_retry,}


# Web resolve entry (IF-WEBAUTH-001 §1f.4): access.denied surface token.
_RESOLUTION_SURFACE = "resolve_thread"

# Root-comment blockquote line: ``> **Speaker [STATUS]:** body``.
_ROOT_COMMENT = re.compile(r"^\s*(>+)\s*(.*)$")


def _apply_resolved(text: str, thread: Thread) -> str:
    """Flip ``thread`` to resolved using the discuss canonical root format. §1f.4 FR-090"""
    lines = text.splitlines()
    tag = parse_tag(_ROOT_COMMENT.match(lines[thread.root_line - 1]).group(2))
    lines[thread.root_line - 1] = writer.format_root(thread.initiator, "resolved", tag[2])[0]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    deduplicated: bool
    status: str  # accepted | completed | rejected (persist-time outcomes)
    # v0.11 §1f.3: the submit_intake 202 payload carries the created
    # intake_id alongside the command_id; None for every other kind.
    intake_id: str | None = None
    # v0.11 §2b #40: decide_intake's synchronous outcome — the
    # develop-over-active-run terminal answers 200 {outcome: backlog_recorded}.
    intake_outcome: dict | None = None


@dataclass(frozen=True)
class Rejection(Exception):
    reason: str  # closed set, interfaces §1a #8
    detail: str
    # RULING seq 10924: optional EXPLICIT HTTP status — the missing
    # Idempotency-Key refusal is 400 on every kind, preferred by the HTTP
    # accept path over the per-endpoint reason map (None falls back).
    status: int | None = None


class CommandService:
    """Accept/persist/dedup/query commands; execution via workers (§1b)."""

    def __init__(self, home: Path, db: Any, config: Any, scheduler: Any = None) -> None:
        self.home = Path(home)
        self._db = db
        self.config = config
        self._scheduler = scheduler
        self._fallback: ServiceDB | None = None

    def _schedule(self) -> Any:
        """The schedule collaborator (§1i), built lazily over this service."""
        if self._scheduler is None:
            from tracks.supervisor.scheduler import Scheduler  # cycle-safe import

            self._scheduler = Scheduler(self._store(), service=self)
        return self._scheduler

    def _store(self) -> ServiceDB:
        """Injected store when it is a ServiceDB, else the home-backed one. SM-01.1"""
        if isinstance(self._db, ServiceDB):
            return self._db
        if self._fallback is None:
            self._fallback = ServiceDB(self.home)
        return self._fallback

    def _deny(
        self,
        store: ServiceDB, kind: str, request: dict, actor: str, actor_class: str, reason: str,
        detail: str,
        *,
        access_denied: bool, status: int | None = None,
    ) -> None:
        """Persist the rejection audit and raise (SM-01.5"""
        store.append_event("command.rejected",
            {
                "kind": kind, "reason": reason, "detail": detail, "actor": actor,
                "actor_class": actor_class,
            },
            project_id=request.get("project_id"), run_id=request.get("run_id"),)
        if access_denied:
            store.append_event("access.denied",
                { "surface": kind, "reason": reason, "actor": actor, "actor_class": actor_class, },
                project_id=request.get("project_id"), run_id=request.get("run_id"),)
        raise Rejection(reason=reason, detail=detail, status=status)

    def _check_duplicate(
        self, store: ServiceDB, request: dict, idempotency_key: str, digest: str
    ) -> CommandReceipt | None:
        """Idempotency lookup (§1b.2): dedup receipt, conflict, or None."""
        existing = store.find_by_idempotency(idempotency_key)
        if existing is None:
            return None
        if existing.get("params_digest") == digest:
            original = existing["command_id"]
            store.append_event("command.deduplicated",
                {
                    "idempotency_key": idempotency_key, "original_command_id": original,
                    "command_id": original,
                },
                project_id=request.get("project_id"), run_id=request.get("run_id"),
                command_id=original,)
            return CommandReceipt(command_id=original, deduplicated=True,
                status=existing.get("status") or "accepted",)
        return "conflict"

    def _preflight_register_project(
        self, store: ServiceDB, request: dict, actor: str
    ) -> None:
        """FR-0289 §1g.2: admit registration only in the serve scope. IF-CMDGUARD-001"""
        roots = _permitted_repo_roots(self.config) or [self.home.parent]
        from tracks.server.guard import (  # cycle-safe: guard imports this module
            GuardRejection,
            check_registration_scope,
        )

        repo_path = str(request.get("repo_path"))
        try:
            check_registration_scope(Path(repo_path), roots)
        except GuardRejection as rejection:
            store.append_event("project.registration_rejected",
                {"repo_path": repo_path, "reason": rejection.reason, "actor": actor},)
            raise Rejection(reason=rejection.reason, detail=rejection.detail) from None

    @staticmethod
    def _readiness_failed(store: ServiceDB, project_id: str | None) -> bool:
        """True only on explicit-negative readiness (absent state is the"""
        if project_id is None:
            return False
        failed = False
        for event in store.read_events():
            if event.get("type") != "project.readiness_checked":
                continue
            payload = event.get("payload") or {}
            if event.get("project_id") != project_id and payload.get("project_id") != project_id:
                continue
            failed = payload.get("ok", True) is False
        return failed

    def _preflight_create_run(
        self,
        store: ServiceDB, request: dict, actor: str, actor_class: str,
    ) -> None:
        """create_run business preflight (§1b.1) FR-0292"""
        schedule = store.get_schedule()
        active = schedule.get("active_run") if schedule is not None else None
        # Re-accepting the same run (same create_run_id) is not a second
        # active run — the schedule no-ops it (IF-SCHED-001).
        if active and not request.get("preempt") and active != create_run_id(request):
            self._deny(store, "create_run", request, actor, actor_class, "active_run_exists",
                f"active run {active!r} exists and preempt is false", access_denied=False,)
        if self._readiness_failed(store, request.get("project_id")):
            self._deny(store, "create_run", request, actor, actor_class, "validation_failed",
                "project readiness checks are failing; resolve them before creating a run",
                access_denied=False,)
        self._preflight_hotfix_journey(store, request, actor, actor_class)

    def _deny_hotfix_precheck(
        self,
        store: ServiceDB, request: dict, actor: str, actor_class: str, detail: str,
        *,
        status: int | None = None,
    ) -> None:
        """FR-0292 rejection: the precheck's concrete reason rides the detail §1a#8 SM-01.5"""
        self._deny(store, "create_run", request, actor, actor_class, "validation_failed",
            f"hotfix precheck rejected: {detail}", access_denied=False, status=status,)

    def _preflight_hotfix_journey(
        self, store: ServiceDB, request: dict, actor: str, actor_class: str
    ) -> None:
        """FR-0292 / AC-FR0292-02 §1b#3 §1i.2"""
        scenario = _HOTFIX_SCENARIOS.get(str(request.get("journey") or ""))
        if scenario is None:
            return
        project = store.get_project(request.get("project_id"))
        if project is None:
            self._deny_hotfix_precheck(store, request, actor, actor_class,
                f"project {request.get('project_id')!r} is not registered on this service",)
        repo = Path(str(project["repo_path"]))
        try:
            report = _hotfix_precheck(repo, _issue_number(request.get("issue")), scenario,)
        except (OSError, sqlite3.Error, ValueError) as error:
            self._deny_hotfix_precheck(store, request, actor, actor_class,
                f"preconditions could not be verified: {error}",)
            return
        if report.status == "rejected":
            self._deny_hotfix_precheck(store, request, actor, actor_class,
                f"{report.reason}: {report.next}",)
        self._deny_hotfix_unanchored(store, request, actor, actor_class, repo, report)

    def _deny_hotfix_unanchored(
        self,
        store: ServiceDB, request: dict, actor: str, actor_class: str, repo: Path, report: Any,
    ) -> None:
        """T-008 (M2/B): the acceptance-time anchoring belt — a passing SM-01.5"""
        target = getattr(report, "target_version", None)
        vdir = (paths.version_dir(paths.tracks_home(repo), str(target)) if target else None)
        missing = [
            name for name in (
                "story.md", "spec.md", "acceptance.md"
            ) if vdir is None or not (vdir / name).is_file()]
        if not missing:
            return
        self._deny_hotfix_precheck(store, request, actor, actor_class,
                "hotfix anchoring unavailable: target version"
                    f" {target if target else '<none>'} baseline is missing"
                    f" {', '.join(missing)}; approve/complete the target version"
                " baseline first (interfaces §1i.2)",
            status=422,)

    def _reconcile_terminal_drive(
        self, store: ServiceDB, request: dict, deny: RejectFn
    ) -> None:
        """IF-SCHED-001: the acceptance face releases the single active slot"""
        run_id = request.get("run_id")
        schedule = store.get_schedule()
        if not run_id or schedule is None or schedule.get("active_run") != run_id:
            return
        state = self._run_state(store, run_id)
        if state is None or (state.status != "completed" and state.terminal_state is None):
            return
        terminal = state.terminal_state or state.status
        self._schedule().on_run_terminal(str(run_id))
        deny("validation_failed",
            f"run {run_id!r} already reached terminal state {terminal!r}; "
                "the schedule released its active slot",)

    def _preflight_webgate(
        self,
        store: ServiceDB, kind: str, request: dict, deny: RejectFn,
    ) -> None:
        """Accept-time web-gate binding (IF-WEBGATE-001 §1b.1) SM-01.5"""
        if kind not in _WEBGATE_KINDS:
            return
        run_id = request.get("run_id")
        project = self._project_for_run(store, run_id)
        if project is None:
            deny("not_found", f"run {run_id!r} is not registered on this service")
        _BINDINGS[kind](Path(str(project["repo_path"])), str(run_id), request, deny, project)

    @staticmethod
    def _project_for_run(store: ServiceDB, run_id: Any) -> dict | None:
        """The registered project owning run_id through the shared three-step §1c"""
        if not run_id:
            return None
        return store.get_project(store.find_run_project_id(str(run_id)))

    def _record_acceptance(
        self,
        store: ServiceDB, kind: str, request: dict, actor: str, actor_class: str, surface: str,
        idempotency_key: str, digest: str, command_id: str,
        *,
        run_id: str | None = None, extras: dict[str, Any] | None = None,
    ) -> None:
        """Persist command.accepted plus the service-produced kind event."""
        project_id = request.get("project_id")
        run_id = run_id if run_id is not None else request.get("run_id")
        store.append_event("command.accepted",
            {
                "command_id": command_id, "kind": kind, "idempotency_key": idempotency_key,
                "params_digest": digest, "actor": actor, "actor_class": actor_class,
                "surface": surface, "project_id": project_id, "run_id": run_id,
            },
            project_id=project_id, run_id=run_id, command_id=command_id,)
        if kind == "pause_run":
            store.append_event("run.pause_requested",
                {"run_id": run_id, "actor": actor, "command_id": command_id}, run_id=run_id,
                command_id=command_id,)
        elif kind == "resume_run":
            store.append_event("run.resumed",
                {
                    "run_id": run_id, "actor": actor, "command_id": command_id,
                    "from_stage": self._resume_stage(store, run_id),
                },
                run_id=run_id, command_id=command_id,)
        elif kind == "edit_material":
            self._apply_edit(store, request, actor, surface, command_id)
        elif kind == "submit_clarification":
            self._apply_clarification(store, request, actor, command_id)
        elif kind == "submit_intake":
            row = _apply_intake_submitted(store, request, actor, command_id)
            if extras is not None:
                extras["intake_id"] = row.get("intake_id")
        elif kind == "decide_intake":
            outcome = self._apply_intake_decided(store, request, actor, command_id)
            if extras is not None and isinstance(outcome, dict):
                extras["intake_outcome"] = outcome
        elif kind == "dispose_backlog":
            _apply_backlog_disposed(store, request, actor, command_id)
        elif kind == "return_stage":
            self._apply_return_stage(store, request, command_id)

    def _apply_edit(
        self, store: ServiceDB, request: dict, actor: str, surface: str, command_id: str
    ) -> None:
        """Apply an accepted material edit via the revision flow. SM-01.1 §1o.1b §1n.2"""
        run_id = request.get("run_id")
        project = self._project_for_run(store, run_id)
        if project is None:
            return
        repo = Path(str(project["repo_path"]))
        version = _material_version(project, self._run_state(store, run_id))
        kind = str(request.get("revision_kind") or "trio")
        doc_file = _material_file(repo, version, request.get("doc"), kind)
        if doc_file is None:
            return
        revision_of = _docs_revision_or_none if kind == "docs" else _current_revision
        from_revision = revision_of(repo, version) or request.get("base_revision")
        _write_material(doc_file, str(request.get("content") or ""))
        # v0.11 §1n.2: a pre-approval save (null base, token structurally
        # unavailable) still records a resolvable to_revision — the
        # globalized docs_revision of the saved state.
        to_revision = revision_of(repo, version) or _docs_revision_or_none(repo, version)
        _commit_material(repo, doc_file, str(request.get("doc") or ""), actor)
        store.append_event("material.edited",
            {
                "run_id": run_id, "doc": request.get("doc"), "from_revision": from_revision,
                "to_revision": to_revision, "actor": actor, "surface": surface,
            },
            run_id=run_id, command_id=command_id,)

    def _apply_clarification(
        self, store: ServiceDB, request: dict, actor: str, command_id: str
    ) -> None:
        """v0.11 §1n.3 / §1b short-write #4"""
        run_id = request.get("run_id")
        project = self._project_for_run(store, run_id)
        if project is None:
            return
        repo = Path(str(project["repo_path"]))
        version = _material_version(project, self._run_state(store, run_id))
        doc_file = _material_file(repo, version, request.get("doc"), "docs")
        if doc_file is None or not doc_file.is_file():
            return
        body = str(request.get("body") or "")
        token = request.get("thread_token")
        anchor = request.get("anchor_line")

        def transform(text: str) -> str:
            if token is not None:
                thread = next((t for t in parse_threads(text) if t.thread_id == str(token)),None)
                if thread is None:
                    return text  # v0.9 reply semantics: deferred, never a refusal
                return writer.reply(text, str(token), token_for(thread), actor, body)
            line_no = _resolve_anchor(text, anchor)
            if line_no is None:
                return text  # unreachable: the binding refused unresolvable text
            return writer.start(text, line_no, actor, body)

        self._locked_write(doc_file, transform)

    def _bind_anchor_landing(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """The anchor_line form's landing promise (§1n.3, PLANNING revision AC-FR0344-02"""
        anchor = request.get("anchor_line")
        if anchor is None or isinstance(anchor, (int, bool)):
            return  # the numeric form is bounds-tolerated by the writer
        if str(anchor).strip().isdigit():
            return
        project = self._project_for_run(store, request.get("run_id"))
        if project is None:
            return
        repo = Path(str(project["repo_path"]))
        version = _material_version(project, self._run_state(store, request.get("run_id")))
        doc_file = _material_file(repo, version, request.get("doc"), "docs")
        if doc_file is None or not doc_file.is_file():
            return
        if _resolve_anchor(doc_file.read_text(encoding="utf-8"), anchor) is None:
            deny("validation_failed",
                    f"anchor_line {str(anchor)[:80]!r} matches no line of"
                        f" {request.get('doc')!r}; the inline comment must anchor on an"
                    " existing line (interfaces §1n.3)",)

    def _preflight_submit_intake(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #14: the unified-entry admission — registered project, closed IF-HOTFIX-011"""
        source = str(request.get("source") or "")
        if source not in _INTAKE_SOURCES:
            deny("validation_failed",
                f"unknown source {source!r}; closed set is text|issue|backlog",)
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
                f"issue {number} could not be fetched ({classification}): {exc}",)
        if issue is None:
            deny("issue_fetch_failed",
                f"issue {number} could not be fetched (not_found):"
                    " the host confirms no such issue",)
        return issue

    def _preflight_decide_intake(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #15: the decision admission — evaluated-only readiness, SM-01.5"""
        action = str(request.get("action") or "")
        if action not in _INTAKE_ACTIONS:
            deny("validation_failed",
                f"unknown action {action!r}; closed set is backlog|archive|remove_backlog|develop",)
        intake = store.get_intake(request.get("intake_id"))
        if intake is None or intake.get("status") != "evaluated":
            deny("intake_not_ready",
                f"intake {request.get('intake_id')!r} is not in the evaluated state",)
        if action == "remove_backlog" and str(intake.get("source") or "") != "backlog":
            deny("validation_failed",
                "remove_backlog is only legal for a backlog-sourced intake (SM-01.5)",)
        if action == "develop":
            _preflight_develop_gates(store, intake, request, deny)

    def _preflight_dispose_backlog(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """§1b #16: the entry must exist and be undisposed; a promotion needs"""
        disposition = str(request.get("disposition") or "")
        if disposition not in _BACKLOG_DISPOSITIONS:
            deny("validation_failed",
                f"unknown disposition {disposition!r}; closed set is removed|promoted",)
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

    def _preflight_gate_command(
        self, store: ServiceDB, kind: str, request: dict, deny: RejectFn
    ) -> None:
        """§1b #17/#18 awaiting preflight"""
        values = _GATE_AWAITING[kind]
        field = "decision" if kind == "record_triage" else "action"
        allowed = _TRIAGE_DECISIONS if kind == "record_triage" else _REVIEW_ACTIONS
        value = str(request.get(field) or "")
        if value not in allowed:
            deny("validation_failed",
                f"unknown {field} {value!r}; closed set is {'|'.join(sorted(allowed))}",)
        project = self._project_for_run(store, request.get("run_id"))
        if project is None:
            deny("not_found", f"run {request.get('run_id')!r} is not registered on this service")
        state = self._run_state(store, request.get("run_id"))
        if state is None or state.awaiting not in values:
            deny("awaiting_mismatch",
                f"run {request.get('run_id')!r} is not awaiting {'|'.join(sorted(values))}",)

    def _preflight_return_stage(self, store: ServiceDB, request: dict, deny: RejectFn) -> None:
        """v0.9 §1b#11 as revised by v0.11 PLANNING round 1 IF-RETURN-001"""
        project = self._project_for_run(store, request.get("run_id"))
        if project is None:
            deny("not_found", f"run {request.get('run_id')!r} is not registered on this service")
        repo = Path(str(project["repo_path"]))
        run_id = str(request.get("run_id"))
        run_store = Store(paths.tracks_home(repo))
        try:
            _state, allowed, err = _return_gate(run_store, run_id)
            if err:
                deny("validation_failed", f"return gate refused: {err}")
            target = str(request.get("to") or "")
            if target not in allowed:
                deny("validation_failed",
                    f"invalid to {target!r}: must be one of {'|'.join(allowed)}",)
            executed = _already_executed_ops(run_store, run_id)
            if executed and not request.get("confirm"):
                summary = "; ".join(
                    f"{op['operation_kind']} {op['target']} ({op['type']})" for op in executed)
                deny("validation_failed",
                    "already_executed operations this return would cross"
                        f" (re-submit with confirm=true): {summary}",)
        finally:
            run_store.close()

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
                }, entry,)
            outcome = {"backlog_entry_id": entry}
        elif action == "remove_backlog" and repo is not None:
            entry = str(intake.get("origin_entry") or "")
            _append_backlog_event(repo, str(project.get("version") or "v0.1"),
                "backlog.dispositioned", {"entry": entry, "disposition": "removed", "to": None},
                new_ulid(),)
            outcome = {"backlog_entry_id": entry, "disposition": "removed"}
        elif action == "develop":
            outcome = self._apply_develop(store, intake, project, actor)
        else:
            outcome = "archive"
        store.update_intake(str(intake.get("intake_id")), status="decided", decision=action,
            decided_at=_now_iso(),)
        store.append_event("intake.decided",
            {
                "intake_id": intake.get("intake_id"), "action": action, "outcome": outcome,
                "actor": actor, "command_id": command_id,
            },
            project_id=intake.get("project_id"), command_id=command_id,)
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
                active,)
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
            idempotency_key=f"intake-develop-{intake.get('intake_id')}",)
        # T-008 (AC-FR0338/FR-0339, §1f.3/§1d.3): a successful develop
        # promotes the origin entry — the SAME backlog.dispositioned event
        # dispose_backlog lands (合同唯一); a denied create_run raises above,
        # so no half-promoted residue (single-disposition invariant).
        origin_entry = str(intake.get("origin_entry") or "")
        if str(intake.get("source") or "") == "backlog" and origin_entry:
            _append_backlog_event(repo, str(project.get("version") or "v0.1"),
                "backlog.dispositioned",
                { "entry": origin_entry, "disposition": "promoted", "to": params["version"], },
                new_ulid(),)
        return {"outcome": "develop", "run_id": create_run_id(params), "version": params["version"],
            "branch": f"releases/{params['version']}",}

    def _apply_return_stage(self, store: ServiceDB, request: dict, command_id: str) -> None:
        """v0.11 PLANNING round 1 IF-RETURN-001 §1a"""
        project = self._project_for_run(store, request.get("run_id"))
        if project is None:
            return
        repo = Path(str(project["repo_path"]))
        run_id = str(request.get("run_id"))
        target = str(request.get("to") or "")
        reason = str(request.get("reason") or "")
        run_store = Store(paths.tracks_home(repo))
        try:
            state = run_store.state(run_id)
            version = str(state.version or project.get("version") or "")
            _establish_escape_barrier(run_store, run_id, version)
            _stale_downstream_evidence(run_store, run_id, version, target)
            run_store.append(
                run_id, version, "human.return",
                {
                    "actor": "Human", "from": state.stage, "to": target,
                    "to_stage": target,  # the kernel hard-read key
                    "reason": reason,
                },
            )
            run_store.append(run_id, version, "stage.rolled_back",
                {
                    "from_stage": state.stage, "to_stage": target, "reason": "human_return",
                    "quarantined": [],
                },)
        finally:
            run_store.close()

    def _resume_stage(self, store: ServiceDB, run_id: Any) -> str | None:
        state = self._run_state(store, run_id)
        return state.stage if state is not None else None

    def _run_state(self, store: ServiceDB, run_id: Any) -> Any | None:
        """Projected State of run_id from its registered project's run plane."""
        if not run_id:
            return None
        project = self._project_for_run(store, run_id)
        if project is None:
            return None
        try:
            _events, state = _run_plane(str(project["repo_path"]), str(run_id))
        except (OSError, sqlite3.Error, ValueError):
            return None
        return state

    def accept(
        self,
        kind: str, params: dict,
        *,
        actor: str, actor_class: str, surface: str, idempotency_key: str | None,
    ) -> CommandReceipt:
        """SM-01 accept path: validate -> dedup -> persist -> receipt. SM-01.5"""
        request = dict(params or {})
        store = self._store()
        deny = (  # noqa: E731
            lambda reason, detail, *, access_denied=False, status=None: self._deny(
                store, kind, request, actor, actor_class, reason, detail,
                access_denied=access_denied, status=status,
            )
        )
        self._check_actor_class(kind, actor_class, deny)
        if kind not in SERVICE_COMMAND_KINDS:
            deny("guard_blocked", f"unknown command kind {kind!r}")
        self._require_params(kind, request, deny)
        if kind == "create_run" and request.get("journey") not in _CREATE_RUN_JOURNEYS:
            deny("validation_failed", f"unknown journey {request.get('journey')!r}")
        if idempotency_key is None:
            if surface == "http":
                # RULING R1 (seq 10924): the missing-credential refusal is 400
                # on EVERY kind — the explicit status outranks the per-endpoint
                # shape overrides (422) here and in the HTTP accept path.
                deny("validation_failed", "idempotency key required for HTTP changes", status=400,)
            idempotency_key = uuid.uuid4().hex
        digest = _params_digest(request)
        extras: dict[str, Any] = {}
        duplicate = self._check_duplicate(store, request, idempotency_key, digest)
        if duplicate == "conflict":
            deny("idempotency_conflict",
                f"idempotency key {idempotency_key!r} was used with different params",)
        if duplicate is not None:
            return duplicate
        self._preflights(store, kind, request, actor, actor_class, deny)
        command_id = uuid.uuid4().hex
        recorded_run_id = self._register_accepted(store, kind, request, command_id, idempotency_key,
            digest, actor, actor_class, surface,)
        self._record_acceptance(store, kind, request, actor, actor_class, surface, idempotency_key,
            digest, command_id, run_id=recorded_run_id, extras=extras,)
        self._on_accepted(store, kind, request, actor)
        return CommandReceipt(command_id=command_id, deduplicated=False, status="accepted",
            intake_id=extras.get("intake_id"), intake_outcome=extras.get("intake_outcome"),)

    def _require_params(self, kind: str, request: dict, deny: RejectFn) -> None:
        missing = [key for key in _REQUIRED_PARAMS[kind] if key not in request]
        if missing:
            deny("validation_failed", f"missing params: {', '.join(missing)}")

    def _register_accepted(
        self,
        store: ServiceDB, kind: str, request: dict, command_id: str, idempotency_key: str,
        digest: str, actor: str, actor_class: str, surface: str,
    ) -> Any:
        """Persist the accepted command row IF-DOCREV-001"""
        recorded_run_id = (
            create_run_id(request) if kind == "create_run" else request.get("run_id"))
        store.register_command(
            {
                "command_id": command_id, "kind": kind,
                "params_json": json.dumps(request, sort_keys=True), "params_digest": digest,
                "idempotency_key": idempotency_key, "actor": actor, "actor_class": actor_class,
                "surface": surface, "project_id": request.get("project_id"),
                "run_id": recorded_run_id,
            })
        return recorded_run_id

    def _check_actor_class(self, kind: str, actor_class: str, deny: RejectFn) -> None:
        """Actor-class partition (interfaces §1f.5)"""
        if kind in HUMAN_ONLY_KINDS and actor_class != "human":
            deny("forbidden_actor", f"kind {kind!r} requires actor_class 'human'",
                access_denied=True,)
        if kind in SYSTEM_ONLY_KINDS and actor_class != "system":
            deny("forbidden_actor", f"kind {kind!r} requires actor_class 'system'",
                access_denied=True,)

    def _preflights(
        self,
        store: ServiceDB, kind: str, request: dict, actor: str, actor_class: str, deny: RejectFn,
    ) -> None:
        """Business preflights ahead of persistence (§1b.1)"""
        if kind == "register_project":
            self._preflight_register_project(store, request, actor)
        elif kind == "create_run":
            self._preflight_create_run(store, request, actor, actor_class)
        elif kind == "drive_run":
            self._reconcile_terminal_drive(store, request, deny)
        elif kind == "submit_intake":
            self._preflight_submit_intake(store, request, deny)
        elif kind == "decide_intake":
            self._preflight_decide_intake(store, request, deny)
        elif kind == "dispose_backlog":
            self._preflight_dispose_backlog(store, request, deny)
        elif kind in _GATE_AWAITING:
            self._preflight_gate_command(store, kind, request, deny)
        elif kind == "submit_clarification":
            _bind_clarification(request, deny)
            self._bind_anchor_landing(store, request, deny)
        elif kind == "return_stage":
            self._preflight_return_stage(store, request, deny)
        self._preflight_webgate(store, kind, request, deny)

    def _on_accepted(self, store: ServiceDB, kind: str, request: dict, actor: str) -> None:
        """Kind-specific side effects after persistence (SM-01 persist-first). IF-SCHED-001"""
        if kind == "create_run":
            self._schedule().on_run_created(
                create_run_id(request),
                journey=str(request.get("journey")),
                preempt=bool(request.get("preempt")),
                actor=actor,
            )

    def status(self, command_id: str) -> dict | None:
        """Persisted command status/result; survives restarts (§1b)."""
        row = self._store().get_command(command_id)
        if row is None:
            return None
        outcome = None
        if row.get("result_json"):
            with contextlib.suppress(ValueError, TypeError):
                outcome = json.loads(row["result_json"])
        result = {"command_id": row["command_id"], "kind": row["kind"], "status": row["status"],
            "actor": row.get("actor"), "actor_class": row.get("actor_class"),
            "surface": row.get("surface"), "project_id": row.get("project_id"),
            "run_id": row.get("run_id"),}
        if row["status"] == "failed":
            result["failure"] = outcome
        elif row["status"] == "completed":
            result["result"] = outcome
        return result

    def resolve_discussion_thread(
        self, doc_path: Path, thread_token: str, *, actor: str, actor_class: str
    ) -> None:
        """set-status resolved honoring adjudication ownership (§1f.4). FR-090"""
        self._locked_write(doc_path,
            lambda text: self._resolve_text(text, thread_token, actor, actor_class))

    def _resolve_text(self, text: str, thread_token: str, actor: str, actor_class: str) -> str:
        """Adjudication-aware ``resolved`` transform (fail closed)."""
        owner = adjudication_owner(text, thread_token)
        if owner is not None and speaker_key(owner) != speaker_key(actor):
            self._deny_resolution(actor, actor_class,
                f"thread {thread_token!r} requests adjudication by {owner!r}")
        thread = next((t for t in parse_threads(text) if t.thread_id == thread_token), None)
        if thread is None:
            raise Rejection(reason="not_found",
                detail=f"thread {thread_token!r} not found in this document")
        if owner is not None:
            return _apply_resolved(text, thread)
        try:
            return writer.set_status(text, thread_token, token_for(thread), "resolved", actor)
        except writer.WriteError as exc:
            self._deny_resolution(actor, actor_class, str(exc))

    def _deny_resolution(self, actor: str, actor_class: str, detail: str) -> None:
        """Persist the denial audit and raise (status unchanged, §1f.4)."""
        self._store().append_event(
            "access.denied",
            {
                "surface": _RESOLUTION_SURFACE, "reason": "forbidden_actor", "actor": actor,
                "actor_class": actor_class,
            },
        )
        raise Rejection(reason="forbidden_actor", detail=detail) from None

    def _locked_write(self, doc_path: Path, transform) -> None:
        """flock-serialized read-modify-write (FR-110 parity with the CLI)."""
        if not doc_path.exists():
            raise Rejection(reason="not_found",detail=f"document not found: {doc_path.name}")
        lock_path = doc_path.with_name(doc_path.name + ".lock")
        with open(lock_path, "w", encoding="utf-8") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                text = doc_path.read_text(encoding="utf-8")
                tmp = doc_path.with_name(doc_path.name + ".tmp")
                tmp.write_text(transform(text), encoding="utf-8")
                tmp.replace(doc_path)
            finally:
                fcntl.flock(lockf, fcntl.LOCK_UN)
