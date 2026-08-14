"""Agent backend seam (ARCH-003 §4 / IF-003 §3·§4).

The Executor dispatches agent work through an ``AgentBackend``. v0.2 adds a real
``OpencodeBackend`` alongside the deterministic ``FakeBackend``. The backend is an
EFFECTS-boundary concern: kernel ``decide()``/``project()`` never see it, and the
backend/simulate choice is read only at the cli/executor boundary (IF-001 §5).

``valid_test_tasks`` (D-28) is the single production-neutral Shield test-task
contract validator: the executor checks ``assignment.test_tasks`` with it before
dispatching Shield, and FakeBackend rejects with the same grammar — one source,
no divergent copies.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable

# Shield test-task contract (D-28 / M-DESIGN→M-TEST, items 3-4): the layers a
# Shield task may carry and the AC/IF id grammar the parser produces.
SHIELD_LAYERS = ("integration", "e2e")
TASK_AC_ID = re.compile(r"^AC-(?:N?FR)\d{4}-\d{2}$")
TASK_IF_ID = re.compile(r"^IF-[A-Z]+-\d{3}$")


def valid_test_tasks(tasks: object) -> bool:
    """Structured Shield WRITE contract: a non-empty list of ``{ac_id, layers,
    if_ids}`` dicts; every ac_id unique and grammar-shaped, every layer in
    {integration, e2e} and non-empty, every if_ids list non-empty and
    grammar-shaped. Anything else (missing/empty/malformed) is invalid."""
    if not isinstance(tasks, list) or not tasks:
        return False
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            return False
        ac_id = task.get("ac_id")
        layers = task.get("layers")
        if_ids = task.get("if_ids")
        if not isinstance(ac_id, str) or not TASK_AC_ID.fullmatch(ac_id) or ac_id in seen:
            return False
        if (
            not isinstance(layers, list)
            or not layers
            or any(layer not in SHIELD_LAYERS for layer in layers)
            or len(set(layers)) != len(layers)
        ):
            return False
        if (
            not isinstance(if_ids, list)
            or not if_ids
            or any(not isinstance(i, str) or not TASK_IF_ID.fullmatch(i) for i in if_ids)
            or len(set(if_ids)) != len(if_ids)
        ):
            return False
        seen.add(ac_id)
    return True


@runtime_checkable
class AgentBackend(Protocol):
    """One agent invocation.

    Returns an Outcome dict: ``status`` ("done"/"blocked"/"failed"),
    ``artifact_ref``, ``self_report`` (Runtime 不信任), optional ``verdict``
    (review roles), and — for OpencodeBackend — ``diff_ref`` (权威产物：目标文件
    受控 diff), ``audit_evidence`` (越权路径级证据), ``failure_class``
    (IF-003 §1a/§4)。FakeBackend 仅有前三项 + verdict。
    """

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
    ) -> dict:
        """Execute the agent's work for one dispatch; return the Outcome dict."""
        ...
