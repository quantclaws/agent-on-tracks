"""T-013 RED (binding invariant round): collect_binding_violations -- the
IF-VERIFY-001 full-chain binding scan (FR-0268 AC face, interfaces §1d).

Pins the still-undelivered stub at tracks/executor/m_verify.py (the
NotImplementedError("IF-VERIFY-001") body of ``collect_binding_violations``):

1. Unbound release-chain evidence is flagged -- events bound to a foreign
   candidate SHA, and release-chain events carrying no binding at all, are
   reported as violations; events correctly bound to the candidate are not
   flagged (interfaces §1d "collect_binding_violations", IF-VERIFY-001
   "全链事件绑定校验"; NFR-0143: the candidate SHA is the single primary
   identity every release event must carry).
2. A fully bound chain is accepted -- every event bound to the same
   candidate SHA yields an empty violation list (the positive half of the
   same invariant).

Event fixtures follow the in-repo binding convention: release-chain event
payloads carry ``candidate_sha`` (tracks/checks/trace.py composer closed
field set; kernel/release.py NFR-0143 projection), kinds follow the
M-VERIFY producer chain (candidate.frozen / evidence.reused / ci.run_observed
/ prism.verdict).

Both anchors fail on the current baseline with assertion_failure on the
contract token (NotImplementedError stub bodies are converted to assertion
failures). Only unit tests are added (RED discipline, manifest
red_test_paths = tests/unit); the fullf_reuse anchors stay untouched.
"""

from __future__ import annotations

from tracks.executor import m_verify

_CANDIDATE = "c" * 40
_FOREIGN = "f" * 40


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _scan(events, candidate_sha):
    scan = getattr(m_verify, "collect_binding_violations", None)
    if scan is None:
        _fail(
            "executor/m_verify.py missing collect_binding_violations export "
            "(IF-VERIFY-001 interfaces §1d full-chain binding scan)"
        )
    try:
        return scan(events, candidate_sha)
    except NotImplementedError as err:
        _fail(f"collect_binding_violations not implemented ({err})")


def _event(kind: str, candidate_sha: str | None) -> dict:
    event = {"kind": kind}
    if candidate_sha is not None:
        event["candidate_sha"] = candidate_sha
    return event


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-VERIFY-001 binding scan flags unbound
def test_collect_binding_violations_flags_unbound_evidence():
    """Foreign-SHA and binding-less release-chain evidence are flagged;
    events bound to the candidate are not (interfaces §1d, IF-VERIFY-001
    全链事件绑定校验, NFR-0143)."""
    events = [
        _event("candidate.frozen", _CANDIDATE),
        _event("ci.run_observed", _CANDIDATE),
        _event("evidence.reused", _FOREIGN),
        _event("prism.verdict", None),
    ]
    violations = _scan(events, _CANDIDATE)
    if len(violations) != 2:
        _fail(
            f"exactly the 2 unbound events must be flagged, got "
            f"{len(violations)} violation(s): {violations!r}"
        )
    joined = " ".join(str(v) for v in violations)
    for unbound_kind in ("evidence.reused", "prism.verdict"):
        if unbound_kind not in joined:
            _fail(
                f"violations must identify the unbound {unbound_kind} event, "
                f"got {violations!r}"
            )
    for bound_kind in ("candidate.frozen", "ci.run_observed"):
        if bound_kind in joined:
            _fail(
                f"event bound to the candidate must not be flagged, but "
                f"{bound_kind!r} appears in {violations!r}"
            )


# AC-FR0268-02@v0.8 TRACKS-TRACE IF-VERIFY-001 fully bound chain accepted
def test_collect_binding_violations_accepts_fully_bound_chain():
    """A chain whose every event carries the same candidate SHA yields an
    empty violation list (interfaces §1d positive half)."""
    events = [
        _event(kind, _CANDIDATE)
        for kind in (
            "candidate.frozen",
            "evidence.reused",
            "ci.run_observed",
            "prism.verdict",
        )
    ]
    violations = _scan(events, _CANDIDATE)
    if violations != []:
        _fail(
            f"fully bound chain must yield no violations, got "
            f"{violations!r}"
        )
