"""Declared-envelope reply seam (IF-ENVELOPE-002 prerequisite slice).

Tiny shared helpers for the backend raw-output propagation. The Runtime's
collection face (``Executor._format_error_shortcircuit``) owns classification;
the backends only attach or encode the actual reply text — they never
classify, never reconstruct a real reply from a normalized payload, and never
synthesize a successful reply.

Two invariants the parity design depends on live here:

- ``is_declared_assignment`` is the single predicate both backends (and their
  tests) share for "this dispatch declares the kernel envelope". Undeclared
  results must keep their exact legacy shape (no ``raw_output`` key).
- ``encode_envelope_reply`` stamps the version the DECLARING implementation
  passes in — its own reviewed declaration, not an import of the Runtime
  authority — so a stale implementation cannot claim a version it does not
  actually implement.
"""

from __future__ import annotations

import json

ENVELOPE_FENCE_OPEN = "```tracks-envelope\n"
ENVELOPE_FENCE_CLOSE = "\n```\n"


def is_declared_assignment(assignment: object) -> bool:
    """True when the dispatch assignment declares the kernel envelope."""
    return isinstance(assignment, dict) and bool(assignment.get("envelope"))


def declared_result_path(assignment: object) -> str | None:
    """#174 result-file pointer: assignment result_file.result_path.

    Single source for both consumers — the executor's collection face
    (file-preferred envelope load) and the backend's Devon evidence
    extraction (file payload first, text fallback). A missing key (or a
    non-dict block) is the legacy text path.
    """
    if not isinstance(assignment, dict):
        return None
    block = assignment.get("result_file")
    if not isinstance(block, dict):
        return None
    path = block.get("result_path")
    return path if isinstance(path, str) and path else None


def encode_envelope_reply(kind: str, payload: dict, version: int) -> str:
    """The exact fenced reply text for an envelope the backend actually speaks.

    ``kind`` must be the kind the assignment declared and ``version`` the
    backend's own reviewed contract declaration — the Runtime re-parses and
    re-validates the whole reply regardless, so the seam only fixes the wire
    shape (one fenced block, one JSON object, header kind/version + payload).
    """
    envelope = {
        "envelope": {"kind": kind, "version": version},
        "payload": payload,
    }
    return (
        ENVELOPE_FENCE_OPEN
        + json.dumps(envelope, sort_keys=True)
        + ENVELOPE_FENCE_CLOSE
    )
