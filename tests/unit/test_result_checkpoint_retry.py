"""Generic ResultCheckpoint validation retry projection regressions."""

import pytest

from tests.unit.helpers import seq
from tracks.kernel import decide, project


@pytest.mark.parametrize(
    ("stage", "substate", "role", "setup", "check"),
    [
        # Author DRAFT substates have requires_diff=False, so no_diff can
        # never fire; use "schema" (a check that CAN fire for DRAFT via
        # _check_templates) instead.
        (
            "M-STORY",
            "DRAFT",
            "scribe",
            [
                ("human.triage", {"decision": "go"}),
            ],
            "schema",
        ),
        ("M-SPEC", "DRAFT", "sage", [], "schema"),
        ("M-ACC", "DRAFT", "sage", [], "schema"),
        (
            "M-STORY",
            "SAGE_REVIEW",
            "sage",
            [
                ("story.committed", {"final": False}),
            ],
            "no_diff",
        ),
        (
            "M-SPEC",
            "LEX_REVIEW",
            "lex",
            [
                ("spec.committed", {"final": False}),
            ],
            "no_diff",
        ),
        (
            "M-ACC",
            "LEX_REVIEW",
            "lex",
            [
                ("acceptance.committed", {"final": False}),
            ],
            "no_diff",
        ),
        # M-TEST/WRITE is an author substate with requires_diff=True; in v0.5
        # no_diff routes through no_diff.detected peer review and surfaces as
        # no_diff_justified (post-review rejection), not direct no_diff.
        ("M-TEST", "WRITE", "shield", [], "no_diff_justified"),
        (
            "M-TEST",
            "PRISM_REVIEW",
            "prism",
            [
                ("test.collected", {"status": "passed"}),
            ],
            "no_diff",
        ),
        # Item 8: M-DESIGN author (DRAFT) and reviewer (PRISM_REVIEW).
        # PRISM_REVIEW requires 3 design.committed events (one per doc)
        # before the state enters PRISM_REVIEW (the review substate).
        ("M-DESIGN", "DRAFT", "archer", [], "schema"),
        (
            "M-DESIGN",
            "PRISM_REVIEW",
            "prism",
            [
                (
                    "design.committed",
                    {"doc": "architecture.md", "commit_sha": "sha-a", "final": False},
                ),
                (
                    "design.committed",
                    {"doc": "interfaces.md", "commit_sha": "sha-a", "final": False},
                ),
                (
                    "design.committed",
                    {"doc": "test-plan.md", "commit_sha": "sha-a", "final": False},
                ),
            ],
            "no_diff",
        ),
    ],
)
def test_checkpoint_validation_failure_retries_only_current_actor(
    stage, substate, role, setup, check
):
    checkpoint = {
        "stage": stage,
        "substate": substate,
        "actor_kind": "agent",
        "result_id": "result-1",
    }
    events = [
        ("story.requested", {"raw_chars": 1}),
        ("stage.entered", {"stage": stage}),
        *setup,
        (
            "command.issued",
            {
                "command": {
                    "kind": "dispatch_agent",
                    "params": {"role": role, "substate": substate},
                    "command_id": "command-1",
                }
            },
        ),
        (
            "outcome.received",
            {
                "role": role,
                "status": "done",
                "result_checkpoint": checkpoint,
            },
        ),
        (
            "verdict.failed",
            {
                "check": check,
                "reason": "validation failure",
                "attempt": 1,
            },
        ),
    ]

    state = project(seq(*events))
    command = decide(state)

    assert state.stage == stage and state.substate == substate
    assert state.current_attempt == 1
    assert state.last_failure["check"] == check
    assert command.kind == "dispatch_agent"
    assert command.params["role"] == role
    assert command.params["attempt"] == 2
