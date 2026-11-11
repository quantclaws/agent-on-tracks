"""Stopping a captured tree must never signal a recycled or unobservable PID."""

import signal
import subprocess
from types import SimpleNamespace

import pytest

from tracks import processes


# AC-NFR0159-01@v0.12 TRACKS-TRACE old process identity never targets a reused PID
@pytest.mark.parametrize("replacement", ["birth", "command", "zombie", "gone"])
def test_recycled_or_terminated_process_is_not_signalled(monkeypatch, replacement):
    captured = processes.ProcessIdentity(123, "Mon Nov 9 01:00:00 2026", "owned agent")
    current = processes.ProcessIdentity(
        123, "Tue Nov 10 01:00:00 2026" if replacement == "birth" else captured.started,
        "unrelated agent" if replacement == "command" else captured.command,
    )
    table = {} if replacement == "gone" else {
        123: (current, 1, "Z" if replacement == "zombie" else "S"),
    }
    monkeypatch.setattr(processes, "_process_table", lambda: table)
    signals = []
    monkeypatch.setattr(processes.os, "kill", lambda *args: signals.append(args))
    processes.signal_captured_processes((captured,), signal.SIGKILL)
    assert processes.wait_captured_processes((captured,), timeout=0)
    assert signals == []


# AC-NFR0159-01@v0.12 TRACKS-TRACE failed inspection cannot authorize process termination
def test_process_inspection_failure_is_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(OSError, match="inspect"):
        processes.capture_process_tree(123)
