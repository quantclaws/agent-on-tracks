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

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WaitPolicy:
    """NFR-0152 locked defaults; overridable by serve flags (§1j)."""

    initial_s: int = 60
    cap_s: int = 900
    poll_interval_s: int = 5
    poll_idle_cap_s: int = 60


def classify_wait(failure: dict) -> Any:
    """Map an executor/harness failure onto WaitSpec (wait_class, known_reset,
    retry_at | backoff); unrecoverable failures return None (§1d)."""
    raise NotImplementedError("IF-WAIT-001")


def enter_wait(db: Any, run_id: str, spec: Any) -> None:
    """Persist the active wait row and emit wait.entered (§1a #13)."""
    raise NotImplementedError("IF-WAIT-001")


def resolve_wait(db: Any, run_id: str, resolved_by: str) -> None:
    """Clear the active wait row and emit wait.resolved (§1a #14)."""
    raise NotImplementedError("IF-WAIT-001")


def next_probe(policy: WaitPolicy, backoff: dict | None) -> dict:
    """Compute the next bounded backoff state (interval doubles to cap_s)."""
    raise NotImplementedError("IF-WAIT-001")
