"""Black-box helpers for the v0.5 M-IMPL contract tests."""

import json
import subprocess
from pathlib import Path

from tests.integration.helpers import walk_to_m_test

ASSETS = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


def run_m_impl_journey(trac, event_log, *, simulate=None):
    """Drive the public CLI through M-TEST and return the persisted event stream."""
    run_id = walk_to_m_test(trac)
    result = trac("run", simulate=simulate) if simulate else trac("run")
    assert result.returncode == 0, result.stderr
    return run_id, result, event_log(run_id)


def events_of(events, event_type):
    """Return public event rows of one type."""
    return [event for event in events if event["type"] == event_type]


def command_dispatches(events, role=None, substate=None):
    """Return observable dispatch_agent command.issued events."""
    found = []
    for event in events_of(events, "command.issued"):
        command = event["payload"].get("command", {})
        params = command.get("params", {})
        if command.get("kind") != "dispatch_agent":
            continue
        if role is not None and params.get("role") != role:
            continue
        if substate is not None and params.get("substate") != substate:
            continue
        found.append(event)
    return found


def first_m_impl_index(events):
    """Locate the public M-IMPL entry event."""
    matches = [
        index for index, event in enumerate(events)
        if event["type"] == "stage.entered"
        and event["payload"].get("stage") == "M-IMPL"
    ]
    assert matches, "public event stream did not enter M-IMPL"
    return matches[0]


def setup_synthetic_project(host_repo):
    """Create a minimal .tracks/projects/v0.5/ with acceptance.md + interfaces.md."""
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(
        (ASSETS / "acceptance.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (vdir / "interfaces.md").write_text(
        (ASSETS / "interfaces.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return vdir


def git_read(repo, *args):
    """Run a git command in repo and return stripped stdout."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def validate_tasksjson(trac, host_repo, fixture_name):
    """Create project dir, write fixture to tasks.json, run validate, return
    (result, output).

    Output is the lowered combined stdout+stderr.
    """
    vdir = setup_synthetic_project(host_repo)
    tasks_json = vdir / "tasks.json"
    tasks_json.write_text(
        (ASSETS / fixture_name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = trac("validate", "--file", str(tasks_json))
    output = f"{result.stdout}\n{result.stderr}".lower()
    return result, output


def read_tasksjson(host_repo):
    """Read and parse tasks.json from .tracks/projects/v0.5/."""
    tasks_json_path = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    return json.loads(tasks_json_path.read_text(encoding="utf-8"))
