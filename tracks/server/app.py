"""Starlette app factory and web composition root (IF-SERVE-001).

Composition root of the v0.9 web surface: wires the service-level store
(``tracks/supervisor/db.py``), the command service, the supervisor handle,
auth middleware and the route tables into the Starlette application that
``cmd_serve`` runs under uvicorn. HTTP handlers never touch the executor;
mutations go through the command service only (interfaces §1b).

Contract tokens: IF-SERVE-001, IF-WEBAUTH-001, IF-QUERY-001, IF-STREAM-001.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_app(home: Path, service: Any, supervisor: Any, config: Any) -> Any:
    """Build the Starlette application (routes/middleware/static wiring).

    The returned app serves the pages and API of interfaces §2b and mounts
    ``tracks/server/static/`` at ``/static/``; uvicorn runs it in the serve
    process while the supervisor drives runs in the same process (workers are
    child processes).
    """
    raise NotImplementedError("IF-SERVE-001")


def health_response(home: Path) -> Any:
    """Return the ``GET /healthz`` payload: 200 ``{status, projects, version,
    uptime_s}`` or 503 ``{status: unavailable, reasons: [...]}`` (§2b #3)."""
    raise NotImplementedError("IF-SERVE-001")
