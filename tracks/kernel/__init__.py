"""Pure kernel: types + `project()`/`decide()` (NFR-02, no I/O)."""

from .events import Command, EventEnvelope
from .machine import State, apply, decide, project

__all__ = [
    "Command",
    "EventEnvelope",
    "State",
    "apply",
    "decide",
    "project",
]
