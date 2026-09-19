"""Behavior coverage for the kernel machine decide/result/outcome modules.

Targets ``machine_decide`` command builders (no-diff explain/review, DRAFT
pipelines, exit gates, reject teardown), the ``machine_results``
ResultCheckpoint/no-diff reducers, and the ``machine_outcomes`` failure
digestion paths (infra/format classes, stage routing, hotfix accounting).
Pure State-level tests: FR-0210, FR-11, SM-01.5-.7, SM-01.12, D-32.
"""

from __future__ import annotations

from types import SimpleNamespace

from tracks.kernel.machine import State
from tracks.kernel.machine_decide import (
    _decide_design_draft,
    _decide_design_exit,
    _decide_draft,
    _decide_exit,
    _decide_exit_gate,
    _decide_no_diff_explain,
    _decide_no_diff_review,
    _decide_pipeline,
    _decide_reject,
    _decide_result_pipeline,
    _decide_review,
    _decide_triage,
    _dispatch,
    _no_diff_dispatch_params,
)
from tracks.kernel.machine_outcomes import (
    _handle_failed_outcome,
    _handle_format_failure,
    _handle_infra_failure,
    _handle_verdict_format_failure,
    _is_format_verdict,
    _is_infra_failure,
    _on_command_issued,
    _on_outcome_received,
    _reset_m_test_dispatch_flag,
    _route_failed_outcome_by_stage,
    _track_hotfix_command_progress,
    _track_non_dispatch_milestone,
)
from tracks.kernel.machine_results import (
    _on_no_diff_detected,
    _on_no_diff_explained,
    _on_no_diff_reviewed,
    _on_result_checkpointed,
    _on_result_submitted,
    _on_result_validated,
)

EV = SimpleNamespace(seq=1, type="x", payload={})


# ---------------------------------------------------------------------------
# _dispatch / _decide_draft / design pipelines
# ---------------------------------------------------------------------------


def test_dispatch_omits_optional_params_when_absent():
    cmd = _dispatch("scribe", "DRAFT", "write story.md")
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["assignment"]["template_kind"] is None
    assert "doc" not in cmd.params and "stage" not in cmd.params
    assert "attempt" not in cmd.params and "review_round" not in cmd.params


def test_decide_draft_respond_carries_diff_ref_and_evidence():
    s = State(stage="M-STORY", substate="RESPOND", current_attempt=1, review_round=2)
    s.review_diff_ref = "deadbeef"
    s.last_failure = {"check": "template", "reason": "bad"}
    cmd = _decide_draft(s, "M-STORY", "RESPOND")
    assert cmd.params["diff_ref"] == "deadbeef"
    assert cmd.params["evidence"] == {"check": "template", "reason": "bad"}
    assert cmd.params["attempt"] == 2


def test_decide_draft_post_dispatch_states():
    s = State(stage="M-STORY", substate="DRAFT")
    s.doc_dispatched = True
    assert _decide_draft(s, "M-STORY", "DRAFT") is None
    s.doc_produced = True
    cmd = _decide_draft(s, "M-STORY", "DRAFT")
    assert cmd.kind == "validate_document"
    s.doc_validated = True
    cmd = _decide_draft(s, "M-STORY", "DRAFT")
    assert cmd.kind == "commit_document"
    assert cmd.params["message"] == "M-STORY: draft story.md"
    s.story_committed = True
    assert _decide_draft(s, "M-STORY", "DRAFT") is None


def test_decide_design_draft_carries_evidence_and_multi_skill():
    s = State(stage="M-DESIGN", substate="DRAFT", current_attempt=0, review_round=1)
    cmd = _decide_design_draft(s, "DRAFT")
    assert cmd.params["docs"] == ["architecture.md", "interfaces.md", "test-plan.md"]
    assert cmd.params["assignment"]["skills"] == [
        "tracks-discuz",
        "tracks-archer-design",
        "tracks-quality-guards",
    ]
    assert "evidence" not in cmd.params
    s.last_failure = {"check": "template"}
    cmd = _decide_design_draft(s, "RESPOND")
    assert cmd.params["evidence"] == {"check": "template"}
    assert cmd.params["substate"] == "RESPOND"
    s.doc_dispatched = True
    assert _decide_design_draft(s, "RESPOND") is None


def test_decide_design_exit_sequence_and_test_plan_checks():
    s = State(stage="M-DESIGN", design_validated=0)
    cmd = _decide_design_exit(s)
    assert cmd.params == {"doc": "architecture.md", "checks": ["template", "discussion_ready"]}
    s.design_validated = 1
    assert _decide_design_exit(s).params["doc"] == "interfaces.md"
    s.design_validated = 2
    cmd = _decide_design_exit(s)
    assert cmd.params["doc"] == "test-plan.md"
    assert cmd.params["checks"] == ["template", "discussion_ready", "trace", "test_tasks"]
    s.design_validated = 99
    assert _decide_design_exit(s).params["doc"] == "test-plan.md"
    s.exit_validated = True
    cmd = _decide_design_exit(s)
    assert cmd.kind == "write_frontmatter"
    assert cmd.params == {"stage": "M-DESIGN"}
    s.stage_exited = True
    assert _decide_design_exit(s) is None


def test_decide_exit_and_gate_routes():
    s = State(stage="M-STORY")
    cmd = _decide_exit(s, "M-STORY")
    assert cmd.params["checks"] == ["template", "discussion_ready"]
    s.exit_validated = True
    cmd = _decide_exit(s, "M-STORY")
    assert cmd.kind == "write_frontmatter"
    assert cmd.params["field"] == "sha"
    s.stage_exited = True
    assert _decide_exit(s, "M-STORY") is None
    design = State(stage="M-DESIGN", exit_validated=True)
    assert _decide_exit_gate(design, "M-DESIGN").kind == "write_frontmatter"
    story = State(stage="M-STORY")
    assert _decide_exit_gate(story, "M-STORY").kind == "validate_document"


def test_decide_pipeline_routes_design_to_multi_doc_builder():
    design = State(stage="M-DESIGN", substate="DRAFT")
    assert _decide_pipeline(design, "M-DESIGN", "DRAFT").params["docs"]
    story = State(stage="M-STORY", substate="DRAFT")
    assert _decide_pipeline(story, "M-STORY", "DRAFT").kind == "dispatch_agent"


def test_no_diff_explain_carries_last_failure_evidence():
    s = State(stage="M-TEST", substate="NO_DIFF_EXPLAIN")
    s.last_failure = {"check": "template", "reason": "no diff"}
    cmd = _decide_no_diff_explain(s)
    assert cmd.params["evidence"] == {"check": "template", "reason": "no diff"}


def test_decide_reject_teardown_sequence():
    s = State(version="v1", triage_decision="park")
    cmd = _decide_reject(s)
    assert cmd.kind == "record_backlog"
    assert cmd.params["decision"] == "park"
    s.backlog_recorded = True
    cmd = _decide_reject(s)
    assert cmd.kind == "delete_branch"
    assert cmd.params["branch_name"] == "releases/v1"
    s.branch_deleted = True
    cmd = _decide_reject(s)
    assert cmd.kind == "complete_run"
    assert cmd.params == {"terminal_state": "park"}


def test_decide_triage_waits_after_dispatch():
    s = State(stage="M-STORY", substate="TRIAGE")
    cmd = _decide_triage(s, "M-STORY")
    assert cmd.params["substate"] == "TRIAGE"
    s.doc_dispatched = True
    assert _decide_triage(s, "M-STORY") is None


def test_m_impl_reply_format_error_on_review_substate_resets_reviewer_flag():
    """2026-09-19 (run 01M2QTJB PRISM_RED): a reply_format_error verdict on
    an M-IMPL review substate must reset reviewer_dispatched (the generic
    fallthrough only cleared doc flags, parking the run forever)."""
    from tracks.kernel.m_impl_routing import _on_m_impl_verdict_failed

    s = State(stage="M-IMPL", substate="PRISM_RED", current_attempt=0)
    s.reviewer_dispatched = True
    _on_m_impl_verdict_failed(
        s, {"check": "reply_format_error", "reason": "missing_kind"}
    )
    assert s.reviewer_dispatched is False
    assert s.substate == "PRISM_RED"
    assert s.current_attempt == 1


# ---------------------------------------------------------------------------
# _decide_review
# ---------------------------------------------------------------------------


def test_decide_review_waits_after_dispatch_and_carries_evidence():
    s = State(stage="M-STORY", substate="SAGE_REVIEW", current_attempt=1)
    s.reviewer_dispatched = True
    assert _decide_review(s, "M-STORY", "SAGE_REVIEW") is None
    s.reviewer_dispatched = False
    s.last_failure = {"check": "template"}
    cmd = _decide_review(s, "M-STORY", "SAGE_REVIEW")
    assert cmd.params["evidence"] == {"check": "template"}
    assert cmd.params["role"] == "sage"
    assert "docs" not in cmd.params


def test_decide_review_design_switches_to_multi_skill_pack():
    s = State(stage="M-DESIGN", substate="PRISM_REVIEW", current_attempt=0)
    cmd = _decide_review(s, "M-DESIGN", "PRISM_REVIEW")
    assert cmd.params["docs"] == ["architecture.md", "interfaces.md", "test-plan.md"]
    assignment = cmd.params["assignment"]
    assert assignment["docs"] == ["architecture.md", "interfaces.md", "test-plan.md"]
    assert "skill" not in assignment and "skill_version" not in assignment
    assert assignment["skills"] == ["tracks-discuz", "tracks-prism-design"]


# ---------------------------------------------------------------------------
# no_diff explain/review
# ---------------------------------------------------------------------------


def test_no_diff_dispatch_params_include_result_context():
    s = State(stage="M-STORY", substate="SAGE_REVIEW", current_attempt=1, review_round=3)
    s.active_result = {
        "artifacts": ["story.md"],
        "base_sha": "BASE",
        "result_id": "R1",
        "stage": "M-STORY",
        "no_diff_origin_substate": "DRAFT",
    }
    s.no_diff_explanation = "nothing changed"
    params = _no_diff_dispatch_params(s, "NO_DIFF_REVIEW", "sage", [], "story.md", "review")
    assert params["no_diff_context"]["explanation"] == "nothing changed"
    assert params["no_diff_context"]["result_id"] == "R1"
    assert params["no_diff_context"]["substate"] == "DRAFT"
    assert params["assignment"]["template_kind"] == "story"
    assert params["doc"] == "story.md"
    assert "docs" not in params


def test_no_diff_dispatch_params_without_active_result_and_with_docs():
    s = State(stage="M-TEST", substate="PRISM_REVIEW")
    s.active_result = None
    params = _no_diff_dispatch_params(
        s, "NO_DIFF_EXPLAIN", "shield", ["test-plan.md"], None, "explain"
    )
    assert params["docs"] == ["test-plan.md"]
    assert params["assignment"]["docs"] == ["test-plan.md"]
    assert params["no_diff_context"]["artifacts"] == []
    assert params["no_diff_context"]["stage"] == "M-TEST"
    assert "explanation" not in params["no_diff_context"]


def test_decide_no_diff_explain_waits_and_routes_per_stage():
    s = State(stage="M-TEST", substate="NO_DIFF_EXPLAIN")
    s.doc_dispatched = True
    assert _decide_no_diff_explain(s) is None
    s.doc_dispatched = False
    cmd = _decide_no_diff_explain(s)
    assert cmd.params["role"] == "shield"
    assert cmd.params["docs"] == ["test-plan.md", "interfaces.md", "acceptance.md"]

    s = State(stage="M-IMPL", substate="NO_DIFF_EXPLAIN")
    cmd = _decide_no_diff_explain(s)
    assert cmd.params["role"] == "devon"
    assert "explain why no implementation diff" in cmd.params["objective"]

    s = State(stage="M-DESIGN", substate="NO_DIFF_EXPLAIN")
    cmd = _decide_no_diff_explain(s)
    assert cmd.params["role"] == "archer"
    assert cmd.params["docs"] == ["architecture.md", "interfaces.md", "test-plan.md"]

    s = State(stage="M-STORY", substate="NO_DIFF_EXPLAIN")
    cmd = _decide_no_diff_explain(s)
    assert cmd.params["doc"] == "story.md"
    assert cmd.params["role"] == "scribe"
    assert _decide_no_diff_explain(State(stage="NOPE")) is None


def test_decide_no_diff_review_waits_and_routes_per_stage():
    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW")
    s.no_diff_reviewer_dispatched = True
    assert _decide_no_diff_review(s) is None
    s.no_diff_reviewer_dispatched = False
    cmd = _decide_no_diff_review(s)
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "NO_DIFF_REVIEW"

    for stage in ("M-IMPL", "M-DESIGN"):
        s = State(stage=stage, substate="NO_DIFF_REVIEW")
        assert _decide_no_diff_review(s).params["role"] == "prism"

    s = State(stage="M-SPEC", substate="NO_DIFF_REVIEW")
    cmd = _decide_no_diff_review(s)
    assert cmd.params["role"] == "lex"
    assert cmd.params["doc"] == "spec.md"
    assert _decide_no_diff_review(State(stage="NOPE")) is None


def test_decide_result_pipeline_validate_checkpoint_publish():
    s = State(stage="M-STORY")
    s.active_result = {"result_id": "R1", "artifacts": ["story.md"]}
    cmd = _decide_result_pipeline(s)
    assert cmd.kind == "validate_result"
    assert cmd.params["result_id"] == "R1"

    s.active_result["validated"] = True
    cmd = _decide_result_pipeline(s)
    assert cmd.kind == "checkpoint_result"
    assert cmd.params["allowed_paths"] == []
    assert cmd.params["no_diff_approved"] is False

    s.active_result["checkpointed"] = True
    cmd = _decide_result_pipeline(s)
    assert cmd.kind == "publish_result"
    assert cmd.params["created_commit"] is False
    assert cmd.params["domain_event"] == {}


# ---------------------------------------------------------------------------
# machine_results: ResultCheckpoint reducers
# ---------------------------------------------------------------------------


def test_on_result_submitted_captures_and_clears_pending():
    s = State(pending={"kind": "dispatch_agent"})
    _on_result_submitted(s, {"result_id": "R1", "source": "agent"}, EV)
    assert s.active_result == {"result_id": "R1", "source": "agent"}
    assert s.pending is None


def test_on_result_validated_sets_doc_validated_for_author_substates():
    s = State(pending={"x": 1})
    _on_result_validated(s, {}, EV)
    assert s.pending is None
    assert s.active_result is None

    for substate, expect in (("DRAFT", True), ("RESPOND", True), ("SAGE_REVIEW", False)):
        s = State()
        s.active_result = {"substate": substate}
        _on_result_validated(s, {}, EV)
        assert s.active_result["validated"] is True
        assert s.doc_validated is expect


def test_on_result_checkpointed_marks_commit_metadata():
    s = State(pending={"x": 1})
    _on_result_checkpointed(s, {}, EV)
    assert s.pending is None and s.active_result is None

    s = State()
    s.active_result = {}
    _on_result_checkpointed(s, {"commit_sha": "abc", "created_commit": True}, EV)
    assert s.active_result["checkpointed"] is True
    assert s.active_result["commit_sha"] == "abc"
    assert s.active_result["created_commit"] is True


# ---------------------------------------------------------------------------
# machine_results: no_diff reducers
# ---------------------------------------------------------------------------


def test_on_no_diff_detected_preserves_result_and_resets_flags():
    s = State(stage="M-STORY", substate="SAGE_REVIEW", pending={"x": 1})
    s.active_result = {"result_id": "R1"}
    s.no_diff_reviewer_dispatched = True
    _on_no_diff_detected(s, {}, EV)
    assert s.active_result["no_diff_origin_substate"] == "SAGE_REVIEW"
    assert s.active_result["result_id"] == "R1"
    assert s.substate == "NO_DIFF_EXPLAIN"
    assert s.doc_dispatched is False
    assert s.no_diff_explanation is None
    assert s.no_diff_reviewer_dispatched is False


def test_on_no_diff_explained_captures_text():
    s = State(stage="M-STORY", substate="NO_DIFF_EXPLAIN", pending={"x": 1})
    _on_no_diff_explained(s, {"explanation": "because"}, EV)
    assert s.no_diff_explanation == "because"
    assert s.substate == "NO_DIFF_REVIEW"
    assert s.no_diff_reviewer_dispatched is False


def test_on_no_diff_reviewed_pass_resumes_pipeline():
    s = State(stage="M-STORY", substate="NO_DIFF_REVIEW")
    s.active_result = {"no_diff_origin_substate": "DRAFT"}
    s.no_diff_explanation = "why"
    _on_no_diff_reviewed(s, {"verdict": "pass"}, EV)
    assert s.active_result["validated"] is True
    assert s.active_result["no_diff_approved"] is True
    assert s.substate == "DRAFT"
    assert s.no_diff_explanation is None
    assert s.no_diff_reviewer_dispatched is False


def test_on_no_diff_reviewed_pass_without_active_result():
    s = State(stage="M-STORY", substate="NO_DIFF_REVIEW")
    _on_no_diff_reviewed(s, {"verdict": "pass"}, EV)
    assert s.active_result is None
    assert s.substate == "NO_DIFF_REVIEW"


def test_on_no_diff_reviewed_reject_routes_by_stage():
    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW", current_attempt=0)
    s.active_result = {"no_diff_origin_substate": "DRAFT"}
    s.no_diff_explanation = "excuse"
    _on_no_diff_reviewed(s, {"verdict": "revise", "attempt": 2}, EV)
    assert s.active_result is None
    assert s.substate == "WRITE"
    assert s.last_failure["check"] == "no_diff_justified"
    assert s.last_failure["evidence"] == "excuse"
    assert s.current_attempt == 1

    s = State(stage="M-IMPL", substate="NO_DIFF_REVIEW", current_attempt=0)
    s.active_result = {"no_diff_origin_substate": "GREEN"}
    s.doc_dispatched = True
    _on_no_diff_reviewed(s, {"verdict": "revise"}, EV)
    assert s.substate == "GREEN"
    assert s.doc_dispatched is False
    assert s.current_attempt == 1

    s = State(stage="M-DESIGN", substate="NO_DIFF_REVIEW", current_attempt=0)
    s.active_result = {"no_diff_origin_substate": "DRAFT"}
    _on_no_diff_reviewed(s, {"verdict": "revise", "attempt": 2}, EV)
    assert s.substate == "DRAFT"
    assert s.current_attempt == 2


def test_on_no_diff_reviewed_reject_generic_review_and_doc_paths():
    s = State(stage="M-STORY", substate="SAGE_REVIEW", current_attempt=0)
    s.active_result = {"no_diff_origin_substate": "SAGE_REVIEW"}
    s.reviewer_dispatched = True
    _on_no_diff_reviewed(s, {"verdict": "revise"}, EV)
    assert s.substate == "SAGE_REVIEW"
    assert s.reviewer_dispatched is False

    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    s.active_result = {"no_diff_origin_substate": "DRAFT"}
    s.doc_dispatched = True
    _on_no_diff_reviewed(s, {"verdict": "revise", "attempt": 3}, EV)
    assert s.substate == "DRAFT"
    assert s.doc_dispatched is False
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"


def test_on_no_diff_reviewed_reject_without_active_result_defaults():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    _on_no_diff_reviewed(s, {"verdict": "revise"}, EV)
    assert s.substate == "DRAFT"
    assert s.current_attempt == 1


# ---------------------------------------------------------------------------
# machine_outcomes: command.issued / outcome.received routing
# ---------------------------------------------------------------------------


def test_command_issued_no_diff_flags_checked_before_stage():
    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW")
    _on_command_issued(s, {"command": {"kind": "dispatch_agent", "params": {}}}, EV)
    assert s.no_diff_reviewer_dispatched is True
    assert s.doc_dispatched is False

    s = State(stage="M-TEST", substate="NO_DIFF_EXPLAIN")
    _on_command_issued(s, {"command": {"kind": "dispatch_agent", "params": {}}}, EV)
    assert s.doc_dispatched is True


def test_command_issued_m_test_dispatch_sets_substate_and_flags():
    s = State(stage="M-TEST", substate="DISPATCH")
    _on_command_issued(s, {"command": {"kind": "dispatch_agent", "params": {}}}, EV)
    assert s.substate == "WRITE"
    assert s.doc_dispatched is True

    s = State(stage="M-TEST", substate="WRITE")
    _on_command_issued(
        s,
        {"command": {"kind": "dispatch_agent", "params": {"substate": "PRISM_REVIEW"}}},
        EV,
    )
    assert s.reviewer_dispatched is True
    assert s.doc_dispatched is False


def test_track_non_dispatch_milestones_collect_and_hotfix():
    s = State(stage="M-TEST", substate="WRITE")
    _track_non_dispatch_milestone(s, "collect_tests")
    assert s.substate == "COLLECT"
    _track_non_dispatch_milestone(State(stage="M-TEST", substate="WRITE"), "other")
    _track_non_dispatch_milestone(State(stage="M-IMPL"), "collect_tests")
    hotfix = State(stage="M-HOTFIX-TRIAGE")
    _track_non_dispatch_milestone(hotfix, "precheck_hotfix")
    assert hotfix.doc_dispatched is True

    s = State(stage="M-HOTFIX-TRIAGE")
    _track_hotfix_command_progress(s, "precheck_hotfix")
    assert s.doc_dispatched is True
    _track_hotfix_command_progress(s, "validate_anchor")
    assert s.doc_validated is True
    _track_hotfix_command_progress(s, "complete_hotfix_entry")
    assert s.doc_dispatched is True


def test_command_issued_non_dispatch_records_pending_and_milestone():
    s = State(stage="M-TEST", substate="WRITE")
    _on_command_issued(s, {"command": {"kind": "collect_tests", "params": {}}}, EV)
    assert s.pending["kind"] == "collect_tests"
    assert s.substate == "COLLECT"


def test_command_issued_m_impl_and_generic_stage_flags():
    s = State(stage="M-IMPL", substate="PLANNING")
    _on_command_issued(
        s, {"command": {"kind": "dispatch_agent", "params": {"substate": "PLANNING"}}}, EV
    )
    assert s.doc_dispatched is True

    s = State(stage="M-IMPL", substate="PRISM_FINAL")
    _on_command_issued(
        s,
        {"command": {"kind": "dispatch_agent", "params": {"substate": "PRISM_FINAL"}}},
        EV,
    )
    assert s.reviewer_dispatched is True

    s = State(stage="M-STORY", substate="SAGE_REVIEW")
    _on_command_issued(
        s,
        {"command": {"kind": "dispatch_agent", "params": {"substate": "SAGE_REVIEW"}}},
        EV,
    )
    assert s.reviewer_dispatched is True

    s = State(stage="M-STORY", substate="DRAFT")
    _on_command_issued(
        s, {"command": {"kind": "dispatch_agent", "params": {"substate": "DRAFT"}}}, EV
    )
    assert s.doc_dispatched is True


def test_outcome_received_security_scope_is_ignored():
    s = State(stage="M-STORY", substate="DRAFT")
    _on_outcome_received(s, {"scope": "security", "status": "failed"}, EV)
    assert s.doc_produced is False
    assert s.last_failure is None


def test_outcome_received_failed_routes_through_digestion():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    _on_outcome_received(s, {"status": "failed", "self_report": "boom"}, EV)
    assert s.last_failure["reason"] == "boom"
    assert s.current_attempt == 1

    s = State(stage="M-TEST", substate="WRITE")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.substate == "COLLECT"

    s = State(stage="M-TEST", substate="PRISM_REVIEW")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.reviewer_produced is True

    s = State(stage="M-IMPL", substate="RED")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.substate == "RED_GATE"


def test_outcome_received_issues_substate_consumes_failed_attempt():
    s = State(stage="M-STORY", substate="ISSUES", current_attempt=0)
    _on_outcome_received(s, {"status": "failed"}, EV)
    assert s.current_attempt == 1

    s = State(stage="M-STORY", substate="ISSUES", current_attempt=1)
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.current_attempt == 1


def test_outcome_received_done_projects_checkpoint_and_substates():
    s = State(stage="M-STORY", substate="DRAFT")
    _on_outcome_received(s, {"status": "done", "result_checkpoint": {"result_id": "R"}}, EV)
    assert s.active_result == {"result_id": "R"}
    assert s.doc_produced is True

    s = State(stage="M-STORY", substate="TRIAGE")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.awaiting == "triage"

    s = State(stage="M-STORY", substate="SAGE_REVIEW")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.reviewer_produced is True

    s = State(stage="M-STORY", substate="DRAFT")
    _on_outcome_received(s, {"status": "done"}, EV)
    assert s.doc_produced is True


def test_reset_m_test_dispatch_flag_routes_substates():
    s = State(stage="M-TEST", substate="PRISM_REVIEW")
    s.reviewer_dispatched = True
    _reset_m_test_dispatch_flag(s)
    assert s.reviewer_dispatched is False

    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW")
    s.no_diff_reviewer_dispatched = True
    _reset_m_test_dispatch_flag(s)
    assert s.no_diff_reviewer_dispatched is False

    s = State(stage="M-TEST", substate="WRITE")
    s.doc_dispatched = True
    _reset_m_test_dispatch_flag(s)
    assert s.doc_dispatched is False


# ---------------------------------------------------------------------------
# machine_outcomes: failure classes
# ---------------------------------------------------------------------------


def test_format_and_infra_classifiers():
    assert _is_format_verdict({"failure_class": "manifest_malformed"}) is True
    assert _is_format_verdict({"check": "evidence_malformed"}) is True
    assert _is_format_verdict({}) is False
    assert _is_infra_failure({"failure_class": "timeout"}) is True
    assert _is_infra_failure({}) is False  # default agent_error is semantic
    assert _is_infra_failure({"failure_class": "agent_error"}) is False


def test_handle_verdict_format_failure_routes_and_escalates():
    s = State(stage="M-TEST", substate="RED_GATE")
    s.doc_dispatched = True
    _handle_verdict_format_failure(s, {"check": "manifest_malformed", "reason": "shape"})
    assert s.substate == "RED"
    assert s.doc_dispatched is False
    assert s.format_failure_streak == 1

    s = State(stage="M-TEST", substate="PRISM_REVIEW")
    s.reviewer_dispatched = True
    _handle_verdict_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.reviewer_dispatched is False

    s = State(stage="M-STORY", substate="DRAFT")
    s.doc_dispatched = True
    _handle_verdict_format_failure(s, {"check": "evidence_malformed"})
    assert s.doc_dispatched is False

    s = State(stage="M-IMPL", substate="RED", format_failure_streak=1)
    _handle_verdict_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.substate == "RULING"
    assert s.status == "active"

    s = State(stage="M-IMPL", substate="RED", format_failure_streak=4)
    _handle_verdict_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"

    s = State(stage="M-TEST", substate="WRITE", format_failure_streak=2)
    _handle_verdict_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.substate == "WRITE"


def test_handle_format_failure_records_evidence_not_attempt():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=1)
    s.doc_dispatched = True
    _handle_format_failure(
        s,
        {
            "failure_class": "evidence_malformed",
            "self_report": "too long",
            "audit_evidence": "blob-1",
        },
    )
    assert s.current_attempt == 1
    assert s.last_failure == {
        "check": "evidence_malformed",
        "reason": "too long",
        "evidence": "blob-1",
    }
    assert s.doc_dispatched is False

    s = State(stage="M-TEST", substate="PRISM_REVIEW", current_attempt=1)
    s.reviewer_dispatched = True
    _handle_format_failure(s, {"artifact_ref": "ref-2"})
    assert s.last_failure["check"] == "manifest_malformed"
    assert s.last_failure["evidence"] == "ref-2"
    assert s.reviewer_dispatched is False


def test_handle_format_failure_streak_policies():
    s = State(stage="M-IMPL", substate="RED", format_failure_streak=1)
    _handle_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.substate == "RULING"
    assert s.status == "active"

    s = State(stage="M-TEST", substate="WRITE", format_failure_streak=2)
    _handle_format_failure(s, {"failure_class": "evidence_malformed"})
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"

    s = State(stage="M-IMPL", substate="RED", format_failure_streak=4)
    _handle_format_failure(s, {"failure_class": "manifest_malformed"})
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"
    assert s.substate == "RED"


def test_handle_infra_failure_resets_without_touching_failure_or_attempt():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=2)
    s.last_failure = {"check": "previous"}
    s.doc_dispatched = True
    _handle_infra_failure(s)
    assert s.current_attempt == 2
    assert s.last_failure == {"check": "previous"}
    assert s.doc_dispatched is False
    assert s.infra_failure_streak == 1
    assert s.status == "active"

    s = State(stage="M-STORY", substate="SAGE_REVIEW", infra_failure_streak=2)
    s.reviewer_dispatched = True
    _handle_infra_failure(s)
    assert s.reviewer_dispatched is False
    assert s.status == "awaiting_human"
    assert s.awaiting == "escalation"


def test_handle_infra_failure_m_test_no_diff_review_clears_guard():
    """OOB 2026-09-18 (run 01M2Q...): an operator-killed NO_DIFF_REVIEW
    dispatch produced an infra 'signal' outcome; the generic doc/review
    reset left no_diff_reviewer_dispatched stuck True and the run parked
    invisibly (decide() None forever)."""
    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW")
    s.no_diff_reviewer_dispatched = True
    _handle_infra_failure(s)
    assert s.no_diff_reviewer_dispatched is False
    assert s.status == "active"
    assert s.infra_failure_streak == 1


def test_handle_format_failure_m_test_no_diff_review_clears_guard():
    s = State(stage="M-TEST", substate="NO_DIFF_REVIEW")
    s.no_diff_reviewer_dispatched = True
    _handle_format_failure(
        s, {"failure_class": "manifest_malformed", "self_report": "x"}
    )
    assert s.no_diff_reviewer_dispatched is False
    assert s.format_failure_streak == 1


def test_handle_failed_outcome_format_class_short_circuits():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    _handle_failed_outcome(s, {"failure_class": "manifest_malformed", "self_report": "x"})
    assert s.current_attempt == 0
    assert s.format_failure_streak == 1


def test_handle_failed_outcome_infra_class_short_circuits():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    _handle_failed_outcome(s, {"failure_class": "non_zero_exit"})
    assert s.current_attempt == 0
    assert s.infra_failure_streak == 1
    assert s.last_failure is None


def test_handle_failed_outcome_semantic_records_and_consumes():
    s = State(stage="M-STORY", substate="DRAFT", current_attempt=0)
    s.infra_failure_streak = 1
    s.format_failure_streak = 1
    _handle_failed_outcome(
        s, {"self_report": "bad doc", "artifact_ref": "blob-3"}
    )
    assert s.last_failure == {
        "check": "agent_error",
        "reason": "bad doc",
        "evidence": "blob-3",
    }
    assert s.infra_failure_streak == 0
    assert s.format_failure_streak == 0
    assert s.current_attempt == 1
    assert s.doc_dispatched is False

    s = State(stage="M-STORY", substate="SAGE_REVIEW", current_attempt=2)
    s.reviewer_dispatched = True
    _handle_failed_outcome(s, {})
    assert s.current_attempt == 3
    assert s.status == "awaiting_human"
    assert s.reviewer_dispatched is False


def test_route_failed_outcome_by_stage_m_impl_devon_rgr_to_diagnose():
    s = State(stage="M-IMPL", substate="RED", current_attempt=0)
    assert _route_failed_outcome_by_stage(
        s, {"role": "devon", "status": "failed"}
    ) is True
    assert s.substate == "DIAGNOSE"
    assert s.current_attempt == 1

    s = State(stage="M-IMPL", substate="PRISM_FINAL", current_attempt=0)
    s.reviewer_dispatched = True
    assert _route_failed_outcome_by_stage(s, {"role": "prism"}) is True
    assert s.reviewer_dispatched is False
    assert s.current_attempt == 1

    s = State(stage="M-IMPL", substate="GREEN", current_attempt=0)
    s.doc_dispatched = True
    assert _route_failed_outcome_by_stage(s, {"role": "devon", "status": "done"}) is True
    assert s.doc_dispatched is False
    assert s.current_attempt == 1


def test_route_failed_outcome_by_stage_m_test_stub_gap_and_other():
    s = State(stage="M-TEST", substate="RED_CHECK", current_attempt=0)
    assert _route_failed_outcome_by_stage(
        s, {"failure_class": "stub_gap"}
    ) is True
    assert s.substate == "DIAGNOSE"
    assert s.diagnose_classification == "stub_gap"
    assert s.current_attempt == 0

    s = State(stage="M-TEST", substate="WRITE", current_attempt=0)
    s.doc_dispatched = True
    assert _route_failed_outcome_by_stage(s, {"role": "shield"}) is True
    assert s.doc_dispatched is False
    assert s.current_attempt == 1

    s = State(stage="M-START")
    assert _route_failed_outcome_by_stage(s, {}) is False


def test_handle_hotfix_failed_outcome_park_at_three():
    s = State(stage="M-HOTFIX-TRIAGE", substate="SAGE_TRIAGE", current_attempt=0)
    _handle_failed_outcome(s, {"status": "failed", "attempt": 1})
    assert s.substate == "SAGE_TRIAGE"
    assert s.current_attempt == 1
    assert s.status == "active"

    s = State(stage="M-HOTFIX-TRIAGE", substate="SAGE_TRIAGE", current_attempt=2)
    _handle_failed_outcome(s, {"status": "failed", "attempt": 3})
    assert s.substate == "AWAIT_HUMAN"
    assert s.status == "awaiting_human"
    assert s.awaiting == "hotfix_triage"
