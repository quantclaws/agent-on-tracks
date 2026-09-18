"""Environment readiness probes (IF-PROJ-001).

Four closed probes per FR-0290: contract (project.toml parse + required
sections), harness_model (configured agent backend executable present),
credentials_ref (required credential env NAMES configured — values are never
read into the UI), tools (required executables on PATH). Each probe reports
{ok, reason}; a failed probe blocks run creation with the concrete reason
(no fake runs). The existing configured environment is used as-is — no
package-manager GUI this version.

Contract token: IF-PROJ-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROBE_KINDS = ("contract", "harness_model", "credentials_ref", "tools")


@dataclass(frozen=True)
class ProbeResult:
    check: str  # one of PROBE_KINDS
    ok: bool
    reason: str | None


def run_readiness(repo: Path) -> list:
    """Run all four probes against the registered repo; emit the
    project.readiness_checked event through the command service caller."""
    raise NotImplementedError("IF-PROJ-001")
