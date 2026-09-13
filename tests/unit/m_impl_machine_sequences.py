"""Shared M-IMPL machine event sequences (unit fixtures).

Centralizes the long event prefixes that the M-IMPL machine unit modules used
to re-declare inline, which triggered pylint R0801 duplicate-code reports.
Each builder returns the exact ordered ``(type, payload)`` event list its name
describes. Higher builders compose lower ones so every event appears exactly
once; ``graph_committed`` overrides the standard single-task ``taskgraph.committed``
payload for the multi-task fixtures that need ``{"task_count": 2}``.

Only this module (plus the five ``test_machine_m_impl_*`` modules) is allowed
to change; the sequences below are byte-equivalent to the event lists the
original tests passed through ``state_of``/``project`` so reducers receive
identical input.
"""

from tests.unit.helpers import ARCHER_DISPATCH as ARCHER_DISPATCH
from tests.unit.helpers import ARCHER_DONE as ARCHER_DONE
from tests.unit.helpers import BASELINE_CMD as BASELINE_CMD
from tests.unit.helpers import BASELINE_FROZEN as BASELINE_FROZEN
from tests.unit.helpers import DEVON_GREEN_DISPATCH as DEVON_GREEN_DISPATCH
from tests.unit.helpers import DEVON_GREEN_DONE as DEVON_GREEN_DONE
from tests.unit.helpers import DEVON_RED_DISPATCH as DEVON_RED_DISPATCH
from tests.unit.helpers import DEVON_RED_DONE as DEVON_RED_DONE
from tests.unit.helpers import GREEN_COMMIT_CMD as GREEN_COMMIT_CMD
from tests.unit.helpers import GREEN_COMMITTED as GREEN_COMMITTED
from tests.unit.helpers import GREEN_GATE_CMD as GREEN_GATE_CMD
from tests.unit.helpers import GREEN_PASS as GREEN_PASS
from tests.unit.helpers import ISLAND1_CMD as ISLAND1_CMD
from tests.unit.helpers import ISLAND1_PASS as ISLAND1_PASS
from tests.unit.helpers import PRISM_PLAN_DISPATCH as PRISM_PLAN_DISPATCH
from tests.unit.helpers import PRISM_PLAN_DONE as PRISM_PLAN_DONE
from tests.unit.helpers import PRISM_PLAN_PASS as PRISM_PLAN_PASS
from tests.unit.helpers import PRISM_RED_DISPATCH as PRISM_RED_DISPATCH
from tests.unit.helpers import PRISM_RED_DONE as PRISM_RED_DONE
from tests.unit.helpers import PRISM_RED_PASS as PRISM_RED_PASS
from tests.unit.helpers import RED_CHECKPOINT_CMD as RED_CHECKPOINT_CMD
from tests.unit.helpers import RED_CHECKPOINTED as RED_CHECKPOINTED
from tests.unit.helpers import RED_GATE_CMD as RED_GATE_CMD
from tests.unit.helpers import RED_VALID_PASS as RED_VALID_PASS
from tests.unit.helpers import SELECT_TASK_CMD as SELECT_TASK_CMD
from tests.unit.helpers import TASK_STARTED as TASK_STARTED
from tests.unit.helpers import TASKGRAPH_CMD as TASKGRAPH_CMD
from tests.unit.helpers import TASKGRAPH_COMMITTED as TASKGRAPH_COMMITTED
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK as _M_IMPL_CRITERIA_PACK

DEVON_REFACTOR_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "devon", "substate": "REFACTOR"},
            "command_id": "C14",
        }
    },
)

DEVON_REFACTOR_DONE = ("outcome.received", {"role": "devon", "status": "done"})

REFACTOR_GATE_CMD = (
    "command.issued",
    {"command": {"kind": "run_refactor_gate", "params": {"stage": "M-IMPL"}, "command_id": "C15"}},
)

REFACTOR_COMMITTED = ("refactor.committed", {"commit_sha": "ghi789"})

TASK_REVIEW_CMD = (
    "command.issued",
    {
        "command": {
            "kind": "run_task_gates",
            "params": {"stage": "M-IMPL", "gate": "TASK_REVIEW"},
            "command_id": "C16",
        }
    },
)

TASK_REVIEW_PASS = ("verdict.passed", {"check": "task_review"})

PRISM_FINAL_DISPATCH = (
    "command.issued",
    {
        "command": {
            "kind": "dispatch_agent",
            "params": {"role": "prism", "substate": "PRISM_FINAL"},
            "command_id": "C17",
        }
    },
)

PRISM_FINAL_DONE = ("outcome.received", {"role": "prism", "status": "done"})

PRISM_FINAL_PASS = (
    "prism.verdict",
    {"verdict": "pass", "criteria_pack": dict(_M_IMPL_CRITERIA_PACK)},
)

COMPLETE_TASK_CMD = (
    "command.issued",
    {
        "command": {
            "kind": "complete_task",
            "params": {"stage": "M-IMPL", "task_id": "T1"},
            "command_id": "C18",
        }
    },
)

TASK_COMPLETED = ("task.completed", {"task_id": "T1"})

ISLAND2_CMD = (
    "command.issued",
    {"command": {"kind": "check_island_2", "params": {"stage": "M-IMPL"}, "command_id": "C19"}},
)

FULL1_CLEAN = (
    "full.executed",
    {"round": "FULL_1", "passed": True, "serves_as_full_f": True},
)

ISLAND2_PASS = ("verdict.passed", {"check": "island_2"})

EXIT_CMD = (
    "command.issued",
    {"command": {"kind": "write_frontmatter", "params": {"stage": "M-IMPL"}, "command_id": "C20"}},
)

STAGE_EXITED = ("stage.exited", {"stage": "M-IMPL"})


def planning():
    """BASELINE entered -> freeze_baseline issued -> baseline.frozen(current)."""
    return [BASELINE_CMD, BASELINE_FROZEN]


def archer_done():
    """After planning: Archer dispatch + done outcome."""
    return [*planning(), ARCHER_DISPATCH, ARCHER_DONE]


def taskgraph(graph_committed=None):
    """Through a committed taskgraph (default single task, overrideable)."""
    committed = graph_committed if graph_committed is not None else TASKGRAPH_COMMITTED
    return [*archer_done(), TASKGRAPH_CMD, committed]


def island1_cmd(graph_committed=None):
    """Through the island-1 gate command."""
    return [*taskgraph(graph_committed), ISLAND1_CMD]


def island1(graph_committed=None):
    """Through the island-1 pass verdict."""
    return [*island1_cmd(graph_committed), ISLAND1_PASS]


def prism_plan_done(graph_committed=None):
    """Through Prism PRISM_PLAN outcome before its verdict."""
    return [*island1(graph_committed), PRISM_PLAN_DISPATCH, PRISM_PLAN_DONE]


def prism_plan(graph_committed=None):
    """Through the PRISM_PLAN pass verdict."""
    return [*prism_plan_done(graph_committed), PRISM_PLAN_PASS]


def task_started(graph_committed=None):
    """Through task.started (substate RED)."""
    return [*prism_plan(graph_committed), SELECT_TASK_CMD, TASK_STARTED]


def red_done(graph_committed=None):
    """Through the Devon RED outcome (done)."""
    return [*task_started(graph_committed), DEVON_RED_DISPATCH, DEVON_RED_DONE]


def red_gate(graph_committed=None):
    """Through the RED_GATE gate command."""
    return [*red_done(graph_committed), RED_GATE_CMD]


def red_valid(graph_committed=None):
    """Through the red_valid verdict pass."""
    return [*red_gate(graph_committed), RED_VALID_PASS]


def red_checkpoint(graph_committed=None):
    """Through red.checkpointed."""
    return [*red_valid(graph_committed), RED_CHECKPOINT_CMD, RED_CHECKPOINTED]


def prism_red_done(graph_committed=None):
    """Through Prism PRISM_RED outcome before its verdict."""
    return [*red_checkpoint(graph_committed), PRISM_RED_DISPATCH, PRISM_RED_DONE]


def prism_red(graph_committed=None):
    """Through the PRISM_RED pass verdict."""
    return [*prism_red_done(graph_committed), PRISM_RED_PASS]


def green_done(graph_committed=None):
    """Through the Devon GREEN outcome (done)."""
    return [*prism_red(graph_committed), DEVON_GREEN_DISPATCH, DEVON_GREEN_DONE]


def green_gate(graph_committed=None):
    """Through the GREEN_GATE gate command."""
    return [*green_done(graph_committed), GREEN_GATE_CMD]


def green_pass(graph_committed=None):
    """Through the green verdict pass."""
    return [*green_gate(graph_committed), GREEN_PASS]


def green_commit(graph_committed=None):
    """Through green.committed."""
    return [*green_pass(graph_committed), GREEN_COMMIT_CMD, GREEN_COMMITTED]


def refactor_done(graph_committed=None):
    """Through the Devon REFACTOR outcome (done)."""
    return [*green_commit(graph_committed), DEVON_REFACTOR_DISPATCH, DEVON_REFACTOR_DONE]


def refactor_gate(graph_committed=None):
    """Through the REFACTOR_GATE gate command."""
    return [*refactor_done(graph_committed), REFACTOR_GATE_CMD]


def task_review(graph_committed=None):
    """Through refactor.committed (substate TASK_REVIEW)."""
    return [*refactor_gate(graph_committed), REFACTOR_COMMITTED]


def task_review_pass(graph_committed=None):
    """Through the task_review pass verdict."""
    return [*task_review(graph_committed), TASK_REVIEW_CMD, TASK_REVIEW_PASS]


def prism_final_done(graph_committed=None):
    """Through Prism PRISM_FINAL outcome before its verdict."""
    return [*task_review_pass(graph_committed), PRISM_FINAL_DISPATCH, PRISM_FINAL_DONE]


def prism_final(graph_committed=None):
    """Through the PRISM_FINAL pass verdict."""
    return [*prism_final_done(graph_committed), PRISM_FINAL_PASS]


def task_done(graph_committed=None):
    """Through task.completed."""
    return [*prism_final(graph_committed), COMPLETE_TASK_CMD, TASK_COMPLETED]


def island2(graph_committed=None):
    """Through the island-2 pass verdict (full suite clean)."""
    return [*task_done(graph_committed), ISLAND2_CMD, FULL1_CLEAN, ISLAND2_PASS]


def cycle(graph_committed=None):
    """The full single-task lifecycle through stage.exited."""
    return [*island2(graph_committed), EXIT_CMD, STAGE_EXITED]
