"""Persistent command service shared by CLI and Web (IF-CMDSVC-001).

Single accept path for the closed ServiceCommandKind set (interfaces §1b):
authenticate/authorize -> guard validation -> idempotency dedup -> persist
(SM-01.1) -> hand to a supervisor worker (HTTP) or execute inline (CLI).
Business validations reuse the existing gate semantics (cmd_approve /
release_gate / escape / repair) — this service never appends domain events
directly and never bypasses revision/preview binding.

Contract tokens: IF-CMDSVC-001, IF-WEBGATE-001, IF-WEBAUTH-001, IF-PAUSE-001,
IF-SCHED-001.
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
from tracks.cli.gate_cmd import _retry_gate_error
from tracks.discuss import writer
from tracks.discuss.gate import adjudication_owner
from tracks.discuss.locate import token_for
from tracks.discuss.model import Thread, speaker_key
from tracks.discuss.parser import parse_tag, parse_threads
from tracks.executor.release_authorization import assess_release, validate_authorization
from tracks.kernel.events import EventEnvelope
from tracks.store import Store
from tracks.supervisor.db import ServiceDB

# Closed command-kind set (interfaces §1b).
SERVICE_COMMAND_KINDS = (
    "register_project",
    "check_readiness",
    "create_run",
    "submit_clarification",
    "edit_material",
    "record_stage_approval",
    "record_release_decision",
    "pause_run",
    "resume_run",
    "abandon_run",
    "return_stage",
    "retry_run",
    "drive_run",
)

# actor_class partition (interfaces §1f.5).
HUMAN_ONLY_KINDS = frozenset(k for k in SERVICE_COMMAND_KINDS if k != "drive_run")
SYSTEM_ONLY_KINDS = frozenset({"drive_run"})

# create_run journey enum (interfaces §1b #3).
_CREATE_RUN_JOURNEYS = frozenset({"feature", "hotfix_post", "hotfix_dev"})

# Required params per kind (interfaces §1b params column; optional keys such
# as issue/target/reason?/confirm may be absent or null).
_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "register_project": ("repo_path",),
    "check_readiness": ("project_id",),
    "create_run": ("project_id", "journey", "version"),
    "submit_clarification": ("run_id", "doc", "thread_token", "body"),
    "edit_material": ("run_id", "doc", "base_revision", "content"),
    "record_stage_approval": ("run_id", "object", "expected_revision", "decision"),
    "record_release_decision": ("run_id", "action", "preview_digest"),
    "pause_run": ("run_id",),
    "resume_run": ("run_id",),
    "abandon_run": ("run_id", "reason"),
    "return_stage": ("run_id", "to", "reason", "confirm"),
    "retry_run": ("run_id",),
    "drive_run": ("run_id",),
}


def _params_digest(params: dict) -> str:
    """Idempotency digest: sha256 over the canonical JSON of params (§1b.2)."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_run_id(params: dict) -> str:
    """Stable run identity of a create_run acceptance (interfaces §1b#3, §2b#8).

    The HTTP outlet derives the 202 ``run_id`` from the same canonical
    params, so the schedule names the identical run that acceptance
    announced; a repeated acceptance of the same identity is not a second
    active run.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return uuid.uuid5(uuid.NAMESPACE_OID, canonical).hex


# -- web-gate accept-time binding (IF-WEBGATE-001 §1b.1) ----------------------
#
# The web human decisions bind the object they were made against at
# *acceptance* time (SM-01.1 persist-then-execute): an approval binds the
# revision the user actually reviewed (FR-0308), a release decision binds the
# live preview digest (FR-0309), and a controlled retry only accepts from the
# legal v0.8 repair/escape position (FR-0311). The binding reuses the existing
# shared validators — the service never appends domain events itself.

_WEBGATE_KINDS = frozenset(
    {"record_stage_approval", "edit_material", "record_release_decision", "retry_run"}
)

# The accept-tier deny closure: ``deny(reason, detail)`` audits a
# ``command.rejected`` and raises ``Rejection`` (SM-01.5), so every binding
# failure leaves the command unpersisted and unexecuted.
RejectFn = Callable[..., None]

_RUNS_BY_ID = "SELECT 1 FROM runs WHERE run_id = ? LIMIT 1"
_EVENTS_BY_RUN = "SELECT 1 FROM events WHERE run_id = ? LIMIT 1"


def _run_exists(repo_path: str, run_id: str) -> bool:
    """True when the repo's tracks.db already holds run_id (realpath read)."""
    db_path = paths.db_path(paths.tracks_home(Path(repo_path)))
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        for query in (_RUNS_BY_ID, _EVENTS_BY_RUN):
            try:
                if conn.execute(query, (run_id,)).fetchone() is not None:
                    return True
            except sqlite3.Error:
                continue
        return False
    finally:
        conn.close()


def _run_plane(repo_path: str, run_id: str) -> tuple[list[EventEnvelope], Any]:
    """(events, state) for a run, read from its tracks.db."""
    store = Store(paths.tracks_home(Path(repo_path)))
    try:
        return list(store.events(run_id)), store.state(run_id)
    finally:
        store.close()


def _current_revision(repo: Path, version: str | None) -> str | None:
    """Material trio digest the reviewer would have signed — the same content
    addressing ``cmd_approve``/``baseline.revision_digest`` uses. None when the
    version docs are absent or unreadable (fail closed at the binding)."""
    if not version:
        return None
    try:
        return revision_digest(paths.version_dir(paths.tracks_home(repo), version))
    except (OSError, UnicodeDecodeError):
        return None


# Material document set of the review face (interfaces §2b #15): the trio
# members carry the revision digest an approval binds; the design entry is the
# primary design document.
_MATERIAL_FILES = {
    "story": "story.md",
    "spec": "spec.md",
    "acceptance": "acceptance.md",
    "design": "architecture.md",
}


def _material_file(repo: Path, version: str | None, doc: Any) -> Path | None:
    """The version-dir document a material edit targets, or None."""
    name = _MATERIAL_FILES.get(str(doc))
    if name is None or not version:
        return None
    return paths.version_dir(paths.tracks_home(repo), str(version)) / name


def _material_version(project: dict, state: Any) -> str | None:
    """Version dir of a run: projected run version, then the registered
    project's version (the doc read path addresses the same directory)."""
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
    """Record the material revision in the run repo (interfaces §1b #5).

    Best-effort by design: the revision identity is content-addressed and
    already on disk, so a repo without a git identity/repository must not
    undo an accepted edit; when the repo can commit, the edited document
    joins the run's revision history like the Runtime's own doc commits.
    """
    try:
        relative = doc_file.relative_to(repo).as_posix()
    except ValueError:
        return
    added = subprocess.run(
        ["git", "add", "--", relative],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if added.returncode != 0:
        return
    subprocess.run(
        ["git", "commit", "--only", "-m", f"material edit ({doc}) by {actor}", "--", relative],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def _bind_approval(
    repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict
) -> None:
    """FR-0308: the approval binds the revision the user actually reviewed."""
    _events, state = _run_plane(str(repo), run_id)
    current = _current_revision(repo, _material_version(project, state))
    if current is None or request.get("expected_revision") != current:
        deny(
            "stale_revision",
            "approval rejected: the reviewed revision is stale; "
            f"current revision is {current or 'unavailable'}",
        )


def _bind_edit(repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict) -> None:
    """FR-0294.3: the edit binds the revision the user actually reviewed.

    A stale base revision is rejected before anything persists (SM-01.5);
    the acceptance then applies the edit through the existing revision flow.
    """
    _events, state = _run_plane(str(repo), run_id)
    version = _material_version(project, state)
    doc_file = _material_file(repo, version, request.get("doc"))
    if doc_file is None or not doc_file.is_file():
        deny("not_found", f"material {request.get('doc')!r} has no editable document")
    current = _current_revision(repo, version)
    if current is None or request.get("base_revision") != current:
        deny(
            "stale_revision",
            "edit rejected: the reviewed revision is stale; "
            f"current revision is {current or 'unavailable'}",
        )


def _latest_preview(events: list[EventEnvelope]) -> EventEnvelope | None:
    """The newest ``release.previewed`` event of a run, if any."""
    return next(
        (event for event in reversed(events) if event.type == "release.previewed"),
        None,
    )


def _deny_stale_preview(deny: RejectFn, detail: str) -> None:
    """Close the release decision path on a stale/unverifiable preview
    (FR-0309): the decision is never queued against a preview it was not
    made under."""
    deny("stale_preview", detail)


def _bind_release(
    repo: Path, run_id: str, request: dict, deny: RejectFn, project: dict
) -> None:
    """FR-0309: the decision binds the live preview digest; stale rejects."""
    events, state = _run_plane(str(repo), run_id)
    version = _material_version(project, state) or ""
    preview_event = _latest_preview(events)
    if preview_event is None:
        _deny_stale_preview(
            deny, "release decision rejected: no release preview is bound to this run"
        )
    preview = preview_event.payload if isinstance(preview_event.payload, dict) else {}
    if request.get("preview_digest") != preview.get("preview_digest"):
        _deny_stale_preview(
            deny,
            "release decision rejected: the submitted digest does not match "
            "the bound release preview",
        )
    try:
        authorization = assess_release(
            repo, paths.tracks_home(repo), events, preview_event, version
        )
    except (OSError, ValueError) as error:
        _deny_stale_preview(
            deny,
            f"release decision rejected: release preview could not be verified: {error}",
        )
    if authorization.preview_stale:
        _deny_stale_preview(
            deny,
            "release decision rejected: release preview is stale: "
            f"{authorization.stale_reason}",
        )
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


_BINDINGS = {
    "record_stage_approval": _bind_approval,
    "edit_material": _bind_edit,
    "record_release_decision": _bind_release,
    "retry_run": _bind_retry,
}


# Web resolve entry (IF-WEBAUTH-001 §1f.4): access.denied surface token.
_RESOLUTION_SURFACE = "resolve_thread"

# Root-comment blockquote line: ``> **Speaker [STATUS]:** body``.
_ROOT_COMMENT = re.compile(r"^\s*(>+)\s*(.*)$")


def _apply_resolved(text: str, thread: Thread) -> str:
    """Flip ``thread`` to resolved using the discuss canonical root format.

    The adjudication grant (§1f.4) supersedes the FR-090 operator==initiator
    rule on the web face, so the flip cannot ride ``writer.set_status``; the
    mechanics mirror it via the public writer/parser primitives only.
    """
    lines = text.splitlines()
    tag = parse_tag(_ROOT_COMMENT.match(lines[thread.root_line - 1]).group(2))
    lines[thread.root_line - 1] = writer.format_root(thread.initiator, "resolved", tag[2])[0]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    deduplicated: bool
    status: str  # accepted | completed | rejected (persist-time outcomes)


@dataclass(frozen=True)
class Rejection(Exception):
    reason: str  # closed set, interfaces §1a #8
    detail: str


class CommandService:
    """Accept/persist/dedup/query commands; execution via workers (§1b)."""

    def __init__(self, home: Path, db: Any, config: Any, scheduler: Any = None) -> None:
        self.home = Path(home)
        self._db = db
        self.config = config
        self._scheduler = scheduler
        self._fallback: ServiceDB | None = None

    def _schedule(self) -> Any:
        """The schedule collaborator (§1i), built lazily over this service.

        The composition root may inject one via ``scheduler=``; standalone
        services build a ``Scheduler`` over the same store so a create_run
        acceptance lands the run in the single-active schedule and the swap
        steps ride this service's acceptance tiers.
        """
        if self._scheduler is None:
            from tracks.supervisor.scheduler import Scheduler  # cycle-safe import

            self._scheduler = Scheduler(self._store(), service=self)
        return self._scheduler

    def _store(self) -> ServiceDB:
        """Injected store when it is a ServiceDB, else the home-backed one.

        The composition root injects ``ServiceDB(home)``; direct
        constructions that pass a non-store placeholder still get a working
        file-backed store so accept/status stay persist-first (SM-01.1).
        """
        if isinstance(self._db, ServiceDB):
            return self._db
        if self._fallback is None:
            self._fallback = ServiceDB(self.home)
        return self._fallback

    def _deny(
        self,
        store: ServiceDB,
        kind: str,
        request: dict,
        actor: str,
        actor_class: str,
        reason: str,
        detail: str,
        *,
        access_denied: bool,
    ) -> None:
        """Persist the rejection audit and raise (SM-01.5: no execution)."""
        store.append_event(
            "command.rejected",
            {
                "kind": kind,
                "reason": reason,
                "detail": detail,
                "actor": actor,
                "actor_class": actor_class,
            },
            project_id=request.get("project_id"),
            run_id=request.get("run_id"),
        )
        if access_denied:
            store.append_event(
                "access.denied",
                {
                    "surface": kind,
                    "reason": reason,
                    "actor": actor,
                    "actor_class": actor_class,
                },
                project_id=request.get("project_id"),
                run_id=request.get("run_id"),
            )
        raise Rejection(reason=reason, detail=detail)

    def _check_duplicate(
        self, store: ServiceDB, request: dict, idempotency_key: str, digest: str
    ) -> CommandReceipt | None:
        """Idempotency lookup (§1b.2): dedup receipt, conflict, or None."""
        existing = store.find_by_idempotency(idempotency_key)
        if existing is None:
            return None
        if existing.get("params_digest") == digest:
            original = existing["command_id"]
            store.append_event(
                "command.deduplicated",
                {
                    "idempotency_key": idempotency_key,
                    "original_command_id": original,
                    "command_id": original,
                },
                project_id=request.get("project_id"),
                run_id=request.get("run_id"),
                command_id=original,
            )
            return CommandReceipt(
                command_id=original,
                deduplicated=True,
                status=existing.get("status") or "accepted",
            )
        return "conflict"

    @staticmethod
    def _readiness_failed(store: ServiceDB, project_id: str | None) -> bool:
        """True only on explicit-negative readiness (absent state is the
        guard tier's business, not the accept tier's)."""
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
        store: ServiceDB,
        request: dict,
        actor: str,
        actor_class: str,
    ) -> None:
        """create_run business preflight (§1b.1): active-run guard and
        explicit-negative readiness. Hotfix journey prechecks ride the
        integration surface (deferred); registration/scope checks belong
        to the guard tier, not the accept tier."""
        schedule = store.get_schedule()
        active = schedule.get("active_run") if schedule is not None else None
        # The guard compares run identity: re-accepting the *same* run (same
        # canonical params, hence the same create_run_id) is not a second
        # active run — the schedule no-ops it (IF-SCHED-001).
        if active and not request.get("preempt") and active != create_run_id(request):
            self._deny(
                store,
                "create_run",
                request,
                actor,
                actor_class,
                "active_run_exists",
                f"active run {active!r} exists and preempt is false",
                access_denied=False,
            )
        if self._readiness_failed(store, request.get("project_id")):
            self._deny(
                store,
                "create_run",
                request,
                actor,
                actor_class,
                "validation_failed",
                "project readiness checks are failing; resolve them before creating a run",
                access_denied=False,
            )

    def _reconcile_terminal_drive(
        self, store: ServiceDB, request: dict, deny: RejectFn
    ) -> None:
        """IF-SCHED-001: the acceptance face releases the single active slot
        of a run that already reached a terminal state instead of re-driving
        it; the queue head then advances on the supervisor's next tick."""
        run_id = request.get("run_id")
        schedule = store.get_schedule()
        if not run_id or schedule is None or schedule.get("active_run") != run_id:
            return
        state = self._run_state(store, run_id)
        if state is None or (state.status != "completed" and state.terminal_state is None):
            return
        terminal = state.terminal_state or state.status
        self._schedule().on_run_terminal(str(run_id))
        deny(
            "validation_failed",
            f"run {run_id!r} already reached terminal state {terminal!r}; "
            "the schedule released its active slot",
        )

    def _preflight_webgate(
        self,
        store: ServiceDB,
        kind: str,
        request: dict,
        deny: RejectFn,
    ) -> None:
        """Accept-time web-gate binding (IF-WEBGATE-001 §1b.1): run the
        revision/preview/evidence binding before any persistence. Rejection
        leaves no command row and only the ``command.rejected`` audit
        (SM-01.5) — the accepted decision is never queued on a stale object."""
        if kind not in _WEBGATE_KINDS:
            return
        run_id = request.get("run_id")
        project = self._project_for_run(store, run_id)
        if project is None:
            deny("not_found", f"run {run_id!r} is not registered on this service")
        _BINDINGS[kind](Path(str(project["repo_path"])), str(run_id), request, deny, project)

    @staticmethod
    def _project_for_run(store: ServiceDB, run_id: Any) -> dict | None:
        """The registered project owning run_id: the service-plane rows first
        (commands / service events), then a tracks.db scan across the
        registered projects — same discovery order as the read projections."""
        if not run_id:
            return None
        project = store.get_project(store.find_run_project_id(str(run_id)))
        if project is not None:
            return project
        for candidate in store.list_projects():
            if _run_exists(str(candidate.get("repo_path") or ""), str(run_id)):
                return candidate
        return None

    def _record_acceptance(
        self,
        store: ServiceDB,
        kind: str,
        request: dict,
        actor: str,
        actor_class: str,
        surface: str,
        idempotency_key: str,
        digest: str,
        command_id: str,
        *,
        run_id: str | None = None,
    ) -> None:
        """Persist command.accepted plus the service-produced kind event."""
        project_id = request.get("project_id")
        run_id = run_id if run_id is not None else request.get("run_id")
        store.append_event(
            "command.accepted",
            {
                "command_id": command_id,
                "kind": kind,
                "idempotency_key": idempotency_key,
                "params_digest": digest,
                "actor": actor,
                "actor_class": actor_class,
                "surface": surface,
                "project_id": project_id,
                "run_id": run_id,
            },
            project_id=project_id,
            run_id=run_id,
            command_id=command_id,
        )
        if kind == "pause_run":
            store.append_event(
                "run.pause_requested",
                {"run_id": run_id, "actor": actor, "command_id": command_id},
                run_id=run_id,
                command_id=command_id,
            )
        elif kind == "resume_run":
            store.append_event(
                "run.resumed",
                {
                    "run_id": run_id,
                    "actor": actor,
                    "command_id": command_id,
                    "from_stage": self._resume_stage(store, run_id),
                },
                run_id=run_id,
                command_id=command_id,
            )
        elif kind == "edit_material":
            self._apply_edit(store, request, actor, surface, command_id)

    def _apply_edit(
        self, store: ServiceDB, request: dict, actor: str, surface: str, command_id: str
    ) -> None:
        """Apply an accepted material edit through the existing revision flow.

        SM-01.1 is already honored (command.accepted persisted); the new
        revision is content-addressed: the document is written, the run repo
        records the revision, and ``material.edited`` (interfaces §1a #22)
        carries the from/to pair the approval binding then holds the reviewer
        to. The preflight bound the submitted base revision, so a resolvable
        document is guaranteed here.
        """
        run_id = request.get("run_id")
        project = self._project_for_run(store, run_id)
        if project is None:
            return
        repo = Path(str(project["repo_path"]))
        version = _material_version(project, self._run_state(store, run_id))
        doc_file = _material_file(repo, version, request.get("doc"))
        if doc_file is None or not doc_file.is_file():
            return
        from_revision = _current_revision(repo, version) or request.get("base_revision")
        _write_material(doc_file, str(request.get("content") or ""))
        to_revision = _current_revision(repo, version)
        _commit_material(repo, doc_file, str(request.get("doc") or ""), actor)
        store.append_event(
            "material.edited",
            {
                "run_id": run_id,
                "doc": request.get("doc"),
                "from_revision": from_revision,
                "to_revision": to_revision,
                "actor": actor,
                "surface": surface,
            },
            run_id=run_id,
            command_id=command_id,
        )

    def _resume_stage(self, store: ServiceDB, run_id: Any) -> str | None:
        """The stage a resumed run continues from (interfaces §1a#21).

        Resolved best-effort from the registered project's run plane; None
        only when the run is not resolvable here (the worker carries the
        stage at execution).
        """
        state = self._run_state(store, run_id)
        return state.stage if state is not None else None

    def _run_state(self, store: ServiceDB, run_id: Any) -> Any | None:
        """Projected State of run_id from its registered project's run plane.

        None when the run is not resolvable on the service plane (unknown
        project, unreadable run plane) — callers fail open on absence.
        """
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
        kind: str,
        params: dict,
        *,
        actor: str,
        actor_class: str,
        surface: str,
        idempotency_key: str | None,
    ) -> CommandReceipt:
        """SM-01 accept path: validate -> dedup -> persist -> receipt.

        Raises Rejection (mapped to ``command.rejected`` / HTTP status) on
        guard/validation/idempotency/business-preflight failures; rejected
        commands produce no execution side effects (SM-01.5).
        """
        request = dict(params or {})
        store = self._store()
        deny = lambda reason, detail, *, access_denied=False: self._deny(  # noqa: E731
            store, kind, request, actor, actor_class, reason, detail,
            access_denied=access_denied,
        )
        self._check_actor_class(kind, actor_class, deny)
        if kind not in SERVICE_COMMAND_KINDS:
            deny("guard_blocked", f"unknown command kind {kind!r}")
        missing = [key for key in _REQUIRED_PARAMS[kind] if key not in request]
        if missing:
            deny("validation_failed", f"missing params: {', '.join(missing)}")
        if kind == "create_run" and request.get("journey") not in _CREATE_RUN_JOURNEYS:
            deny("validation_failed", f"unknown journey {request.get('journey')!r}")
        if idempotency_key is None:
            if surface == "http":
                deny("validation_failed", "idempotency key required for HTTP changes")
            idempotency_key = uuid.uuid4().hex
        digest = _params_digest(request)
        duplicate = self._check_duplicate(store, request, idempotency_key, digest)
        if duplicate == "conflict":
            deny(
                "idempotency_conflict",
                f"idempotency key {idempotency_key!r} was used with different params",
            )
        if duplicate is not None:
            return duplicate
        self._preflights(store, kind, request, actor, actor_class, deny)
        command_id = uuid.uuid4().hex
        # The create_run acceptance announces (and now records) the run it
        # creates: the derived canonical identity lands on the command row
        # and the command.accepted payload so the run is resolvable from the
        # service plane — projections, timeline and the web-gate bindings
        # find it before its run plane exists (IF-DOCREV-001 visibility).
        recorded_run_id = (
            create_run_id(request) if kind == "create_run" else request.get("run_id")
        )
        store.register_command(
            {
                "command_id": command_id,
                "kind": kind,
                "params_json": json.dumps(request, sort_keys=True),
                "params_digest": digest,
                "idempotency_key": idempotency_key,
                "actor": actor,
                "actor_class": actor_class,
                "surface": surface,
                "project_id": request.get("project_id"),
                "run_id": recorded_run_id,
            }
        )
        self._record_acceptance(
            store, kind, request, actor, actor_class, surface, idempotency_key, digest,
            command_id, run_id=recorded_run_id,
        )
        self._on_accepted(store, kind, request, actor)
        return CommandReceipt(command_id=command_id, deduplicated=False, status="accepted")

    def _check_actor_class(self, kind: str, actor_class: str, deny: RejectFn) -> None:
        """Actor-class partition (interfaces §1f.5): human-only kinds reject
        non-human callers and the system-only drive kind rejects humans."""
        if kind in HUMAN_ONLY_KINDS and actor_class != "human":
            deny(
                "forbidden_actor",
                f"kind {kind!r} requires actor_class 'human'",
                access_denied=True,
            )
        if kind in SYSTEM_ONLY_KINDS and actor_class != "system":
            deny(
                "forbidden_actor",
                f"kind {kind!r} requires actor_class 'system'",
                access_denied=True,
            )

    def _preflights(
        self,
        store: ServiceDB,
        kind: str,
        request: dict,
        actor: str,
        actor_class: str,
        deny: RejectFn,
    ) -> None:
        """Business preflights ahead of persistence (§1b.1): create_run
        admission, terminal-drive reconciliation, and the web-gate object
        bindings."""
        if kind == "create_run":
            self._preflight_create_run(store, request, actor, actor_class)
        elif kind == "drive_run":
            self._reconcile_terminal_drive(store, request, deny)
        self._preflight_webgate(store, kind, request, deny)

    def _on_accepted(self, store: ServiceDB, kind: str, request: dict, actor: str) -> None:
        """Kind-specific side effects after persistence (SM-01 persist-first).

        IF-SCHED-001: an accepted create_run enters the schedule through the
        locked outlet — active singleton, queue, or the auditable hotfix
        preemption — so the swap sequence is reachable in production.
        """
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
        result = {
            "command_id": row["command_id"],
            "kind": row["kind"],
            "status": row["status"],
            "actor": row.get("actor"),
            "actor_class": row.get("actor_class"),
            "surface": row.get("surface"),
            "project_id": row.get("project_id"),
            "run_id": row.get("run_id"),
        }
        if row["status"] == "failed":
            result["failure"] = outcome
        elif row["status"] == "completed":
            result["result"] = outcome
        return result

    def resolve_discussion_thread(
        self, doc_path: Path, thread_token: str, *, actor: str, actor_class: str
    ) -> None:
        """set-status resolved honoring adjudication ownership (§1f.4).

        The web equivalent entry: the operator is the authenticated session
        actor. A thread whose root comment explicitly requests a party is
        resolved only by that party; any other attempt leaves the status
        unchanged and lands an auditable ``access.denied(forbidden_actor)``.
        Unrequested threads keep the discuss operator==initiator rule
        (FR-090) via ``writer.set_status``. Fail closed: nothing is written
        unless the whole transform succeeds.
        """
        self._locked_write(
            doc_path, lambda text: self._resolve_text(text, thread_token, actor, actor_class)
        )

    def _resolve_text(self, text: str, thread_token: str, actor: str, actor_class: str) -> str:
        """Adjudication-aware ``resolved`` transform (fail closed)."""
        owner = adjudication_owner(text, thread_token)
        if owner is not None and speaker_key(owner) != speaker_key(actor):
            self._deny_resolution(
                actor, actor_class, f"thread {thread_token!r} requests adjudication by {owner!r}"
            )
        thread = next((t for t in parse_threads(text) if t.thread_id == thread_token), None)
        if thread is None:
            raise Rejection(
                reason="not_found", detail=f"thread {thread_token!r} not found in this document"
            )
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
                "surface": _RESOLUTION_SURFACE,
                "reason": "forbidden_actor",
                "actor": actor,
                "actor_class": actor_class,
            },
        )
        raise Rejection(reason="forbidden_actor", detail=detail) from None

    def _locked_write(self, doc_path: Path, transform) -> None:
        """flock-serialized read-modify-write (FR-110 parity with the CLI).

        The lock-file name matches ``tracks.discuss.cli._atomic_write`` so
        web-face and CLI-face writes mutually exclude; a raising transform
        leaves the document byte-for-byte unchanged.
        """
        if not doc_path.exists():
            raise Rejection(
                reason="not_found", detail=f"document not found: {doc_path.name}"
            )
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
