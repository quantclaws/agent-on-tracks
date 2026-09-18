"""Read-model projections over tracks.db + service.db (IF-QUERY-001).

Every projection is computed on demand from the event stores and project
documents (read-only connections); nothing here keeps in-memory "running"
facts. Progress reports test counts and AC-closure counts separately with
identifiable sources; no fabricated percentages (FR-0302). The human todo
projection lists only legitimate human decisions — quota/CI/network waits
never appear (FR-0307).

Contract token: IF-QUERY-001.
"""

from __future__ import annotations

from pathlib import Path


def project_overview(home: Path) -> dict:
    """Projects + runs + per-run human-todo counts (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


def project_run_detail(home: Path, run_id: str) -> dict:
    """Stage/substate/control state/wait/progress/executions snapshot (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


def project_timeline(
    home: Path,
    run_id: str,
    *,
    command_id: str | None = None,
    task_id: str | None = None,
    ac_id: str | None = None,
    after_seq: str | None = None,
) -> dict:
    """Merged tracks.db + service_events timeline with cursor (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


def project_ac_chain(home: Path, run_id: str) -> list:
    """AC -> test node -> result -> candidate -> evidence chain (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


def project_todos(home: Path) -> list:
    """Legitimate human decisions only (§1e); waits/env issues excluded."""
    raise NotImplementedError("IF-QUERY-001")


def project_service_config(home: Path) -> dict:
    """Effective wait/poll/lease configuration values (§1e, NFR-0152)."""
    raise NotImplementedError("IF-WAIT-001")


def project_executions(home: Path, run_id: str) -> list:
    """Per-role/task execution state + log refs (FR-0303; no session content)."""
    raise NotImplementedError("IF-QUERY-001")
