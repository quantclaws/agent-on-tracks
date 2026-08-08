"""Black-box helpers for the v0.5 M-IMPL contract tests."""

from tests.integration.helpers import walk_to_m_test


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
