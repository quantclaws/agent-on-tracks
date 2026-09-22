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


# -- web-gate accept-time binding (IF-WEBGATE-001 §1b.1) ----------------------
#
# The web human decisions bind the object they were made against at
# *acceptance* time (SM-01.1 persist-then-execute): an approval binds the
# revision the user actually reviewed (FR-0308), a release decision binds the
# live preview digest (FR-0309), and a controlled retry only accepts from the
# legal v0.8 repair/escape position (FR-0311). The binding reuses the existing
# shared validators — the service never appends domain events itself.

_WEBGATE_KINDS = frozenset(
    {"record_stage_approval", "record_release_decision", "retry_run"}
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


def _bind_approval(repo: Path, run_id: str, request: dict, deny: RejectFn) -> None:
    """FR-0308: the approval binds the revision the user actually reviewed."""
    _events, state = _run_plane(str(repo), run_id)
    current = _current_revision(repo, state.version)
    if current is None or request.get("expected_revision") != current:
        deny(
            "stale_revision",
            "approval rejected: the reviewed revision is stale; "
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


def _bind_release(repo: Path, run_id: str, request: dict, deny: RejectFn) -> None:
    """FR-0309: the decision binds the live preview digest; stale rejects."""
    events, state = _run_plane(str(repo), run_id)
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
            repo, paths.tracks_home(repo), events, preview_event, state.version or ""
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


def _bind_retry(repo: Path, run_id: str, request: dict, deny: RejectFn) -> None:
    """FR-0311: retry only from the legal position (v0.8 repair/escape gate)."""
    _events, state = _run_plane(str(repo), run_id)
    gate_error = _retry_gate_error(state, bool(request.get("clear_evidence")))
    if gate_error is not None:
        deny("validation_failed", f"retry rejected: {gate_error}")


_BINDINGS = {
    "record_stage_approval": _bind_approval,
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

    def __init__(self, home: Path, db: Any, config: Any) -> None:
        self.home = Path(home)
        self._db = db
        self.config = config
        self._fallback: ServiceDB | None = None

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
        if schedule is not None and schedule.get("active_run") and not request.get("preempt"):
            self._deny(
                store,
                "create_run",
                request,
                actor,
                actor_class,
                "active_run_exists",
                f"active run {schedule['active_run']!r} exists and preempt is false",
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
        _BINDINGS[kind](Path(str(project["repo_path"])), str(run_id), request, deny)

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
    ) -> None:
        """Persist command.accepted plus the service-produced kind event."""
        project_id = request.get("project_id")
        run_id = request.get("run_id")
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
                    "from_stage": None,
                },
                run_id=run_id,
                command_id=command_id,
            )

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
        if kind == "create_run":
            self._preflight_create_run(store, request, actor, actor_class)
        self._preflight_webgate(store, kind, request, deny)
        command_id = uuid.uuid4().hex
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
                "run_id": request.get("run_id"),
            }
        )
        self._record_acceptance(
            store, kind, request, actor, actor_class, surface, idempotency_key, digest,
            command_id,
        )
        return CommandReceipt(command_id=command_id, deduplicated=False, status="accepted")

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
