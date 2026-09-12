"""Task-graph human projection: tasks.md render + guard (OOB b89 OB-4).

Extracted from ``taskgraph.py`` for module-size compliance (C0302). Pure
functions; :mod:`tracks.executor.taskgraph` re-exports every name.
"""

from __future__ import annotations


def render_tasks_md(tasks) -> str:  # noqa: CCR001
    """OOB b89 OB-4: single rendering implementation.

    Byte-identical to legacy ``m_impl_runtime._tasks_md``.

    Each ``tasks`` element is a :class:`TaskNode`; the output is the
    deterministic human-readable projection consumed by ``.tracks/projects/<v>/tasks.md``.
    The runtime and the guard both call this function, so there is a single
    source of truth for the projection.
    """
    lines = ["# Task Graph", ""]
    for task in tasks:
        lines.extend(
            [
                f"## {task.task_id}",
                f"- Issue: #{task.issue_number}",
                f"- Description: {task.description}",
                f"- AC refs: {', '.join(task.ac_refs)}",
                f"- FR refs: {', '.join(task.fr_refs)}",
                f"- IF ids: {', '.join(task.if_ids)}",
                f"- Unit refs: {', '.join(task.unit_refs) if task.unit_refs else '-'}",
                f"- Acceptance refs: {', '.join(task.acceptance_refs)}",
                f"- Scope: {task.scope_boundary}",
                f"- Depends on: {', '.join(task.depends_on) if task.depends_on else '-'}",
                f"- Batch: {task.batch}",
                f"- Parallel: {task.parallel}",
                "",
            ]
        )
    return "\n".join(lines)

def classify_tasks_md_guard(  # noqa: CCR001
    pre_contains_md: bool,
    rendered: str | None,
    post_md: str | None,
) -> str:
    """OOB b89 OB-4 guard decision (pure, three-branch).

    * ``rendered is None`` → ``skipped`` (tasks.json unparsable; taskgraph
      channel reports).
    * ``rendered == post_md`` → ``ok`` (no mismatch, no action).
    * ``rendered != post_md`` and ``pre_contains_md`` → ``violation``
      (this turn's diff contains tasks.md → fail-closed).
    * ``rendered != post_md`` and not ``pre_contains_md`` → ``system_repaired``
      (inherited dirty → self-heal, no violation).

    The caller is responsible for computing ``pre_contains_md`` as
    ``tasks.md ∈ diff(pre_snapshot, post_snapshot)`` (incremental attribution).
    """
    if rendered is None:
        return "skipped"
    if rendered == post_md:
        return "ok"
    if pre_contains_md:
        return "violation"
    return "system_repaired"
