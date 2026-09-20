"""Task-graph validators: DAG, scope, structure, coverage (FR-0030/FR-0180).

Extracted from ``taskgraph.py`` for module-size compliance (C0302). Pure
functions; :mod:`tracks.executor.taskgraph` re-exports every name.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracks.executor.taskgraph_parse import (
    _INTEGRATION_PREFIX,
    _SCHEMA_V2,
    TaskNode,
)


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

def _is_integration_task(task) -> bool:
    return bool(getattr(task, "integration", False))

def _scope_single_errors(task: TaskNode) -> list[str]:
    if _is_integration_task(task):
        return []
    errs: list[str] = []
    paths, path_errors = _scope_paths(task.scope_boundary, task.task_id)
    if path_errors:
        errs.extend(path_errors)
    if not paths:
        errs.append(f"{task.task_id}: scope_boundary must not be empty")
    return errs

def _scope_overlap_errors(tasks: list[TaskNode]) -> list[str]:
    errs: list[str] = []
    for i, t1 in enumerate(tasks):
        if _is_integration_task(t1):
            continue
        s1 = _parse_scope_paths(t1.scope_boundary)
        for t2 in tasks[i + 1 :]:
            if _is_integration_task(t2):
                continue
            overlap = s1 & _parse_scope_paths(t2.scope_boundary)
            for path in sorted(overlap):
                errs.append(f"scope overlap: {t1.task_id} and {t2.task_id} both target {path}")
    return errs

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
        errors.extend(_scope_single_errors(task))
    errors.extend(_scope_overlap_errors(tasks))
    return (len(errors) == 0, errors)

def _parse_scope_paths(scope_boundary: str) -> frozenset[str]:
    paths, _ = _scope_paths(scope_boundary)
    return frozenset(paths)

def validate_task_structure(tasks: list[TaskNode]) -> list[str]:
    """Validate task fields that are structural but not graph relations."""
    if not tasks:
        return ["tasks.json: task graph must contain at least one task"]
    return [error for task in tasks for error in _task_structure_errors(task)]

def _common_structure_errors(task: TaskNode) -> list[str]:
    errs: list[str] = []
    if not task.description.strip():
        errs.append(f"{task.task_id}: description must not be empty")
    if not task.batch.strip():
        errs.append(f"{task.task_id}: batch must not be empty")
    if task.budget < 1:
        errs.append(f"{task.task_id}: budget must be a positive integer")
    return errs

def _integration_structure_errors(task: TaskNode) -> list[str]:
    if task.schema == _SCHEMA_V2:
        return []
    if not task.test_refs and not getattr(task, "deferred_refs", ()):
        return [f"{task.task_id}: test_refs must not be empty"]
    return []

def _standard_acfr_errors(task: TaskNode) -> list[str]:
    errs: list[str] = []
    if not task.ac_refs:
        errs.append(f"{task.task_id}: ac_refs must not be empty")
    if not task.fr_refs:
        errs.append(f"{task.task_id}: fr_refs must not be empty")
    if not task.if_ids:
        errs.append(f"{task.task_id}: if_ids must not be empty")
    return errs

def _standard_ref_errors(task: TaskNode) -> list[str]:
    if task.schema == _SCHEMA_V2:
        if not task.acceptance_refs and not getattr(task, "deferred_refs", ()):
            return [f"{task.task_id}: acceptance_refs must not be empty"]
        return []
    if not task.test_refs:
        return [f"{task.task_id}: test_refs must not be empty"]
    return []

def _task_structure_errors(task: TaskNode) -> list[str]:
    errors = _common_structure_errors(task)
    if _is_integration_task(task):
        errors.extend(_integration_structure_errors(task))
        return errors
    errors.extend(_standard_acfr_errors(task))
    errors.extend(_standard_ref_errors(task))
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
        for ref in (*task.acceptance_refs, *getattr(task, "deferred_refs", ())):
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

def validate_declared_anchor_rows(
    tasks: list[TaskNode],
    plan_text: str,
) -> tuple[bool, list[str]]:
    """#171 plan-coverage machine gate, reverse direction: every declared
    acceptance/deferred anchor must name a §8 row target (dirty-anchor
    rejection).

    The forward closure (``validate_acceptance_coverage``) proves every §8
    integration row is discharged; this proves the converse — a declared
    anchor with no §8 row anchors nothing in the frozen plan. Live
    motivation (run 01M2QTJB PRISM-PLAN-01): reverse dirt surfaced only at
    review time through the whole Prism→Archer→re-review loop; both
    directions are now set arithmetic at commit time (cheap verification
    before expensive review). Same schema-2 bound as the forward check.
    Returns (all_rows_named, dirty_errors). Dirty ->
    (False, ['T-002 declares anchor tests/integration/x.py::ghost with no
    §8 row (dirty anchor)']).
    """
    if not tasks or tasks[0].schema != _SCHEMA_V2:
        return (True, [])
    row_files, row_nodes = _plan_row_target_index(plan_text)
    errors: list[str] = []
    for task in tasks:
        for raw_ref in (*task.acceptance_refs, *getattr(task, "deferred_refs", ())):
            error = _dirty_anchor_error(task.task_id, raw_ref, row_files, row_nodes)
            if error is not None:
                errors.append(error)
    return (not errors, errors)


def _plan_row_target_index(plan_text: str) -> tuple[set[str], set[str]]:
    """§8 target index: (row file paths, row node ids) over rows of ANY
    layer — e2e/unit declarations are not acceptance obligations but are
    legitimate anchor targets, so their rows count for the reverse check."""
    from tracks.executor.test_tasks import _coverage_rows_with_test

    row_files: set[str] = set()
    row_nodes: set[str] = set()
    for _ac_id, _layer_cell, test_cell, _if_cell in _coverage_rows_with_test(plan_text):
        for path, node in plan_row_targets(str(test_cell)):
            row_files.add(path)
            if node is not None:
                row_nodes.add(node)
    return row_files, row_nodes


def _dirty_anchor_error(
    task_id: str,
    raw_ref: object,
    row_files: set[str],
    row_nodes: set[str],
) -> str | None:
    """One declared anchor's dirty-anchor error, or None when legal.

    Normalization shared with ``plan_row_targets`` (bare file names land
    under tests/integration/). Legality: a NODE-level declaration is legal
    iff that exact node is a §8 target; a FILE-level declaration is legal
    iff the file is a §8 target (it covers every node the rows name in
    that file — the same expansion the forward check applies).
    """
    ref = str(raw_ref).strip()
    if not ref:
        return None
    file_part, sep, node_part = ref.partition("::")
    if not file_part:
        return None
    path = _normalized_test_path(file_part)
    if sep and node_part:
        legal = f"{path}::{node_part}" in row_nodes
    else:
        legal = path in row_files
    if legal:
        return None
    return f"{task_id} declares anchor {ref} with no §8 row (dirty anchor)"


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

    B52 (#68): an item that already carries an explicit tests/ root keeps
    its layer (``tests/e2e/...`` stays e2e) -- only bare file names
    normalize to tests/integration/. E2e targets are terminal-coverage
    anchors (ISLAND_GATE_2/FULL), never per-task acceptance obligations.
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
        path = _normalized_test_path(file_part)
        pair = (path, f"{path}::{node_part}") if sep and node_part else (path, None)
        if pair not in targets:
            targets.append(pair)
    return targets


def _normalized_test_path(file_part: str) -> str:
    """Layer normalization shared by the §8 item parser and the dirty-anchor
    checker (Prism #171 R1 DRY): an explicit tests/ root keeps its layer
    (tests/e2e/... stays e2e); a bare file name lands under
    tests/integration/."""
    if file_part.startswith("tests/"):
        return file_part
    return f"tests/integration/{Path(file_part).name}"

def _row_undeclared_targets(
    ac_id: str,
    test_cell: str,
    declared: set[str],
    declared_paths: set[str],
) -> list[str]:
    """§8 row targets (per ``+``-separated item) missing from the declared set.

    A NODE target is covered by an exact node declaration OR by a FILE-level
    declaration of its file (a whole-file declaration covers every node in
    it -- the same expansion the runtime gate applies).

    B52 (#68): e2e-layer targets (``tests/e2e/...``) are terminal-coverage
    anchors (ISLAND_GATE_2/FULL) -- they are NOT per-task acceptance
    obligations and are skipped here."""
    errors: list[str] = []
    for path, node in plan_row_targets(test_cell):
        if not path.startswith(_INTEGRATION_PREFIX):
            continue
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
