"""Run timeline projection extension (IF-TIMELINE-001, IF-QUERY-001).

Drives the documented read outlets over a real serve subprocess
(interfaces §4a): the timeline response gains the ``stage_order`` display
ordering (the 13-stage contract of §1q.1), the projection stays consistent
with the ac-chain source, and every entered stage keeps its timeline entry
(no partial delivery).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_get,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration

_TS = "2026-09-27T00:00:00+00:00"

# The §1q.1 display order: M-START, then the canonical stage order with
# M-REQ-APPROVAL inserted between M-ACC and M-DESIGN.
_STAGE_ORDER = [
    "M-START",
    "M-STORY",
    "M-SPEC",
    "M-ACC",
    "M-REQ-APPROVAL",
    "M-DESIGN",
    "M-TEST",
    "M-IMPL",
    "M-VERIFY",
    "M-SECURITY",
    "M-RELEASE",
    "M-PUBLISH",
    "M-MILESTONE",
]


def _start_timeline_serve(tmp_path: Path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("timeline host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "timeline@example.com"),
        ("config", "user.name", "Timeline Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _seed_run(home: Path, repo: Path, run_id: str, *, attempts: int = 1) -> None:
    from tracks.paths import tracks_home
    from tracks.store import Store

    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            ("proj-timeline-1", str(repo), "v1.0", "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, "v1.0", "story.requested", {"raw_chars": 1})
        for stage in ("M-START", "M-STORY", "M-IMPL"):
            store.append(run_id, "v1.0", "stage.entered", {"stage": stage})
        # a retried attempt re-enters the stage: the timeline must keep both
        # nodes (AC-FR0326-02: attempts are not folded)
        for _ in range(attempts - 1):
            store.append(run_id, "v1.0", "stage.entered", {"stage": "M-IMPL"})
        store.append(run_id, "v1.0", "taskgraph.committed", {"task_count": 2})
    finally:
        store.close()


# AC-FR0326-01@v0.10 TRACKS-TRACE stage order and timeline consistency
def test_stage_order_and_timeline_consistency(tmp_path: Path):
    """AC-FR0326-01 (projection half): the timeline response carries the
    13-stage ``stage_order`` display ordering and the timeline events stay
    consistent with the ac-chain interface data (one source, no second
    synthesis)."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        run_id = "run-timeline-1"
        _seed_run(home, repo, run_id)

        status, body = http_get(
            base, f"/api/runs/{run_id}/timeline", cookies=cookie
        )
        assert status == 200, body[:200]
        timeline = json.loads(body)
        assert timeline["stage_order"] == _STAGE_ORDER, (
            "the timeline must declare the §1q.1 thirteen-stage display order: "
            f"{timeline.get('stage_order')!r}"
        )
        entered = [
            event["payload"]["stage"]
            for event in timeline["events"]
            if event["type"] == "stage.entered"
        ]
        assert entered == ["M-START", "M-STORY", "M-IMPL"], entered

        # one source: the ac-chain reads the same run event stream
        status, body = http_get(base, f"/api/runs/{run_id}/ac-chain", cookies=cookie)
        assert status == 200, body[:200]
        chain = json.loads(body)
        assert isinstance(chain, list)

        # an unknown run stays 404 (the projection never fabricates a timeline)
        status, body = http_get(
            base, "/api/runs/run-does-not-exist/timeline", cookies=cookie
        )
        assert status == 404, body[:200]
    finally:
        stop_serve(proc)


# AC-FR0326-02@v0.10 TRACKS-TRACE timeline entry present, no partial delivery
def test_timeline_entry_present(tmp_path: Path):
    """AC-FR0326-02: the timeline projection ships complete for the run —
    every entered stage keeps its own timeline entry (attempts are not
    folded) and the stage_order covers the full display order (no partial
    delivery residue)."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        run_id = "run-timeline-2"
        _seed_run(home, repo, run_id, attempts=2)

        status, body = http_get(
            base, f"/api/runs/{run_id}/timeline?command_id=", cookies=cookie
        )
        assert status == 200, body[:200]
        timeline = json.loads(body)
        entries = [
            event for event in timeline["events"] if event["type"] == "stage.entered"
        ]
        assert len(entries) == 4, (
            "each entered stage is an independent timeline entry (not folded)"
        )
        assert [event["payload"]["stage"] for event in entries] == [
            "M-START",
            "M-STORY",
            "M-IMPL",
            "M-IMPL",
        ]
        seqs = [event["seq"] for event in timeline["events"]]
        assert seqs == sorted(seqs), "the timeline keeps the merged event order"
        assert len(timeline["stage_order"]) == 13, (
            "the display order must cover all thirteen stages"
        )
    finally:
        stop_serve(proc)
