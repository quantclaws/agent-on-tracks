"""Generic ResultCheckpoint validation retry projection regressions."""

import pytest

from tests.unit.helpers import seq
from tracks.kernel import decide, project


@pytest.mark.parametrize(
    ("stage", "substate", "role", "setup"),
    [
        (
            "M-STORY",
            "DRAFT",
            "scribe",
            [
                ("human.triage", {"decision": "go"}),
            ],
        ),
        ("M-SPEC", "DRAFT", "sage", []),
        ("M-ACC", "DRAFT", "sage", []),
        (
            "M-STORY",
            "SAGE_REVIEW",
            "sage",
            [
                ("story.committed", {"final": False}),
            ],
        ),
        (
            "M-SPEC",
            "LEX_REVIEW",
            "lex",
            [
                ("spec.committed", {"final": False}),
            ],
        ),
        (
            "M-ACC",
            "LEX_REVIEW",
            "lex",
            [
                ("acceptance.committed", {"final": False}),
            ],
        ),
        ("M-TEST", "WRITE", "shield", []),
        (
            "M-TEST",
            "PRISM_REVIEW",
            "prism",
            [
                ("test.collected", {"status": "passed"}),
            ],
        ),
        # Item 8: M-DESIGN author (DRAFT) and reviewer (PRISM_REVIEW).
        # PRISM_REVIEW requires 3 design.committed events (one per doc)
        # before the state enters PRISM_REVIEW (the review substate).
        ("M-DESIGN", "DRAFT", "archer", []),
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
        ),
    ],
)
def test_checkpoint_validation_failure_retries_only_current_actor(stage, substate, role, setup):
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
                "check": "no_diff",
                "reason": "result requires a diff",
                "attempt": 1,
            },
        ),
    ]

    state = project(seq(*events))
    command = decide(state)

    assert state.stage == stage and state.substate == substate
    assert state.current_attempt == 1
    assert state.last_failure["check"] == "no_diff"
    assert command.kind == "dispatch_agent"
    assert command.params["role"] == role
    assert command.params["attempt"] == 2
