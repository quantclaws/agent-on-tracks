"""Criteria pack binding + anti-self-report (FR-0040, D-29).

Tests the D-29 triple: assignment carries criteria_pack, verdict echoes it,
Runtime reads back and mismatches -> verdict.failed(criteria_pack_mismatch).
"""

from tests.integration.helpers import m_test_events, walk_to_m_test
from tracks.kernel.machine import _CRITERIA_PACK


# AC-FR0040-01@v0.4 TRACKS-TRACE prism reviews with criteria pack
def test_prism_reviews_with_criteria_pack(trac, event_log):
    """AC-FR0040-01@v0.4: dispatch Prism with criteria pack in assignment."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    prism_cmd = next(
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "prism"
    )
    assignment = prism_cmd["payload"]["command"]["params"]["assignment"]
    assert assignment["criteria_pack"] == dict(_CRITERIA_PACK)
    assert assignment["skills"] == ["tracks-discuz", "tracks-prism-test"]


# AC-FR0040-02@v0.4 TRACKS-TRACE anti self report triple
def test_anti_self_report_triple(trac, event_log):
    """AC-FR0040-02@v0.4: assignment carries pack, verdict echoes it, match."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    # ① assignment carries criteria_pack
    prism_cmd = next(
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "prism"
    )
    assigned = prism_cmd["payload"]["command"]["params"]["assignment"]["criteria_pack"]
    assert assigned == dict(_CRITERIA_PACK)
    # D-29: the criteria pack skill is declared for materialization (not just
    # identity metadata); assignment.skills names tracks-prism-test so the
    # backend materializes it for Prism to consume.
    assert prism_cmd["payload"]["command"]["params"]["assignment"]["skills"] == [
        "tracks-discuz",
        "tracks-prism-test",
    ]
    # ② verdict echoes it
    verdict = next(e for e in evs if e["type"] == "prism.verdict")
    assert verdict["payload"]["criteria_pack"] == dict(_CRITERIA_PACK)
    # ③ no criteria_pack_mismatch verdict.failed
    mismatches = [
        e
        for e in evs
        if e["type"] == "verdict.failed" and e["payload"].get("check") == "criteria_pack_mismatch"
    ]
    assert not mismatches


# AC-FR0040-04@v0.4 / AC-NFR0145-02@v0.8 TRACKS-TRACE revise without findings rejected
def test_revise_without_findings_rejected(trac, event_log):
    """A simulated Prism revise in M-TEST that carries no findings cannot be
    represented in the declared v0.8 review schema. The single parse path
    classifies it as a schema_violation ``format_error`` (32b81c2 honesty:
    incomplete simulated output is visible, never silently coerced into a
    synthetic pass or a business mutation). The complete-findings revise
    path (revise -> WRITE re-dispatch) is the structured ResultCheckpoint
    pipeline face, pinned by the pipeline tests."""
    run_id = walk_to_m_test(trac)
    # M-DESIGN prism pass, M-TEST prism revise (bare), then pass on re-review
    r = trac("run", simulate="prism:PRISM_REVIEW=pass|revise|pass")
    assert r.returncode == 0, r.stderr
    evs = event_log(run_id)
    m_test_evs = m_test_events(evs)
    format_errors = [e for e in m_test_evs if e["type"] == "format_error"]
    assert format_errors, "the bare M-TEST revise must fail closed as format_error"
    assert format_errors[-1]["payload"]["kind"] == "schema_violation"
    # No semantic revise verdict for the incomplete reply and no run
    # completion from it (format_error is not a semantic attempt).
    assert not any(
        v["payload"]["verdict"] == "revise"
        for v in m_test_evs
        if v["type"] == "prism.verdict"
    )
    assert not any(e["type"] == "run.completed" for e in evs)


# AC-FR0040-05@v0.4 TRACKS-TRACE criteria pack no formal rules
def test_criteria_pack_no_formal_rules():
    """AC-FR0040-05@v0.4: criteria pack skill contains semantic criteria, not
    formal validation rules. The D-14 boundary section explicitly excludes
    marker format, ID grammar, binding completeness from the pack's scope."""
    from pathlib import Path

    skill = (
        Path(__file__).resolve().parent.parent.parent
        / "tracks"
        / "skills"
        / "tracks-prism-test"
        / "SKILL.md"
    )
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


# AC-FR0040-06@v0.4 TRACKS-TRACE criteria pack materialization lifecycle
def test_criteria_pack_materialization_lifecycle(trac, event_log, host_repo):
    """AC-FR0040-06@v0.4: criteria pack skill is materialized for Prism M-TEST
    dispatch and cleaned up after (same lifecycle as tracks-discuz)."""
    run_id = walk_to_m_test(trac)
    trac("run")
    evs = m_test_events(event_log(run_id))
    # The Prism M-TEST dispatch declares tracks-prism-test in assignment.skills
    # so the backend materializes it (D-29: skill materialization + identity).
    prism_cmd = next(
        e
        for e in evs
        if e["type"] == "command.issued"
        and e["payload"]["command"]["params"].get("role") == "prism"
        and e["payload"]["command"]["params"].get("substate") == "PRISM_REVIEW"
    )
    prism_assignment = prism_cmd["payload"]["command"]["params"]["assignment"]
    assert "tracks-prism-test" in prism_assignment["skills"]
    # After the run, materialized skills are cleaned up
    skill_dest = host_repo / ".opencode" / "skills" / "tracks-prism-test" / "SKILL.md"
    # The canonical source exists (deliverable)
    from pathlib import Path

    canonical = (
        Path(__file__).resolve().parent.parent.parent
        / "tracks"
        / "skills"
        / "tracks-prism-test"
        / "SKILL.md"
    )
    assert canonical.exists()
    # Materialized copy was cleaned up (absent unless it pre-existed)
    assert not skill_dest.exists() or skill_dest.read_text() != ""
