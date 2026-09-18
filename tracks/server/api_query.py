"""Read-only HTTP routes -> projections (interfaces §2b, §1e).

All handlers are read-only: independent read-only SQLite connections, no
writer_lock, no events emitted (NFR-0150). Responses pass through the
redactor before serialization (§1g.3).

Contract tokens: IF-QUERY-001, IF-SERVE-001, IF-SECRECY-001, IF-DOCREV-001.
"""

from __future__ import annotations

from typing import Any


async def healthz(request: Any) -> Any:
    """GET /healthz (public): 200 {status, projects, version, uptime_s} | 503."""
    raise NotImplementedError("IF-SERVE-001")


async def service_config(request: Any) -> Any:
    """GET /api/service/config: effective wait/poll/lease config (§1e)."""
    raise NotImplementedError("IF-WAIT-001")


async def list_projects(request: Any) -> Any:
    """GET /api/projects: registered projects with attribution (§2b #6)."""
    raise NotImplementedError("IF-PROJ-001")


async def project_overview(request: Any) -> Any:
    """GET /api/projects/{pid}/overview: runs + human-todo count (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


async def run_detail(request: Any) -> Any:
    """GET /api/runs/{run_id}: detail snapshot incl. event_cursor (§1e)."""
    raise NotImplementedError("IF-QUERY-001")


async def timeline(request: Any) -> Any:
    """GET /api/runs/{run_id}/timeline: filtered, cursor-paginated events."""
    raise NotImplementedError("IF-QUERY-001")


async def ac_chain(request: Any) -> Any:
    """GET /api/runs/{run_id}/ac-chain: AC -> test node -> result -> evidence."""
    raise NotImplementedError("IF-QUERY-001")


async def run_todos(request: Any) -> Any:
    """GET /api/runs/{run_id}/todos: human decisions for this run only."""
    raise NotImplementedError("IF-QUERY-001")


async def global_todos(request: Any) -> Any:
    """GET /api/todos: the human todo center (quota/CI waits never listed)."""
    raise NotImplementedError("IF-QUERY-001")


async def read_doc(request: Any) -> Any:
    """GET /api/runs/{run_id}/docs/{doc}: current content + revision history."""
    raise NotImplementedError("IF-DOCREV-001")


async def doc_diff(request: Any) -> Any:
    """GET /api/runs/{run_id}/docs/{doc}/diff?from=&to=: unified diff."""
    raise NotImplementedError("IF-DOCREV-001")


async def release_preview(request: Any) -> Any:
    """GET /api/runs/{run_id}/release-preview: digest + staleness (§2b #20)."""
    raise NotImplementedError("IF-WEBGATE-001")


async def command_status(request: Any) -> Any:
    """GET /api/commands/{command_id}: persisted command status/result."""
    raise NotImplementedError("IF-CMDSVC-001")
