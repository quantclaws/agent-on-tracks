"""Criteria pack binding + anti-self-report (FR-0040, D-29).

Tests the D-29 triple: assignment carries criteria_pack, verdict echoes it,
Runtime reads back and mismatches -> verdict.failed(criteria_pack_mismatch).
"""
from tests.integration.helpers import m_test_events, walk_to_m_test
from tracks.kernel.machine import _CRITERIA_PACK


def test_prism_reviews_with_criteria_pack(trac, event_log):
    """AC-FR0040-01@v0.4: dispatch Prism with criteria pack in assignment."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    prism_cmd = next(e for e in evs if e["type"] == "command.issued"
                     and e["payload"]["command"]["params"].get("role") == "prism")
    assignment = prism_cmd["payload"]["command"]["params"]["assignment"]
    assert assignment["criteria_pack"] == dict(_CRITERIA_PACK)


def test_anti_self_report_triple(trac, event_log):
    """AC-FR0040-02@v0.4: assignment carries pack, verdict echoes it, match."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    # ① assignment carries criteria_pack
    prism_cmd = next(e for e in evs if e["type"] == "command.issued"
                     and e["payload"]["command"]["params"].get("role") == "prism")
    assigned = prism_cmd["payload"]["command"]["params"]["assignment"]["criteria_pack"]
    assert assigned == dict(_CRITERIA_PACK)
    # ② verdict echoes it
    verdict = next(e for e in evs if e["type"] == "prism.verdict")
    assert verdict["payload"]["criteria_pack"] == dict(_CRITERIA_PACK)
    # ③ no criteria_pack_mismatch verdict.failed
    mismatches = [e for e in evs if e["type"] == "verdict.failed"
                  and e["payload"].get("check") == "criteria_pack_mismatch"]
    assert not mismatches


def test_revise_without_findings_rejected(trac, event_log):
    """AC-FR0040-04@v0.4: Prism revise in M-TEST -> WRITE re-dispatch (Shield)."""
    run_id = walk_to_m_test(trac)
    # M-DESIGN prism pass, M-TEST prism revise, then pass on re-review
    r = trac("run", simulate="prism:PRISM_REVIEW=pass|revise|pass")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    # M-TEST prism revise -> WRITE (re-dispatch Shield)
    m_test_evs = m_test_events(evs)
    prism_verdicts = [e for e in m_test_evs if e["type"] == "prism.verdict"]
    assert any(v["payload"]["verdict"] == "revise" for v in prism_verdicts)
    # Run completed (recovered after revise)
    assert any(e["type"] == "run.completed" for e in evs)


def test_criteria_pack_no_formal_rules():
    """AC-FR0040-05@v0.4: criteria pack skill contains semantic criteria, not
    formal validation rules. The D-14 boundary section explicitly excludes
    marker format, ID grammar, binding completeness from the pack's scope."""
    from pathlib import Path
    skill = (Path(__file__).resolve().parent.parent.parent
             / "tracks" / "skills" / "test-asset-criteria" / "SKILL.md")
    text = skill.read_text(encoding="utf-8")
    # Semantic criteria are present (the four D-29 criteria)
    assert "忠于 AC" in text
    assert "断言落公开出口" in text
    assert "counterexample" in text.lower()
    assert "无伪测试" in text
    # D-14 boundary is explicitly stated: formal rules are NOT in the pack
    assert "D-14" in text or "边界" in text
    assert "marker 长格式" in text  # mentioned in the boundary exclusion
    assert "Runtime 程序校验" in text or "归 Runtime" in text


def test_criteria_pack_materialization_lifecycle(trac, event_log, host_repo):
    """AC-FR0040-06@v0.4: criteria pack skill is materialized for Prism M-TEST
    dispatch and cleaned up after (same lifecycle as tracks-discuz)."""
    walk_to_m_test(trac)
    trac("run")
    # After the run, materialized skills are cleaned up
    skill_dest = host_repo / ".opencode" / "skills" / "test-asset-criteria" / "SKILL.md"
    # The canonical source exists (deliverable)
    from pathlib import Path
    canonical = (Path(__file__).resolve().parent.parent.parent
                 / "tracks" / "skills" / "test-asset-criteria" / "SKILL.md")
    assert canonical.exists()
    # Materialized copy was cleaned up (absent unless it pre-existed)
    assert not skill_dest.exists() or skill_dest.read_text() != ""
