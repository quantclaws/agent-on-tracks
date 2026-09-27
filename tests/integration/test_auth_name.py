"""Login shell + name binding end-to-end contracts (IF-AUTHNAME-001,
IF-WEBAUTH-001, IF-WORKBENCH-001).

Drives the documented HTTP/auth outlets over a real serve subprocess
(interfaces §4a/§4b): the login response fields, the name gate, the
name-binding endpoint and its validation, the service-plane audit event and
storage consequences, and the single-identity boundary.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_get,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration

_TS = "2026-09-27T00:00:00+00:00"


def _start_name_serve(tmp_path: Path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("name host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "name@example.com"),
        ("config", "user.name", "Name Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """An opener that surfaces the 302 itself instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NOFOLLOW = urllib.request.build_opener(_NoRedirect())


def _get_redirect(base_url: str, path: str, cookies: str = "") -> tuple[int, str]:
    """GET without following redirects: (status, Location) — the redirect
    contract outlet (§1m.2, §1f.3)."""
    req = urllib.request.Request(base_url + path, method="GET")
    if cookies:
        req.add_header("Cookie", cookies)
    try:
        with _NOFOLLOW.open(req, timeout=5) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")


def _lower_headers(headers) -> dict:
    """Case-insensitive header mapping (urllib lowercases on iteration)."""
    return {str(key).lower(): value for key, value in headers.items()}


def _post_capture(base_url: str, path: str, payload: dict, *, cookies: str = "", csrf: str = ""):
    """POST JSON, returning (status, headers, body) — the raw response the
    cookie-clearing contract is observed through."""
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if cookies:
        headers["Cookie"] = cookies
    if csrf:
        headers["X-Trac-CSRF"] = csrf
    req = urllib.request.Request(
        base_url + path, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, _lower_headers(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, _lower_headers(exc.headers), exc.read()


def _store(home: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(home / "service.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _events(home: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT type, payload FROM service_events ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    return [
        {"type": row["type"], "payload": json.loads(row["payload"])} for row in rows
    ]


# AC-FR0320-02@v0.10 TRACKS-TRACE unauthenticated redirect, logout clears session
def test_unauthenticated_redirect_and_logout_clears(tmp_path: Path):
    """AC-FR0320-02: unauthenticated page visits redirect to /login and API
    misses answer 401; after logout the server session row is deleted, the
    response clears the browser cookie, and the workbench requires a fresh
    login."""
    proc, home, _repo = _start_name_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)

        status, _headers, body = _post_capture(base, "/api/auth/login", {"password": "wrong"})
        assert status == 401, body[:200]
        status, location = _get_redirect(base, "/")
        assert status == 302 and location.rstrip("/").endswith("/login"), (
            f"an unauthenticated page visit must redirect to /login: {status} {location!r}"
        )
        status, body = http_get(base, "/api/projects")
        assert status == 401, body[:200]
        assert json.loads(body)["error"]["reason"] == "unauthenticated"

        cookie, csrf = login_session(base)
        status, _body = http_get(base, "/", cookies=cookie)
        assert status in (200, 302), "an authenticated page visit must not be refused"

        status, headers, body = _post_capture(
            base, "/api/auth/logout", {}, cookies=cookie, csrf=csrf
        )
        assert status == 204, body[:200]
        set_cookie = headers.get("set-cookie", "")
        cookie_name = set_cookie.split("=", 1)[0].strip() if set_cookie else ""
        assert cookie_name == "trac_session", (
            f"logout must clear the session cookie itself: {set_cookie!r}"
        )
        assert (
            "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()
            or "Expires=" in set_cookie
        ), f"the clearing cookie must expire the session cookie: {set_cookie!r}"

        with contextlib.closing(_store(home)) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert rows[0] == 0, "logout must delete the server-side session row"

        status, location = _get_redirect(base, "/", cookies=cookie)
        assert status == 302 and location.rstrip("/").endswith("/login"), (
            "a logged-out session must redirect to /login again: "
            f"{status} {location!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0318-03@v0.10 TRACKS-TRACE logout clears cookie, account menu only logout
def test_logout_clears_session_and_cookie(tmp_path: Path):
    """AC-FR0318-03: after logout the browser keeps no credential — the
    session row is gone, the cookie is expired, and no residual session
    resolves (a replayed cookie is unauthenticated)."""
    proc, home, _repo = _start_name_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        status, headers, _body = _post_capture(
            base, "/api/auth/logout", {}, cookies=cookie, csrf=csrf
        )
        assert status == 204

        # the browser's persisted cookie must no longer authenticate
        replayed = cookie.split(";", 1)[0]
        status, body = http_get(base, "/api/projects", cookies=replayed)
        assert status == 401, (
            "a replayed post-logout cookie must not resolve (§1m.4)"
        )
        # a fresh login is required to re-enter the workbench
        status, location = _get_redirect(base, "/", cookies=replayed)
        assert status == 302 and location.rstrip("/").endswith("/login"), (
            "the workbench must require a fresh login after logout: "
            f"{status} {location!r}"
        )
        with contextlib.closing(_store(home)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert row[0] == 0
        set_cookie = headers.get("set-cookie", "")
        assert set_cookie.split("=", 1)[0].strip() == "trac_session", (
            f"logout must clear the session cookie: {set_cookie!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0321-01@v0.10 TRACKS-TRACE name binding flows to events and audit actor
def test_name_binding_flows_to_events_and_discussion(tmp_path: Path):
    """AC-FR0321-01: a first login requires the display name; binding it
    persists ``auth.display_name``, updates the session actor, audits
    ``auth.name_bound``, surfaces through the profile outlet, and flows into
    the audit actor of subsequent web decisions; invalid names fail closed."""
    from tracks.paths import tracks_home
    from tracks.store import Store

    proc, home, repo = _start_name_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        status, _headers, body = _post_capture(
            base, "/api/auth/login", {"password": "v09-test-password"}
        )
        assert status == 200, body[:200]
        login = json.loads(body)
        assert login["name_required"] is True, (
            "a first login must declare name_required (§2b #1, §1m.2)"
        )
        cookie = _headers.get("set-cookie", "").split(";", 1)[0]
        csrf = login["csrf_token"]

        # the name gate: a session without a display name cannot enter
        status, _b = http_get(base, "/", cookies=cookie)
        assert status == 302, "an un-named session must not enter the workbench data face"

        # invalid names fail closed (validation_failed), auth table unchanged
        for bad in ("", "   ", "x" * 65, "bad\x07name"):
            status, _h, body = _post_capture(
                base,
                "/api/auth/name",
                {"name": bad},
                cookies=cookie,
                csrf=csrf,
            )
            assert status == 400, f"name {bad!r} must be validation_failed: {body[:120]}"
            assert json.loads(body)["error"]["reason"] == "validation_failed"

        # the valid name binds
        status, _h, body = _post_capture(
            base, "/api/auth/name", {"name": "  Shell Human  "}, cookies=cookie, csrf=csrf
        )
        assert status == 200, body[:200]
        assert json.loads(body)["actor"] == "Shell Human"

        # storage consequences: display_name persisted, session actor updated,
        # the audit event landed with the effective actor + http surface
        with contextlib.closing(_store(home)) as conn:
            auth = conn.execute("SELECT actor, display_name FROM auth LIMIT 1").fetchone()
            session = conn.execute("SELECT actor FROM sessions LIMIT 1").fetchone()
        assert auth["display_name"] == "Shell Human"
        assert session["actor"] == "Shell Human"
        bound = [e for e in _events(home) if e["type"] == "auth.name_bound"]
        assert bound, "the binding must audit auth.name_bound (§1a #25)"
        assert bound[-1]["payload"]["actor"] == "Shell Human"
        assert bound[-1]["payload"]["surface"] == "http"

        # the profile outlet reports the effective actor
        status, body = http_get(base, "/api/auth/profile", cookies=cookie)
        assert status == 200, body[:200]
        profile = json.loads(body)
        assert profile["actor"] == "Shell Human"
        assert profile["name_set"] is True

        # the workbench entry opens once the name is bound
        status, _b = http_get(base, "/", cookies=cookie)
        assert status == 200, "a named session must enter the workbench"

        # the effective actor flows into subsequent web-decision audit events
        conn = sqlite3.connect(str(home / "service.db"))
        try:
            conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?)",
                ("proj-name-1", str(repo), "v1.0", "local-user", _TS),
            )
            conn.commit()
        finally:
            conn.close()
        run_id = "run-name-1"
        store = Store(tracks_home(repo))
        try:
            store.append(run_id, "v1.0", "story.requested", {"raw_chars": 1})
            store.append(run_id, "v1.0", "stage.entered", {"stage": "M-IMPL"})
        finally:
            store.close()
        req = urllib.request.Request(
            base + f"/api/runs/{run_id}/docs/spec/edits",
            data=json.dumps({"base_revision": "rev-0", "content": "actor probe\n"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-Trac-CSRF": csrf,
                "Idempotency-Key": "name-actor-1",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                edit_status = resp.status
        except urllib.error.HTTPError as exc:
            edit_status = exc.code
        assert edit_status in (202, 409), "the authenticated edit must be accepted"
        accepted = [
            e
            for e in _events(home)
            if e["type"] == "command.accepted" and e["payload"].get("kind") == "edit_material"
        ]
        assert accepted, "the accepted edit must be audited on the service plane"
        assert accepted[-1]["payload"]["actor"] == "Shell Human", (
            "web decisions must audit the effective display name as actor (§1m.3)"
        )
    finally:
        stop_serve(proc)


# AC-FR0321-02@v0.10 TRACKS-TRACE no registration surface, single identity
def test_no_registration_surface_single_identity(tmp_path: Path):
    """AC-FR0321-02: no registration or multi-account surface exists; the name
    binding annotates the same single user instead of creating a second
    identity (auth table stays single-row)."""
    proc, home, _repo = _start_name_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)

        for path in (
            "/api/auth/register",
            "/api/auth/signup",
            "/api/auth/users",
            "/api/auth/accounts",
        ):
            status, _h, _b = _post_capture(
                base, path, {"password": "x"}, cookies=cookie, csrf=csrf
            )
            assert status in (404, 405), (
                f"{path} must not exist (no registration surface, §1m.3)"
            )

        status, _h, body = _post_capture(
            base, "/api/auth/name", {"name": "Single Identity"}, cookies=cookie, csrf=csrf
        )
        assert status == 200, body[:200]
        with contextlib.closing(_store(home)) as conn:
            rows = conn.execute("SELECT COUNT(*) FROM auth").fetchone()
        assert rows[0] == 1, (
            "the display name annotates the same single user, never a second identity"
        )
    finally:
        stop_serve(proc)
