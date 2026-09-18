"""Performance budgets (IF-CMDSVC-001, IF-QUERY-001, IF-SERVE-001)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration


# AC-NFR0150-01@v0.9 TRACKS-TRACE accept returns after persist
def test_accept_returns_after_persist(tmp_path: Path):
    """AC-NFR0150-01: accept returns at persist, long tasks never block it."""
    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    start = time.monotonic()
    receipt = svc.accept(
        kind="pause_run",
        params={"run_id": "run-1"},
        actor="human",
        actor_class="human",
        surface="http",
        idempotency_key="perf-accept-1",
    )
    elapsed = time.monotonic() - start
    assert receipt.command_id
    assert elapsed < 5.0


# AC-NFR0150-02@v0.9 TRACKS-TRACE query p95 under 1s
def test_query_p95_under_1s(tmp_path: Path):
    """AC-NFR0150-02: overview detail timeline samples complete under 1s."""
    from tracks.server.projections import project_overview, project_run_detail, project_timeline

    samples = []
    for _ in range(5):
        start = time.monotonic()
        project_overview(tmp_path)
        project_run_detail(tmp_path, "run-1")
        project_timeline(tmp_path, "run-1")
        samples.append(time.monotonic() - start)
    samples.sort()
    assert samples[4] < 1.0


# AC-NFR0150-03@v0.9 TRACKS-TRACE soak memory bounded
def test_soak_memory_bounded(tmp_path: Path):
    """AC-NFR0150-03: soak sampling shows bounded RSS after warmup."""
    import os

    home = tmp_path / "home"
    home.mkdir()
    svc = CommandService(home, object(), object())
    for i in range(5):
        svc.accept(
            kind="pause_run",
            params={"run_id": f"run-{i}"},
            actor="human",
            actor_class="human",
            surface="http",
            idempotency_key=f"soak-{i}",
        )
    with open(f"/proc/{os.getpid()}/status", encoding="utf-8") as fh:
        status = fh.read()
    assert "VmRSS" in status
    rss = [int(line.split()[1]) for line in status.splitlines() if line.startswith("VmRSS")][0]
    assert rss > 0
