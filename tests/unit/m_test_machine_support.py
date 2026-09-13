"""Shared M-TEST reducer/decide event fixtures and state builders.

Not pytest-collected: the module name does not match ``test_*`` / ``*_test``
(see ``pyproject.toml`` ``testpaths``). Houses the SM-01 / flow.md §9 event
sequences consumed by ``test_machine_m_test.py`` and
``test_kernel_final_review_blockers.py``, plus the state-building helpers used
across the M-TEST machine tests.

The event group tuples (``TO_COLLECT``, ``TO_RED_CHECK``, ...) are literal
concatenations of the base events, preserved here so every test that walks the
same M-TEST forward path reads ``seq(*ENTER_M_TEST, *TO_...)`` instead of the
same ten-line literal. ``BASELINE_CAPTURED`` is the first-ever (empty) capture
used by the machine suite; ``BASELINE_CAPTURED_POPULATED`` is the seeded
baseline used by the kernel final-review-blockers suite.
"""

from tests.unit.helpers import seq
from tracks.kernel import project
from tracks.kernel.machine import _CRITERIA_PACK, _M_TEST_CONTEXT_DOCS

ENTER_M_TEST = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-TEST"}),
]

# D-41 v3 timing (flow.md §9.1): the pre-WRITE R1 snapshot capture is the first
# M-TEST command and its passed event gates the first Shield WRITE dispatch.
CAPTURE_CMD = (
    "command.issued",
    {"command": {"kind": "capture_baseline", "params": {"stage": "M-TEST"}, "command_id": "C0"}},
)


def _baseline_captured(nodes_count, empty_baseline):
    return (
        "test.baseline_captured",
        {
            "status": "passed",
            "baseline_id": "b0",
            "baseline_tree": "t0",
            "layers": ["unit", "integration", "e2e"],
            "nodes_count": nodes_count,
            "empty_baseline": empty_baseline,
            "node_digest_blob": None,
            "errors": [],
        },
    )


BASELINE_CAPTURED = _baseline_captured(nodes_count=0, empty_baseline=True)
BASELINE_CAPTURED_POPULATED = _baseline_captured(nodes_count=1, empty_baseline=False)

SHIELD_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "shield", "substate": "WRITE"},
            "command_id": "C1",
        }
    },
)
SHIELD_DONE = ("outcome.received", {"role": "shield", "status": "done"})
SHIELD_FAILED = (
    "outcome.received",
    {"role": "shield", "status": "failed", "failure_class": "agent_failed"},
)
STUB_GAP_OUTCOME = (
    "outcome.received",
    {
        "role": "shield",
        "status": "failed",
        "failure_class": "stub_gap",
        "self_report": "M-DESIGN test-task contract invalid",
        "audit_evidence": "empty test_tasks",
    },
)
COLLECT_CMD = (
    "command.issued",
    {"command": {"kind": "collect_tests", "params": {"stage": "M-TEST"}, "command_id": "C2"}},
)
COLLECTED = ("test.collected", {"status": "passed", "collected_count": 1, "errors": []})
PRISM_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "prism", "substate": "PRISM_REVIEW"},
            "command_id": "C3",
        }
    },
)
PRISM_DONE = ("outcome.received", {"role": "prism", "status": "done"})
PRISM_PASS = ("prism.verdict", {"verdict": "pass", "criteria_pack": dict(_CRITERIA_PACK)})
RUN_CMD = (
    "command.issued",
    {"command": {"kind": "run_tests", "params": {"stage": "M-TEST"}, "command_id": "C4"}},
)
RED_VALID = ("red.validated", {"status": "valid", "findings": []})
TRACE_CMD = (
    "command.issued",
    {"command": {"kind": "check_trace", "params": {"stage": "M-TEST"}, "command_id": "C5"}},
)
TRACE_PASS = ("verdict.passed", {"check": "trace", "detail": "closure verified"})
COMMIT_CMD = (
    "command.issued",
    {"command": {"kind": "commit_tests", "params": {"stage": "M-TEST"}, "command_id": "C6"}},
)
TEST_COMMITTED = ("test.committed", {"commit_sha": "abc", "test_count": 1})

# Event-group prefixes: literal concatenations of the base events above. Each
# tuple ends in the event that lands the machine in the named substate.
PRE_WRITE = (CAPTURE_CMD, BASELINE_CAPTURED)
TO_COLLECT = (*PRE_WRITE, SHIELD_DISPATCH, SHIELD_DONE)
TO_RED_CHECK = (*TO_COLLECT, COLLECT_CMD, COLLECTED)
TO_COLLECT_POPULATED = (CAPTURE_CMD, BASELINE_CAPTURED_POPULATED, SHIELD_DISPATCH, SHIELD_DONE)
TO_RED_CHECK_POPULATED = (*TO_COLLECT_POPULATED, COLLECT_CMD, COLLECTED)
TO_PRISM_REVIEW = (*TO_RED_CHECK, RUN_CMD, RED_VALID)
IN_PRISM_REVIEW = (*TO_PRISM_REVIEW, PRISM_DISPATCH, PRISM_DONE)

ENTER_M_SPEC = [
    ("story.requested", {"raw_chars": 5}),
    ("stage.entered", {"stage": "M-SPEC"}),
]


def state_of(*items):
    return project(seq(*ENTER_M_TEST, *items))


TERMINAL_EVENTS = [
    TRACE_CMD,
    TRACE_PASS,
    COMMIT_CMD,
    TEST_COMMITTED,
    ("stage.exited", {"stage": "M-TEST"}),
    ("run.completed", {"terminal_state": "boundary"}),
]


def _full_cycle():
    """CAPTURE -> DISPATCH -> WRITE -> COLLECT -> RED_CHECK -> PRISM_REVIEW ->
    EXIT (D-41 v3 timing)."""
    return [
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        RED_VALID,
        PRISM_DISPATCH,
        PRISM_DONE,
        PRISM_PASS,
        *TERMINAL_EVENTS,
    ]


def _diagnose_state(classification):
    invalid = ("red.validated", {"status": "invalid", "findings": []})
    verdict = (
        "verdict.failed",
        {
            "check": classification,
            "target_stage": {
                "test_defect": "M-TEST",
                "stub_gap": "M-DESIGN",
                "ac_gap": "M-ACC",
                "spec_gap": "M-SPEC",
            }[classification],
            "artifact_disposition": "rewrite",
            "attempt": 1,
        },
    )
    return state_of(
        CAPTURE_CMD,
        BASELINE_CAPTURED,
        SHIELD_DISPATCH,
        SHIELD_DONE,
        COLLECT_CMD,
        COLLECTED,
        RUN_CMD,
        invalid,
        verdict,
    )


def _shield_checkpoint(cmd_id="C1"):
    """Minimal result_checkpoint payload for M-TEST Shield WRITE."""
    return {
        "source": "shield",
        "stage": "M-TEST",
        "substate": "WRITE",
        "actor_kind": "agent",
        "artifacts": ["tests/integration/test_ac_fr0010_01.py"],
        "allowed_paths": ["tests/integration/test_ac_fr0010_01.py"],
        "base_sha": "b",
        "checks": ["write_scope", "collection"],
        "requires_diff": False,
        "forbid_diff": False,
        "discussion_only": False,
        "commit_label": "M-TEST: shield commit",
        "result_id": cmd_id,
        "digests": {},
        "domain_event": {"type": "test.written", "payload": {}},
    }


def _prism_checkpoint(verdict="pass", cmd_id="C3"):
    """Minimal result_checkpoint payload for M-TEST Prism review."""
    return {
        "source": "prism",
        "stage": "M-TEST",
        "substate": "PRISM_REVIEW",
        "actor_kind": "agent",
        "verdict": verdict,
        "artifacts": list(_M_TEST_CONTEXT_DOCS),
        "allowed_paths": list(_M_TEST_CONTEXT_DOCS),
        "base_sha": "b",
        "checks": ["template"],
        "requires_diff": verdict != "pass",
        "forbid_diff": False,
        "discussion_only": True,
        "commit_label": f"M-TEST: prism ({verdict}) checkpoint",
        "result_id": cmd_id,
        "digests": {},
        "domain_event": {
            "type": "prism.verdict",
            "payload": {"verdict": verdict, "criteria_pack": dict(_CRITERIA_PACK)},
        },
    }


SHIELD_DONE_CP = (
    "outcome.received",
    {"role": "shield", "status": "done", "result_checkpoint": _shield_checkpoint()},
)


def _prism_revise_with_dc(dc):
    """prism.verdict(revise) with the given defect_classification."""
    payload = {"verdict": "revise", "criteria_pack": dict(_CRITERIA_PACK)}
    if dc is not None:
        payload["defect_classification"] = dc
    return ("prism.verdict", payload)
