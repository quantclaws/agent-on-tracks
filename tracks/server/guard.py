"""HTTP command guard and access-scope enforcement (IF-CMDGUARD-001).

Structured-command validation against the closed ServiceCommandKind set
(interfaces §1b): unknown kinds, extra fields and free-form execution
payloads are rejected before persistence; path/scope parameters must resolve
inside the registered project's authorized scope (§1g).

The closed kind set, the human-only/system-only actor partition and the
required-param registry are shared with the command service so CLI and HTTP
validate against one contract. The guard adds what the HTTP face requires
(§1g.1): rejection of unknown extra fields, type mismatches, enum violations
and free-form execution payloads; ``check_path_scope`` enforces the realpath
authorized scope (§1g.2) and ``check_registration_scope`` carries the
registration rejection's own reason token (``outside_permitted_scope``,
§1b#1 / §1a#4) over the same prefix rule.

Contract tokens: IF-CMDGUARD-001, IF-SECRECY-001.
"""

from __future__ import annotations

from pathlib import Path

from tracks.supervisor.service import (
    _REQUIRED_PARAMS,
    HUMAN_ONLY_KINDS,
    SERVICE_COMMAND_KINDS,
    SYSTEM_ONLY_KINDS,
)


class GuardRejection(Exception):
    """Raised when a command payload fails guard validation.

    Carries the closed-set reason for ``command.rejected``
    (``guard_blocked`` / ``validation_failed`` / ``scope_violation`` /
    the registration token ``outside_permitted_scope``, §1b#1).
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


_NULL = type(None)

# Param schema registry (interfaces §1b params column). The value is the
# accepted Python type (or a tuple allowing the null alternative for the
# optional keys); every other key is an unexpected / free-form field.
_PARAM_TYPES: dict[str, dict[str, object]] = {
    "register_project": {"repo_path": str},
    "check_readiness": {"project_id": str},
    "create_run": {
        "project_id": str,
        "journey": str,
        "version": str,
        "story": (str, _NULL),
        "issue": (int, _NULL),
        "target": (str, _NULL),
        "preempt": bool,
    },
    # v0.11 §1n.3 (PLANNING round 1): the clarification takes thread_token
    # XOR anchor_line — the XOR itself is validated service-side; the guard
    # tolerates either key (null included; a JSON body carries the anchor
    # line as an integer, the str form stays tolerated too).
    "submit_clarification": {
        "run_id": str,
        "doc": str,
        "body": str,
        "thread_token": (str, _NULL),
        "anchor_line": (str, int, _NULL),
    },
    "edit_material": {"run_id": str, "doc": str, "base_revision": str, "content": str},
    "record_stage_approval": {
        "run_id": str,
        "object": str,
        "expected_revision": str,
        "decision": str,
    },
    "record_release_decision": {
        "run_id": str,
        "action": str,
        "preview_digest": str,
        "reason": (str, _NULL),
        "target": (str, _NULL),
    },
    "pause_run": {"run_id": str},
    "resume_run": {"run_id": str},
    "abandon_run": {"run_id": str, "reason": str},
    # v0.9 §1b#11 as revised by v0.11 PLANNING round 1: confirm is a
    # conditional gate (only crossing already-executed irreversible
    # operations demands confirm=true), so the key is optional here — the
    # service-side preflight enforces the condition.
    "return_stage": {
        "run_id": str,
        "to": str,
        "reason": str,
        "confirm": (bool, _NULL),
    },
    "retry_run": {"run_id": str},
    "drive_run": {"run_id": str},
    # v0.11 §1b #14-18 (PLANNING round 1 guard mirror): the five new kinds
    # with their optional keys — the same closed set the service accepts.
    "submit_intake": {
        "project_id": str,
        "source": str,
        "content": (str, _NULL),
        "issue": (int, _NULL),
        "origin_entry": (str, _NULL),
    },
    "decide_intake": {"intake_id": str, "action": str, "confirm": (bool, _NULL)},
    "dispose_backlog": {
        "project_id": str,
        "entry_id": str,
        "disposition": str,
        "to": (str, _NULL),
    },
    "record_triage": {"run_id": str, "decision": str},
    "record_review": {"run_id": str, "action": str, "comments": (str, _NULL)},
}

# Closed enums per kind (interfaces §1b); unknown values are rejected.
_ENUM_PARAMS: dict[str, dict[str, frozenset]] = {
    "create_run": {"journey": frozenset({"feature", "hotfix_post", "hotfix_dev"})},
    "record_stage_approval": {"decision": frozenset({"approve", "revise"})},
    "record_release_decision": {"action": frozenset({"release", "delay", "return"})},
    "submit_intake": {"source": frozenset({"text", "issue", "backlog"})},
    "decide_intake": {
        "action": frozenset({"backlog", "archive", "remove_backlog", "develop"})
    },
    "dispose_backlog": {"disposition": frozenset({"removed", "promoted"})},
    "record_triage": {"decision": frozenset({"go", "no_go", "park"})},
    "record_review": {"action": frozenset({"comment", "no_comment"})},
}


def _check_required(kind: str, payload: dict) -> None:
    for key in _REQUIRED_PARAMS[kind]:
        if key not in payload or payload[key] is None:
            raise GuardRejection("validation_failed", f"missing required param {key!r}")


def _check_params(expected: dict, payload: dict) -> None:
    for key, value in payload.items():
        if key not in expected:
            raise GuardRejection("validation_failed", f"unexpected param {key!r}")
        if value is not None and not isinstance(value, expected[key]):
            raise GuardRejection("validation_failed", f"param {key!r} has the wrong type")


def _check_enums(kind: str, payload: dict) -> None:
    for key, allowed in _ENUM_PARAMS.get(kind, {}).items():
        value = payload.get(key)
        if value is not None and value not in allowed:
            raise GuardRejection("validation_failed", f"unknown {key!r} value {value!r}")


def validate_command_payload(kind: str, params: dict) -> None:
    """Validate params against the kind's schema; raise GuardRejection."""
    payload = dict(params or {})
    if kind not in SERVICE_COMMAND_KINDS:
        raise GuardRejection("guard_blocked", f"unknown command kind {kind!r}")
    _check_required(kind, payload)
    _check_params(_PARAM_TYPES[kind], payload)
    _check_enums(kind, payload)


def check_actor_class(kind: str, actor_class: str) -> None:
    """Enforce the human-only / system-only kind partition (§1b, §1f.5)."""
    if kind in HUMAN_ONLY_KINDS and actor_class != "human":
        raise GuardRejection("forbidden_actor", f"kind {kind!r} requires actor_class 'human'")
    if kind in SYSTEM_ONLY_KINDS and actor_class != "system":
        raise GuardRejection("forbidden_actor", f"kind {kind!r} requires actor_class 'system'")


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def check_path_scope(path: Path, permitted_roots: list[Path]) -> None:
    """Require path (realpath) to resolve inside the permitted scope (§1g.2)."""
    candidate = Path(path).resolve()
    roots = [Path(root).resolve() for root in permitted_roots]
    if not any(_inside(candidate, root) for root in roots):
        raise GuardRejection("scope_violation", f"path {path} is outside the permitted scope")


def check_registration_scope(repo_path: Path, permitted_roots: list[Path]) -> None:
    """Require a registration repo path inside the permitted serve scope.

    Same realpath prefix rule as ``check_path_scope`` (§1g.2); the
    registration face carries its own closed reason token
    (``outside_permitted_scope``, §1b#1 / §1a#4) for
    ``project.registration_rejected``.
    """
    try:
        check_path_scope(repo_path, permitted_roots)
    except GuardRejection as rejection:
        raise GuardRejection("outside_permitted_scope", rejection.detail) from None
