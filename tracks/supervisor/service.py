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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        self.home = home

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
        raise NotImplementedError("IF-CMDSVC-001")

    def status(self, command_id: str) -> dict:
        """Persisted command status/result; survives restarts (§1b)."""
        raise NotImplementedError("IF-CMDSVC-001")

    def resolve_discussion_thread(
        self, doc_path: Path, thread_token: str, *, actor: str, actor_class: str
    ) -> None:
        """set-status resolved honoring adjudication ownership (§1f.4):
        only the requested party (authenticated) may resolve."""
        raise NotImplementedError("IF-WEBAUTH-001")
