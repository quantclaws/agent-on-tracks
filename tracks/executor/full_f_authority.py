"""IF-FULLF-001 FULL_F authority predicate.

``is_authoritative_full_f`` is the single arbiter of whether a
``full.executed`` event payload is an authoritative FULL_F proof
(design-delta INV-1):

  passed is True AND full_f_eligible is True
  AND round in {"FULL_1" (empty ledger), "fallback_full", "FULL_F"}
  AND (candidate_sha is None OR payload.execution_commit == candidate_sha)
  AND non-empty evidence_ids
  AND identity quadruple match (selection_id)

It MUST NOT trust ``payload.serves_as_full_f`` alone: historical seq 7362
carries ``serves_as_full_f=false, full_f_eligible=true`` and is authoritative,
while seq 5896 carries ``serves_as_full_f=true, full_f_eligible=false`` and is
not.

Callers that have already resolved a bound ``evidence.reused(kind="full_f")``
whose source event is bound to the candidate pass ``candidate_sha=None``:
the reuse binding itself is the candidate proof. Callers holding a raw
``full.executed`` pass the expected candidate and the payload's
``execution_commit`` must match.
"""

from __future__ import annotations

from collections.abc import Mapping

_AUTHORITATIVE_ROUNDS = frozenset({"FULL_1", "fallback_full", "FULL_F"})


def _candidate_matches(payload: Mapping, candidate_sha: str | None) -> bool:
    if candidate_sha is None:
        return True
    return payload.get("execution_commit") == candidate_sha


def _evidence_ids_present(payload: Mapping) -> bool:
    evidence_ids = payload.get("evidence_ids")
    if not isinstance(evidence_ids, (list, tuple)):
        return False
    return any(
        isinstance(evidence_id, str) and evidence_id.strip()
        for evidence_id in evidence_ids
    )


def _selection_id(payload: Mapping) -> str | None:
    selection_id = payload.get("selection_id")
    if isinstance(selection_id, str) and selection_id.strip():
        return selection_id
    return None


def _identity_mapping_matches(identity: object, selection_id: str) -> bool:
    if not isinstance(identity, Mapping):
        return False
    return identity.get("selection_id") == selection_id


def _identity_basis_matches(
    basis: object, identity: Mapping | None, selection_id: str
) -> bool:
    if not isinstance(basis, (list, tuple)) or len(basis) != 4:
        return False
    if basis[3] != selection_id:
        return False
    if identity is None:
        return True
    command = identity.get("command")
    if not isinstance(command, (list, tuple)):
        return False
    if not isinstance(basis[1], (list, tuple)):
        return False
    return (
        basis[0] == identity.get("tree")
        and list(basis[1]) == list(command)
        and basis[2] == identity.get("env")
    )


def _identity_quadruple_matches(payload: Mapping) -> bool:
    """The recorded identity quadruple must be internally consistent.

    The quadruple is (tree, command, env, selection_id). New payloads carry
    both an ``identity`` mapping and its flattened ``identity_basis`` list;
    legacy payloads may carry only one of them. Either way the selection id
    must be present and every recorded component must agree.
    """
    selection_id = _selection_id(payload)
    if selection_id is None:
        return False
    identity = payload.get("identity")
    basis = payload.get("identity_basis")
    if identity is None and basis is None:
        return False
    if identity is not None and not _identity_mapping_matches(identity, selection_id):
        return False
    return basis is None or _identity_basis_matches(basis, identity, selection_id)


def is_authoritative_full_f(
    payload: Mapping,
    candidate_sha: str | None = None,
) -> bool:
    """Whether *payload* is an authoritative FULL_F proof (INV-1)."""
    if not isinstance(payload, Mapping):
        return False
    if payload.get("passed") is not True:
        return False
    if payload.get("full_f_eligible") is not True:
        return False
    round_name = payload.get("round")
    if not isinstance(round_name, str) or round_name not in _AUTHORITATIVE_ROUNDS:
        return False
    if not _candidate_matches(payload, candidate_sha):
        return False
    if not _evidence_ids_present(payload):
        return False
    return _identity_quadruple_matches(payload)
