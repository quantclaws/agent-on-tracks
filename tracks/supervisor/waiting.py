"""External-wait persistence, quota handling and bounded backoff (IF-WAIT-001).

Waits are durable state, not transient errors: wait.entered persists the
reason and the next wake-up (retry_at) and survives restarts; quota waits
consume the harness quota signal (known reset -> exact wake; unknown reset
-> bounded exponential probing under the NFR-0152 policy). No countdown is
ever fabricated and no busy loop is allowed; effective pause always wins
over automatic wake (SM-02.5).

Contract token: IF-WAIT-001.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class WaitPolicy:
    """NFR-0152 locked defaults; overridable by serve flags (§1j)."""

    initial_s: int = 60
    cap_s: int = 900
    poll_interval_s: int = 5
    poll_idle_cap_s: int = 60


@dataclass(frozen=True)
class WaitSpec:
    """Durable external-wait description (interfaces §1d/§1j)."""

    wait_class: str  # "ci" | "quota" | "network" | "agent" | "external"
    reason: str
    retry_at: str | None  # ISO8601 when the reset time is known
    known_reset: bool
    backoff: dict | None  # {"interval_s", "cap_s", "next_probe_at"}


_WAIT_CLASSES = ("ci", "quota", "network", "agent", "external")

_UNRECOVERABLE_KINDS = frozenset({"unrecoverable", "config_error"})

_QUOTA_TOKENS = ("quota", "rate_limit", "rate limit", "429", "4008", "exceeded")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_of(failure: dict) -> str:
    parts = [
        str(failure.get("kind") or ""),
        str(failure.get("error") or ""),
        str(failure.get("stderr") or ""),
        str(failure.get("message") or ""),
    ]
    return " ".join(parts).lower()


def _known_retry_at(failure: dict) -> str | None:
    for key in ("retry_after", "reset_at", "retry_at"):
        value = failure.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _initial_backoff(policy: WaitPolicy | None) -> dict:
    initial = policy.initial_s if policy is not None else 60
    cap = policy.cap_s if policy is not None else 900
    probe_at = datetime.now(timezone.utc) + timedelta(seconds=initial)
    return {"interval_s": initial, "cap_s": cap, "next_probe_at": probe_at.isoformat()}


_KIND_TO_CLASS = {
    "ci_timeout": "ci",
    "provider_quota": "quota",
    "network_error": "network",
    "agent_busy": "agent",
    "external_blocked": "external",
}


def _class_from_kind(kind: str) -> str | None:
    if "ci" in kind:
        return "ci"
    if "quota" in kind:
        return "quota"
    if "network" in kind:
        return "network"
    if "agent" in kind:
        return "agent"
    if "external" in kind:
        return "external"
    return _KIND_TO_CLASS.get(kind)


def _class_from_text(text: str) -> str | None:
    if "ci" in text.split():
        return "ci"
    if any(token in text for token in _QUOTA_TOKENS):
        return "quota"
    if "connection" in text or "network" in text:
        return "network"
    if "agent" in text:
        return "agent"
    if "external" in text or "waiting" in text:
        return "external"
    return None


def _reason_of(data: dict, kind: str, wait_class: str) -> str:
    reason = str(
        data.get("reason") or data.get("stderr") or data.get("error")
        or data.get("message") or kind or wait_class
    ).strip()
    return reason or wait_class


def _quota_spec(reason: str, data: dict, policy: WaitPolicy | None) -> WaitSpec:
    retry_at = _known_retry_at(data)
    if retry_at is not None:
        return WaitSpec(
            wait_class="quota",
            reason=reason,
            retry_at=retry_at,
            known_reset=True,
            backoff=None,
        )
    return WaitSpec(
        wait_class="quota",
        reason=reason,
        retry_at=None,
        known_reset=False,
        backoff=_initial_backoff(policy or WaitPolicy()),
    )


def classify_wait(failure: dict, *, policy: WaitPolicy | None = None) -> Any:
    """Map an executor/harness failure onto WaitSpec (wait_class, known_reset,
    retry_at | backoff); unrecoverable failures return None (§1d)."""
    data = dict(failure or {})
    kind = str(data.get("kind") or "").lower()
    if kind in _UNRECOVERABLE_KINDS:
        return None
    wait_class = _class_from_kind(kind) or _class_from_text(_text_of(data))
    if wait_class not in _WAIT_CLASSES:
        wait_class = "external"
    reason = _reason_of(data, kind, wait_class)
    if wait_class == "quota":
        return _quota_spec(reason, data, policy)
    return WaitSpec(
        wait_class=wait_class,
        reason=reason,
        retry_at=None,
        known_reset=False,
        backoff=None,
    )


def _write_wait_row(db: Any, run_id: str, wait_class: str, reason: str,
                    retry_at: str | None, known_reset: bool,
                    backoff_json: str | None, entered_at: str) -> None:
    """Upsert the single active waits row via the store's own write face.

    ServiceDB exposes no generic execute: it owns waits writes through
    dedicated methods below; duck-typed doubles (unit _Db) expose execute.
    Production composition root (worker) passes the ServiceDB, so both
    faces are honored without inventing a new store method.
    """
    writer = getattr(db, "upsert_wait", None)
    if callable(writer):
        writer(run_id, wait_class, reason, retry_at, known_reset, backoff_json,
               entered_at)
        return
    db.execute(
        "INSERT OR REPLACE INTO waits (run_id, wait_class, reason, retry_at,"
        " known_reset, backoff_json, entered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, wait_class, reason, retry_at, 1 if known_reset else 0,
         backoff_json, entered_at),
    )


def _clear_wait_row(db: Any, run_id: str) -> None:
    clearer = getattr(db, "clear_wait", None)
    if callable(clearer):
        clearer(run_id)
        return
    db.execute("DELETE FROM waits WHERE run_id = ?", (run_id,))


def enter_wait(db: Any, run_id: str, spec: Any) -> None:
    """Persist the active wait row and emit wait.entered (§1a #13)."""
    if isinstance(spec, dict):
        wait_class = str(spec.get("wait_class") or "external")
        reason = str(spec.get("reason") or wait_class)
        retry_at = spec.get("retry_at")
        known_reset = bool(spec.get("known_reset", retry_at is not None))
        backoff = spec.get("backoff")
    else:
        wait_class = str(getattr(spec, "wait_class", "external") or "external")
        reason = str(getattr(spec, "reason", None) or wait_class)
        retry_at = getattr(spec, "retry_at", None)
        known_reset = bool(getattr(spec, "known_reset", retry_at is not None))
        backoff = getattr(spec, "backoff", None)
    backoff_json = json.dumps(backoff) if backoff is not None else None
    _write_wait_row(db, run_id, wait_class, reason, retry_at, known_reset,
                    backoff_json, _utcnow())
    db.append_event(
        "wait.entered",
        {
            "run_id": run_id,
            "wait_class": wait_class,
            "reason": reason,
            "retry_at": retry_at,
            "known_reset": known_reset,
            "backoff": backoff,
        },
        run_id=run_id,
    )


def resolve_wait(db: Any, run_id: str, resolved_by: str) -> None:
    """Clear the active wait row and emit wait.resolved (§1a #14)."""
    _clear_wait_row(db, run_id)
    db.append_event(
        "wait.resolved",
        {"run_id": run_id, "resolved_by": resolved_by},
        run_id=run_id,
    )


def _spec_from_row(row: Any) -> WaitSpec:
    """Map a persisted waits row onto WaitSpec (decoded probe plan)."""
    backoff = row.get("backoff_json")
    if isinstance(backoff, str) and backoff:
        try:
            backoff = json.loads(backoff)
        except ValueError:
            backoff = None
    if not isinstance(backoff, dict):
        backoff = None
    return WaitSpec(
        wait_class=str(row.get("wait_class") or "external"),
        reason=str(row.get("reason") or ""),
        retry_at=row.get("retry_at"),
        known_reset=bool(row.get("known_reset")),
        backoff=backoff,
    )


def load_wait(db: Any, run_id: str) -> WaitSpec | None:
    """Read back the persisted active wait for a run (§1j).

    The supervisor consumes this to wake at a known retry_at or to follow
    the bounded probe plan; None when the run has no active wait. Reads
    through the ServiceDB wait face so the composition-root store stays the
    single storage implementation.
    """
    row = db.get_wait(run_id)
    if row is None:
        return None
    return _spec_from_row(row)


def active_wait_runs(db: Any) -> list:
    """Run ids with an active wait row: the restart recovery sweep (§1j)."""
    return [str(row["run_id"]) for row in db.list_waits()]


def next_probe(policy: WaitPolicy, backoff: dict | None) -> dict:
    """Compute the next bounded backoff state (interval doubles to cap_s)."""
    cap = policy.cap_s
    if not backoff:
        probe_at = datetime.now(timezone.utc) + timedelta(seconds=policy.initial_s)
        return {
            "interval_s": policy.initial_s,
            "cap_s": cap,
            "next_probe_at": probe_at.isoformat(),
        }
    current = int(backoff.get("interval_s") or policy.initial_s)
    stepped = min(current * 2 if current >= policy.initial_s else policy.initial_s, cap)
    probe_at = datetime.now(timezone.utc) + timedelta(seconds=stepped)
    return {"interval_s": stepped, "cap_s": cap,
            "next_probe_at": probe_at.isoformat()}
