"""Failure-evidence end-to-end review chain (FR-0280, NFR-0146).

emitted → stored (append-only) → selected (round/source rule) → injected →
consumed → acked → invalidated → replay. Rich evidence still awaiting
consumption is never silently overwritten by ordinary failures; the review
reducer verifies chain consistency and reports gaps fail-closed.
"""

from __future__ import annotations

from typing import Literal

ReviewOutcome = Literal["consistent", "evidence_lost", "mismatched"]


def record_failure(run_id: str, round_no: int, source: str, record: dict) -> dict:
    raise NotImplementedError("IF-FAILURE-001")


def select_failure(run_id: str, role: str, round_no: int) -> dict | None:
    """Deterministic round/source selection rule shared by every role."""
    raise NotImplementedError("IF-FAILURE-001")


def inject_into_assignment(assignment: dict, failure: dict) -> dict:
    raise NotImplementedError("IF-FAILURE-001")


def acknowledge_failure(run_id: str, failure_id: str, role: str) -> dict:
    raise NotImplementedError("IF-FAILURE-001")


def invalidate_failure(run_id: str, failure_id: str, reason: str) -> dict:
    raise NotImplementedError("IF-FAILURE-001")


def review_failure_chain(events: list[dict]) -> tuple[ReviewOutcome, list[str]]:
    raise NotImplementedError("IF-FAILURE-001")
