"""Task graph parsing and validation (FR-0030/FR-0180, IF-IMPL-003).

Pure functions: parse task-plan.md Task List + Dependency Graph, validate DAG
acyclicity (Kahn's algorithm), scope non-overlap (manifest whitelist
intersection empty), and required AC coverage closure.

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
    description: str
    target_files: tuple[str, ...]     # manifest whitelist (scope)
    if_ids: tuple[str, ...]           # implemented IF- set
    depends_on: tuple[str, ...]       # dependency task IDs
    parallel: bool                    # [P] marker (v0.5 serial, record only)
    budget: int                       # attempt budget


@dataclass(frozen=True)
class TaskGraphReport:
    status: Literal["pass", "fail"]
    tasks: tuple[TaskNode, ...]
    errors: tuple[str, ...]           # DAG cycle / scope overlap / AC coverage gap
    ac_coverage: dict[str, list[str]] # {ac_id: [task_id, ...]} required AC coverage


def parse_taskgraph(
    taskplan_text: str,
) -> tuple[list[TaskNode], str | None]:
    """FR-0030/FR-0180 parse task-plan.md Task List table + Dependency Graph.

    Returns (task_nodes, error). Tolerant parsing (skip blank/comment rows).
    Table columns: ID / Task description / Related test / Target file /
    Depends on / Parallel marker / Status.
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_dag(
    tasks: list[TaskNode],
) -> tuple[bool, str | None]:
    """FR-0030 DAG acyclicity check (Kahn's algorithm topological sort).

    Returns (is_acyclic, cycle_description). Cycle ->
    (False, 'cycle: T-001->T-002->T-001').
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_scope(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0030 scope non-overlap check (manifest whitelist intersection empty).

    Returns (no_overlap, overlap_errors). Overlap ->
    (False, ['T-001 and T-002 both target tracks/foo.py']).
    """
    raise NotImplementedError("IF-IMPL-003")


def validate_ac_coverage(
    tasks: list[TaskNode],
    required_acs: list[str],     # all required AC IDs from acceptance.md
    if_registry: set[str],       # IF- ids defined in interfaces.md §5
) -> tuple[bool, list[str]]:
    """FR-0030 required AC coverage closure check.

    Each required AC must be covered by at least one task's IF- set.
    Returns (all_covered, gap_errors). Gap ->
    (False, ['AC-FR0010-01 not covered by any task']).
    IF- ids declared by tasks must exist in if_registry (validity check).
    """
    raise NotImplementedError("IF-IMPL-003")
