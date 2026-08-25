"""Command executor + agent backend seam (NFR-01; ARCH-003 §4).

The deterministic FakeBackend lives in tracks/effects (the effects boundary);
``FakeAgent`` is kept as a v0.1 compatibility alias.
"""

from tracks.effects import FakeBackend

# Import mutation_manifest helper so that hasattr(tracks.executor,
# "mutation_manifest") works for the R test.
from . import mutation_manifest  # noqa: F401
from .executor import Executor, git

FakeAgent = FakeBackend  # v0.1 compatibility alias

__all__ = ["Executor", "FakeAgent", "FakeBackend", "git"]
