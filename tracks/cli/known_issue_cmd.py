"""``trac known-issue`` maintenance face.

Append-only resolution of registered Known Issues: a Human resolves a false
or discharged registration by appending a ``known_issue.registered`` event
with ``fixed: true``. The latest event per issue number supersedes earlier
ones everywhere (preview, waiver projection, release guard); no prior event
is updated or deleted.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor.repair import latest_known_issues

from .common import _err, _human_actor, active_run_context

_KNOWN_ISSUE_USAGE = (
    "usage: trac known-issue resolve --issue N [--issue N ...] --reason TEXT"
)

_RESOLUTION_FIELDS = (
    "issue_number",
    "url",
    "label",
    "item_or_ac",
    "candidate_sha",
    "task_id",
    "ac_refs",
    "node_refs",
    "evidence_refs",
)
_LIST_FIELDS = ("ac_refs", "node_refs", "evidence_refs")


def _parse_issue_number(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 1 else None


def _consume_issue(issues: list[int], value: str) -> bool:
    number = _parse_issue_number(value)
    if number is None:
        return False
    if number not in issues:
        issues.append(number)
    return True


def _valid_resolve(issues: list[int], reason: str | None) -> bool:
    return bool(issues) and reason is not None and bool(reason.strip())


def _parse_known_issue_args(args: tuple[str, ...]) -> tuple[list[int], str] | None:
    """Parse ``resolve --issue N [--issue N ...] --reason TEXT``.

    Returns (deduplicated issue numbers in input order, reason) or None on a
    usage error.
    """
    if not args or args[0] != "resolve":
        return None
    issues: list[int] = []
    reason: str | None = None
    index = 1
    while index < len(args):
        flag = args[index]
        if flag not in ("--issue", "--reason") or index + 1 >= len(args):
            return None
        value = args[index + 1]
        if flag == "--issue":
            if not _consume_issue(issues, value):
                return None
        elif reason is not None:
            return None
        else:
            reason = value
        index += 2
    if not _valid_resolve(issues, reason):
        return None
    assert reason is not None
    return issues, reason


def _resolved_payload(registration: dict, reason: str, resolved_by: str) -> dict:
    """Original registration fields plus the durable resolution evidence."""
    payload = {field: registration.get(field) for field in _RESOLUTION_FIELDS}
    for field in _LIST_FIELDS:
        value = payload.get(field)
        payload[field] = list(value) if isinstance(value, (list, tuple)) else []
    payload["fixed"] = True
    payload["reason"] = reason
    payload["resolved_by"] = resolved_by
    return payload


def cmd_known_issue(repo: Path, *args: str) -> int:
    """``trac known-issue resolve --issue N [--issue N ...] --reason TEXT``.

    Resolves the current ACTIVE run only. Unknown issue numbers fail closed
    with no events written; an already-fixed latest registration is a no-op
    success (idempotent), so repeated operator calls are safe.
    """
    parsed = _parse_known_issue_args(args)
    if parsed is None:
        return _err(_KNOWN_ISSUE_USAGE)
    issues, reason = parsed
    with active_run_context(repo) as opened:
        if opened is None:
            return _err("no active run")
        _home, store, run_id = opened
        latest = {
            str(registration.get("issue_number")): registration
            for registration in latest_known_issues(store.events(run_id))
        }
        unknown = [str(number) for number in issues if str(number) not in latest]
        if unknown:
            return _err("unknown known issue(s): " + ", ".join(unknown))
        version = store.state(run_id).version
        resolved_by = _human_actor(repo)
        for number in issues:
            registration = latest[str(number)]
            if registration.get("fixed"):
                print(f"known issue #{number} already fixed")
                continue
            store.append(
                run_id,
                version,
                "known_issue.registered",
                _resolved_payload(registration, reason, resolved_by),
            )
            print(f"known issue #{number} resolved: fixed=true")
    return 0
