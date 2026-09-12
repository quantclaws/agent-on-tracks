"""Task-graph parsing: TaskNode model + tasks.json reader (FR-0030/FR-0180).

Extracted from ``taskgraph.py`` for module-size compliance (C0302). Pure
functions; :mod:`tracks.executor.taskgraph` re-exports every name.
"""

from __future__ import annotations

import json
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
    # B94 deferred anchors: dynamic cross-boundary anchors that run but do
    # not gate the owner task. Collected in deferred_refs; the integration
    # task's hard gate merges all deferred.
    deferred_refs: tuple[str, ...] = ()
    integration: bool = False
    # #129 debt: explicit undelivered-scope ledger, pure annotation.
    # Never participates in identity/equivalence; carried to assignment.
    debt: tuple[dict, ...] = ()


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

_E2E_PREFIX = "tests/e2e/"


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

def _tid_of(raw: dict, index: int) -> str:
    return str(raw.get("task_id", f"task[{index}]"))

def _missing_required_field(raw: dict, tid: str) -> str | None:
    for field in _REQUIRED_FIELDS:
        if field not in raw:
            return f"tasks.json: {tid}: missing required field '{field}'"
    return None

def _validate_deferred_refs(
    raw: dict, tid: str, acceptance_refs
) -> tuple[list[str] | None, str | None]:
    deferred_raw = raw.get("deferred_refs", [])
    if not isinstance(deferred_raw, list):
        return None, f"tasks.json: {tid}: deferred_refs must be a list"
    for item in deferred_raw:
        if not isinstance(item, str):
            return None, f"tasks.json: {tid}: deferred_refs must be a list of strings"
        if not (item.startswith(_INTEGRATION_PREFIX) or item.startswith(_E2E_PREFIX)):
            return None, (
                f"tasks.json: {tid}: deferred_refs entry must live under "
                f"tests/integration/ or tests/e2e/: {item}"
            )
    if any(item in acceptance_refs for item in deferred_raw):
        return None, f"tasks.json: {tid}: deferred_refs must not intersect acceptance_refs"
    return deferred_raw, None

def _validate_integration_flag(raw: dict, tid: str) -> tuple[bool | None, str | None]:
    val = raw.get("integration", False)
    if not isinstance(val, bool):
        return None, f"tasks.json: {tid}: integration must be a boolean"
    return bool(val), None

_DEBT_KINDS = frozenset({"undelivered_scope"})


def _validate_debt_shape(raw: dict, tid: str) -> tuple[list[dict] | None, str | None]:
    val = raw.get("debt", [])
    if not isinstance(val, list):
        return None, f"tasks.json: {tid}: debt must be a list"
    for item in val:
        if not isinstance(item, dict):
            return None, f"tasks.json: {tid}: debt entry must be an object"
        kind = item.get("kind")
        if kind not in _DEBT_KINDS:
            return None, f"tasks.json: {tid}: debt kind must be one of {sorted(_DEBT_KINDS)}"
        deliverable = item.get("deliverable")
        if not isinstance(deliverable, str) or not deliverable.strip():
            return None, f"tasks.json: {tid}: debt deliverable must be a non-empty string"
        carrier = item.get("carrier_task")
        if not isinstance(carrier, str) or not carrier.strip():
            return None, f"tasks.json: {tid}: debt carrier_task must be a non-empty string"
        if "evidence" in item and not isinstance(item.get("evidence"), str):
            return None, f"tasks.json: {tid}: debt evidence must be a string"
    return val, None

def validate_debt_references(tasks: list[TaskNode]) -> list[str]:
    ids = {t.task_id for t in tasks}
    errors: list[str] = []
    for task in tasks:
        for item in getattr(task, "debt", ()) or ():
            carrier = item.get("carrier_task") if isinstance(item, dict) else None
            if carrier not in ids:
                errors.append(
                    f"{task.task_id} declares unknown debt carrier_task '{carrier}'"
                )
    return errors

def _parse_task(raw: object, index: int, schema: int = 1) -> tuple[TaskNode | None, str | None]:
    if not isinstance(raw, dict):
        return (None, f"tasks.json: task[{index}] must be an object")
    tid = _tid_of(raw, index)
    if not isinstance(tid, str) or not tid:
        return (None, f"tasks.json: task[{index}] task_id must be a non-empty string")
    miss = _missing_required_field(raw, tid)
    if miss:
        return (None, miss)
    err = _validate_scalar_fields(raw, tid)
    if err:
        return (None, err)
    lists = _parse_task_lists(raw, tid, schema)
    if isinstance(lists, str):
        return (None, lists)
    ac_refs, fr_refs, if_ids, test_refs, depends, unit_refs, acceptance_refs = lists
    deferred_raw, err = _validate_deferred_refs(raw, tid, acceptance_refs)
    if err:
        return (None, err)
    integration, err = _validate_integration_flag(raw, tid)
    if err:
        return (None, err)
    debt_raw, err = _validate_debt_shape(raw, tid)
    if err:
        return (None, err)
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
            deferred_refs=tuple(deferred_raw or []),
            integration=bool(integration),
            debt=tuple(dict(item) for item in (debt_raw or [])),
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
