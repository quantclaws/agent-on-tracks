"""Static text asset freshness (IF-STATICCACHE-001, IF-QUERY-001).

AC-FR0360-01: text static assets answer ``Cache-Control: no-cache``, keep
ETag revalidation (unchanged -> 304), return the new bytes after a deploy
content change, and API routes keep their semantics without the header.
AC-NFR0160-01 (static half): the cache change does not force a full reload
of unchanged files and does not alter API semantics.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests._support.v012_install import git_init, http_request
from tests._support.v09_web import (
    parse_base_url,
    stop_serve,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration


def _start_prefix_serve(global_cli, home: Path, repo: Path, outside: Path):
    home.mkdir(parents=True, exist_ok=True)
    script = (
        "import pathlib, sys\n"
        "from tracks.cli.serve_cmd import cmd_serve\n"
        "raise SystemExit(cmd_serve(pathlib.Path('.'), *sys.argv[1:]))\n"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            "--repo",
            str(repo),
            "--home",
            str(home),
            "--port",
            "0",
            "--password-stdin",
        ],
        cwd=str(outside),
        env=global_cli.env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write("v012-test-password\n")
    proc.stdin.flush()
    return proc


@pytest.fixture
def prefix_serve(v012_global, tmp_path: Path):
    repo = git_init(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    proc = _start_prefix_serve(v012_global, tmp_path / "home", repo, outside)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        yield base, v012_global.site
    finally:
        stop_serve(proc)


def _headers_lower(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


# AC-FR0360-01@v0.12 TRACKS-TRACE text assets no-cache 304 fresh bytes
# AC-NFR0160-01@v0.12 TRACKS-TRACE unchanged assets revalidate changed refresh
def test_text_assets_no_cache_and_304(prefix_serve):
    """AC-FR0360-01: ``/static/app/*`` and text-suffixed assets carry
    ``Cache-Control: no-cache`` while preserving ETag; an unchanged asset
    answers 304 to ``If-None-Match``, and a content change returns the new
    bytes on a plain request (no stale heuristic cache)."""
    base, site = prefix_serve
    status, headers, body = http_request("GET", base + "/static/app/api.js")
    assert status == 200
    lower = _headers_lower(headers)
    assert lower.get("cache-control") == "no-cache"
    etag = lower.get("etag")
    assert etag, "the static response must keep its ETag"
    assert body

    status, headers, _body = http_request(
        "GET", base + "/static/app/api.js", headers={"If-None-Match": etag}
    )
    assert status == 304, "an unchanged file must revalidate with 304"
    assert _headers_lower(headers).get("etag") == etag

    css_status, css_headers, _css = http_request("GET", base + "/static/styles.css")
    assert css_status == 200
    assert _headers_lower(css_headers).get("cache-control") == "no-cache"

    asset = site / "tracks" / "server" / "static" / "app" / "api.js"
    original = asset.read_bytes()
    original_etag = etag
    try:
        asset.write_bytes(original + b"\n// v012 deploy marker\n")
        status, headers, body = http_request("GET", base + "/static/app/api.js")
        assert status == 200
        assert b"v012 deploy marker" in body, (
            "a plain refresh must return the new deploy bytes"
        )
        assert _headers_lower(headers).get("etag") != original_etag
    finally:
        asset.write_bytes(original)


# AC-FR0360-01@v0.12 TRACKS-TRACE api routes keep semantics without no-cache
def test_api_semantics_unchanged(prefix_serve):
    """AC-FR0360-01 (API half): API and health responses keep their status
    and payload semantics and are not touched by the static no-cache
    middleware."""
    base, _site = prefix_serve
    login = http_request(
        "POST",
        base + "/api/auth/login",
        headers={"Content-Type": "application/json"},
        data=b'{"password": "v012-test-password"}',
    )
    assert login[0] == 200, login[2][:200]
    cookies = _headers_lower(login[1]).get("set-cookie", "").split(";")[0]
    assert cookies

    status, headers, body = http_request(
        "GET", base + "/api/projects", headers={"Cookie": cookies}
    )
    assert status == 200, body[:200]
    assert "cache-control" not in _headers_lower(headers), (
        "API routes must not receive the static no-cache header"
    )

    status, headers, _body = http_request("GET", base + "/healthz")
    assert status == 200
    assert "cache-control" not in _headers_lower(headers)
