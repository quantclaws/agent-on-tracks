"""Milestone closing terminal-state awareness (IF-MILESTONE-002).

Drives the real release chain to the M-MILESTONE closing tail over the
loopback CI stand-in and a real bare remote (the inherited harness), then
reproduces the #182 shape: the M-IMPL closure boundary pseudo-completion
(``run.completed(terminal_state=boundary)``) sits in the run log when the
closing tail executes. The contract outlet is the closing event stream —
``run.completed(terminal_state=released, release_tag)`` must land exactly
once and ``close_milestone`` must not re-emit completed sub-steps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.helpers import init_bare_remote, walk_to_awaiting_release

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _store_events(repo: Path, run_id: str) -> list[dict]:
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(repo))
    try:
        return [
            {"seq": event.seq, "type": event.type, "payload": dict(event.payload or {})}
            for event in store.events(run_id)
        ]
    finally:
        store.close()


def _insert_boundary_pseudo_completion(repo: Path, run_id: str) -> None:
    """Reproduce the #182 log shape: the M-IMPL closure boundary completion
    immediately after ``stage.exited(M-IMPL)``, before the post-boundary
    release tail (seq-renumbering keeps the append-only ordering)."""
    from tracks.paths import tracks_home
    from tracks.store.store import SCHEMA_VERSION, Store

    store = Store(tracks_home(repo))
    try:
        exit_seq = next(
            event.seq
            for event in store.events(run_id)
            if event.type == "stage.exited" and (event.payload or {}).get("stage") == "M-IMPL"
        )
        with store.conn:
            # renumber in two hops (a single +1 shift collides on the PK);
            # the boundary completion lands immediately after stage.exited
            store.conn.execute(
                "UPDATE events SET seq = seq + 1000 WHERE run_id = ? AND seq > ?",
                (run_id, exit_seq),
            )
            store.conn.execute(
                "INSERT INTO events (run_id, seq, ts, version, type, schema_version,"
                " command_id, task_id, payload) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    exit_seq + 1,
                    "2026-09-27T00:00:00+00:00",
                    "v0.8",
                    "run.completed",
                    SCHEMA_VERSION,
                    None,
                    None,
                    json.dumps({"terminal_state": "boundary"}),
                ),
            )
            store.conn.execute(
                "UPDATE events SET seq = seq - 999 WHERE run_id = ? AND seq > ?",
                (run_id, exit_seq + 1000),
            )
        store.rebuild_projections()
    finally:
        store.close()


# AC-FR0327-01@v0.10 TRACKS-TRACE released reachable despite boundary completion
def test_released_reachable_despite_boundary_completion(host_repo, trac, event_log, ci_echo_standin):
    """AC-FR0327-01: with the M-IMPL closure boundary pseudo-completion in the
    log, the closing tail still lands ``run.completed(terminal_state=released,
    release_tag)`` exactly once — the idempotency guard must not mistake the
    boundary for a finished closing, and a re-issued close must not repeat the
    completed sub-steps."""
    init_bare_remote(host_repo, "milestone_bare.git")
    run_id = walk_to_awaiting_release(trac, host_repo=host_repo)
    assert run_id
    assert trac("release", "--action", "release").returncode == 0

    # the boundary pseudo-completion coexists with the release tail
    _insert_boundary_pseudo_completion(host_repo, run_id)
    seeded = _store_events(host_repo, run_id)
    boundary = [e for e in seeded if e["type"] == "run.completed"]
    assert boundary and boundary[0]["payload"]["terminal_state"] == "boundary"

    # drive the closing tail over the same write-ahead command surface the
    # runtime uses, from a subprocess whose cwd is the host repo (the publish
    # effects resolve candidate refs repo-relatively); the CLI gate stops at
    # a completed projection, the tail itself is the contract under test
    driver = (
        "import sys;"
        "from tracks.executor.executor import Executor;"
        "from tracks.kernel.events import Command;"
        "from tracks.paths import tracks_home;"
        "from tracks.store import Store;"
        "import pathlib;"
        "store = Store(tracks_home('.'));"
        "ex = Executor(store, pathlib.Path('.'), sys.argv[1]);"
        "ex.issue(Command(kind='execute_publish', params={}));"
        "params = {'tracker': {'repo': sys.argv[2], 'project': '', 'milestone': ''}};"
        "ex.issue(Command(kind='close_milestone', params=params));"
        "ex.issue(Command(kind='close_milestone', params=params));"
        "store.close()"
    )
    env = {k: v for k, v in os.environ.items() if k != "TRACKS_HOME"}
    env["PYTHONPATH"] = str(_REPO_ROOT)
    driven = subprocess.run(
        [sys.executable, "-c", driver, run_id, ci_echo_standin.repo],
        cwd=host_repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert driven.returncode == 0, driven.stderr[-2000:]

    events = event_log(run_id)
    released = [
        e for e in events if e["type"] == "run.completed"
        and (e["payload"] or {}).get("terminal_state") == "released"
    ]
    assert released, (
        "the closing tail must land run.completed(terminal_state=released) "
        "despite the boundary pseudo-completion in the log (#182)"
    )
    assert len(released) == 1, "released must land exactly once"
    assert released[0]["payload"].get("release_tag")
    boundary_seq = next(
        e["seq"] for e in events
        if e["type"] == "run.completed"
        and (e["payload"] or {}).get("terminal_state") == "boundary"
    )
    assert boundary_seq < released[0]["seq"], (
        "the boundary pseudo-completion precedes the released completion"
    )
    traces = [e for e in events if e["type"] == "milestone.trace_closed"]
    assert len(traces) == 1, "close_milestone must not re-emit a completed sub-step"
    stall = [
        e for e in events
        if e["type"] == "attention.required"
        and (e["payload"] or {}).get("area") in ("publish", "project_close")
        and (e["payload"] or {}).get("reason")
        in ("missing_operations", "milestone_not_found", "close_unconfirmed")
    ]
    assert not stall, f"the closing tail must not stall: {stall!r}"
