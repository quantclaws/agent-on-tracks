"""Web hotfix journeys e2e happy paths (test-plan section 9.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


# AC-FR0292-01@v0.9 TRACKS-TRACE post release hotfix web journey
def test_post_release_hotfix_web_journey(tmp_path: Path):
    """Post-release hotfix via web entry reaches patch tag and release."""
    from tracks.cli.serve_cmd import cmd_serve
    from tracks.server.app import create_app

    home = tmp_path / "home-post"
    repo = tmp_path / "repo-post"
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


# AC-FR0292-01@v0.9 TRACKS-TRACE dev hotfix web journey
def test_dev_hotfix_web_journey(tmp_path: Path):
    """Dev hotfix via web entry ends prerelease with no public tag."""
    from tracks.cli.serve_cmd import cmd_serve
    from tracks.server.app import create_app

    home = tmp_path / "home-dev"
    repo = tmp_path / "repo-dev"
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
