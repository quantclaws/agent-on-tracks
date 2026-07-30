"""Agent backends — effects boundary (ARCH-003 §4).

``select_backend`` reads ``TRAC_AGENT_BACKEND`` ONLY here, at the cli/executor
boundary (IF-001 §5 / IF-003 §9); kernel ``decide()``/``project()`` never see the
backend choice or any test/simulate mode.

- ``TRAC_AGENT_BACKEND=fake|opencode``. Phase 1 ships FakeBackend only and
  defaults to ``fake`` (keeps the v0.1 suite green); Phase 2 adds OpencodeBackend
  and flips the default to ``opencode`` (conftest forces fake for E2E, per spec).
- ``TRAC_FAKE_SIMULATE`` is honored inside FakeBackend (deterministic suite).
"""
from __future__ import annotations

import os
from pathlib import Path

from tracks.effects.backend import AgentBackend
from tracks.effects.fake import FakeBackend

__all__ = ["AgentBackend", "FakeBackend", "select_backend"]


def select_backend(repo: Path, version: str) -> AgentBackend:
    """Pick the agent backend at the boundary. Default ``fake`` in Phase 1."""
    kind = os.environ.get("TRAC_AGENT_BACKEND", "fake").strip().lower()
    if kind == "opencode":
        raise NotImplementedError(
            "TRAC_AGENT_BACKEND=opencode arrives in v0.2 item 1 phase 2 "
            "(OpencodeBackend); use fake until then.")
    if kind != "fake":
        raise ValueError(f"unknown TRAC_AGENT_BACKEND: {kind!r} (want fake|opencode)")
    return FakeBackend(repo, version)
