"""Item 5: Executor invalid-task preflight must explicitly require
``role=shield`` and ``M-TEST/WRITE`` before rejecting an assignment for
invalid test_tasks.

A non-shield role dispatched at M-TEST/WRITE (e.g. a Prism or other agent
mistakenly routed there) must NOT be rejected by the _reject_invalid_test_tasks
gate — the test-task contract is Shield-specific, and a non-shield assignment
should reach the backend normally.

Also covers the contract argv resolver (live replay fix): when a worktree's
project.toml declares ``.venv/bin/python`` but has no ``.venv``, the resolver
substitutes the Runtime's own venv interpreter.
"""

import os
import sys

from tests.integration.result_checkpoint_support import _setup_m_test
from tests.m_test_support import make_m_test_dispatch_cmd


def test_non_shield_role_at_m_test_write_not_rejected(tmp_path):
    """A non-shield role at M-TEST/WRITE with invalid test_tasks must reach
    the backend (not be short-circuited by _reject_invalid_test_tasks)."""
    ex, store, run_id = _setup_m_test(tmp_path)

    backend_called = []

    class _SpyBackend:
        def act(self, role, substate, doc, doc_path, assignment=None):
            backend_called.append((role, substate))
            return {"status": "done", "artifact_ref": None, "self_report": "non-shield dispatched"}

    ex.backend = _SpyBackend()
    ex.issue(make_m_test_dispatch_cmd(role="prism"))

    assert backend_called, (
        "non-shield role at M-TEST/WRITE must reach the backend; "
        "_reject_invalid_test_tasks must only gate role=shield"
    )
    assert backend_called[0][0] == "prism"


# -- contract argv resolver (live replay fix) --------------------------------

def test_resolve_contract_argv0_venv_absent_uses_runtime_executable(tmp_path):
    """Live replay fix: a worktree's project.toml contract declares
    ``.venv/bin/python -m pytest ...`` but the worktree has no ``.venv``.
    The resolver must substitute the Runtime's own ``sys.executable`` — but
    ONLY when it is genuinely inside a venv (never a system Python), so the
    subprocess doesn't crash with ``FileNotFoundError``.

    Reproduces the live failure: ``FileNotFoundError: [Errno 2] No such file
    or directory: '.venv/bin/python'`` from ``_run_contract_sections``."""
    from tracks.executor.executor import _resolve_contract_argv0

    # The verification command runs under .venv/bin/python, so the Runtime
    # is inside a venv — the substitution path must be reachable.
    assert sys.prefix != sys.base_prefix, "test must run inside a venv"
    assert os.access(sys.executable, os.X_OK)

    # No .venv under tmp_path — simulates an external worktree without one.
    argv = [".venv/bin/python", "-m", "pytest", "--collect-only", "-q"]
    resolved = _resolve_contract_argv0(argv, tmp_path)
    assert resolved[0] == sys.executable
    assert resolved[1:] == argv[1:]


def test_resolve_contract_argv0_venv_present_preserves_project_interpreter(
    tmp_path,
):
    """When the project cwd has its own ``.venv/bin/python`` the resolver
    must use it verbatim — the project interpreter is authoritative and must
    never be replaced by the Runtime's own executable."""
    from tracks.executor.executor import _resolve_contract_argv0

    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    argv = [".venv/bin/python", "-m", "pytest", "--collect-only", "-q"]
    resolved = _resolve_contract_argv0(argv, tmp_path)
    assert resolved[0] == ".venv/bin/python"
    assert resolved == argv
