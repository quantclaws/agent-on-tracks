"""IF-FULLF-001 FULL_F authority predicate (interface stub).

`is_authoritative_full_f` is the single arbiter of whether a `full.executed`
event payload is an authoritative FULL_F proof (design-delta INV-1):

  passed is True AND full_f_eligible is True
  AND round in {"FULL_1" (empty ledger), "fallback_full", "FULL_F"}
  AND (candidate_sha is None
       OR payload.execution_commit == candidate_sha
       OR bound evidence.reused(kind="full_f") whose source event's
          execution_commit == candidate_sha)
  AND non-empty evidence_ids
  AND identity quadruple match (selection_id)

It MUST NOT trust payload.serves_as_full_f alone. Stub for Shield contract
tests; behavior implemented by Devon (ARCH-012-HF262 §2).
"""

from __future__ import annotations

from collections.abc import Mapping


def is_authoritative_full_f(
    payload: Mapping,
    candidate_sha: str | None = None,
) -> bool:
    raise NotImplementedError("IF-FULLF-001")
