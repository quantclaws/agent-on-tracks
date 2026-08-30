"""Python reference host provisioning (FR-0282, IF-REFERENCE-001).

Data-driven provisioning from wheel assets: the Python specifics live in
``tracks/assets/reference_host/**`` (declared template data), never in this
module's logic. The provisioned host binds a dedicated real GitHub remote
(Maestro ruling T-003 A) and walks the same release pipeline as the tracks
host itself; missing credentials or remote leave it needs_attention, never a
local-success downgrade.
"""

from __future__ import annotations

from pathlib import Path


def create_reference_host(
    template_dir: Path, target_dir: Path, wheel: Path, remote_url: str | None
) -> dict:
    """Fresh venv + non-editable wheel + asset deployment + remote binding."""
    raise NotImplementedError("IF-REFERENCE-001")


def verify_reference_equivalence(report: dict) -> tuple[bool, tuple[str, ...]]:
    raise NotImplementedError("IF-REFERENCE-001")
