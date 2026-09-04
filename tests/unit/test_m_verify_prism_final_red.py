"""T-013 RED (Prism final-review round): build_prism_final_review_assignment
-- the IF-VERIFY-005 same-candidate consistency-review dispatch envelope
(FR-0268 AC face, interfaces §1d).

Pins the still-undelivered stub at tracks/executor/m_verify.py (the
NotImplementedError("IF-VERIFY-005") body of
``build_prism_final_review_assignment``): after local + CI gates pass, the
M-VERIFY chain dispatches a same-candidate consistency review whose input
evidence set is bound to ONE candidate SHA (interfaces §1d
"build_prism_final_review_assignment 的输入证据集合必须全部绑定同一
candidate_sha"; IF-VERIFY-005 "派发同 candidate 一致性复审
（scope=verify_final）").

The anchor pins the dispatch envelope's contract tokens, following the
in-repo binding convention (tracks/checks/trace.py composer closed field
set ``{candidate_sha, artifact_digest, evidence_digests, ...}``):

- ``candidate_sha``: the envelope carries THE candidate SHA being reviewed;
- ``scope``: the review scope is ``verify_final`` (IF-VERIFY-005 token);
- ``evidence_digests``: every input evidence digest travels in the envelope,
  i.e. the whole evidence set is bound under that one candidate SHA.

The anchor fails on the current baseline with assertion_failure on the
contract token (NotImplementedError stub body converted to assertion
failure). Only unit tests are added (RED discipline, manifest
red_test_paths = tests/unit); the fullf_reuse anchors stay untouched.
"""

from __future__ import annotations

from tracks.executor import m_verify

_CANDIDATE = "c" * 40
_DIGESTS = {"full_f": "d-full", "ci": "d-ci", "preview": "d-preview"}


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


# AC-FR0268-03@v0.8 TRACKS-TRACE IF-VERIFY-005 final-review envelope binding
def test_prism_final_assignment_binds_evidence_digests():
    """The dispatch envelope carries the candidate SHA, the verify_final
    scope and every evidence digest bound under that one SHA (interfaces
    §1d, IF-VERIFY-005 scope=verify_final)."""
    build = getattr(m_verify, "build_prism_final_review_assignment", None)
    if build is None:
        _fail(
            "executor/m_verify.py missing build_prism_final_review_assignment "
            "export (IF-VERIFY-005 interfaces §1d Prism final-review assembly)"
        )
    try:
        envelope = build(_CANDIDATE, dict(_DIGESTS))
    except NotImplementedError as err:
        _fail(f"build_prism_final_review_assignment not implemented ({err})")
    if not isinstance(envelope, dict):
        _fail(
            f"dispatch envelope must be a dict, got "
            f"{type(envelope).__name__}"
        )
    if envelope.get("candidate_sha") != _CANDIDATE:
        _fail(
            f"envelope must carry the reviewed candidate SHA {_CANDIDATE!r}, "
            f"got candidate_sha={envelope.get('candidate_sha')!r} in "
            f"{sorted(envelope)} (interfaces §1d 同 candidate 终审)"
        )
    if envelope.get("scope") != "verify_final":
        _fail(
            f"review scope must be 'verify_final' (IF-VERIFY-005), got "
            f"scope={envelope.get('scope')!r}"
        )
    if envelope.get("evidence_digests") != _DIGESTS:
        _fail(
            f"every evidence digest must travel bound in the envelope, got "
            f"evidence_digests={envelope.get('evidence_digests')!r}, want "
            f"{_DIGESTS!r} (interfaces §1d: 证据集合绑定同一 candidate_sha)"
        )
