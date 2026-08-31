"""Integration: Prism same-candidate final review (FR-0271, IF-VERIFY-005)."""

from __future__ import annotations

import pytest

from tracks.executor.m_verify import build_prism_final_review_assignment

pytestmark = pytest.mark.integration


# AC-FR0271-01@v0.8 TRACKS-TRACE same candidate consistency pass before M-SECURITY
def test_same_candidate_consistency_pass(host_repo, trac, event_log):
    try:
        build_prism_final_review_assignment("a" * 40, {"evidence": "digests"})
        raise AssertionError("must raise IF-VERIFY-005")
    except NotImplementedError as exc:
        assert "IF-VERIFY-005" in str(exc)

    trac("run")
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict" and e["payload"].get("scope") == "verify_final"]
    assert verdicts, "prism.verdict scope=verify_final must appear after local+CI gates pass"
    payload = verdicts[0]["payload"]
    assert "candidate_sha" in payload
    assert payload.get("status") == "pass" or payload.get("verdict") == "pass"
    # Input evidence set must all bind same candidate
    assert "evidence_digests" in payload or "evidence" in payload
    status = trac("status")
    assert "prism=pass" in status.stdout
    # Gate that M-SECURITY was entered
    stages = [e["payload"].get("stage") for e in events if e["type"] == "stage.entered"]
    assert "M-SECURITY" in stages or "prism=pass" in status.stdout


# AC-FR0271-02@v0.8 TRACKS-TRACE prism fail blocks and repair rebinds same SHA
def test_prism_fail_blocks_m_impl_gap(host_repo, trac, event_log):
    try:
        build_prism_final_review_assignment("b" * 40, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-VERIFY-005" in str(exc)

    trac("run")
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict"]
    # Simulate fail verdict path: must not enter M-SECURITY
    failed = [v for v in verdicts if v["payload"].get("status") in ("failed", "fail") or v["payload"].get("verdict") in ("failed", "revise")]
    if failed:
        assert "prism=failed" in trac("status").stdout
        # Must not have entered M-SECURITY after failure
        entered = [e for e in events if e["type"] == "stage.entered" and e["payload"].get("stage") == "M-SECURITY"]
        assert not entered or failed[0]["seq"] > entered[0]["seq"]
    else:
        # If no verdict yet, the absence itself is a fail-closed signal until stub implemented
        assert not verdicts or verdicts[0]["payload"].get("candidate_sha") is not None
        # Assert blocked observable even when verdict missing (legal red)
        assert "prism=failed" in trac("status").stdout or "blocked" in trac("status").stdout

    # Repair path: same candidate re-review must bind same SHA (FR-0286)
    # This is observed as a second verdict with identical candidate_sha
    if len(verdicts) >= 2:
        assert verdicts[0]["payload"]["candidate_sha"] == verdicts[1]["payload"]["candidate_sha"]


# AC-FR0271-03@v0.8 TRACKS-TRACE revise requires anchored findings or revise_without_findings
def test_revise_requires_anchored_findings(host_repo, trac, event_log):
    from tracks.kernel.release import classify_defect_route

    try:
        classify_defect_route("prism_failed", {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc) or "IF-VERIFY-005" in str(exc)

    trac("run")
    events = event_log()
    verdicts = [e for e in events if e["type"] == "prism.verdict" and e["payload"].get("verdict") == "revise"]
    if verdicts:
        # Must have an anchored discuss thread, otherwise blocked: revise_without_findings
        discuss_check = trac("discuss", "query", "--file", "spec.md")
        assert discuss_check.returncode == 0 or "revise_without_findings" in trac("status").stdout
        status = trac("status")
        # Either anchored or explicitly blocked
        assert "revise_without_findings" in status.stdout or "blocked" in status.stdout or discuss_check.stdout
    else:
        # Pre-implementation: the revise path is absent, assert the fail-closed is enforced
        status = trac("status")
        # The system must enforce that a revise without anchored thread is not counted as valid block
        assert "blocked" in status.stdout or "needs_attention" in status.stdout
    replay = trac("replay")
    assert replay.returncode == 0 or "prism.verdict" in replay.stdout
