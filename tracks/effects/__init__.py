"""Agent backends — effects boundary (ARCH-003 §4).

``select_backend`` reads ``TRAC_AGENT_BACKEND`` ONLY here, at the cli/executor
boundary (IF-001 §5 / IF-003 §9); kernel ``decide()``/``project()`` never see the
backend choice or any test/simulate mode.

- ``TRAC_AGENT_BACKEND=fake|opencode`` (default ``opencode``, SPEC FR-020).
- ``TRAC_FAKE_SIMULATE`` (non-empty) forces the fake backend even when
  ``TRAC_AGENT_BACKEND=opencode`` (deterministic suite / behavior injection).
- ``TRAC_AGENT_MODEL`` (opencode path only) overrides the opencode default
  model; empty/unset lets opencode resolve its own configured model (spec §3.1).
- conftest forces ``TRAC_AGENT_BACKEND=fake`` for the deterministic E2E channel;
  the live opencode channel opts in explicitly (SPEC test-plan §6).
"""
from __future__ import annotations

import os
from pathlib import Path

from tracks.effects.backend import AgentBackend
from tracks.effects.fake import FakeBackend

__all__ = ["AgentBackend", "FakeBackend", "select_backend"]


def select_backend(repo: Path, version: str) -> AgentBackend:
    """Pick the agent backend at the boundary (default opencode)."""
    if os.environ.get("TRAC_FAKE_SIMULATE"):
        return FakeBackend(repo, version)  # simulate forces fake (FR-020)
    kind = os.environ.get("TRAC_AGENT_BACKEND", "opencode").strip().lower()
    if kind == "fake":
        return FakeBackend(repo, version)
    if kind == "opencode":
        from tracks.effects.opencode import OpencodeBackend
        timeout = int(os.environ.get("TRAC_AGENT_TIMEOUT", "600"))
        model = os.environ.get("TRAC_AGENT_MODEL", "").strip() or None
        return OpencodeBackend(repo, version, timeout=timeout, model=model)
    raise ValueError(f"unknown TRAC_AGENT_BACKEND: {kind!r} (want fake|opencode)")
