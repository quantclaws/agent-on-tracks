"""Item 7: M-TEST escalation -> `trac return --to M-DESIGN --reason ...` ->
human.return event -> replay yields rollback_stage(M-DESIGN).

Also verifies:
- M-REQ-APPROVAL (awaiting=approval) rejects `--to M-DESIGN` (C-03/SM-05.7a).
- M-TEST (awaiting=escalation) accepts `--to M-DESIGN`.
- Normal approval flow (M-REQ-APPROVAL) with `--to M-STORY` still works.

Contract extension: any author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|
M-TEST) at awaiting=escalation accepts a `trac return --to` the current or an
earlier author stage (closed per-stage set); M-TEST itself is never a target.
"""

from tests.m_test_support import make_dispatch_agent_payload
from tests.unit.helpers import seq


def _m_test_escalation_events():
    """Events that bring M-TEST to awaiting=escalation (3 failed no_diff).

    v0.5: Shield WRITE is an author substate, so no_diff routes through
    no_diff.detected peer review and surfaces as no_diff_justified
    (post-review rejection), not direct no_diff. 3x no_diff_justified
    consumes the shared <=3 attempt budget and escalates."""
    items = [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": "M-TEST"}),
    ]
    for attempt in range(1, 4):
        items.append(
            (
                "command.issued",
                {
                    "command": make_dispatch_agent_payload(
                        attempt=attempt,
                        review_round=1,
                        command_id=f"shield-{attempt}",
                    ),
                },
            )
        )
        items.append(
            (
                "outcome.received",
                {
                    "role": "shield",
                    "status": "done",
                    "artifact_ref": "tests",
                    "self_report": "wrote",
                },
            )
        )
        items.append(
            (
                "verdict.failed",
                {
                    "check": "no_diff_justified",
                    "reason": "reviewer rejected no-diff explanation",
                    "attempt": attempt,
                },
            )
        )
    return items


def test_m_test_escalation_human_return_to_m_design():
    """After 3 failed Shield cycles, M-TEST is awaiting=escalation. A
    `trac return --to M-DESIGN` emits human.return; decide() yields
    rollback_stage(M-DESIGN)."""
    from tracks.kernel import decide, project

    events = _m_test_escalation_events()
    state = project(seq(*events))
    assert state.stage == "M-TEST"
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"
    assert decide(state) is None  # halted for human

    events_with_return = events + [
        ("human.return", {"reason": "bad test plan", "to_stage": "M-DESIGN"}),
    ]
    state = project(seq(*events_with_return))
    assert state.substate == "RETURNED"
    assert state.return_target == "M-DESIGN"
    assert state.status == "active"

    cmd = decide(state)
    assert cmd is not None
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-DESIGN"
    assert cmd.params["reason"] == "human_return"


def test_m_test_escalation_return_gate_accepts_m_design():
    """The _return_gate for M-TEST awaiting=escalation includes M-DESIGN."""
    from tracks.cli.main import _RETURN_STAGES_ESCALATION

    assert "M-DESIGN" in _RETURN_STAGES_ESCALATION
    assert "M-STORY" in _RETURN_STAGES_ESCALATION
    assert "M-SPEC" in _RETURN_STAGES_ESCALATION
    assert "M-ACC" in _RETURN_STAGES_ESCALATION


def test_m_req_approval_return_gate_rejects_m_design():
    """M-REQ-APPROVAL (awaiting=approval) must NOT accept `--to M-DESIGN`
    (C-03/SM-05.7a: only upstream requirement stages)."""
    from tracks.cli.main import _RETURN_STAGES_APPROVAL

    assert "M-DESIGN" not in _RETURN_STAGES_APPROVAL
    assert "M-STORY" in _RETURN_STAGES_APPROVAL
    assert "M-SPEC" in _RETURN_STAGES_APPROVAL
    assert "M-ACC" in _RETURN_STAGES_APPROVAL


def test_replay_after_m_test_escalation_return_is_deterministic():
    """Replaying the same event log always yields rollback_stage(M-DESIGN)."""
    from tracks.kernel import decide, project

    events = _m_test_escalation_events() + [
        ("human.return", {"reason": "bad test plan", "to_stage": "M-DESIGN"}),
    ]
    s1 = project(seq(*events))
    s2 = project(seq(*events))
    assert s1.substate == s2.substate == "RETURNED"
    assert s1.return_target == s2.return_target == "M-DESIGN"
    c1 = decide(s1)
    c2 = decide(s2)
    assert c1 == c2
    assert c1.kind == "rollback_stage"
    assert c1.params["to_stage"] == "M-DESIGN"


# -- Contract extension: state-specific Human escalation return ---------------
#
# Any author/review stage (M-STORY|M-SPEC|M-ACC|M-DESIGN|M-TEST) at
# awaiting=escalation accepts a return to the current or an earlier author
# stage. Forward targets and M-REQ-APPROVAL are never allowed; M-TEST itself
# is not a re-author target (existing semantics).


def test_escalation_return_targets_per_stage():
    """Closed per-stage target sets for an escalation return."""
    from tracks.cli.main import _escalation_return_targets

    assert _escalation_return_targets("M-STORY") == ("M-STORY",)
    assert _escalation_return_targets("M-SPEC") == ("M-STORY", "M-SPEC")
    assert _escalation_return_targets("M-ACC") == ("M-STORY", "M-SPEC", "M-ACC")
    assert _escalation_return_targets("M-DESIGN") == (
        "M-STORY",
        "M-SPEC",
        "M-ACC",
        "M-DESIGN",
    )
    # M-TEST: all four author stages; M-TEST itself is not a re-author target.
    assert _escalation_return_targets("M-TEST") == (
        "M-STORY",
        "M-SPEC",
        "M-ACC",
        "M-DESIGN",
    )
    # M-REQ-APPROVAL / M-START / unknown have no escalation return targets.
    assert _escalation_return_targets("M-REQ-APPROVAL") == ()
    assert _escalation_return_targets("M-START") == ()


def test_m_story_escalation_accepts_same_stage():
    """M-STORY awaiting=escalation accepts `--to M-STORY` (same-stage rollback
    — the real blocker: SAGE_REVIEW escalation must return to M-STORY)."""
    from tracks.cli.main import _escalation_return_targets

    targets = _escalation_return_targets("M-STORY")
    assert "M-STORY" in targets
    assert "M-SPEC" not in targets
    assert "M-ACC" not in targets
    assert "M-DESIGN" not in targets


def test_m_spec_escalation_rejects_forward_m_design():
    """M-SPEC awaiting=escalation rejects a forward `--to M-DESIGN`."""
    from tracks.cli.main import _escalation_return_targets

    targets = _escalation_return_targets("M-SPEC")
    assert "M-DESIGN" not in targets
    assert "M-STORY" in targets
    assert "M-SPEC" in targets


def test_m_design_escalation_accepts_m_design():
    """M-DESIGN awaiting=escalation accepts `--to M-DESIGN` (same-stage)."""
    from tracks.cli.main import _escalation_return_targets

    targets = _escalation_return_targets("M-DESIGN")
    assert "M-DESIGN" in targets
    assert "M-STORY" in targets


def test_m_story_same_stage_human_return_rolls_back():
    """Same-stage human.return at M-STORY -> decide() yields
    rollback_stage(M-STORY). The kernel must decide/execute a same-stage
    rollback, not reject it."""
    from tracks.kernel import decide, project

    events = [
        ("story.requested", {"raw_chars": 5}),
        ("stage.entered", {"stage": "M-STORY"}),
        ("human.return", {"reason": "re-scope", "to_stage": "M-STORY"}),
    ]
    s = project(seq(*events))
    assert s.stage == "M-STORY"
    assert s.substate == "RETURNED"
    assert s.return_target == "M-STORY"
    assert s.status == "active"
    cmd = decide(s)
    assert cmd is not None
    assert cmd.kind == "rollback_stage"
    assert cmd.params["to_stage"] == "M-STORY"
    assert cmd.params["reason"] == "human_return"
