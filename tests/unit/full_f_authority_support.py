"""Shared payload fixtures for the IF-FULLF-001 authority tests.

The expected values are enumerated from INV-1 / interfaces §1a (the
contract text), never derived from the implementation under test.
"""

from __future__ import annotations

from collections.abc import Mapping

from tracks.executor.full_f_authority import is_authoritative_full_f

# seq 7362 shape (v0.12.0 slice): FULL_F, eligible, execution_commit 44c14de…
CANDIDATE_SHA = "44c14de" + "0" * 33
# seq 5896 shape: serves_as_full_f=true but ineligible and a foreign commit.
FOREIGN_SHA = "ee781bf" + "0" * 33
SELECTION_ID = "selection-7362"

IDENTITY = {
    "tree": "tree-stamp",
    "command": ["pytest", "-q", "tests/unit"],
    "env": "env-digest",
    "selection_id": SELECTION_ID,
}


def authoritative_payload(**overrides) -> dict:
    """A complete authoritative ``full.executed`` payload (7362 shape)."""
    payload = {
        "round": "FULL_F",
        "passed": True,
        "full_f_eligible": True,
        "serves_as_full_f": False,
        "execution_commit": CANDIDATE_SHA,
        "evidence_ids": ["evidence-7362"],
        "selection_id": SELECTION_ID,
        "identity": dict(IDENTITY),
        "identity_basis": [
            IDENTITY["tree"],
            list(IDENTITY["command"]),
            IDENTITY["env"],
            IDENTITY["selection_id"],
        ],
    }
    payload.update(overrides)
    return payload


def authority(payload: Mapping | None, candidate_sha: str | None = CANDIDATE_SHA) -> bool:
    """Evaluate the predicate, turning the interface stub into a real assertion."""
    try:
        return is_authoritative_full_f(payload, candidate_sha)
    except NotImplementedError:
        raise AssertionError(
            "IF-FULLF-001 仍是桩：合同要求 is_authoritative_full_f 已实现行为"
        ) from None
