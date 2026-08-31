"""Integration: in-place repair and new candidate re-walk (FR-0286, IF-REPAIR-001/002)."""

from __future__ import annotations

import subprocess

import pytest

from tracks.executor.repair import (
    assert_frozen_tests_untouched,
    classify_defect,
    mark_fix_new_candidate,
    open_repair_round,
)
from tracks.kernel.release import classify_defect_route

pytestmark = pytest.mark.integration


# AC-FR0286-01@v0.8 TRACKS-TRACE no auto rollback in place rounds
def test_no_auto_rollback_in_place_rounds(host_repo, trac, event_log):
    # Test each repair helper with correct arity where possible
    try:
        classify_defect({}, {})  # type: ignore[call-arg]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)
    try:
        open_repair_round("run", {}, 3)  # type: ignore[call-arg]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)
    try:
        assert_frozen_tests_untouched(host_repo, {})  # type: ignore[call-arg]
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)
    try:
        classify_defect_route("local_gate_failed", {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)

    trac("run")
    events = event_log()
    # No automatic stage.rolled_back to M-DESIGN
    rolled = [e for e in events if e["type"] == "stage.rolled_back" and "M-DESIGN" in str(e["payload"])]
    assert not rolled, "in-place repair must not produce automatic stage.rolled_back to M-DESIGN"
    # Must show repair round started with classification instead
    repair = [e for e in events if e["type"] == "repair.round_started"]
    assert repair, "repair.round_started must appear for blocking defect"
    assert repair[0]["payload"]["round"] <= 3
    assert "classification" in repair[0]["payload"]
    assert "budget" in repair[0]["payload"]
    status = trac("status").stdout
    # Status must render repair=in_place
    assert "repair=in_place" in status
    # Discuss must not show auto rollback
    assert "stage.rolled_back" not in trac("discuss", "query", "--file", "spec.md").stdout


# AC-FR0286-02@v0.8 TRACKS-TRACE repair disciplines and frozen tests untouched
def test_repair_disciplines_and_frozen_tests(host_repo, trac, event_log):
    try:
        classify_defect({"kind": "behavior"}, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)
    try:
        assert_frozen_tests_untouched(host_repo, {})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-001" in str(exc)

    trac("run")
    trac("run")
    events = event_log()
    # Discipline must be one of closed set and repair must have started
    repairs = [e for e in events if e["type"] == "repair.round_started"]
    assert repairs, "repair.round_started must appear for discipline check"
    for r in repairs:
        disc = r["payload"].get("classification", {}).get("discipline")
        assert disc in ("red_first", "verification_only", "cve_advisory", "contract_delta", None)
    # Frozen tests must not change except Shield targeted additions
    diff = subprocess.run(["git", "diff", "--name-only"], cwd=host_repo, capture_output=True, text=True, check=True).stdout
    assert "tests/integration/test_verify_candidate.py" not in diff
    assert "discipline" in str(repairs[0]["payload"].get("classification", {}))


# AC-FR0286-03@v0.8 TRACKS-TRACE fix new candidate rewalks verify
def test_fix_new_candidate_rewalks_verify(host_repo, trac, event_log):
    try:
        mark_fix_new_candidate("run", "a" * 40)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-002" in str(exc)
    except TypeError:
        try:
            mark_fix_new_candidate("run", "old")  # type: ignore[call-arg]
            raise AssertionError("expected NotImplementedError")
        except NotImplementedError as exc2:
            assert "IF-REPAIR-002" in str(exc2)

    trac("run")
    events = event_log()
    candidate_before = next((e["payload"]["candidate_sha"] for e in events if "candidate_sha" in e["payload"]), None)
    # Create a fix commit => new candidate
    (host_repo / "fix.txt").write_text("fix\n", encoding="utf-8")
    subprocess.run(["git", "add", "fix.txt"], cwd=host_repo, check=True)
    subprocess.run(["git", "commit", "-m", "fix: repair"], cwd=host_repo, check=True)
    trac("run")
    events2 = event_log()
    staled = [e for e in events2 if e["type"] == "evidence.staled" and e["payload"].get("reason") == "fix_new_candidate"]
    assert staled, "evidence.staled reason=fix_new_candidate must appear after fix commit"
    # Old preview must not be reused
    previewed = [e for e in events2 if e["type"] == "release.previewed"]
    if previewed and candidate_before:
        assert all(p["payload"]["candidate_sha"] != candidate_before for p in previewed if p["seq"] > staled[0]["seq"])
    # New candidate.frozen must appear
    frozen = [e for e in events2 if e["type"] == "candidate.frozen"]
    assert frozen
    new_candidate = frozen[-1]["payload"]["candidate_sha"]
    assert new_candidate != candidate_before
    assert "candidate" in trac("status").stdout


# AC-FR0286-04@v0.8 TRACKS-TRACE irreparable blocked routes to known issue or escape
def test_irreparable_blocked_routes_to_known_issue_or_escape(host_repo, trac, event_log):
    from tracks.executor.repair import judge_irreparable

    try:
        judge_irreparable(3, 3, {"attribution": "unchanged"})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-REPAIR-002" in str(exc)

    trac("run")
    # Exhaust budget: simulate 3 failed rounds with unchanged attribution
    for _ in range(3):
        trac("run")
    events = event_log()
    irreparable = [e for e in events if "irreparable" in str(e.get("payload", "")).lower() or "blocked" in str(e).lower()]
    assert irreparable, "irreparable or blocked event must appear"
    status = trac("status").stdout.lower()
    assert "blocked: irreparable" in status or "irreparable" in status
    # Must be routable to Known Issue or escape
    assert "known_issue" in status or "escape" in status or "return" in status or "abandon" in status or "blocked" in status
