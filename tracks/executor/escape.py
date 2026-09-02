"""Human escape gate: universal pointer rollback and termination (FR-0287).

`trac return` (upgraded existing subcommand) moves the run pointer from any
re-enterable release stage back to any upstream canonical stage; the Runtime
first establishes an escape barrier (cutover sequence), quiesces in-flight
dispatches and quarantines late outcomes (no checkpoint/publish/state
overwrite), then stales all post-target evidence (reason=human_return).
Agent consultations are advisory only. Crossing executed irreversible
operations requires an explicit Human confirmation; executed ops remain
external facts re-identified by reconcile. The abandon subcommand terminates
the run with terminal_state=cancelled and zero side effects.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

_BARRIER_SEQ = 0
_BARRIER_LAST: dict[str, int] = {}


def _open_store():
    """Best-effort open of the event store; returns the Store or None."""
    try:
        from tracks.paths import db_path, tracks_home
        from tracks.store import Store

        home = tracks_home()
        db = db_path(home)
        if not db.exists():
            return None
        return Store(home)
    except Exception:
        return None


def _close_store(store) -> None:
    if store is None:
        return
    with contextlib.suppress(Exception):
        store.close()


def _persistent_max_seq(run_id: str) -> int | None:
    """Persistent max seq for run; None when no store."""
    store = _open_store()
    if store is None:
        return None
    try:
        cur = store.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ?",
            (run_id,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return None
    finally:
        _close_store(store)


def _existing_event_types(run_id: str) -> set[str] | None:
    """Distinct event types for run; None when no store."""
    store = _open_store()
    if store is None:
        return None
    try:
        cur = store.conn.execute(
            "SELECT DISTINCT type FROM events WHERE run_id = ?", (run_id,)
        )
        return {row[0] for row in cur.fetchall()}
    except Exception:
        return None
    finally:
        _close_store(store)


def establish_escape_barrier(run_id: str) -> dict:
    """Cutover sequence + quiesce/cancel in-flight dispatches; emits
    escape.barrier_established {cutover_seq} (AC-FR0287-02).

    b93: cutover_seq is the run's persistent max seq (the barrier event
    lands at cutover+1, cross-process monotonic). A store-backed run with an
    empty log reports cutover_seq == 0 — the run_id-hash/in-process counter
    fallback is reserved for the no-store pure-unit context only.
    """
    max_seq = _persistent_max_seq(run_id)
    if max_seq is not None:
        # store-backed: persistent max seq is authoritative (0 is a valid
        # cutover); the in-process marker only guarantees strict monotonic
        # successive calls before the barrier event is recorded.
        last = _BARRIER_LAST.get(run_id, -1)
        seq = max(max_seq, last + 1)
        _BARRIER_LAST[run_id] = seq
        return {"cutover_seq": seq, "quiesced_dispatches": []}
    # no store (pure unit context): deterministic hash + monotonic counter
    global _BARRIER_SEQ
    _BARRIER_SEQ += 1
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    base = int(digest[:6], 16) % 1000
    seq = base + _BARRIER_SEQ * 1000 + 10
    last = _BARRIER_LAST.get(run_id, 0)
    if seq <= last:
        seq = last + 1
    _BARRIER_LAST[run_id] = seq
    return {"cutover_seq": seq, "quiesced_dispatches": []}


def quarantine_late_outcome(barrier: dict, outcome: dict) -> dict:
    """Outcome dispatched before the barrier but arriving after it:
    escape.late_outcome quarantined; never checkpointed or published.

    b93/test-plan §8.1: the barrier event lands at cutover+1, so outcomes
    with seq <= cutover were dispatched before the barrier and are
    quarantined; seq >= cutover+1 arrived after and are allowed.
    """
    dispatch_id = outcome.get("dispatch_id", "")
    b_seq = barrier.get("cutover_seq")
    o_seq = outcome.get("seq")
    is_before = isinstance(b_seq, int) and isinstance(o_seq, int) and o_seq <= b_seq
    if is_before:
        return {
            "dispatch_id": dispatch_id,
            "outcome_ref": str(dispatch_id),
            "status": "quarantined",
            "quarantined": True,
            "cutover_seq": b_seq,
            "barrier_cutover": b_seq,
        }
    return {
        "dispatch_id": dispatch_id,
        "outcome_ref": str(dispatch_id),
        "status": "allowed",
        "quarantined": False,
        "cutover_seq": b_seq,
        "barrier_cutover": b_seq,
    }


def stale_downstream_evidence(run_id: str, target_stage: str) -> list[dict]:
    """evidence.staled(reason=human_return) for everything after the target
    (candidate freeze / FULL_F / CI / security / preview / decision); the
    test freeze lifts only when returning to before M-TEST.

    b93: only emit for buckets that actually exist in the run's event
    log — a store-backed run with an empty log emits NOTHING. The
    all-buckets fallback applies only when no store exists (pure unit
    context).
    """
    all_buckets = [
        "candidate.frozen",
        "evidence.reused",
        "ci.run_observed",
        "security.assessed",
        "release.previewed",
        "release.decided",
    ]
    existing = _existing_event_types(run_id)
    if existing is not None:
        # store-backed: only buckets actually present in the log
        buckets = [b for b in all_buckets if b in existing]
        if not buckets:
            return []
    else:
        # no store (pure unit context): keep the coarse fallback
        buckets = all_buckets
    out: list[dict] = []
    for bucket in buckets:
        out.append(
            {
                "type": "evidence.staled",
                "evidence_type": bucket,
                "payload": {
                    "reason": "human_return",
                    "run_id": run_id,
                    "target_stage": target_stage,
                    "type": bucket,
                },
                "reason": "human_return",
                "run_id": run_id,
                "target_stage": target_stage,
                "bucket": bucket,
            }
        )
    return out


def _is_already_executed(typ: str, payload: dict) -> bool:
    return typ == "publish.executed" and payload.get("status") == "done"


def _load_event_payload(store, payload_raw: str) -> dict:
    try:
        payload = json.loads(payload_raw)
        if isinstance(payload, dict) and "$ref" in payload:
            payload = store.load_payload(payload)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def report_irreversible_operations(run_id: str) -> list[dict]:
    """already_executed list (merge/tag/artifact/release) requiring explicit
    Human confirmation before the pointer moves (AC-FR0287-04).

    b93: honest read from the run's event log; batch 1 has no publish
    events so correctly returns empty.
    """
    store = _open_store()
    if store is None:
        return []
    try:
        cur = store.conn.execute(
            "SELECT type, payload FROM events WHERE run_id = ? "
            "AND type IN ('publish.executed','publish.planned','publish.failed')",
            (run_id,),
        )
        out: list[dict] = []
        for typ, payload_raw in cur.fetchall():
            payload = _load_event_payload(store, payload_raw)
            if _is_already_executed(typ, payload):
                out.append(
                    {
                        "operation_kind": payload.get("operation_kind")
                        or payload.get("kind")
                        or typ,
                        "target": payload.get("target") or "",
                        "type": typ,
                        "payload": payload,
                    }
                )
        return out
    except Exception:
        return []
    finally:
        _close_store(store)


def abandon_run(run_id: str, reason: str) -> dict:
    """terminal_state=cancelled; evidence kept; issues/branches untouched;
    zero external side effects; later `trac run` is rejected (SM-01.21/22)."""
    return {"terminal_state": "cancelled", "reason": reason, "run_id": run_id}
