"""B94 deferred anchor gate (thin runtime helpers, CCR001-clean).

Pure helpers for the deferred-ref classification, legal-red set,
integration scope/hard-gate helpers and verdict bucketing. All
integration/TaskNode accesses go through duck-typing so unit tests
can pass dicts.
"""

from __future__ import annotations

_DEFERRED_PREFIXES = ("tests/integration/", "tests/e2e/")


def _norm_ref(ref: str) -> str:
    return (ref or "").strip()


def is_deferred_ref(ref: str) -> bool:
    return _norm_ref(ref).startswith(_DEFERRED_PREFIXES)


def node_matches_ref(node: str, ref: str) -> bool:
    node_s = _norm_ref(node)
    ref_s = _norm_ref(ref)
    if not node_s or not ref_s:
        return False
    if node_s == ref_s:
        return True
    if "::" not in ref_s:
        return node_s.startswith(ref_s + "::")
    return False


def node_in_refset(node: str, refset) -> bool:
    if not refset:
        return False
    return any(node_matches_ref(node, str(ref)) for ref in refset)


def _node_of(item) -> str:
    if isinstance(item, dict):
        return str(item.get("node") or "")
    return str(item)


def _wrap_item(item, node: str):
    if isinstance(item, dict):
        return item
    return {"node": node}


def _task_list(task, name: str):
    val = getattr(task, name, None)
    if val is None and isinstance(task, dict):
        val = task.get(name) or ()
    return val or ()


def _add_stripped(target: set[str], refs) -> None:
    for ref in refs or []:
        if isinstance(ref, str) and ref.strip():
            target.add(ref.strip())


def partition_failures(
    failed_nodes: list[dict], deferred_refs
) -> tuple[list[dict], list[dict]]:
    """Split failed nodes into (hard, deferred) by deferred membership."""
    deferred_set = {_norm_ref(r) for r in (deferred_refs or []) if isinstance(r, str)}
    hard: list[dict] = []
    deferred: list[dict] = []
    for item in failed_nodes or []:
        node = _node_of(item)
        is_def = node_in_refset(node, deferred_set)
        bucket = deferred if is_def else hard
        bucket.append(_wrap_item(item, node))
    return hard, deferred


def selection_union(task) -> list[str]:
    """Task test selection set = acceptance_refs ∪ deferred_refs."""
    union: set[str] = set()
    _add_stripped(union, _task_list(task, "acceptance_refs"))
    _add_stripped(union, _task_list(task, "deferred_refs"))
    return sorted(union)


def is_integration(task) -> bool:
    val = getattr(task, "integration", None)
    if val is None and isinstance(task, dict):
        val = task.get("integration")
    return bool(val)


def _scope_of(task) -> set[str]:
    # Reuse canonical taskgraph parser to avoid R0801 duplication
    from tracks.executor.taskgraph import _parse_scope_paths as _tg_parse

    val = getattr(task, "scope_boundary", None)
    if val is None and isinstance(task, dict):
        val = task.get("scope_boundary") or ""
    # _tg_parse returns frozenset; convert to set for mutation parity
    return set(_tg_parse(str(val or "")))


def _unit_files_of(task) -> set[str]:
    out: set[str] = set()
    for ref in _task_list(task, "unit_refs"):
        if isinstance(ref, str) and ref.startswith("tests/unit/"):
            out.add(ref.split("::", 1)[0].strip())
    return out


def integration_allowed_paths(all_tasks) -> list[str]:
    """Union of all task scope paths (integration scope exemption)."""
    acc: set[str] = set()
    for t in all_tasks or []:
        acc.update(_scope_of(t))
        acc.update(_unit_files_of(t))
    return sorted(acc)


def _acceptance_of(task) -> list[str]:
    return list(_task_list(task, "acceptance_refs"))


def _deferred_of(task) -> list[str]:
    return list(_task_list(task, "deferred_refs"))


def integration_hard_refs(task, all_tasks) -> list[str]:
    """Hard gate for integration = own acceptance ∪ all deferred."""
    union: set[str] = set()
    _add_stripped(union, _acceptance_of(task))
    for t in all_tasks or []:
        _add_stripped(union, _deferred_of(t))
    return sorted(union)


def _all_deferred_set(tasks) -> set[str]:
    out: set[str] = set()
    for t in tasks or []:
        _add_stripped(out, _deferred_of(t))
    return out


def _unfinished_acceptance_set(tasks, completed: set[str]) -> set[str]:
    out: set[str] = set()
    for t in tasks or []:
        tid = getattr(t, "task_id", None)
        if tid is None and isinstance(t, dict):
            tid = t.get("task_id")
        if tid in completed:
            continue
        _add_stripped(out, _acceptance_of(t))
    return out


def legal_red_refs(tasks, completed_ids: set[str] | None) -> set[str]:
    """Legal red set = all deferred ∪ acceptance of unfinished tasks."""
    completed = set(completed_ids or [])
    legal = _all_deferred_set(tasks or [])
    legal.update(_unfinished_acceptance_set(tasks or [], completed))
    return legal


def unexpected_reds(failed_nodes: list[str], legal_refs: set[str]) -> list[str]:
    """Return failed nodes not covered by legal red set (file-aware)."""
    legal = {_norm_ref(r) for r in (legal_refs or []) if isinstance(r, str)}
    bad: list[str] = []
    for node in failed_nodes or []:
        n = _norm_ref(str(node))
        if not n:
            continue
        if node_in_refset(n, legal):
            continue
        bad.append(n)
    return sorted(set(bad))


def _is_deferred_only_flag(payload: dict) -> bool:
    return bool(payload.get("deferred_only"))


def _has_empty_hard(payload: dict) -> bool:
    hard = payload.get("hard_failures")
    return isinstance(hard, list) and not hard


def should_count_breaker_task_failure(payload: dict) -> bool:
    """B94: task_failures counts only non-deferred failures."""
    if not isinstance(payload, dict):
        return True
    if _is_deferred_only_flag(payload):
        return False
    return not (
        payload.get("deferred_failures") is not None
        and not payload.get("hard_failures")
        and _has_empty_hard(payload)
    )


def plan_declared_union(tasks) -> set[str]:
    """Coverage union: acceptance ∪ deferred (plan gate)."""
    out: set[str] = set()
    for t in tasks or []:
        _add_stripped(out, _acceptance_of(t))
        _add_stripped(out, _deferred_of(t))
    return out
