"""Progress projection (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_run_detail

pytestmark = pytest.mark.integration


# AC-FR0302-01@v0.9 TRACKS-TRACE dual counts presented
def test_dual_counts_presented(tmp_path: Path):
    """AC-FR0302-01: tests_passed and ac counts presented separately."""
    detail = project_run_detail(tmp_path, "run-1")
    progress = detail["progress"]
    assert "tests_passed" in progress
    assert "ac_closed" in progress and "ac_total" in progress


# AC-FR0302-02@v0.9 TRACKS-TRACE progress matches real counts
def test_progress_matches_real_counts(tmp_path: Path):
    """AC-FR0302-02: progress numbers trace to real counts, no fabricated pct."""
    detail = project_run_detail(tmp_path, "run-1")
    progress = detail["progress"]
    assert progress["tasks_done"] <= progress["tasks_total"]
    assert "percent" not in progress
