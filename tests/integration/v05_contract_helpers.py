"""Black-box helpers for the v0.5 M-IMPL contract tests."""

import json
import subprocess
from pathlib import Path

from tests.integration.helpers import walk_to_m_test

ASSETS = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


def run_m_impl_journey(trac, event_log, *, simulate=None):
    """Drive the public CLI through M-TEST and return the persisted event stream."""
    run_id = walk_to_m_test(trac, version="v0.5")
    result = trac("run", simulate=simulate) if simulate else trac("run")
    assert result.returncode == 0, result.stderr
    return run_id, result, event_log(run_id)


def first_event(events, event_type):
    """The single expected event of one type (fails when absent)."""
    found = [event for event in events if event["type"] == event_type]
    assert found, f"expected at least one {event_type} event"
    return found[0]


def pause_then_adjudicate(
    trac,
    event_log,
    host_repo,
    monkeypatch,
    run_id,
    *,
    route,
    resolve_root=None,
):
    """Drive the SM-02 pause -> Prism adjudication -> resume public journey.

    The first ``trac run`` parked on doc_comment.detected/outcome.quarantined.
    This helper reads the record identity from those PUBLIC events, applies
    Prism's out-of-band adjudication marker on the document surface (plus an
    optional legal root resolve), disarms the injection hook and continues
    the run.  Returns the post-resume event stream.
    """
    from tests._support.doc_gap_injection import (
        disarm_doc_delta,
        prism_adjudicates,
    )

    events = event_log(run_id)
    detected = first_event(events, "doc_comment.detected")["payload"]
    quarantined = first_event(events, "outcome.quarantined")["payload"]
    prism_adjudicates(
        host_repo,
        route=route,
        quarantine_id=quarantined["quarantine_id"],
        threads=list(detected["thread_ids"]),
        resolve_root=resolve_root,
    )
    disarm_doc_delta(monkeypatch)
    result = trac("run")
    assert result.returncode == 0, result.stderr
    return event_log(run_id)


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
        index
        for index, event in enumerate(events)
        if event["type"] == "stage.entered" and event["payload"].get("stage") == "M-IMPL"
    ]
    assert matches, "public event stream did not enter M-IMPL"
    return matches[0]


def first_m_impl_attempt_segment(events):
    """Return the first M-IMPL attempt, excluding M-TEST dispatches.

    ``run_m_impl_journey`` returns the full stream, and ``test.committed`` is
    legitimately emitted twice: once during M-TEST EXIT (freezing the test
    asset, before M-IMPL is entered) and once during an M-IMPL SHIELD_FIX. The
    Shield assertion must only observe the latter, so this returns the slice
    covering the first M-IMPL attempt:

    - start at the first ``stage.entered(M-IMPL)``;
    - end, inclusive, at the first subsequent ``stage.exited(M-IMPL)`` or
      ``stage.rolled_back(from_stage=M-IMPL)``;
    - if neither terminator occurs, return the remaining stream from entry.

    This keeps M-TEST's pre-stage ``test.committed`` out of the Shield verdict
    without suppressing or renaming it.
    """
    start = first_m_impl_index(events)
    for end in range(start + 1, len(events)):
        event = events[end]
        if event["type"] == "stage.exited" and event["payload"].get("stage") == "M-IMPL":
            return events[start : end + 1]
        if event["type"] == "stage.rolled_back" and event["payload"].get("from_stage") == "M-IMPL":
            return events[start : end + 1]
    return events[start:]


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
    tasks_json.write_text((ASSETS / fixture_name).read_text(encoding="utf-8"), encoding="utf-8")
    result = trac("validate", "--file", str(tasks_json))
    output = f"{result.stdout}\n{result.stderr}".lower()
    return result, output


def read_tasksjson(host_repo):
    """Read and parse tasks.json from .tracks/projects/v0.5/."""
    tasks_json_path = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    return json.loads(tasks_json_path.read_text(encoding="utf-8"))
