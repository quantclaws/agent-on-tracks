"""Command executor + deterministic FakeAgent (NFR-01)."""
from .executor import Executor, git
from .fake_agent import FakeAgent

__all__ = ["Executor", "FakeAgent", "git"]
