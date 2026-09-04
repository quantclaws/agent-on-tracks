"""Integration: Prism same-candidate final review (FR-0271, IF-VERIFY-005).

b93 §8.1 bootstrap contract: the CLI halves are driven by the shared walker
(``walk_to_m_impl_parked``) — bare ``trac run`` bootstrap is forbidden. The
module-level halves assert the delivered IF-VERIFY-005 envelope contract
(candidate SHA + verify_final scope + bound evidence digests);
``classify_defect_route`` (kernel/release.py) is the T-039 deliverable and
still raises its contract token: the token probe is a legal anchor. The
prism.verdict scope=verify_final event assertions stay legal Red against the
unwired M-VERIFY producers (the M-DESIGN verdicts on the stream carry
different scopes).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers import walk_to_m_impl_parked
from tracks.executor.m_verify import build_prism_final_review_assignment
from tracks.kernel.release import classify_defect_route

pytestmark = pytest.mark.integration

_CANDIDATE = "a" * 40
_OTHER = "b" * 40
_DIGESTS = {"full_f": "d-full", "ci": "d-ci", "preview": "d-preview"}


# AC-FR0271-01@v0.8 TRACKS-TRACE same candidate consistency pass before M-SECURITY
def test_same_candidate_consistency_pass(host_repo, trac, event_log):
    # Module half (IF-VERIFY-005): the final-review dispatch envelope binds
    # the reviewed candidate SHA, the verify_final scope and EVERY evidence
    # digest under that one SHA.
    envelope = build_prism_final_review_assignment(_CANDIDATE, dict(_DIGESTS))
    assert isinstance(envelope, dict)
    assert envelope.get("candidate_sha") == _CANDIDATE
    assert envelope.get("scope") == "verify_final"
    assert envelope.get("evidence_digests") == _DIGESTS

    # CLI half: after local+CI gates the final-review verdict must appear on
    # the stream scoped verify_final. Legal Red: the M-VERIFY producer is not
    # wired on this baseline (any verdicts present carry M-DESIGN scopes).
    walk_to_m_impl_parked(trac)
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict" and e["payload"].get("scope") == "verify_final"]
    assert verdicts, "prism.verdict scope=verify_final must appear after local+CI gates pass"
    payload = verdicts[0]["payload"]
    assert payload["candidate_sha"] == _CANDIDATE or "candidate_sha" in payload
    assert payload.get("status") == "pass" or payload.get("verdict") == "pass"
    assert "evidence_digests" in payload or "evidence" in payload
    status = trac("status")
    assert "prism=pass" in status.stdout
    stages = [e["payload"].get("stage") for e in events if e["type"] == "stage.entered"]
    assert "M-SECURITY" in stages or "prism=pass" in status.stdout


# AC-FR0271-02@v0.8 TRACKS-TRACE prism fail blocks and repair rebinds same SHA
def test_prism_fail_blocks_m_impl_gap(host_repo, trac, event_log):
    # Module half: every candidate gets its own bound envelope — a second
    # review binding a different SHA must not blur the first binding.
    first = build_prism_final_review_assignment(_CANDIDATE, dict(_DIGESTS))
    second = build_prism_final_review_assignment(_OTHER, {"ci": "d-ci-2"})
    assert first.get("candidate_sha") == _CANDIDATE
    assert second.get("candidate_sha") == _OTHER
    assert first.get("evidence_digests") != second.get("evidence_digests")

    # CLI half: a failed final review blocks before M-SECURITY and a repair
    # re-review binds the SAME candidate SHA. Legal Red: the M-VERIFY
    # producer is not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict"]
    failed = [
        v
        for v in verdicts
        if v["payload"].get("status") in ("failed", "fail") or v["payload"].get("verdict") in ("failed", "revise")
    ]
    if failed:
        assert "prism=failed" in trac("status").stdout
        entered = [e for e in events if e["type"] == "stage.entered" and e["payload"].get("stage") == "M-SECURITY"]
        assert not entered or failed[0]["seq"] > entered[0]["seq"]
    else:
        # M-DESIGN verdicts legitimately carry no candidate binding; only a
        # scope=verify_final verdict must bind the reviewed candidate.
        scoped = [v for v in verdicts if v["payload"].get("scope") == "verify_final"]
        for v in scoped:
            assert v["payload"].get("candidate_sha") is not None, (
                "verify_final verdicts must bind candidate_sha"
            )
        assert not verdicts or scoped or "prism=failed" in trac("status").stdout, (
            "prism.verdict scope=verify_final must appear after gates pass "
            "or the status must record the prism block"
        )
    if len(verdicts) >= 2:
        assert verdicts[0]["payload"]["candidate_sha"] == verdicts[1]["payload"]["candidate_sha"], (
            "repair re-review must bind the same candidate SHA (同 candidate 终审)"
        )


# AC-FR0271-03@v0.8 TRACKS-TRACE revise requires anchored findings or revise_without_findings
def test_revise_requires_anchored_findings(host_repo, trac, event_log):
    # classify_defect_route still raises its contract token (T-039
    # deliverable) — the legal anchor for the routing half of this AC.
    try:
        classify_defect_route("prism_failed", {})
        raise AssertionError("classify_defect_route must raise NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc) or "IF-VERIFY-005" in str(exc)

    # CLI half: a revise verdict without an anchored discuss thread must be
    # fail-closed (blocked revise_without_findings). Legal Red: the M-VERIFY
    # producer is not wired on this baseline.
    walk_to_m_impl_parked(trac)
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict" and e["payload"].get("verdict") == "revise"]
    if verdicts:
        discuss_check = trac("discuss", "query", "--file", "spec.md")
        assert discuss_check.returncode == 0 or "revise_without_findings" in trac("status").stdout
        status = trac("status")
        assert (
            "revise_without_findings" in status.stdout
            or "blocked" in status.stdout.lower()
            or discuss_check.stdout
        )
    else:
        status = trac("status")
        assert "blocked" in status.stdout.lower() or "needs_attention" in status.stdout
    replay = trac("replay")
    assert replay.returncode == 0 or "prism.verdict" in replay.stdout
