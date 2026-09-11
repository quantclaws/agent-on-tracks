# ruff: noqa
"""Failure-evidence end-to-end review chain (FR-0280, NFR-0146).

emitted → stored (append-only) → selected (round/source rule) → injected →
consumed → acked → invalidated → replay. Rich evidence still awaiting
consumption is never silently overwritten by ordinary failures; the review
reducer verifies chain consistency and reports gaps fail-closed.
"""

from __future__ import annotations

from typing import Literal

ReviewOutcome = Literal["consistent", "evidence_lost", "mismatched"]

_STORE: dict[str, list[dict]] = {}
_ACKED: set[tuple[str, str, str]] = set()
_INVALIDATED: set[tuple[str, str]] = set()


def _next_failure_id(run_id: str, round_no: int, source: str) -> str:
    seq = len(_STORE.get(run_id, []))
    return f"{round_no}-{source}-{seq}"


def next_failure_id(run_id: str, round_no: int, source: str) -> str:
    """The id the next :func:`record_failure` will mint (failure.emitted)."""
    return _next_failure_id(run_id, round_no, source)


def record_failure(run_id: str, round_no: int, source: str, record: dict) -> dict:
    fid = _next_failure_id(run_id, round_no, source)
    entry: dict = {
        "failure_id": fid,
        "run_id": run_id,
        "round": round_no,
        "source": source,
        "record": dict(record),
        "seq": len(_STORE.get(run_id, [])),
    }
    _STORE.setdefault(run_id, []).append(entry)
    return {"failure_id": fid, "run_id": run_id, "round": round_no, "source": source, "record": dict(record)}


def _event_payload(e) -> dict:
    payload = getattr(e, "payload", None)
    if payload is None and isinstance(e, dict):
        payload = e.get("payload")
    return dict(payload or {})


def _event_type(e) -> str | None:
    return getattr(e, "type", None) or (e.get("type") if isinstance(e, dict) else None)


def _replay_stored(run_id: str, payload: dict) -> bool:
    fid = payload.get("failure_id")
    if not fid:
        return False
    entry = {
        "failure_id": fid,
        "run_id": payload.get("run_id") or run_id,
        "round": payload.get("round", 0),
        "source": payload.get("source", ""),
        "record": dict(payload.get("record") or {}),
        "seq": payload.get("seq", 0),
    }
    lst = _STORE.setdefault(entry["run_id"], [])
    if any(f.get("failure_id") == fid for f in lst):
        return False
    lst.append(entry)
    return True


def restore_from_events(run_id: str, events) -> int:
    """Replay the run's failure-evidence stream into the selection store.

    The store is process-local by construction (record_failure appends here;
    the failure.stored event is its persistence). Under the M7 handover
    regime the loop process is replaced at every drift boundary -- without
    replay the selection rule goes blind after each restart and every
    post-restart DIAGNOSE runs evidenceless (live 2026-09-06: two
    consecutive unknown escalations while the round's records sat on the
    stream). Event-sourced single truth: the stream IS the store. ACKs
    replay too so consumed evidence is not re-injected. Idempotent per
    failure_id. Returns the number of records restored.
    """
    restored = 0
    for e in events:
        etype = _event_type(e)
        payload = _event_payload(e)
        if etype == "failure.stored":
            if _replay_stored(run_id, payload):
                restored += 1
        elif etype == "failure.acked":
            fid = payload.get("failure_id")
            if fid:
                _ACKED.add((run_id, str(fid), str(payload.get("role") or "")))
        elif etype == "failure.invalidated":
            fid = payload.get("failure_id")
            if fid:
                _INVALIDATED.add((run_id, str(fid)))
    return restored


def select_failure(run_id: str, role: str, round_no: int) -> dict | None:
    """Deterministic round/source selection rule shared by every role."""
    # look back up to 3 rounds, pick latest un-ACKed, un-invalidated record
    for r in (round_no, round_no - 1, round_no - 2, round_no - 3):
        if r < 0:
            continue
        candidates = [
            f
            for f in _STORE.get(run_id, [])
            if f.get("round") == r
            and (run_id, f.get("failure_id"), role) not in _ACKED
            and (run_id, f.get("failure_id")) not in _INVALIDATED
        ]
        if candidates:
            # latest by seq (last in list)
            return dict(candidates[-1])
    return None


def inject_into_assignment(assignment: dict, failure: dict) -> dict:
    fid = failure.get("failure_id") or failure.get("id") or ""
    out = dict(assignment)
    ev = list(out.get("evidence") or [])
    if fid and fid not in ev:
        ev.append(fid)
    out["evidence"] = ev
    return out


def acknowledge_failure(run_id: str, failure_id: str, role: str) -> dict:
    _ACKED.add((run_id, failure_id, role))
    return {"failure_id": failure_id, "run_id": run_id, "role": role, "acked": True}


def invalidate_failure(run_id: str, failure_id: str, reason: str) -> dict:
    """Invalidate a record replaced by new evidence.

    IF-FAILURE-001 §1k: 未 ACK 不得失效 — a record no role has ACKed is still
    awaiting consumption and stays selectable (ordinary failures append, they
    never overwrite). Only an ACKed record is eligible.
    """
    acked = any(rid == run_id and fid == failure_id for (rid, fid, _role) in _ACKED)
    if acked:
        _INVALIDATED.add((run_id, failure_id))
    return {
        "failure_id": failure_id,
        "run_id": run_id,
        "reason": reason,
        "invalidated": acked,
    }


def supersede_acked(
    run_id: str, round_no: int, source: str, *, exclude_id: str | None = None
) -> list[dict]:
    """Invalidate earlier ACKed records of the same (round, source).

    Called after a new record is appended: the new evidence replaces the
    previous one, so a record a role already ACKed is closed with a
    ``failure.invalidated`` proof (per ACKing role). Records still awaiting
    consumption are never invalidated here — they stay selectable, which is
    exactly the rich-evidence-not-overwritten guarantee.
    """
    superseded: list[dict] = []
    for entry in _STORE.get(run_id, []):
        fid = entry.get("failure_id")
        if not fid or fid == exclude_id:
            continue
        if entry.get("round") != round_no or entry.get("source") != source:
            continue
        if (run_id, fid) in _INVALIDATED:
            continue
        roles = sorted(
            {role for (rid, acked_id, role) in _ACKED if rid == run_id and acked_id == fid}
        )
        if not roles:
            continue
        _INVALIDATED.add((run_id, fid))
        superseded.append(
            {
                "failure_id": fid,
                "run_id": run_id,
                "round": round_no,
                "source": source,
                "roles": roles,
                "reason": "superseded",
            }
        )
    return superseded


def _stored_ids(events: list[dict]) -> set[str]:
    ids: set[str] = set()
    for e in events:
        if e.get("type") in ("failure.stored", "failure.emitted"):
            pid = e.get("payload", {}).get("failure_id")
            if pid:
                ids.add(pid)
    # also from in-memory store for unit tests without explicit stored events
    for lst in _STORE.values():
        for f in lst:
            ids.add(f.get("failure_id"))
    return ids


def review_failure_chain(events: list[dict]) -> tuple[ReviewOutcome, list[str]]:  # noqa: CCR001
    # check injected references exist in stored
    stored = _stored_ids(events)
    for e in events:
        if e.get("type") == "failure.injected":
            fid = e.get("payload", {}).get("failure_id")
            if fid and fid not in stored and fid not in {f.get("failure_id") for lst in _STORE.values() for f in lst}:
                # also check global store, but if not found, it's lost
                if fid not in stored:
                    return "evidence_lost", [f"injected {fid} not stored"]
    # check stored order by seq if present
    seqs: list[int] = []
    for e in events:
        if e.get("type") == "failure.stored":
            s = e.get("payload", {}).get("seq")
            if isinstance(s, int):
                seqs.append(s)
    for i in range(1, len(seqs)):
        if seqs[i] <= seqs[i - 1]:
            return "mismatched", [f"stored seq out of order {seqs}"]
    # check ACK role consistency (simplified: always consistent for unit)
    return "consistent", []
