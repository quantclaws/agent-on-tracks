"""Agent backends — effects boundary (ARCH-003 §4).

``select_backend`` reads ``TRAC_AGENT_BACKEND`` ONLY here, at the cli/executor
boundary (IF-001 §5 / IF-003 §9); kernel ``decide()``/``project()`` never see the
backend choice or any test/simulate mode.

- ``TRAC_AGENT_BACKEND=fake|opencode`` (default ``opencode``, SPEC FR-020).
- ``TRAC_FAKE_SIMULATE`` (non-empty) forces the fake backend even when
  ``TRAC_AGENT_BACKEND=opencode`` (deterministic suite / behavior injection).
- ``TRAC_AGENT_MODEL`` (opencode path only) overrides the opencode default
  model; empty/unset lets opencode resolve its own configured model (spec §3.1).
  The Runtime Agent runs to completion with no production timeout; operator
  cancellation (Ctrl-C) is honored via process-group kill (ARCH §7).
- ``TRAC_DEBUG`` (non-empty) enables debug logging for opencode subprocesses:
  ``--print-logs --log-level DEBUG`` flags are added to the ``opencode run``
  command, and stderr (opencode logs) is tee'd to
  ``.tracks/runtime/log/<agent>-<timestamp>.log``. Default off (production).
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

        model = os.environ.get("TRAC_AGENT_MODEL", "").strip() or None
        debug = bool(os.environ.get("TRAC_DEBUG", "").strip())
        return OpencodeBackend(repo, version, model=model, debug=debug)
    raise ValueError(f"unknown TRAC_AGENT_BACKEND: {kind!r} (want fake|opencode)")
