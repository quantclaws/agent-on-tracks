"""HTTP command guard and access-scope enforcement (IF-CMDGUARD-001).

Structured-command validation against the closed ServiceCommandKind set
(interfaces §1b): unknown kinds, extra fields and free-form execution
payloads are rejected before persistence; path/scope parameters must resolve
inside the registered project's authorized scope (§1g).

Contract tokens: IF-CMDGUARD-001, IF-SECRECY-001.
"""

from __future__ import annotations

from pathlib import Path


class GuardRejection(Exception):
    """Raised when a command payload fails guard validation.

    Carries the closed-set reason for ``command.rejected``
    (``guard_blocked`` / ``validation_failed`` / ``scope_violation``).
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def validate_command_payload(kind: str, params: dict) -> None:
    """Validate params against the kind's schema; raise GuardRejection."""
    raise NotImplementedError("IF-CMDGUARD-001")


def check_actor_class(kind: str, actor_class: str) -> None:
    """Enforce the human-only / system-only kind partition (§1b, §1f.5)."""
    raise NotImplementedError("IF-CMDGUARD-001")


def check_path_scope(path: Path, permitted_roots: list[Path]) -> None:
    """Require path (realpath) to resolve inside the permitted scope (§1g.2)."""
    raise NotImplementedError("IF-SECRECY-001")
