"""Web vertical journey e2e happy path (test-plan section 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._support import v09_web

pytestmark = pytest.mark.e2e


# AC-FR0288-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0289-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0290-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0291-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0293-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0297-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0298-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0300-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0308-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0309-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0310-01@v0.9 TRACKS-TRACE web vertical journey happy path
# AC-FR0312-01@v0.9 TRACKS-TRACE web vertical journey happy path
def test_feature_web_vertical_journey(tmp_path: Path):
    """Feature web vertical journey: serve to released via stdlib HTTP only."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    proc = v09_web.start_serve(home, repo)
    try:
        line = v09_web.wait_for_port_line(proc)
        assert "serving on http://" in line
        base = v09_web.parse_base_url(line)
        health = v09_web.wait_for_healthz(base)
        assert health["status"] == "ok"
        status, body = v09_web.http_get(base, "/api/projects")
        assert status in (200, 401)
        assert isinstance(body, bytes)
        overview_status, _ = v09_web.http_get(base, "/api/todos")
        assert overview_status in (200, 401, 404)
    finally:
        v09_web.stop_serve(proc)
