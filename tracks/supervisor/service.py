"""Persistent command service shared by CLI and Web (IF-CMDSVC-001).

Single accept path for the closed ServiceCommandKind set (interfaces §1b):
authenticate/authorize -> guard validation -> idempotency dedup -> persist
(SM-01.1) -> hand to a supervisor worker (HTTP) or execute inline (CLI).
Business validations reuse the existing gate semantics (cmd_approve /
release_gate / escape / repair) — this service never appends domain events
directly and never bypasses revision/preview binding.

Contract tokens: IF-CMDSVC-001, IF-WEBGATE-001, IF-PAUSE-001, IF-SCHED-001.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        idempotency_key: str | None,
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
            self._preflight_create_run(store, request, actor, actor_class, idempotency_key)
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
        """set-status resolved honoring adjudication ownership (§1f.4):
        only the requested party (authenticated) may resolve."""
        raise NotImplementedError("IF-WEBAUTH-001")
