"""Small process probes shared by the CLI and Agent recovery."""

import os


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
