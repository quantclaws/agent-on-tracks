"""M-DESIGN reducer/decide branches (flow.md §8, v0.3 BS-01..BS-07) — pure.

DRAFT → PRISM_REVIEW → (revise) RESPOND → … → EXIT → run.completed(boundary).
Pure technical stage: no human.review/human.approval anywhere (BS-05); one
Archer dispatch covers all three docs (Decision A); validate-fail re-dispatch
follows the DRAFT three-attempt escalation pattern.
"""
from tests.unit.helpers import seq
from tracks.kernel import decide, project
from tracks.kernel.machine import DESIGN_DOCS

ENTER_DESIGN = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-DESIGN"}),
]
DISPATCHED = ("command.issued", {"command": {"kind": "dispatch_agent",
                                              "params": {"role": "archer",
                                                         "substate": "DRAFT"},
                                              "command_id": "C1"}})
def _design_author_checkpoint(substate="DRAFT", cmd_id="C1"):
    """Minimal result_checkpoint payload for M-DESIGN author (DRAFT/RESPOND)."""
    return {
        "source": "archer", "stage": "M-DESIGN", "substate": substate,
        "actor_kind": "agent",
        "artifacts": list(DESIGN_DOCS), "allowed_paths": list(DESIGN_DOCS),
        "base_sha": "b", "checks": ["template"],
        "requires_diff": False, "forbid_diff": False,
        "discussion_only": False,
        "commit_label": "M-DESIGN: archer commit",
        "result_id": cmd_id, "digests": {},
        "domain_event": {"type": "design.committed", "payload": {}},
    }


def _design_review_checkpoint(verdict="pass", cmd_id="C2"):
    """Minimal result_checkpoint payload for M-DESIGN Prism review."""
    return {
        "source": "prism", "stage": "M-DESIGN",
        "substate": "PRISM_REVIEW", "actor_kind": "agent",
        "verdict": verdict,
        "artifacts": list(DESIGN_DOCS), "allowed_paths": list(DESIGN_DOCS),
        "base_sha": "b", "checks": ["template"],
        "requires_diff": verdict != "pass", "forbid_diff": False,
        "discussion_only": True,
        "commit_label": f"M-DESIGN: prism ({verdict}) checkpoint",
        "result_id": cmd_id, "digests": {},
        "domain_event": {"type": "prism.verdict",
                          "payload": {"verdict": verdict}},
    }


PRODUCED = ("outcome.received", {"role": "archer", "status": "done",
                                  "result_checkpoint": _design_author_checkpoint()})
PASSED = ("verdict.passed", {"check": "template,trace", "detail": "d"})


def state_of(*items):
    return project(seq(*ENTER_DESIGN, *items))


def draft_cycle():
    """dispatch → outcome (with pipeline) → validated → checkpointed →
    3× design.committed events (one publish_result emits all three)."""
    evs = [DISPATCHED, PRODUCED,
           ("result.validated", {"artifacts": list(DESIGN_DOCS),
                                  "base_sha": "b", "result_id": "C1"}),
           ("result.checkpointed", {"created_commit": True,
                                    "commit_sha": "c", "base_sha": "b",
                                    "result_id": "C1"})]
    for doc in DESIGN_DOCS:
        evs.append(("design.committed", {"doc": doc, "commit_sha": "c",
                                         "final": False}))
    return evs


PRISM_DISPATCH = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                  "params": {"role": "prism",
                                                             "substate": "PRISM_REVIEW"},
                                                  "command_id": "C2"}})
PRISM_PRODUCED = ("outcome.received", {"role": "prism", "status": "done"})
EXIT_PASSED = ("verdict.passed", {"check": "template,discussion_ready",
                                  "detail": "d"})


def test_enter_draft_dispatches_archer_for_all_three_docs():
    # BS-03 / Decision A: one assignment covers the whole design trio.
    s = state_of()
    assert s.stage == "M-DESIGN" and s.substate == "DRAFT"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer" and cmd.params["substate"] == "DRAFT"
    assert cmd.params["docs"] == list(DESIGN_DOCS)
    assert "doc" not in cmd.params  # no single target doc
    assert cmd.params["assignment"]["kind"] == "DRAFT"


def test_design_dispatch_assignments_carry_doc_set():
    # The opencode contract derives the target doc-set from the assignment
    # (not from role names): both the Archer DRAFT/RESPOND dispatch and the
    # Prism review dispatch name the whole trio (flow.md §8, Decision A).
    draft = decide(state_of())
    assert draft.params["assignment"]["docs"] == list(DESIGN_DOCS)
    # Archer drafts from the template trio: the assignment names every kind so
    # the backend materializes them into the host repo (live run043).
    assert draft.params["assignment"]["templates"] == [
        doc.removesuffix(".md") for doc in DESIGN_DOCS]
    assert draft.params["assignment"]["template_kind"] is None
    review = decide(state_of(*draft_cycle()))
    assert review.params["substate"] == "PRISM_REVIEW"
    assert review.params["docs"] == list(DESIGN_DOCS)
    assert review.params["assignment"]["docs"] == list(DESIGN_DOCS)
    assert "templates" not in review.params["assignment"]  # reviewers draft nothing


def test_design_author_assignment_carries_both_skills():
    # batch B: the M-DESIGN author assignment is multi-skill - the discussion
    # protocol plus the host guard-stack catalog; the single-skill shape stays
    # the contract everywhere else (other reviewers).
    draft = decide(state_of())
    assignment = draft.params["assignment"]
    assert assignment["skills"] == ["tracks-discuz", "tracks-quality-guards"]
    assert "skill" not in assignment
    review = decide(state_of(*draft_cycle()))
    review_assignment = review.params["assignment"]
    # D-29: Prism's M-DESIGN review is multi-skill - the discussion protocol
    # plus the design criteria pack.
    assert review_assignment["skills"] == ["tracks-discuz", "tracks-prism-design"]
    assert "skill" not in review_assignment


def test_pipeline_validates_all_three_design_docs():
    # v0.5 batch 2: after outcome, decide() drives the ResultCheckpoint
    # pipeline. validate_result checks all 3 design docs in one command
    # (artifacts list), not one-by-one.
    s = state_of(DISPATCHED, PRODUCED)
    assert s.active_result is not None
    cmd = decide(s)
    assert cmd.kind == "validate_result"
    assert cmd.params["artifacts"] == list(DESIGN_DOCS)
    assert cmd.params["checks"] == ["template"]


def test_validate_fail_re_dispatch_budget_then_escalation():
    # Mirrors the existing DRAFT three-attempt escalation pattern: re-dispatch
    # Archer with failure evidence; the 3rd failure escalates to Human.
    def fail(n):
        return ("verdict.failed", {"check": "trace", "reason": "orphan",
                                   "attempt": n})
    one = state_of(DISPATCHED, PRODUCED, fail(1))
    cmd = decide(one)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["evidence"]["check"] == "trace"
    two = state_of(DISPATCHED, PRODUCED, fail(1), DISPATCHED, PRODUCED, fail(2))
    assert decide(two).kind == "dispatch_agent"
    three = state_of(DISPATCHED, PRODUCED, fail(1), DISPATCHED, PRODUCED,
                     fail(2), DISPATCHED, PRODUCED, fail(3))
    assert three.status == "awaiting_human" and three.awaiting == "escalation"
    assert decide(three) is None


def test_three_commits_enter_prism_review():
    s = state_of(*draft_cycle())
    assert s.design_committed == len(DESIGN_DOCS)
    assert s.substate == "PRISM_REVIEW"
    cmd = decide(s)
    # reviewer kind naming stays consistent with SAGE_REVIEW/LEX_REVIEW
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "prism"
    assert cmd.params["substate"] == "PRISM_REVIEW"
    assert cmd.params["assignment"]["kind"] == "PRISM_REVIEW"


def test_prism_pass_goes_straight_to_exit_no_human_gate():
    # BS-05: pass → EXIT without any awaiting; the exit gate re-validates the
    # trio (template + discussion_ready) and exits with a stage-only
    # write_frontmatter (nothing extra is written, Decision A).
    items = draft_cycle() + [PRISM_DISPATCH, PRISM_PRODUCED,
                             ("prism.verdict", {"verdict": "pass"})]
    s = state_of(*items)
    assert s.substate == "EXIT" and s.awaiting is None
    assert s.status == "active" and s.prism_passed_this_round
    for doc in DESIGN_DOCS:
        cmd = decide(state_of(*items))
        assert cmd.kind == "validate_document"
        assert cmd.params == {"doc": doc,
                              "checks": ["template", "discussion_ready"]}
        items.append(EXIT_PASSED)  # each pass advances the exit sequence
    cmd = decide(state_of(*items))
    assert cmd.kind == "write_frontmatter"
    assert cmd.params == {"stage": "M-DESIGN"}  # no doc: nothing to seal


def test_prism_revise_enters_respond_and_new_round():
    # flow.md §8.1: revise → RESPOND (Archer re-dispatched), counters reset;
    # review.round_started opens the next PRISM_REVIEW round.
    base = draft_cycle() + [PRISM_DISPATCH, PRISM_PRODUCED]
    s = state_of(*base, ("prism.verdict", {"verdict": "revise"}),
                 ("review.round_started", {"stage": "M-DESIGN", "round": 2}))
    assert s.substate == "RESPOND" and s.review_round == 2
    assert s.design_committed == 0 and s.design_validated == 0
    assert not s.prism_passed_this_round
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "RESPOND"
    assert cmd.params["review_round"] == 2
    assert cmd.params["docs"] == list(DESIGN_DOCS)
    # RESPOND re-runs the pipeline (validate → checkpoint → publish)
    again = state_of(*base, ("prism.verdict", {"verdict": "revise"}),
                     ("review.round_started", {"stage": "M-DESIGN", "round": 2}),
                     DISPATCHED, PRODUCED)
    assert decide(again).kind == "validate_result"


def test_exit_gate_fail_falls_back_to_respond_not_human():
    # BS-05: even the EXIT gate never awaits a human in M-DESIGN - a failed
    # exit validate falls back to RESPOND; the shared attempt budget escalates.
    def fail(n):
        return ("verdict.failed", {"check": "discussion_ready",
                                   "reason": "threads", "attempt": n})
    base = draft_cycle() + [PRISM_DISPATCH, PRISM_PRODUCED,
                            ("prism.verdict", {"verdict": "pass"}), fail(1)]
    s = state_of(*base)
    assert s.substate == "RESPOND" and s.awaiting is None
    assert s.status == "active" and decide(s).kind == "dispatch_agent"
    escalated = state_of(*base, DISPATCHED, PRODUCED, fail(2),
                         DISPATCHED, PRODUCED, fail(3))
    assert escalated.status == "awaiting_human"
    assert escalated.awaiting == "escalation"


def test_draft_failure_does_not_consume_respond_budget():
    # run061 regression: a DRAFT validate failure consumed an attempt from the
    # shared counter; when Prism later revised, RESPOND inherited the stale
    # count and escalated after only 2 RESPOND failures (3 total with the
    # DRAFT one). flow.md §8.3 mandates a fresh 重派 Archer <=3 budget per
    # RESPOND round, so prism.verdict=revise resets current_attempt.
    def draft_fail(n):
        return ("verdict.failed", {"check": "template", "reason": "bad",
                                   "attempt": n})
    def respond_fail():
        return ("outcome.received", {"role": "archer", "status": "failed",
                                     "failure_class": "no_target_diff",
                                     "self_report": "no diff"})
    # DRAFT attempt 1 fails validation, attempt 2 succeeds and reaches Prism.
    base = [DISPATCHED, PRODUCED, draft_fail(1),
            DISPATCHED, PRODUCED, PASSED, PASSED, PASSED]
    base += [("design.committed", {"doc": doc, "commit_sha": "c", "final": False})
             for doc in DESIGN_DOCS]
    base += [PRISM_DISPATCH, PRISM_PRODUCED,
             ("prism.verdict", {"verdict": "revise"}),
             ("review.round_started", {"stage": "M-DESIGN", "round": 2})]
    s = state_of(*base)
    assert s.substate == "RESPOND" and s.current_attempt == 0, (
        "prism.verdict=revise must reset the attempt budget for RESPOND; "
        f"got current_attempt={s.current_attempt}"
    )
    # Two RESPOND failures must NOT escalate (the 3rd is the budget).
    after_two = state_of(*base, DISPATCHED, respond_fail(),
                         DISPATCHED, respond_fail())
    assert after_two.status == "active", (
        "two RESPOND failures after a DRAFT failure must not escalate; "
        f"got status={after_two.status}"
    )
    assert after_two.current_attempt == 2
    # The third RESPOND failure escalates (the documented <=3 budget).
    after_three = state_of(*base, DISPATCHED, respond_fail(),
                           DISPATCHED, respond_fail(),
                           DISPATCHED, respond_fail())
    assert after_three.status == "awaiting_human"
    assert after_three.awaiting == "escalation"




def test_prism_review_failed_outcome_redispatch_carries_evidence():
    # A failed reviewer outcome (e.g. the opencode revise_without_findings
    # audit, live run042) is not a produced verdict: the review resets and the
    # Prism re-dispatch carries the failure evidence (FR-11), like DRAFT
    # pipelines; the attempt accounting shares the escalation budget.
    items = draft_cycle() + [
        PRISM_DISPATCH,
        ("outcome.received", {"role": "prism", "status": "failed",
                              "failure_class": "revise_without_findings",
                              "self_report": "revise must anchor findings",
                              "audit_evidence":
                                  "revise_without_findings: verdict=revise"}),
    ]
    s = state_of(*items)
    assert s.substate == "PRISM_REVIEW" and not s.reviewer_dispatched
    assert s.last_failure["check"] == "revise_without_findings"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["substate"] == "PRISM_REVIEW"
    assert cmd.params["evidence"]["check"] == "revise_without_findings"
    assert cmd.params["evidence"]["evidence"].startswith("revise_without_findings")


def _drive(items):
    """Pure-machine walk: apply decide() and append the events the executor
    would log for each command until decide() halts. Happy-path fake semantics
    (pipeline validates pass, Prism passes)."""
    evs = list(items)
    for _ in range(300):
        s = project(seq(*evs))
        cmd = decide(s)
        if cmd is None:
            return s, evs
        params = dict(cmd.params)
        cmd_id = f"C{len(evs)}"
        evs.append(("command.issued",
                    {"command": {"kind": cmd.kind, "params": params,
                                 "command_id": cmd_id}}))
        if cmd.kind == "dispatch_agent":
            outcome = {"role": params["role"], "status": "done"}
            stage = params.get("stage", "")
            sub = params.get("substate", "")
            if stage == "M-DESIGN" and sub in ("DRAFT", "RESPOND"):
                outcome["result_checkpoint"] = _design_author_checkpoint(sub, cmd_id)
            elif stage == "M-DESIGN" and sub == "PRISM_REVIEW":
                outcome["result_checkpoint"] = _design_review_checkpoint("pass", cmd_id)
            evs.append(("outcome.received", outcome))
        elif cmd.kind == "validate_result":
            evs.append(("result.validated",
                        {"artifacts": params.get("artifacts", []),
                         "base_sha": params.get("base_sha"),
                         "result_id": params.get("result_id")}))
        elif cmd.kind == "checkpoint_result":
            evs.append(("result.checkpointed",
                        {"created_commit": True, "commit_sha": "c",
                         "base_sha": params.get("base_sha"),
                         "result_id": params.get("result_id")}))
        elif cmd.kind == "publish_result":
            ev_type = params.get("domain_event", {}).get("type", "")
            if ev_type == "design.committed":
                for doc in DESIGN_DOCS:
                    evs.append(("design.committed",
                                {"doc": doc, "commit_sha": "c",
                                 "final": False}))
            elif ev_type == "prism.verdict":
                evs.append(("prism.verdict", {"verdict": "pass"}))
        elif cmd.kind == "validate_document":
            evs.append(("verdict.passed",
                        {"check": ",".join(params["checks"]), "detail": "d"}))
        elif cmd.kind == "write_frontmatter":
            evs.append(("stage.exited", {"stage": params["stage"]}))
            evs.append(("run.completed", {"terminal_state": "boundary"}))
    raise AssertionError("M-DESIGN happy path did not halt within 300 steps")


def test_happy_path_completes_without_any_human_event():
    # BS-05/Decision A: stage.entered(M-DESIGN) → run.completed(boundary) with
    # zero human.* events; every doc committed once via design.committed.
    final, evs = _drive(ENTER_DESIGN)
    types = [t for t, _ in evs]
    assert final.status == "completed" and final.terminal_state == "boundary"
    assert types[-2:] == ["stage.exited", "run.completed"]
    assert not [t for t in types if t.startswith("human.")]
    committed = [p["doc"] for t, p in evs if t == "design.committed"]
    assert committed == list(DESIGN_DOCS)
    assert types.count("prism.verdict") == 1


def test_pre_v03_event_log_replays_unchanged():
    # BS-07: a pre-v0.3 log ending right after M-REQ-APPROVAL still folds to
    # the same terminal state it did before (the machine gained fields with
    # safe defaults; no reducer behavior changed for these events).
    log = [
        ("story.requested", {"raw_chars": 5}),
        ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
        ("preview.generated", {"digest": "d1", "summary": "story; spec; acc"}),
        ("human.approval", {"actor": "Aaron", "digest": "d1", "ts": "t1"}),
        ("approval.recorded", {"actor": "Aaron", "digest": "d1", "ts": "t1",
                               "readonly": True}),
        ("issues.created", {"digest": "d1", "mapping": {}}),
        ("stage.exited", {"stage": "M-REQ-APPROVAL"}),
        ("run.completed", {"terminal_state": "boundary"}),
    ]
    s = project(seq(*log))
    assert s.status == "completed" and s.terminal_state == "boundary"
    assert s.stage == "M-REQ-APPROVAL" and s.awaiting is None
    assert decide(s) is None
    # the new design fields stay at their safe defaults
    assert s.design_validated == 0 and s.design_committed == 0
    assert not s.prism_passed_this_round


def test_state_defaults_are_safe():
    from tracks.kernel.machine import State
    s = State()
    assert s.design_validated == 0 and s.design_committed == 0
    assert s.prism_passed_this_round is False
