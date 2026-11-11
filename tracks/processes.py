"""Small process probes shared by the CLI and Agent recovery."""

import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass


def process_alive(pid: int) -> bool:
    """Probe an existing positive PID; permission denial still means it exists.

    This is an existence check, not proof of process ownership or birth identity.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class ProcessIdentity:
    """A captured PID and birth/command identity; parentage may change on exit."""

    pid: int
    started: str
    command: str


def _process_table() -> dict:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,lstart=,stat=,args="],
        capture_output=True, text=True, timeout=2, check=False,
    )
    if result.returncode != 0:
        raise OSError("cannot inspect the owned process tree")
    table = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) != 9 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        identity = ProcessIdentity(int(parts[0]), " ".join(parts[2:7]), parts[8])
        table[identity.pid] = (identity, int(parts[1]), parts[7])
    return table


def capture_process_tree(pid: int) -> tuple[ProcessIdentity, ...]:
    """Capture a validated service and descendants, including detached sessions."""
    table = _process_table()
    if pid not in table:
        raise OSError("service process is no longer observable")
    pending, captured = [pid], []
    while pending:
        parent = pending.pop(0)
        captured.append(table[parent][0])
        pending.extend(child for child, row in table.items() if row[1] == parent)
    return tuple(captured)


def signal_captured_processes(identities: tuple[ProcessIdentity, ...], sig: signal.Signals) -> None:
    """Signal only captured identities still alive; never act on a reused PID."""
    current = _process_table()
    for identity in identities:
        row = current.get(identity.pid)
        if row is None or row[0] != identity or row[2].startswith("Z"):
            continue
        with suppress(ProcessLookupError):
            os.kill(identity.pid, sig)


def wait_captured_processes(identities: tuple[ProcessIdentity, ...], timeout: float) -> bool:
    """Wait for the owned identities to exit, treating zombies as terminated."""
    deadline = time.monotonic() + timeout
    while True:
        current = _process_table()
        alive = [
            identity for identity in identities
            if (row := current.get(identity.pid)) is not None
            and row[0] == identity and not row[2].startswith("Z")
        ]
        if not alive:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
