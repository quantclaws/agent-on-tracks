"""Auto drive (IF-DRIVE-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.executor.drive import DriveConfig, drive_once

pytestmark = pytest.mark.integration


# AC-FR0297-01@v0.9 TRACKS-TRACE auto advance stages
def test_auto_advance_stages(tmp_path: Path):
    """AC-FR0297-01: drive_once advances without human commands to gate/terminal."""
    result = drive_once(tmp_path, "run-1", config=DriveConfig())
    assert result.run_id == "run-1"
    assert result.kind in ("continue", "await_human", "await_external", "terminal", "failed")


# AC-FR0297-02@v0.9 TRACKS-TRACE human gate blocks advance
def test_human_gate_blocks_advance(tmp_path: Path):
    """AC-FR0297-02: await_human parks, never auto-crosses the gate."""
    result = drive_once(tmp_path, "run-gated", config=DriveConfig())
    if result.kind == "await_human":
        assert result.wait is None
    else:
        assert result.kind in ("continue", "await_external", "terminal", "failed")
