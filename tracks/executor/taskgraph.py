"""Task graph parsing and validation (FR-0030/FR-0180, IF-IMPL-003).

Pure functions: parse tasks.json (task graph machine truth source), validate DAG
acyclicity (Kahn's algorithm), scope boundary non-overlap, required AC coverage
closure, IF- id validity, and issue number validity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TaskNode:
    task_id: str
    issue_number: int
    description: str
    ac_refs: tuple[str, ...]
    fr_refs: tuple[str, ...]
    if_ids: tuple[str, ...]
    test_refs: tuple[str, ...]
    scope_boundary: str
    depends_on: tuple[str, ...]
    batch: str
    parallel: bool
    budget: int


@dataclass(frozen=True)
class TaskGraphReport:
    status: Literal["pass", "fail"]
    tasks: tuple[TaskNode, ...]
    errors: tuple[str, ...]
    ac_coverage: dict[str, list[str]]


_REQUIRED_FIELDS = (
    "task_id",
    "issue_number",
    "description",
    "ac_refs",
    "fr_refs",
    "if_ids",
    "test_refs",
    "scope_boundary",
    "depends_on",
    "batch",
    "parallel",
    "budget",
)


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
    data, err = _parse_json(tasks_json_text)
    if err is not None:
        return ([], err)
    if not isinstance(data, dict):
        return ([], "tasks.json: root must be a JSON object")
    raw_tasks = data.get("tasks")
    if raw_tasks is None:
        return ([], "tasks.json: missing 'tasks' key")
    if not isinstance(raw_tasks, list):
        return ([], "tasks.json: 'tasks' must be a list")
    tasks: list[TaskNode] = []
    for index, raw in enumerate(raw_tasks):
        node, err = _parse_task(raw, index)
        if err is not None:
            return ([], err)
        tasks.append(node)
    return (tasks, None)


def _parse_json(text: str) -> tuple[object | None, str | None]:
    try:
        return (json.loads(text), None)
    except json.JSONDecodeError as exc:
        pos = f"line {exc.lineno} column {exc.colno}"
        return (None, f"tasks.json: invalid JSON at {pos}: {exc.msg}")


def _parse_task(raw: object, index: int) -> tuple[TaskNode | None, str | None]:
    if not isinstance(raw, dict):
        return (None, f"tasks.json: task[{index}] must be an object")
    tid = raw.get("task_id", f"task[{index}]")
    if not isinstance(tid, str) or not tid:
        return (None, f"tasks.json: task[{index}] task_id must be a non-empty string")
    for field in _REQUIRED_FIELDS:
        if field not in raw:
            return (None, f"tasks.json: {tid}: missing required field '{field}'")
    err = _validate_scalar_fields(raw, tid)
    if err:
        return (None, err)
    lists = _parse_list_fields(raw, tid)
    if isinstance(lists, str):
        return (None, lists)
    ac_refs, fr_refs, if_ids, test_refs, depends = lists
    return (
        TaskNode(
            task_id=tid,
            issue_number=raw["issue_number"],
            description=raw["description"],
            ac_refs=tuple(ac_refs),
            fr_refs=tuple(fr_refs),
            if_ids=tuple(if_ids),
            test_refs=tuple(test_refs),
            scope_boundary=raw["scope_boundary"],
            depends_on=tuple(depends),
            batch=raw["batch"],
            parallel=raw["parallel"],
            budget=raw["budget"],
        ),
        None,
    )


def _validate_scalar_fields(raw: dict, tid: str) -> str | None:
    issue = raw["issue_number"]
    if not isinstance(issue, int) or isinstance(issue, bool):
        return f"tasks.json: {tid}: issue_number must be an integer"
    if not isinstance(raw["description"], str):
        return f"tasks.json: {tid}: description must be a string"
    if not isinstance(raw["scope_boundary"], str):
        return f"tasks.json: {tid}: scope_boundary must be a string"
    if not isinstance(raw["batch"], str):
        return f"tasks.json: {tid}: batch must be a string"
    if not isinstance(raw["parallel"], bool):
        return f"tasks.json: {tid}: parallel must be a boolean"
    budget = raw["budget"]
    if not isinstance(budget, int) or isinstance(budget, bool):
        return f"tasks.json: {tid}: budget must be an integer"
    return None


def _parse_list_fields(raw: dict, tid: str) -> tuple[list[str], ...] | str:
    result: list[list[str]] = []
    for field in ("ac_refs", "fr_refs", "if_ids", "test_refs", "depends_on"):
        vals, err = _str_list(raw[field], field, tid)
        if err:
            return err
        result.append(vals)
    return tuple(result)


def _str_list(val: object, name: str, tid: str) -> tuple[list[str] | None, str | None]:
    if not isinstance(val, list):
        return (None, f"tasks.json: {tid}: {name} must be a list")
    for item in val:
        if not isinstance(item, str):
            return (None, f"tasks.json: {tid}: {name} must be a list of strings")
    return (val, None)


def validate_dag(
    tasks: list[TaskNode],
) -> tuple[bool, str | None]:
    """FR-0030/FR-0180 DAG acyclicity check (Kahn's algorithm topological sort).

    Returns (is_acyclic, cycle_description). Unknown dependency ->
    (False, 'T-001 declares unknown dependency 'T-999'').
    Cycle -> (False, 'cycle: T-001->T-002->T-001').
    """
    ids = {t.task_id for t in tasks}
    if len(ids) != len(tasks):
        duplicates = sorted(
            task_id for task_id in ids if sum(t.task_id == task_id for t in tasks) > 1
        )
        return (False, f"duplicate task_id: {', '.join(duplicates)}")
    for t in sorted(tasks, key=lambda x: x.task_id):
        for dep in t.depends_on:
            if dep == "-":
                continue
            if dep not in ids:
                return (False, f"{t.task_id} declares unknown dependency '{dep}'")
    adj: dict[str, list[str]] = {}
    for t in tasks:
        adj[t.task_id] = [d for d in t.depends_on if d != "-" and d in ids]
    cycle = _find_cycle(adj)
    if cycle is not None:
        return (False, f"cycle: {cycle}")
    return (True, None)


def _find_cycle(adj: dict[str, list[str]]) -> str | None:
    WHITE = 0
    color: dict[str, int] = dict.fromkeys(adj, WHITE)
    parent: dict[str, str] = {}
    for start in sorted(adj):
        if color[start] != WHITE:
            continue
        result = _dfs_cycle(start, adj, color, parent)
        if result is not None:
            return result
    return None


def _dfs_cycle(
    start: str,
    adj: dict[str, list[str]],
    color: dict[str, int],
    parent: dict[str, str],
) -> str | None:
    stack: list[tuple[str, int]] = [(start, 0)]
    color[start] = 1
    while stack:
        node, idx = stack[-1]
        neighbors = adj.get(node, [])
        if idx < len(neighbors):
            stack[-1] = (node, idx + 1)
            child = neighbors[idx]
            if color.get(child, 0) == 1:
                return _extract_cycle(node, child, parent)
            if color.get(child, 0) == 0:
                color[child] = 1
                parent[child] = node
                stack.append((child, 0))
        else:
            color[node] = 2
            stack.pop()
    return None


def _extract_cycle(u: str, v: str, parent: dict[str, str]) -> str:
    path = [u]
    node = u
    while node != v and node in parent:
        node = parent[node]
        path.append(node)
    path.reverse()
    return "->".join(path + [path[0]])


def validate_scope(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 scope boundary non-overlap check.

    Scope boundary is manifest authorized scope (not pre-declared output file set).
    Non-overlap is validated by manifest whitelist intersection being empty.
    Returns (no_overlap, overlap_errors). Overlap ->
    (False, ['scope overlap: T-001 and T-002 both target tracks/foo.py']).
    """
    errors: list[str] = []
    for task in tasks:
        paths, path_errors = _scope_paths(task.scope_boundary, task.task_id)
        if path_errors:
            errors.extend(path_errors)
        if not paths:
            errors.append(f"{task.task_id}: scope_boundary must not be empty")
    for i, t1 in enumerate(tasks):
        s1 = _parse_scope_paths(t1.scope_boundary)
        for t2 in tasks[i + 1 :]:
            overlap = s1 & _parse_scope_paths(t2.scope_boundary)
            for path in sorted(overlap):
                errors.append(f"scope overlap: {t1.task_id} and {t2.task_id} both target {path}")
    return (len(errors) == 0, errors)


def _parse_scope_paths(scope_boundary: str) -> frozenset[str]:
    paths, _ = _scope_paths(scope_boundary)
    return frozenset(paths)


def validate_task_structure(tasks: list[TaskNode]) -> list[str]:
    """Validate task fields that are structural but not graph relations."""
    if not tasks:
        return ["tasks.json: task graph must contain at least one task"]
    return [error for task in tasks for error in _task_structure_errors(task)]


def _task_structure_errors(task: TaskNode) -> list[str]:
    errors: list[str] = []
    if not task.description.strip():
        errors.append(f"{task.task_id}: description must not be empty")
    if not task.batch.strip():
        errors.append(f"{task.task_id}: batch must not be empty")
    if task.budget < 1:
        errors.append(f"{task.task_id}: budget must be a positive integer")
    if not task.ac_refs:
        errors.append(f"{task.task_id}: ac_refs must not be empty")
    if not task.fr_refs:
        errors.append(f"{task.task_id}: fr_refs must not be empty")
    if not task.if_ids:
        errors.append(f"{task.task_id}: if_ids must not be empty")
    if not task.test_refs:
        errors.append(f"{task.task_id}: test_refs must not be empty")
    return errors


def validate_island_closure(
    tasks: list[TaskNode],
    architecture_text: str,
) -> tuple[bool, list[str]]:
    """Revalidate the design-time owner/surface/... closure for each task.

    M-IMPL does not create this six-tuple.  It only verifies that the
    architecture still contains a closure entry for every task AC and that the
    entry names the task's declared IF ids.  The check is deliberately based on
    the frozen document text, not on task self-reporting.
    """
    errors: list[str] = []
    required = ("owner=", "surface=", "composition=", "wiring=", "test=", "evidence=")
    lines = architecture_text.splitlines()
    for task in tasks:
        errors.extend(_task_closure_errors(task, lines, required))
    return (not errors, errors)


def _task_closure_errors(
    task: TaskNode,
    lines: list[str],
    required: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    for ac_id in task.ac_refs:
        requirement = _requirement_ref(ac_id)
        matches = _closure_matches(lines, requirement)
        if not matches:
            errors.append(f"{task.task_id}/{ac_id}: six-tuple entry missing")
            continue
        closure = " ".join(matches)
        missing = [field for field in required if field not in closure]
        errors.extend(_closure_field_errors(task.task_id, ac_id, missing))
        errors.extend(_missing_if_errors(task.task_id, ac_id, closure, task.if_ids))
    return errors


def _closure_field_errors(
    task_id: str,
    ac_id: str,
    missing: list[str],
) -> list[str]:
    if not missing:
        return []
    return [f"{task_id}/{ac_id}: missing closure fields " + ", ".join(missing)]


def _missing_if_errors(
    task_id: str,
    ac_id: str,
    closure: str,
    if_ids: tuple[str, ...],
) -> list[str]:
    return [
        f"{task_id}/{ac_id}: {if_id} missing from closure"
        for if_id in if_ids
        if if_id not in closure
    ]


def _scope_paths(
    scope_boundary: str,
    task_id: str = "task",
) -> tuple[list[str], list[str]]:
    raw_paths = [part.strip() for part in scope_boundary.replace("\n", ",").split(",")]
    paths: list[str] = []
    errors: list[str] = []
    for raw in raw_paths:
        if not raw:
            continue
        path = raw.replace("\\", "/")
        if (
            path.startswith("/")
            or path.startswith("~/")
            or ".." in path.split("/")
            or any(char.isspace() for char in path)
        ):
            errors.append(f"{task_id}: invalid scope path '{raw}'")
            continue
        normalized = path.rstrip("/")
        if (
            not normalized
            or normalized == ".tracks/projects"
            or normalized.startswith(".tracks/projects/")
        ):
            errors.append(f"{task_id}: forbidden scope path '{raw}'")
            continue
        if normalized not in paths:
            paths.append(normalized)
    return paths, errors


def _requirement_ref(ac_id: str) -> str:
    match = re.match(r"AC-((?:N?FR)\d{4})-\d{2}$", ac_id)
    if not match:
        return ac_id
    value = match.group(1)
    return f"{value[:-4]}-{value[-4:]}"


def _closure_matches(lines: list[str], requirement: str) -> list[str]:
    matches: list[str] = []
    compact = requirement.replace("-", "")
    for index, line in enumerate(lines):
        if requirement not in line and compact not in line:
            continue
        block = [line]
        for following in lines[index + 1 :]:
            if following.startswith("- **") or following.startswith("## "):
                break
            if following.strip():
                block.append(following)
        matches.append(" ".join(block))
    return matches


def validate_ac_coverage(
    tasks: list[TaskNode],
    required_acs: list[str],
    if_registry: set[str],
) -> tuple[bool, list[str]]:
    """FR-0030/FR-0180 required AC coverage closure + IF- validity check.

    Each required AC must be covered by at least one task's ac_refs + if_ids.
    Returns (all_covered, gap_errors). Gap ->
    (False, ['AC-FR0010-01 not covered by any task']).
    IF- ids declared by tasks (if_ids) must exist in if_registry (validity check,
    AC-FR0180-04 check 4). Invalid IF- -> (False, ['T-001 declares IF-IMPL-999 not in registry']).
    """
    errors: list[str] = []
    covered: set[str] = set()
    for t in tasks:
        covered.update(t.ac_refs)
    for ac in required_acs:
        if ac not in covered:
            errors.append(f"{ac} not covered by any task")
    for t in tasks:
        for if_id in t.if_ids:
            if if_id not in if_registry:
                errors.append(f"{t.task_id} declares {if_id} not in registry")
    return (len(errors) == 0, errors)


def validate_issue_numbers(
    tasks: list[TaskNode],
) -> tuple[bool, list[str]]:
    """FR-0180/FR-0220 issue number validity check (AC-FR0180-04 check 5).

    Each task's issue_number must be a positive integer (>=1).
    Returns (all_valid, errors). Invalid ->
    (False, ['T-001 issue_number=0 is not a positive integer']).
    """
    errors: list[str] = []
    for t in tasks:
        if not isinstance(t.issue_number, int) or t.issue_number < 1:
            errors.append(f"{t.task_id} issue_number={t.issue_number} is not a positive integer")
    return (len(errors) == 0, errors)
