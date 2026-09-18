"""AC evidence view (IF-QUERY-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tracks.server.projections import project_ac_chain

pytestmark = pytest.mark.integration


# AC-FR0304-01@v0.9 TRACKS-TRACE ac chain complete
def test_ac_chain_complete(tmp_path: Path):
    """AC-FR0304-01: each AC links node result candidate and evidence."""
    chain = project_ac_chain(tmp_path, "run-1")
    assert isinstance(chain, list)
    for row in chain:
        assert "test_nodes" in row and "evidence" in row
        assert "latest_result" in row and "candidate_sha" in row


# AC-FR0304-02@v0.9 TRACKS-TRACE missing stale unreviewed marked
def test_missing_stale_unreviewed_marked(tmp_path: Path):
    """AC-FR0304-02: missing stale unreviewed evidence explicitly marked."""
    chain = project_ac_chain(tmp_path, "run-1")
    statuses = {e["status"] for row in chain for e in row.get("evidence", [])}
    assert statuses <= {"ok", "missing", "stale", "unreviewed"}
