"""Pre-commit hook rejection at commit time: the executor emits verdict.failed
(check=commit) instead of crashing, the machine re-dispatches the agent with
hook output as evidence, counters reset so the pipeline re-validates/re-commits,
and the shared attempt budget escalates at >=3 (D-30/F-1).

Pure machine tests: simulate the failure-evidence event in each affected
sub-state and assert the established WRITE redispatch + budget semantics.
"""
from tests.unit.helpers import seq
from tests.unit.test_machine_m_test import (
    COLLECT_CMD,
    COLLECTED,
    PRISM_DISPATCH,
    PRISM_DONE,
    PRISM_PASS,
    RED_VALID,
    RUN_CMD,
    SHIELD_DISPATCH,
    SHIELD_DONE,
    TRACE_CMD,
    TRACE_PASS,
)
from tests.unit.test_machine_m_test import (
    state_of as _m_test_state,
)
from tracks.kernel import decide, project
from tracks.kernel.machine import DESIGN_DOCS

# -- M-DESIGN (multi-doc DRAFT commit failure) --------------------------------

ENTER_DESIGN = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-DESIGN"}),
]
DESIGN_DISPATCHED = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                    "params": {"role": "archer",
                                                               "substate": "DRAFT"},
                                                    "command_id": "C1"}})
DESIGN_PRODUCED = ("outcome.received", {"role": "archer", "status": "done"})
DESIGN_PASSED = ("verdict.passed", {"check": "template,trace", "detail": "d"})
def DESIGN_COMMITTED(doc):
    return ("design.committed", {"doc": doc, "commit_sha": "c",
                                 "final": False})


def _design_state(*items):
    return project(seq(*ENTER_DESIGN, *items))


def _design_commit_fail(attempt):
    return ("verdict.failed", {"check": "commit",
                               "reason": "pre-commit hook rejected the commit",
                               "evidence": "ruff: 6 errors in architecture.md",
                               "attempt": attempt})


def test_m_design_commit_failure_resets_counters_and_redispatches():
    """One doc committed, next commit rejected by hook: design_committed and
    design_validated both reset to 0 so the WRITE redispatch re-validates and
    re-commits all three docs; Archer is dispatched with hook evidence."""
    items = [DESIGN_DISPATCHED, DESIGN_PRODUCED]
    # validate + commit architecture.md, validate interfaces.md
    items += [DESIGN_PASSED, DESIGN_COMMITTED("architecture.md"), DESIGN_PASSED]
    # commit interfaces.md rejected by hook
    items += [_design_commit_fail(1)]
    s = _design_state(*items)
    assert s.design_validated == 0 and s.design_committed == 0
    assert s.substate == "DRAFT" and s.doc_dispatched is False
    assert s.last_failure["check"] == "commit"
    assert "ruff" in s.last_failure["evidence"]
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "archer"
    assert cmd.params["evidence"]["check"] == "commit"
    # after redispatch + produce, decide re-validates from doc 0
    s2 = _design_state(*items, DESIGN_DISPATCHED, DESIGN_PRODUCED)
    cmd2 = decide(s2)
    assert cmd2.kind == "validate_document"
    assert cmd2.params["doc"] == DESIGN_DOCS[0]  # architecture.md, not interfaces.md


def test_m_design_commit_failure_escalation():
    """Three consecutive commit failures exhaust the <=3 budget."""
    base = [DESIGN_DISPATCHED, DESIGN_PRODUCED, DESIGN_PASSED]
    one = _design_state(*base, _design_commit_fail(1))
    assert one.current_attempt == 1 and one.status == "active"
    two = _design_state(*base, _design_commit_fail(1),
                        DESIGN_DISPATCHED, DESIGN_PRODUCED, DESIGN_PASSED,
                        _design_commit_fail(2))
    assert two.current_attempt == 2 and two.status == "active"
    three = _design_state(*base, _design_commit_fail(1),
                          DESIGN_DISPATCHED, DESIGN_PRODUCED, DESIGN_PASSED,
                          _design_commit_fail(2),
                          DESIGN_DISPATCHED, DESIGN_PRODUCED, DESIGN_PASSED,
                          _design_commit_fail(3))
    assert three.status == "awaiting_human" and three.awaiting == "escalation"
    assert decide(three) is None


# -- M-SPEC (single-doc DRAFT commit failure) ---------------------------------

ENTER_SPEC = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-SPEC"}),
]
SPEC_DISPATCHED = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                  "params": {"role": "sage",
                                                             "substate": "DRAFT"},
                                                  "command_id": "C1"}})
SPEC_PRODUCED = ("outcome.received", {"role": "sage", "status": "done"})
SPEC_PASSED = ("verdict.passed", {"check": "template", "detail": "d"})
SPEC_COMMITTED = ("spec.committed", {"commit_sha": "c", "spec_sha": "s",
                                     "final": False})
LEX_DISPATCHED = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                 "params": {"role": "lex",
                                                            "substate": "LEX_REVIEW"},
                                                 "command_id": "C2"}})
LEX_PRODUCED = ("outcome.received", {"role": "lex", "status": "done"})
LEX_PASS = ("lex.verdict", {"verdict": "pass"})
HUMAN_NO_COMMENT = ("human.review", {"action": "no_comment"})
EXIT_PASSED = ("verdict.passed", {"check": "template,discussion_ready",
                                  "detail": "d"})


def _spec_state(*items):
    return project(seq(*ENTER_SPEC, *items))


def _commit_fail(attempt):
    return ("verdict.failed", {"check": "commit",
                               "reason": "pre-commit hook rejected the commit",
                               "evidence": "ruff: lint error in spec.md",
                               "attempt": attempt})


def test_spec_draft_commit_failure_redispatches_with_evidence():
    """DRAFT commit rejected by hook: substate stays DRAFT, spec_committed
    reset, Sage re-dispatched with hook evidence."""
    s = _spec_state(SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED, _commit_fail(1))
    assert s.substate == "DRAFT" and s.spec_committed is False
    assert s.last_failure["check"] == "commit"
    assert "ruff" in s.last_failure["evidence"]
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "sage"
    assert cmd.params["evidence"]["check"] == "commit"


def test_spec_exit_seal_commit_failure_redispatches_with_evidence():
    """EXIT seal commit rejected by hook: substate -> DRAFT, spec_committed
    and exit_validated reset so the full DRAFT->review->EXIT cycle re-runs."""
    items = [SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED, SPEC_COMMITTED,
             LEX_DISPATCHED, LEX_PRODUCED, LEX_PASS, HUMAN_NO_COMMENT,
             EXIT_PASSED]
    s = _spec_state(*items, _commit_fail(1))
    assert s.substate == "DRAFT"
    assert s.spec_committed is False
    assert s.exit_validated is False
    assert s.last_failure["check"] == "commit"
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "sage"
    assert cmd.params["evidence"]["check"] == "commit"


def test_spec_commit_failure_escalation():
    """Three consecutive commit failures exhaust the <=3 budget."""
    base = [SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED]
    three = _spec_state(
        *base, _commit_fail(1),
        SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED, _commit_fail(2),
        SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED, _commit_fail(3),
    )
    assert three.status == "awaiting_human" and three.awaiting == "escalation"
    assert decide(three) is None


def test_exit_seal_commit_failure_does_not_await_review():
    """A commit hook rejection at EXIT must not trigger AC-1502 (awaiting
    human/review) -- it is a transient hook issue, not a review-gate failure."""
    items = [SPEC_DISPATCHED, SPEC_PRODUCED, SPEC_PASSED, SPEC_COMMITTED,
             LEX_DISPATCHED, LEX_PRODUCED, LEX_PASS, HUMAN_NO_COMMENT,
             EXIT_PASSED]
    s = _spec_state(*items, _commit_fail(1))
    assert s.status == "active" and s.awaiting is None


# -- M-TEST (test commit failure) ---------------------------------------------

_M_TEST_PRE_COMMIT = [
    SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED,
    PRISM_DISPATCH, PRISM_DONE, PRISM_PASS, RUN_CMD, RED_VALID,
    TRACE_CMD, TRACE_PASS,
]


def _test_commit_fail(attempt):
    return ("verdict.failed", {"check": "commit",
                               "reason": "pre-commit hook rejected the commit",
                               "evidence": "ruff: lint error in test files",
                               "attempt": attempt})


def test_m_test_commit_failure_redispatches_shield_with_evidence():
    """Test commit rejected by hook: substate -> WRITE, trace_passed reset,
    Shield re-dispatched with hook evidence."""
    s = _m_test_state(*_M_TEST_PRE_COMMIT, _test_commit_fail(1))
    assert s.substate == "WRITE"
    assert s.trace_passed is False
    assert s.doc_dispatched is False
    assert s.last_failure["check"] == "commit"
    assert "ruff" in s.last_failure["evidence"]
    cmd = decide(s)
    assert cmd.kind == "dispatch_agent"
    assert cmd.params["role"] == "shield"
    assert cmd.params["evidence"]["check"] == "commit"


def test_m_test_commit_failure_escalation():
    """Three consecutive commit failures exhaust the shared <=3 budget."""
    one = _m_test_state(*_M_TEST_PRE_COMMIT, _test_commit_fail(1))
    assert one.current_attempt == 1 and one.status == "active"
    two = _m_test_state(*_M_TEST_PRE_COMMIT, _test_commit_fail(1),
                        SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED,
                        PRISM_DISPATCH, PRISM_DONE, PRISM_PASS, RUN_CMD, RED_VALID,
                        TRACE_CMD, TRACE_PASS, _test_commit_fail(2))
    assert two.current_attempt == 2 and two.status == "active"
    three = _m_test_state(*_M_TEST_PRE_COMMIT, _test_commit_fail(1),
                          SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED,
                          PRISM_DISPATCH, PRISM_DONE, PRISM_PASS, RUN_CMD, RED_VALID,
                          TRACE_CMD, TRACE_PASS, _test_commit_fail(2),
                          SHIELD_DISPATCH, SHIELD_DONE, COLLECT_CMD, COLLECTED,
                          PRISM_DISPATCH, PRISM_DONE, PRISM_PASS, RUN_CMD, RED_VALID,
                          TRACE_CMD, TRACE_PASS, _test_commit_fail(3))
    assert three.status == "awaiting_human" and three.awaiting == "escalation"
    assert decide(three) is None
