"""Task graph parsing and validation (FR-0030/FR-0180, IF-IMPL-003).

Pure functions: parse tasks.json (task graph machine truth source), validate DAG
acyclicity (Kahn's algorithm), scope boundary non-overlap, required AC coverage
closure, IF- id validity, and issue number validity.

This module is a contract stub: signatures are frozen, bodies raise
NotImplementedError with the IF- token. Devon fills the implementation during
M-IMPL; the declaration contract must not change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    issue_number: int                 # GitHub issue number (positive integer, FR-0180/FR-0220)
    description: str                  # vertical slice description + implementation intent
    ac_refs: tuple[str, ...]          # AC IDs covered (e.g. AC-FR0030-01)
    fr_refs: tuple[str, ...]          # associated FR/NFR IDs (e.g. FR-0030, NFR-0010)
    if_ids: tuple[str, ...]           # IF- set implemented by this task
    test_refs: tuple[str, ...]        # test refs from test-plan §8 (file::case ids)
    scope_boundary: str               # manifest authorized scope (not pre-declared
                                      # files; observed diff is authoritative)
    depends_on: tuple[str, ...]       # dependency task IDs ([] or ["-"] = no dep)
    batch: str                        # batch marker
    parallel: bool                    # [P] marker (v0.5 serial, record only)
    budget: int                       # attempt budget (<=3, shared with m_impl_attempt)


@dataclass(frozen=True)
class TaskGraphReport:
    status: Literal["pass", "fail"]
    tasks: tuple[TaskNode, ...]
    errors: tuple[str, ...]           # cycle / scope overlap / AC gap / bad IF- / bad issue#
    ac_coverage: dict[str, list[str]] # {ac_id: [task_id, ...]} required AC coverage


def parse_tasks_json(
    tasks_json_text: str,
) -> tuple[list[TaskNode], str | None]:
    """FR-0030/FR-0180 parse tasks.json (task graph machine truth source).

    Returns (task_nodes, error). Tolerant parsing (skip unknown keys gracefully,
    fail on missing required fields). tasks.json schema: array of task objects
    with fields matching TaskNode + Dependency Graph + Runtime Review Result
    checklist (AC-FR0180-02). tasks.md is a human-readable projection generated
    deterministically by Runtime from tasks.json (not parsed here).
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_dag(
    tasks: list[TaskNode],
) -> tuple[bool, str | None]:
    """FR-0030/FR-0180 DAG acyclicity check (Kahn's algorithm topological sort).

    Returns (is_acyclic, cycle_description). Cycle ->
    (False, 'cycle: T-001->T-002->T-001').
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_scope(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 scope boundary non-overlap check.

    Scope boundary is manifest authorized scope (not pre-declared output file set).
    Non-overlap is validated by manifest whitelist intersection being empty.
    Returns (no_overlap, overlap_errors). Overlap ->
    (False, ['T-001 and T-002 both target tracks/foo.py']).
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_ac_coverage(
    tasks: list[TaskNode],
    required_acs: list[str],     # all required AC IDs from acceptance.md
    if_registry: set[str],       # IF- ids defined in interfaces.md §5
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 required AC coverage closure + IF- validity check.

    Each required AC must be covered by at least one task's ac_refs + if_ids.
    Returns (all_covered, gap_errors). Gap ->
    (False, ['AC-FR0010-01 not covered by any task']).
    IF- ids declared by tasks (if_ids) must exist in if_registry (validity check,
    AC-FR0180-04 check 4). Invalid IF- -> (False, ['T-001 declares IF-IMPL-999 not in registry']).
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_issue_numbers(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0180/FR-0220 issue number validity check (AC-FR0180-04 check 5).

    Each task's issue_number must be a positive integer (>=1).
    Returns (all_valid, errors). Invalid ->
    (False, ['T-001 issue_number=0 is not a positive integer']).
    """
    raise NotImplementedError("IF-IMPL-003")
