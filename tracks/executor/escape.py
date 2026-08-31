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


def establish_escape_barrier(run_id: str) -> dict:
    """Cutover sequence + quiesce/cancel in-flight dispatches; emits
    escape.barrier_established {cutover_seq} (AC-FR0287-02)."""
    raise NotImplementedError("IF-ESCAPE-001")


def quarantine_late_outcome(barrier: dict, outcome: dict) -> dict:
    """Outcome dispatched before the barrier but arriving after it:
    escape.late_outcome quarantined; never checkpointed or published."""
    raise NotImplementedError("IF-ESCAPE-001")


def stale_downstream_evidence(run_id: str, target_stage: str) -> list[dict]:
    """evidence.staled(reason=human_return) for everything after the target
    (candidate freeze / FULL_F / CI / security / preview / decision); the
    test freeze lifts only when returning to before M-TEST."""
    raise NotImplementedError("IF-ESCAPE-001")


def report_irreversible_operations(run_id: str) -> list[dict]:
    """already_executed list (merge/tag/artifact/release) requiring explicit
    Human confirmation before the pointer moves (AC-FR0287-04)."""
    raise NotImplementedError("IF-ESCAPE-001")


def abandon_run(run_id: str, reason: str) -> dict:
    """terminal_state=cancelled; evidence kept; issues/branches untouched;
    zero external side effects; later `trac run` is rejected (SM-01.21/22)."""
    raise NotImplementedError("IF-ESCAPE-002")
