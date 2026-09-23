"""Performance budgets (IF-CMDSVC-001, IF-QUERY-001, IF-SERVE-001)."""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.paths import tracks_home
from tracks.store import Store
from tracks.supervisor.db import ServiceDB
from tracks.supervisor.service import CommandService

pytestmark = pytest.mark.integration

# AC-NFR0150-03 soak shape: a warmup pass, then repeated load rounds whose
# accepted commands persist ``command.accepted`` + ``run.pause_requested``
# events (a sustained many-event load on the real serve stack).
_WARMUP_COMMANDS = 100
_SOAK_ROUNDS = 12
_SOAK_ROUND_COMMANDS = 100
# Resource bound after warmup (NFR-0150: no unbounded growth): interpreter
# arenas grow by chunks and settle; an unbounded leak keeps climbing with the
# event count and crosses this allowance.
_RSS_GROWTH_TOLERANCE = 2.0


def _seed_soak_run(tmp_path: Path, run_id: str) -> tuple[Path, Path]:
    """A registered project with one run: the soak target of the command plane."""
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / "v0.9"
    vdir.mkdir(parents=True, exist_ok=True)
    for name in ("story.md", "spec.md", "acceptance.md", "architecture.md"):
        (vdir / name).write_text(f"{name} body\n", encoding="utf-8")
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()
    home = tmp_path / "service"
    ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "local-user", "2026-09-23T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return repo, home


def _seed_query_load(tmp_path: Path, run_id: str) -> Path:
    """A registered project carrying one driven run: a taskgraph with eight
    completed tasks plus an AC registry, so each query does real work."""
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v0.9", "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            "v0.9",
            "taskgraph.committed",
            {"task_count": 8, "tasks": [{"task_id": f"T-{n}"} for n in range(1, 9)]},
        )
        for index in range(1, 9):
            store.append(
                run_id, "v0.9", "task.started", {"task_id": f"T-{index}"}, task_id=f"T-{index}"
            )
            store.append(run_id, "v0.9", "task.completed", {"task_id": f"T-{index}"})
    finally:
        store.close()
    vdir = repo / ".tracks" / "projects" / "v0.9"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(
        "## FR-0302\n\n"
        + "\n".join(f"### AC-FR0302-0{index}\n\n- criterion\n" for index in range(1, 4)),
        encoding="utf-8",
    )
    home = tmp_path / "service"
    ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-1", str(repo), "v0.9", "local-user", "2026-09-23T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return home


def _serve_pid(serving_line: str) -> int:
    """The serve process id from the §2a startup line."""
    return int(re.search(r"\(pid (\d+)\)", serving_line).group(1))


def _sample_rss(pid: int) -> int:
    """Resident memory of the serve process (test-plan §1.1: ``ps``)."""
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=True
    )
    value = int(out.stdout.strip())
    assert value > 0, f"serve pid {pid} reports no resident memory"
    return value


def _drive_soak(base: str, cookie: str, csrf: str, prefix: str, count: int) -> None:
    """Accept ``count`` pause commands through the web command plane."""
    for index in range(count):
        status, body = http_post(
            base,
            "/api/runs/run-1/pause",
            {},
            cookies=cookie,
            csrf=csrf,
            idempotency_key=f"{prefix}-{index}",
        )
        assert status == 202, body


# AC-NFR0150-01@v0.9 TRACKS-TRACE accept returns after persist
# Operator OOB 2026-09-23: verified green-on-arrival in the island-2 terminal sweep; M-TEST re-entry after full implementation (run 01M2QTJB).
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

    home = _seed_query_load(tmp_path, "run-1")

    samples = []
    for _ in range(5):
        start = time.monotonic()
        project_overview(home)
        detail = project_run_detail(home, "run-1")
        project_timeline(home, "run-1")
        samples.append(time.monotonic() - start)
    samples.sort()
    assert all(sample > 0 for sample in samples), "each sample must cover a real query"
    assert samples[4] < 1.0
    # the sampled snapshot reflects the seeded single-run drive load
    assert detail["progress"]["tasks_done"] == 8
    assert detail["progress"]["tasks_total"] == 8
    assert detail["progress"]["ac_total"] == 3


# AC-NFR0150-03@v0.9 TRACKS-TRACE soak memory bounded
def test_soak_memory_bounded(tmp_path: Path):
    """AC-NFR0150-03: soak sampling shows bounded service RSS after warmup."""
    repo, home = _seed_soak_run(tmp_path, "run-1")
    proc = start_serve(home, repo, port=0)
    try:
        line = wait_for_port_line(proc)
        pid = _serve_pid(line)
        base = parse_base_url(line)
        assert wait_for_healthz(base)["status"] == "ok"
        cookie, csrf = login_session(base)
        _drive_soak(base, cookie, csrf, "warmup", _WARMUP_COMMANDS)
        warmup = _sample_rss(pid)
        samples = [warmup]
        for round_index in range(_SOAK_ROUNDS):
            _drive_soak(base, cookie, csrf, f"soak-{round_index}", _SOAK_ROUND_COMMANDS)
            samples.append(_sample_rss(pid))
    finally:
        stop_serve(proc)

    # After warmup the resident memory stays bounded: no monotonic growth
    # trend across the soak and every sample within the allocator allowance.
    assert not all(
        later > earlier
        for earlier, later in zip(samples, samples[1:], strict=False)
    ), f"resident memory grew monotonically under the soak: {samples}"
    assert max(samples) <= warmup * _RSS_GROWTH_TOLERANCE, (
        f"resident memory exceeded the soak bound: warmup={warmup} samples={samples}"
    )
