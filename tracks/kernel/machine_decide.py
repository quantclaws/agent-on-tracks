"""decide()-side command builders: the dispatch_agent Command factory, the
DRAFT/RESPOND/TRIAGE/review/EXIT pipelines for the author stages, the
M-DESIGN multi-doc pipeline, the no_diff explain/review dispatches, the
reject teardown, and the ResultCheckpoint pipeline commands.

Extracted from ``machine.py`` for module-size compliance (C0302).
``machine.decide``/``_decide_stage_route`` route to these builders.

No circular import: imports only ``events``, ``m_impl``/``m_test``
context constants and ``stage_registry`` at runtime; ``State`` is
imported under ``TYPE_CHECKING`` only (duck-typed at runtime).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .events import Command
from .m_impl import _M_IMPL_CONTEXT_DOCS
from .m_test import _M_TEST_CONTEXT_DOCS
from .stage_registry import _REVIEW_SUBSTATE, _STAGES, DESIGN_DOCS

if TYPE_CHECKING:
    from .machine import State


def _dispatch(
    role: str,
    substate: str,
    objective: str,
    doc: str | None = None,
    stage: str | None = None,
    attempt: int | None = None,
    review_round: int | None = None,
) -> Command:
    params = {"role": role, "substate": substate, "objective": objective}
    if doc:
        params["doc"] = doc
    if stage:
        params["stage"] = stage
    if attempt is not None:
        params["attempt"] = attempt
    if review_round is not None:
        params["review_round"] = review_round
    assignment = {
        "kind": substate,
        "template_kind": doc.removesuffix(".md") if doc else None,
        "skill": "tracks-discuz",
        "skill_version": "0.2",
    }
    params["assignment"] = assignment
    return Command(kind="dispatch_agent", params=params)


def _decide_reject(s: State) -> Command:
    """NO-GO / PARK teardown (D-06, FR-09): record backlog → delete the release
    branch → complete the run, each as its own logged command."""
    if not s.backlog_recorded:
        return Command(
            kind="record_backlog",
            params={"version": s.version, "decision": s.triage_decision, "reason": "triage"},
        )
    if not s.branch_deleted:
        return Command(kind="delete_branch", params={"branch_name": f"releases/{s.version}"})
    return Command(kind="complete_run", params={"terminal_state": s.triage_decision})


def _decide_draft(s: State, stage: str, sub: str) -> Command | None:
    """DRAFT / RESPOND pipeline: dispatch -> validate -> commit."""
    sd = _STAGES[stage]
    if not s.doc_dispatched:
        cmd = _dispatch(
            sd.drafting_role,
            sub,
            f"write {sd.doc}",
            sd.doc,
            stage=stage,
            attempt=s.current_attempt + 1,
            review_round=s.review_round,
        )
        if s.last_failure:
            cmd.params["evidence"] = dict(s.last_failure)  # FR-11
        if sub == "RESPOND" and s.review_diff_ref:
            cmd.params["diff_ref"] = s.review_diff_ref  # FR-16
        return cmd
    if not s.doc_produced:
        return None
    if not s.doc_validated:
        # FR-150/AC-1503: outcome-time format gate — Scribe/Sage must produce a
        # template-conforming doc before it is committed / enters review.
        return Command(kind="validate_document", params={"doc": sd.doc, "checks": ["template"]})
    if not getattr(s, sd.committed_flag):
        return Command(
            kind="commit_document",
            params={"doc": sd.doc, "message": f"{stage}: draft {sd.doc}"},
        )
    return None


def _decide_design_draft(s: State, sub: str) -> Command | None:
    """M-DESIGN DRAFT/RESPOND pipeline (flow.md §8): ONE Archer dispatch covers
    all three docs (Decision A). v0.5 batch 2: the ResultCheckpoint pipeline
    handles validate+checkpoint+publish; decide() only fires the dispatch."""
    sd = _STAGES["M-DESIGN"]
    if not s.doc_dispatched:
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": sd.drafting_role,
                "substate": sub,
                "objective": f"write {', '.join(DESIGN_DOCS)}",
                "stage": "M-DESIGN",
                "attempt": s.current_attempt + 1,
                "review_round": s.review_round,
                "docs": list(DESIGN_DOCS),
                "assignment": {
                    "kind": sub,
                    "template_kind": None,
                    "templates": [doc.removesuffix(".md") for doc in DESIGN_DOCS],
                    # batch B: multi-skill — the design doc-set, the
                    # stage methodology skill and the discussion protocol.
                    # The host guard-stack catalog (tracks-quality-guards)
                    # stays only for assignments whose task actually
                    # requires the guard-stack catalog.
                    "skills": [
                        "tracks-discuz",
                        "tracks-archer-design",
                        "tracks-quality-guards",
                    ],
                    "docs": list(DESIGN_DOCS),
                },
            },
        )
        if s.last_failure:
            cmd.params["evidence"] = dict(s.last_failure)  # FR-11
        return cmd
    return None  # awaiting outcome -> pipeline drives validate+checkpoint+publish


def _decide_design_exit(s: State) -> Command | None:
    """M-DESIGN EXIT (flow.md §8 / Decision A): gate-validate the three docs
    (template + discussion_ready), write nothing extra, then exit — the executor
    completes the run at the M-IMPL boundary (M-IMPL is not in v0.3).

    D-28: the test-plan additionally runs the design trace + the structured
    test-task contract check (checks=["trace", "test_tasks"]) so an invalid
    M-DESIGN→M-TEST contract never exits."""
    if s.stage_exited:
        return None  # crash-hole parity with _decide_exit
    if not s.exit_validated:
        doc = DESIGN_DOCS[min(s.design_validated, len(DESIGN_DOCS) - 1)]
        checks = ["template", "discussion_ready"]
        if doc == "test-plan.md":
            checks += ["trace", "test_tasks"]
        return Command(kind="validate_document", params={"doc": doc, "checks": checks})
    return Command(kind="write_frontmatter", params={"stage": "M-DESIGN"})


def _decide_exit(s: State, stage: str) -> Command | None:
    """EXIT: gate-validate (FR-150/AC-1502) then seal the document sha (FR-17/FR-23)."""
    if s.stage_exited:
        return None
    doc = _STAGES[stage].doc
    if not s.exit_validated:
        # Review-exit gate: only unresolved inline discussions block beyond
        # ordinary document/template validity.
        return Command(
            kind="validate_document",
            params={"doc": doc, "checks": ["template", "discussion_ready"]},
        )
    # Executor seals frontmatter sha, commits, emits stage.exited
    # (+ story/spec.committed final sha, + next stage.entered / run.completed).
    return Command(kind="write_frontmatter", params={"doc": doc, "stage": stage, "field": "sha"})


def _decide_pipeline(s: State, stage: str, sub: str) -> Command | None:
    """DRAFT/RESPOND: M-DESIGN runs its own multi-doc pipeline (flow.md §8)."""
    if stage == "M-DESIGN":
        return _decide_design_draft(s, sub)
    return _decide_draft(s, stage, sub)


def _decide_triage(s: State, stage: str) -> Command | None:
    if s.doc_dispatched:
        return None  # awaiting triage (set on outcome.received)
    sd = _STAGES[stage]
    return _dispatch(
        sd.drafting_role,
        "TRIAGE",
        "explore raw requirement",
        sd.doc,
        stage=stage,
        attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )


def _decide_review(s: State, stage: str, sub: str) -> Command | None:
    sd = _STAGES[stage]
    reviewer = _REVIEW_SUBSTATE[sub].reviewer
    if s.reviewer_dispatched:
        return None
    cmd = _dispatch(
        reviewer,
        sub,
        f"{reviewer} review",
        sd.doc,
        stage=stage,
        attempt=s.current_attempt + 1,
        review_round=s.review_round,
    )
    if s.last_failure:
        cmd.params["evidence"] = dict(s.last_failure)  # FR-11
    if sd.docs:
        # Multi-doc stage (M-DESIGN): the reviewer's assignment names the whole
        # doc set like the drafter's (flow.md §8; no single target doc).
        cmd.params["docs"] = list(sd.docs)
        cmd.params["assignment"]["docs"] = list(sd.docs)
        # D-29: Prism's M-DESIGN review consumes the design criteria pack
        # (tracks-prism-design) alongside the discussion protocol; switch the
        # single-skill shape to the multi-skill list, like the Archer DRAFT.
        cmd.params["assignment"].pop("skill", None)
        cmd.params["assignment"].pop("skill_version", None)
        cmd.params["assignment"]["skills"] = ["tracks-discuz", "tracks-prism-design"]
    return cmd


def _decide_exit_gate(s: State, stage: str) -> Command | None:
    if stage == "M-DESIGN":
        return _decide_design_exit(s)
    return _decide_exit(s, stage)


def _no_diff_dispatch_params(s, substate, role, docs, doc, objective):
    """Build dispatch_agent params for NO_DIFF_EXPLAIN / NO_DIFF_REVIEW."""
    ar = s.active_result or {}
    ctx = {
        "artifacts": ar.get("artifacts", []),
        "base_sha": ar.get("base_sha"),
        "result_id": ar.get("result_id"),
        "stage": ar.get("stage", s.stage),
        "substate": ar.get("no_diff_origin_substate", s.substate),
    }
    if substate == "NO_DIFF_REVIEW":
        ctx["explanation"] = s.no_diff_explanation
    params = {
        "role": role,
        "substate": substate,
        "objective": objective,
        "stage": s.stage,
        "attempt": s.current_attempt + 1,
        "review_round": s.review_round,
        "no_diff_context": ctx,
        "assignment": {
            "kind": substate,
            "skill": "tracks-discuz",
            "template_kind": doc.removesuffix(".md") if doc else None,
        },
    }
    if docs:
        params["docs"] = docs
        params["assignment"]["docs"] = list(docs)
    if doc:
        params["doc"] = doc
    if substate == "NO_DIFF_EXPLAIN" and s.last_failure:
        params["evidence"] = dict(s.last_failure)  # FR-11
    return params


def _decide_no_diff_explain(s: State) -> Command | None:
    """Re-dispatch the original author to explain why no diff was produced."""
    if s.doc_dispatched:
        return None  # awaiting the explanation outcome
    if s.stage == "M-TEST":
        role, docs, doc = "shield", list(_M_TEST_CONTEXT_DOCS), None
        objective = "explain why no tests/ diff was produced"
    elif s.stage == "M-IMPL":
        role, docs, doc = "devon", list(_M_IMPL_CONTEXT_DOCS), None
        objective = "explain why no implementation diff was produced"
    elif s.stage == "M-DESIGN":
        role, docs, doc = "archer", list(DESIGN_DOCS), None
        objective = "explain why no design diff was produced"
    else:
        sd = _STAGES.get(s.stage)
        if sd is None:
            return None
        role, docs, doc = sd.drafting_role, [], sd.doc
        objective = f"explain why no {doc} diff was produced"
    return Command(
        kind="dispatch_agent",
        params=_no_diff_dispatch_params(s, "NO_DIFF_EXPLAIN", role, docs, doc, objective),
    )


def _decide_no_diff_review(s: State) -> Command | None:
    """Dispatch the stage's reviewer to judge the no-diff explanation."""
    if s.no_diff_reviewer_dispatched:
        return None  # awaiting the reviewer verdict
    if s.stage == "M-TEST":
        reviewer, docs, doc = "prism", list(_M_TEST_CONTEXT_DOCS), None
    elif s.stage == "M-IMPL":
        reviewer, docs, doc = "prism", list(_M_IMPL_CONTEXT_DOCS), None
    elif s.stage == "M-DESIGN":
        reviewer, docs, doc = "prism", list(DESIGN_DOCS), None
    else:
        sd = _STAGES.get(s.stage)
        if sd is None:
            return None
        reviewer, docs, doc = sd.reviewer, [], sd.doc
    return Command(
        kind="dispatch_agent",
        params=_no_diff_dispatch_params(
            s, "NO_DIFF_REVIEW", reviewer, docs, doc, "review the no-diff explanation"
        ),
    )


def _decide_result_pipeline(s: State) -> Command | None:
    """v0.5 ResultCheckpoint pipeline: validate -> checkpoint -> publish.

    Generic and stage-agnostic. ``active_result`` carries everything the
    executor needs: artifacts to validate, allowed_paths to checkpoint,
    checks to run, and the domain_event to publish. The domain event's
    reducer clears ``active_result`` (or verdict.failed on failure)."""
    ar = s.active_result
    result_id = ar.get("result_id")
    digests = ar.get("digests")
    if not ar.get("validated"):
        return Command(
            kind="validate_result",
            params={
                "artifacts": ar.get("artifacts", []),
                "checks": ar.get("checks", []),
                "base_sha": ar.get("base_sha"),
                "requires_diff": ar.get("requires_diff", False),
                "forbid_diff": ar.get("forbid_diff", False),
                "discussion_only": ar.get("discussion_only", False),
                "verdict": ar.get("verdict"),
                "actor_kind": ar.get("actor_kind"),
                "result_id": result_id,
                "digests": digests,
                "manifest_error": ar.get("manifest_error"),
            },
        )
    if not ar.get("checkpointed"):
        return Command(
            kind="checkpoint_result",
            params={
                "allowed_paths": ar.get("allowed_paths", []),
                "base_sha": ar.get("base_sha"),
                "source": ar.get("source"),
                "stage": ar.get("stage"),
                "requires_diff": ar.get("requires_diff", False),
                "forbid_diff": ar.get("forbid_diff", False),
                "verdict": ar.get("verdict"),
                "commit_label": ar.get("commit_label"),
                "actor_kind": ar.get("actor_kind"),
                "result_id": result_id,
                "digests": digests,
                "no_diff_approved": ar.get("no_diff_approved", False),
            },
        )
    return Command(
        kind="publish_result",
        params={
            "domain_event": ar.get("domain_event", {}),
            "commit_sha": ar.get("commit_sha"),
            "created_commit": ar.get("created_commit", False),
            "source": ar.get("source"),
            "stage": ar.get("stage"),
            "substate": ar.get("substate"),
            "artifacts": ar.get("artifacts", []),
            "verdict": ar.get("verdict"),
            "actor_kind": ar.get("actor_kind"),
            "base_sha": ar.get("base_sha"),
            "result_id": result_id,
        },
    )
