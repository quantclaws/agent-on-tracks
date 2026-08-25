"""Task graph parsing and validation (FR-0030/FR-0180, IF-IMPL-003).

Pure functions: parse tasks.json (task graph machine truth source), validate DAG
acyclicity (Kahn's algorithm), scope boundary non-overlap, required AC coverage
closure, IF- id validity, and issue number validity.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
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
    # B50 (#65) schema v2: explicit layer split. ``unit_refs`` are Devon's
    # RED obligations (tests/unit/ nodes, R-artifact gated); ``acceptance_refs``
    # are the task's acceptance anchors (tests/integration/ nodes, Shield
    # owned, red against stubs). ``test_refs`` stays the combined list for
    # legacy consumers; schema-2 graphs derive it from the split fields.
    # Legacy graphs (schema 1) map test_refs -> acceptance_refs (verified
    # convention: v0.5/v0.6/v0.7 taskgraphs declared integration anchors).
    unit_refs: tuple[str, ...] = ()
    acceptance_refs: tuple[str, ...] = ()
    schema: int = 1


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
    "scope_boundary",
    "depends_on",
    "batch",
    "parallel",
)

_SCHEMA_V2 = 2
_UNIT_PREFIX = "tests/unit/"
_INTEGRATION_PREFIX = "tests/integration/"


def parse_tasks_json(
    tasks_json_text: str,
) -> tuple[list[TaskNode], str | None]:
    """FR-0030/FR-0180 parse tasks.json (task graph machine truth source).

    Returns (task_nodes, error). Tolerant parsing (skip unknown keys gracefully,
    fail on missing required fields). tasks.json schema: array of task objects
    with fields matching TaskNode + Dependency Graph + Runtime Review Result
    checklist (AC-FR0180-02). tasks.md is a human-readable projection generated
    deterministically by Runtime from tasks.json (not parsed here).

    B50 (#65) schema v2 (root marker ``"schema": 2``): the monolithic
    ``test_refs`` is retired -- each task MUST declare the explicit layer
    split ``unit_refs`` (tests/unit/ RED obligations, may be empty) and
    ``acceptance_refs`` (tests/integration/ acceptance anchors, non-empty);
    a legacy ``test_refs`` key on a schema-2 task is rejected. Legacy graphs
    (no marker) keep the old required ``test_refs`` and are mapped
    (test_refs -> acceptance_refs, unit_refs -> ()) so replay stays valid.
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
    schema = data.get("schema", 1)
    # PRISM-B49B50-R1-02: tighten the root marker to a true int -- bools
    # (True -> 1) and floats (2.0 -> 2) must fail closed, not silently pass.
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema not in (1, _SCHEMA_V2)
    ):
        return ([], f"tasks.json: unsupported schema version {schema!r}")
    tasks: list[TaskNode] = []
    for index, raw in enumerate(raw_tasks):
        node, err = _parse_task(raw, index, schema)
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


def _parse_task(raw: object, index: int, schema: int = 1) -> tuple[TaskNode | None, str | None]:
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
    lists = _parse_task_lists(raw, tid, schema)
    if isinstance(lists, str):
        return (None, lists)
    ac_refs, fr_refs, if_ids, test_refs, depends, unit_refs, acceptance_refs = lists
    return (
        TaskNode(
            task_id=tid,
            issue_number=raw["issue_number"],
            description=raw["description"],
            ac_refs=tuple(raw["ac_refs"]),
            fr_refs=tuple(raw["fr_refs"]),
            if_ids=tuple(raw["if_ids"]),
            test_refs=tuple(test_refs),
            scope_boundary=raw["scope_boundary"],
            depends_on=tuple(raw["depends_on"]),
            batch=raw["batch"],
            parallel=raw["parallel"],
            budget=raw.get("budget", 2),
            unit_refs=tuple(unit_refs),
            acceptance_refs=tuple(acceptance_refs),
            schema=schema,
        ),
        None,
    )


def _parse_task_lists(
    raw: dict, tid: str, schema: int
) -> tuple[list[str], ...] | str:
    """Parse the list fields plus the era-specific test-ref split.

    Returns (ac_refs, fr_refs, if_ids, test_refs, depends_on, unit_refs,
    acceptance_refs) or an error string."""
    if schema == _SCHEMA_V2:
        split, err = _parse_split_fields(raw, tid)
        if err is not None:
            return err
        unit_refs, acceptance_refs = split
        test_refs = [*unit_refs, *acceptance_refs]
        lists = _parse_list_fields(raw, tid, include_test_refs=False)
        if isinstance(lists, str):
            return lists
        ac_refs, fr_refs, if_ids, depends = lists
    else:
        if "test_refs" not in raw:
            return f"tasks.json: {tid}: missing required field 'test_refs'"
        lists = _parse_list_fields(raw, tid)
        if isinstance(lists, str):
            return lists
        ac_refs, fr_refs, if_ids, test_refs, depends = lists
        # B50 legacy mapping: historical taskgraphs declared mixed refs in
        # test_refs; layer-route them by path prefix (the verified 196cbc9
        # convention) -- tests/unit/ -> RED obligation, else acceptance.
        unit_refs = [ref for ref in test_refs if ref.startswith(_UNIT_PREFIX)]
        acceptance_refs = [ref for ref in test_refs if not ref.startswith(_UNIT_PREFIX)]
    return (ac_refs, fr_refs, if_ids, test_refs, depends, unit_refs, acceptance_refs)


def _parse_split_fields(
    raw: dict, tid: str
) -> tuple[tuple[list[str], list[str]] | None, str | None]:
    """B50 schema v2: parse + layer-validate the explicit unit/acceptance split."""
    if "test_refs" in raw:
        return (
            None,
            f"tasks.json: {tid}: legacy 'test_refs' is not allowed in schema 2 "
            "-- declare 'unit_refs' and 'acceptance_refs'",
        )
    for field in ("unit_refs", "acceptance_refs"):
        if field not in raw:
            return (None, f"tasks.json: {tid}: missing required field '{field}'")
    unit_vals, err = _str_list(raw["unit_refs"], "unit_refs", tid)
    if err:
        return (None, err)
    acceptance_vals, err = _str_list(raw["acceptance_refs"], "acceptance_refs", tid)
    if err:
        return (None, err)
    for ref in unit_vals:
        if not ref.startswith(_UNIT_PREFIX):
            return (None, f"tasks.json: {tid}: unit_refs entry must live under tests/unit/: {ref}")
    for ref in acceptance_vals:
        if not ref.startswith(_INTEGRATION_PREFIX):
            return (
                None,
                f"tasks.json: {tid}: acceptance_refs entry must live under "
                f"tests/integration/: {ref}",
            )
    return (unit_vals, acceptance_vals), None


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
    # Budget is optional (user ruling 2026-08-15): Archer should not spend
    # effort estimating it - agent speed/rework characteristics diverge from
    # the human-paced training prior the estimate was designed for. The
    # runtime uses it only as a verdict.failed count ceiling (anti
    # spin-loop); a missing budget defaults to 2 in TaskNode construction.
    if "budget" in raw:
        budget = raw["budget"]
        if not isinstance(budget, int) or isinstance(budget, bool):
            return f"tasks.json: {tid}: budget must be an integer"
    return None


def _parse_list_fields(
    raw: dict, tid: str, include_test_refs: bool = True
) -> tuple[list[str], ...] | str:
    fields = ["ac_refs", "fr_refs", "if_ids"]
    if include_test_refs:
        fields.append("test_refs")
    fields.append("depends_on")
    result: list[list[str]] = []
    for field in fields:
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
    if task.schema == _SCHEMA_V2:
        # B50: acceptance anchors are the task's non-negotiable green debt;
        # unit_refs may legitimately be empty (Devon's universal RED
        # obligation already covers unit coverage via the R manifest).
        if not task.acceptance_refs:
            errors.append(f"{task.task_id}: acceptance_refs must not be empty")
    elif not task.test_refs:
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


def validate_acceptance_coverage(
    tasks: list[TaskNode],
    plan_text: str,
) -> tuple[bool, list[str]]:
    """B50 (#65): schema-2 planning-time acceptance coverage closure.

    The union of all tasks' declared acceptance_refs must cover every §8
    integration-layer row target (node or file). This replaces the retired
    runtime GREEN_GATE IF-index inference: a forgotten anchor now fails at
    taskgraph commit (PRISM_PLAN territory, zero Devon attempts burned)
    instead of surfacing mid-M-IMPL. Legacy (schema-1) graphs skip the
    check -- their binding was IF-index inferred by historical runtime.
    Returns (all_covered, gap_errors). Gap ->
    (False, ['§8 row AC-FR0257-03 target tests/integration/x.py::t not
    declared by any task acceptance_refs']).
    """
    if not tasks or tasks[0].schema != _SCHEMA_V2:
        return (True, [])
    from tracks.executor.test_tasks import _coverage_rows_with_test

    declared: set[str] = set()
    for task in tasks:
        for ref in task.acceptance_refs:
            declared.add(str(ref).strip())
    # PRISM-B49B50-R1-01: ONLY a pure FILE declaration (no ``::``) covers
    # every node in the file. A NODE declaration covers exactly itself --
    # declaring one node must not exempt its file-siblings.
    declared_paths = {ref for ref in declared if "::" not in ref}
    errors: list[str] = []
    for ac_id, layer_cell, test_cell, _if_cell in _coverage_rows_with_test(plan_text):
        if not _row_names_integration(layer_cell, test_cell):
            continue
        errors.extend(_row_undeclared_targets(ac_id, str(test_cell), declared, declared_paths))
    return (not errors, errors)


def _row_names_integration(layer_cell: str | None, test_cell: str | None) -> bool:
    """Whether a §8 row names the integration layer and carries tests."""
    if not layer_cell or not test_cell:
        return False
    layers = {
        part.strip().lower()
        for part in re.split(r"[,/+;\s]+", layer_cell)
        if part.strip()
    }
    return "integration" in layers


def plan_row_targets(test_cell: str) -> list[tuple[str, str | None]]:
    """(file_path, node|None) pairs a §8 test cell names, item by item.

    PRISM-B49B50-R1-01 (#65): the single §8 item parser shared by the
    commit-time coverage closure and the runtime gate cross-check -- both
    layers must anchor identical pairs. A NODE item (``file::test``) binds
    that node exactly; a FILE item binds the whole file; a bare file name
    normalizes under tests/integration/.
    """
    items = [
        item.strip().strip("`")
        for item in re.split(r"\+", str(test_cell))
        if item.strip()
    ]
    targets: list[tuple[str, str | None]] = []
    for item in items:
        file_part, sep, node_part = item.partition("::")
        if not file_part:
            continue
        path = (
            file_part
            if file_part.startswith("tests/integration/")
            else f"tests/integration/{Path(file_part).name}"
        )
        pair = (path, f"{path}::{node_part}") if sep and node_part else (path, None)
        if pair not in targets:
            targets.append(pair)
    return targets


def _row_undeclared_targets(
    ac_id: str,
    test_cell: str,
    declared: set[str],
    declared_paths: set[str],
) -> list[str]:
    """§8 row targets (per ``+``-separated item) missing from the declared set.

    A NODE target is covered by an exact node declaration OR by a FILE-level
    declaration of its file (a whole-file declaration covers every node in
    it -- the same expansion the runtime gate applies)."""
    errors: list[str] = []
    for path, node in plan_row_targets(test_cell):
        named = node if node is not None else path
        if named not in declared and path not in declared_paths:
            errors.append(
                f"§8 row {ac_id} target {named} is not declared by any "
                "task acceptance_refs"
            )
    return errors


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
