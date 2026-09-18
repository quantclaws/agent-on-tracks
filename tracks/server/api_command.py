"""Mutating HTTP routes -> CommandService (interfaces §2b).

Every handler authenticates the session, checks CSRF, validates the command
payload through the guard, and delegates to ``CommandService.accept`` with
``surface="http"``. Handlers never execute business logic inline (SM-01.1);
long tasks are driven by supervisor workers.

Contract tokens: IF-CMDSVC-001, IF-CMDGUARD-001, IF-WEBGATE-001,
IF-PAUSE-001, IF-DOCREV-001.
"""

from __future__ import annotations

from typing import Any


async def register_project(request: Any) -> Any:
    """POST /api/projects -> register_project (§1b #1)."""
    raise NotImplementedError("IF-CMDSVC-001")


async def run_readiness(request: Any) -> Any:
    """POST /api/projects/{pid}/readiness -> check_readiness (§1b #2)."""
    raise NotImplementedError("IF-PROJ-001")


async def create_run(request: Any) -> Any:
    """POST /api/projects/{pid}/runs -> create_run (§1b #3)."""
    raise NotImplementedError("IF-CMDSVC-001")


async def submit_clarification(request: Any) -> Any:
    """POST /api/runs/{run_id}/clarifications -> submit_clarification (§1b #4)."""
    raise NotImplementedError("IF-WEBAUTH-001")


async def edit_material(request: Any) -> Any:
    """POST /api/runs/{run_id}/docs/{doc}/edits -> edit_material (§1b #5)."""
    raise NotImplementedError("IF-DOCREV-001")


async def record_approval(request: Any) -> Any:
    """POST /api/runs/{run_id}/approvals -> record_stage_approval (§1b #6)."""
    raise NotImplementedError("IF-WEBGATE-001")


async def record_release_decision(request: Any) -> Any:
    """POST /api/runs/{run_id}/release-decision -> record_release_decision (§1b #7)."""
    raise NotImplementedError("IF-WEBGATE-001")


async def pause_run(request: Any) -> Any:
    """POST /api/runs/{run_id}/pause -> pause_run (§1b #8)."""
    raise NotImplementedError("IF-PAUSE-001")


async def resume_run(request: Any) -> Any:
    """POST /api/runs/{run_id}/resume -> resume_run (§1b #9)."""
    raise NotImplementedError("IF-PAUSE-001")


async def abandon_run(request: Any) -> Any:
    """POST /api/runs/{run_id}/abandon -> abandon_run (§1b #10)."""
    raise NotImplementedError("IF-WEBGATE-001")


async def return_stage(request: Any) -> Any:
    """POST /api/runs/{run_id}/return -> return_stage (§1b #11)."""
    raise NotImplementedError("IF-WEBGATE-001")


async def retry_run(request: Any) -> Any:
    """POST /api/runs/{run_id}/retry -> retry_run (§1b #12)."""
    raise NotImplementedError("IF-WEBGATE-001")
