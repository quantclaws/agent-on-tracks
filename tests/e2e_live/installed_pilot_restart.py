"""Actual installed-service interruption during a real Agent dispatch."""

import json
import sqlite3
import time

from playwright.sync_api import expect

from tests.e2e_live.m_test_helpers import _events
from tracks.processes import (
    _process_table,
    capture_process_tree,
    wait_captured_processes,
)


def restart_active_agent(pilot):
    """Stop via the installed CLI, check for orphans, then reopen the same run."""
    deadline = time.monotonic() + 60
    while True:
        meta = json.loads((pilot.repo / ".tracks/service/serve.pid").read_text())
        owned = capture_process_tree(meta["pid"])
        if any("opencode run --agent Shield" in identity.command for identity in owned):
            break
        assert time.monotonic() < deadline, "interruption requires an active real Shield process"
        pilot.page.wait_for_timeout(100)
    events = _events(pilot.repo, pilot.run_id)
    pending = [e for e in events if e["type"] == "command.issued"][-1]
    assert pending["payload"]["command"]["kind"] == "dispatch_agent"
    assert not any(
        e["type"] == "outcome.received" and e["command_id"] == pending["command_id"]
        for e in events
    ), "Agent completed before the interruption could be exercised"
    with sqlite3.connect(pilot.repo / ".tracks/service/service.db") as connection:
        command_ids = [row[0] for row in connection.execute("SELECT command_id FROM commands")]
    pilot.cli("serve", "--repo", str(pilot.repo), "--stop")
    assert wait_captured_processes(owned, 3), "installed stop left an owned process alive"
    assert not (pilot.repo / ".tracks/service/serve.pid").exists()
    escaped = [
        identity.pid for identity, _, stat in _process_table().values()
        if not stat.startswith("Z") and (
            str(pilot.repo) in identity.command or any(
                "worker_main --command-id " + cid in identity.command for cid in command_ids
            )
        )
    ]
    assert not escaped, f"shutdown spawned replacement actors: {escaped}"
    assert _events(pilot.repo, pilot.run_id) == events, "stop changed pending workflow facts"
    (pilot.install.artifact_dir / "pilot-interruption.json").write_text(json.dumps({
        "run_id": pilot.run_id, "pending_command": pending["command_id"],
        "owned_pids": [identity.pid for identity in owned],
        "all_owned_terminated": True, "same_pending_command_preserved": True,
        "no_replacement_workers_or_agents": True,
    }, indent=2))
    print("PILOT_STOP=all_owned_terminated_and_WAL_preserved", flush=True)
    pilot.open_server()
    pilot.page.goto(pilot.base + "/runs/" + pilot.run_id, wait_until="domcontentloaded")
    expect(pilot.page.get_by_test_id("current-node-card")).to_be_visible()
