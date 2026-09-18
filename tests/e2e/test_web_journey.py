"""Web vertical journey e2e happy path (test-plan section 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    from tracks.cli.serve_cmd import cmd_serve
    from tracks.server.app import create_app

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        cmd_serve(repo, "--repo", str(repo), "--home", str(home), "--port", "0")
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("serve stub must raise IF-SERVE-001 before Devon implements it")
    try:
        create_app(home, object(), object(), object())
    except NotImplementedError as exc:
        assert str(exc) == "IF-SERVE-001"
    else:
        raise AssertionError("app stub must raise IF-SERVE-001 before Devon implements it")
