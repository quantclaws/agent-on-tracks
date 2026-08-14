"""M-REQ-APPROVAL reducer/decide branches (FR-0180/0200, SM-05) — pure (NFR-02).

PREVIEW → AWAIT_HUMAN → APPROVED → ISSUES → boundary exit plus the RETURNED
rollback loop; the AWAIT_HUMAN halt is the hard human gate (Agent 不可代批).
"""

from tests.unit.helpers import seq
from tracks.kernel import decide, project

ENTER = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
]
PREVIEW = ("preview.generated", {"digest": "d1", "summary": "story; spec; acc"})
APPROVAL = ("human.approval", {"actor": "Aaron", "digest": "d1", "ts": "t1"})
RECORDED = ("approval.recorded", {"actor": "Aaron", "digest": "d1", "ts": "t1", "readonly": True})
GH_FAIL = ("outcome.received", {"role": "github", "status": "failed", "failure_class": "network"})


def state_of(*items):
    return project(seq(*ENTER, *items))


def test_enter_preview():
    # SM-05.1: entering M-REQ-APPROVAL starts at PREVIEW; preview is generated
    s = state_of()
    assert s.stage == "M-REQ-APPROVAL" and s.substate == "PREVIEW"
    assert decide(s).kind == "generate_preview"


def test_preview_awaits_human():
    # SM-05.2: preview.generated parks the run at the human gate
    s = state_of(PREVIEW)
    assert s.preview_ready and s.substate == "AWAIT_HUMAN"
    assert s.status == "awaiting_human" and s.awaiting == "approval"


def test_human_gate_halt():
    # SM-05.3: without a human.approval event decide() never proceeds —
    # APPROVED is unreachable for agents (FR-0180 hard gate).
    s = state_of(PREVIEW)
    assert decide(s) is None
    assert not s.approved


def test_approval_binds_identity():
    # SM-05.3: human.approval is the ONLY entry into APPROVED
    s = state_of(PREVIEW, APPROVAL)
    assert s.approved and s.substate == "APPROVED"
    assert s.status == "active" and s.awaiting is None
    assert s.approval_digest == "d1" and s.approval_actor == "Aaron"
    cmd = decide(s)
    assert cmd.kind == "record_approval"
    assert cmd.params == {"actor": "Aaron", "digest": "d1"}


def test_approval_recorded_to_issues():
    # SM-05.5 (C-01): approval.recorded IS the APPROVED->ISSUES transition
    s = state_of(PREVIEW, APPROVAL, RECORDED)
    assert s.substate == "ISSUES" and not s.issues_created
    cmd = decide(s)
    assert cmd.kind == "create_issues" and cmd.params == {"digest": "d1"}


def test_issues_created_boundary_exit():
    # SM-05.6: after the summary event the stage exits at the M-DESIGN boundary
    s = state_of(PREVIEW, APPROVAL, RECORDED, ("issues.created", {"digest": "d1", "mapping": {}}))
    assert s.issues_created
    cmd = decide(s)
    assert cmd.kind == "write_frontmatter"
    assert cmd.params == {"stage": "M-REQ-APPROVAL"}  # no doc to seal
    exited = state_of(
        PREVIEW,
        APPROVAL,
        RECORDED,
        ("issues.created", {"digest": "d1", "mapping": {}}),
        ("stage.exited", {"stage": "M-REQ-APPROVAL"}),
        ("run.completed", {"terminal_state": "boundary"}),
    )
    assert exited.status == "completed" and exited.terminal_state == "boundary"
    assert decide(exited) is None


def test_stale_re_preview_reopens_gate():
    # D-03/C-02: a regenerated preview after approval reopens the human gate
    s = state_of(
        PREVIEW, APPROVAL, RECORDED, ("preview.generated", {"digest": "d2", "summary": "s2"})
    )
    assert not s.approved and s.substate == "AWAIT_HUMAN"
    assert s.awaiting == "approval" and decide(s) is None


def test_return_rolls_back():
    # SM-05.4/.7: human.return -> RETURNED -> rollback command to the target
    s = state_of(PREVIEW, ("human.return", {"to_stage": "M-SPEC", "reason": "r"}))
    assert s.returned and s.substate == "RETURNED"
    assert s.status == "active" and s.return_target == "M-SPEC"
    cmd = decide(s)
    assert cmd.kind == "rollback_stage"
    assert cmd.params == {"to_stage": "M-SPEC", "reason": "human_return"}


def test_rolled_back_clears_return_and_reentry_resets():
    # SM-05.7: rollback lands in the target's DRAFT; re-entering the approval
    # stage later restarts the cycle fresh (no leaked approval identity).
    s = state_of(
        PREVIEW,
        ("human.return", {"to_stage": "M-SPEC", "reason": "r"}),
        ("stage.rolled_back", {"to_stage": "M-SPEC"}),
    )
    assert s.stage == "M-SPEC" and s.substate == "DRAFT"
    assert not s.returned and s.return_target is None
    again = state_of(
        PREVIEW,
        APPROVAL,
        RECORDED,
        ("human.return", {"to_stage": "M-ACC", "reason": "r"}),
        ("stage.rolled_back", {"to_stage": "M-ACC"}),
        ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
    )
    assert again.substate == "PREVIEW" and not again.preview_ready
    assert not again.approved and again.approval_digest is None
    assert not again.issues_created


def test_issues_failure_counts_attempts_then_escalates():
    # FR-0200/NFR-0030: failed create_issues outcomes consume attempts;
    # the 3rd escalates to Human. Progress lives in issue.created (D-06).
    two = state_of(PREVIEW, APPROVAL, RECORDED, GH_FAIL, GH_FAIL)
    assert two.status == "active" and two.current_attempt == 2
    assert decide(two).kind == "create_issues"  # retry resumes, not rebuilds
    three = state_of(PREVIEW, APPROVAL, RECORDED, GH_FAIL, GH_FAIL, GH_FAIL)
    assert three.status == "awaiting_human" and three.awaiting == "escalation"
    assert decide(three) is None
