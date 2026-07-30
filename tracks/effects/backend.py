"""Agent backend seam (ARCH-003 §4 / IF-003 §3·§4).

The Executor dispatches agent work through an ``AgentBackend``. v0.2 adds a real
``OpencodeBackend`` alongside the deterministic ``FakeBackend``. The backend is an
EFFECTS-boundary concern: kernel ``decide()``/``project()`` never see it, and the
backend/simulate choice is read only at the cli/executor boundary (IF-001 §5).
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentBackend(Protocol):
    """One agent invocation.

    Returns an Outcome dict: ``status`` ("done"/"blocked"/"failed"),
    ``artifact_ref``, ``self_report`` (Runtime 不信任), optional ``verdict``
    (review roles), and — for OpencodeBackend — ``diff_ref`` (权威产物：目标文件
    受控 diff), ``audit_evidence`` (越权路径级证据), ``failure_class``
    (IF-003 §1a/§4)。FakeBackend 仅有前三项 + verdict。
    """

    def act(self, role: str, substate: str, doc: str | None,
            doc_path: Path | None) -> dict:
        """Execute the agent's work for one dispatch; return the Outcome dict."""
        ...
