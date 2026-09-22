"""Shared seeding for the IF-LEASE-001 completion-fence anchors.

The completion CAS (``ServiceDB.complete_command``, interfaces §1i) quarantines
an outcome whose claim generation no longer owns the command. Two arrange
facts make that reachable: the command row (so the quarantine is run-scoped,
§1a #17) and a superseded claim generation (the fencing floor moved on after a
lease take-over). This seeder arranges that realistic lifecycle once —
register, claim at the current generation, lease take-over, recovery requeue,
re-claim at the new generation — so the frozen anchors complete with the
superseded generation and assert the contracted quarantine payload instead of
a command id that never existed.
"""

from __future__ import annotations

import json
from typing import Any

from tracks.supervisor.lease import Lease, acquire_lease


def arrange_stale_completion(db: Any, run_id: str, command_id: str) -> tuple[Lease, Lease]:
    """Arrange a command claimed at generation N while the fence sits at N+1.

    Returns ``(stale, renewed)``: ``stale.generation`` is the claim generation
    a superseded worker still holds, ``renewed.generation`` the current fencing
    generation. Completing with ``stale.generation`` is the quarantined late
    result; completing with ``renewed.generation`` commits.
    """
    stale = acquire_lease(db, run_id, "worker-a", 30)
    db.register_command(
        {
            "command_id": command_id,
            "kind": "drive_run",
            "params_json": json.dumps({"run_id": run_id}, sort_keys=True),
            "params_digest": f"digest-{command_id}",
            "idempotency_key": f"{command_id}-key",
            "actor": "local-user",
            "actor_class": "system",
            "surface": "internal",
            "project_id": None,
            "run_id": run_id,
        }
    )
    assert db.claim_command(command_id, "worker-a", stale.generation)
    renewed = acquire_lease(db, run_id, "worker-b", 30)
    db.requeue_claimed("lease_expired")
    assert db.claim_command(command_id, "worker-b", renewed.generation)
    return stale, renewed


def quarantined_events(db: Any, run_id: str) -> list[dict]:
    """The auditable ``worker.late_result`` events for a run (§1a #17)."""
    return [e for e in db.read_events(run_id=run_id) if e["type"] == "worker.late_result"]
