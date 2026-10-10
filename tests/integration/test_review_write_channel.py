"""Review-round write channel (IF-DOCSWRITE-001, IF-DOCSAVE-001).

The v0.11 review-round writes through the command plane: the run-domain edit
face (#17) with the pre-approval null-base mode (§1n.2), and the
inline-comment extension (#18) whose ``anchor_line``/``thread_token`` pair is
strictly exclusive and whose anchor mode opens a traceable root thread
(§1n.3) — while the /discussions path stays write-free (v0.10 §1p.3).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = pytest.mark.integration

_TS = "2026-10-11T00:00:00+00:00"

_TRIO = ("story.md", "spec.md", "acceptance.md")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _start_channel_serve(tmp_path: Path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("channel host\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".tracks/\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "channel@example.com"),
        ("config", "user.name", "Channel Human"),
        ("add", "README.md", ".gitignore"),
        ("commit", "-m", "initial"),
    ):
        _git(repo, *args)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _register(home: Path, repo: Path, project_id: str, version: str) -> None:
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_run(repo: Path, run_id: str, version: str) -> None:
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, "story.requested", {"raw_chars": 9})
        store.append(run_id, version, "stage.entered", {"stage": "M-SPEC"})
    finally:
        store.close()


def _write_docs(repo: Path, version: str, docs: tuple[str, ...]) -> Path:
    vdir = repo / ".tracks" / "projects" / version
    vdir.mkdir(parents=True, exist_ok=True)
    for name in docs:
        (vdir / name).write_text(
            f"---\nenvelope: tracks-envelope:v2\n---\n\n# {version} {name}\n\nreview body\n",
            encoding="utf-8",
        )
    return vdir


def _post(base: str, path: str, payload: dict, cookie: str, csrf: str, key: str):
    return http_post(
        base, path, payload, cookies=cookie, csrf=csrf, idempotency_key=key
    )


def _wait_content(path: Path, needle: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and needle in path.read_text(encoding="utf-8"):
            return True
        time.sleep(0.2)
    return False


# AC-FR0344-01@v0.11 TRACKS-TRACE review round edit and inline comments land
def test_review_round_edit_and_comments(tmp_path: Path):
    """AC-FR0344-01: in the review round the reviewed document accepts direct
    edits and inline comments through the command plane — the edit lands the
    command audit, the ``anchor_line`` clarification opens a new root thread
    on the anchored line (parseable on the document), and the
    ``anchor_line``/``thread_token`` pair is strictly exclusive (both or
    neither is refused 422 with no discussion write)."""
    proc, home, repo = _start_channel_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-chan-1", "v1.0")
        run_id = "run-chan-1"
        _seed_run(repo, run_id, "v1.0")
        vdir = _write_docs(repo, "v1.0", _TRIO)
        spec = vdir / "spec.md"

        # the review-round edit lands through the command face
        from tracks.baseline import revision_digest

        current = revision_digest(vdir)
        status, body = _post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {
                "base_revision": current,
                "content": "---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nreview-round edit\n",
            },
            cookie,
            csrf,
            "chan-edit-1",
        )
        assert status == 202, f"the review-round edit must be accepted: {body[:200]}"
        assert _wait_content(spec, "review-round edit"), (
            "the accepted edit must be applied to the reviewed document"
        )
        # the write is auditable on the command plane (§4b.1): the accepted
        # edit_material command rides service_events
        deadline = time.time() + 15
        audited = False
        while time.time() < deadline and not audited:
            conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT payload FROM service_events WHERE type = 'command.accepted'"
                ).fetchall()
            finally:
                conn.close()
            audited = any(
                json.loads(row[0]).get("kind") == "edit_material" for row in rows
            )
            if not audited:
                time.sleep(0.3)
        assert audited, (
            "the review-round edit must be auditable on the command plane "
            "(command.accepted, §4b.1)"
        )

        # inline comment via anchor_line: opens a new root thread
        status, body = _post(
            base,
            f"/api/runs/{run_id}/clarifications",
            {
                "doc": "spec",
                "anchor_line": "review-round edit",
                "body": "这一段的验收边界需要明确。",
            },
            cookie,
            csrf,
            "chan-comment-1",
        )
        assert status in (200, 202), (
            f"the anchor_line comment must be accepted: {status} {body[:200]}"
        )
        from tracks.discuss.parser import parse_threads

        def _thread_opened() -> bool:
            threads = parse_threads(spec.read_text(encoding="utf-8"))
            return any(
                thread.status == "open"
                and "验收边界" in thread.root_text
                for thread in threads
            )

        deadline = time.time() + 15
        opened = False
        while time.time() < deadline:
            if _thread_opened():
                opened = True
                break
            time.sleep(0.2)
        assert opened, (
            "the anchor_line comment must open a parseable root thread on the "
            "anchored line (§1n.3)"
        )

        # the pair is strictly exclusive: both given → 422, no write
        threads_before = len(parse_threads(spec.read_text(encoding="utf-8")))
        status, body = _post(
            base,
            f"/api/runs/{run_id}/clarifications",
            {
                "doc": "spec",
                "anchor_line": "review-round edit",
                "thread_token": "T-001",
                "body": "both given",
            },
            cookie,
            csrf,
            "chan-comment-both-1",
        )
        assert status == 422, (
            f"anchor_line and thread_token together must be refused: {status} {body[:200]}"
        )
        # neither given → 422 as well
        status, body = _post(
            base,
            f"/api/runs/{run_id}/clarifications",
            {"doc": "spec", "body": "neither given"},
            cookie,
            csrf,
            "chan-comment-neither-1",
        )
        assert status == 422, (
            f"a clarification with neither anchor nor thread must be refused: "
            f"{status} {body[:200]}"
        )
        threads_after = len(parse_threads(spec.read_text(encoding="utf-8")))
        assert threads_after == threads_before, (
            "refused clarifications must not write discussion content"
        )

        # the /discussions path stays write-free (v0.10 §1p.3 boundary)
        status, body = _post(
            base,
            "/api/discussions",
            {"doc": "spec", "body": "nope"},
            cookie,
            csrf,
            "chan-discussions-write-1",
        )
        assert status in (404, 405), (
            f"the /discussions path must expose no write endpoint: {status} {body[:200]}"
        )
    finally:
        stop_serve(proc)


# AC-FR0344-02@v0.11 TRACKS-TRACE pre approval null base not blocked, null bound when available
def test_pre_approval_empty_baseline_not_blocked(tmp_path: Path):
    """AC-FR0344-02: in the pre-approval window (the trio digest structurally
    cannot exist — spec/acceptance not yet written) a save with
    ``base_revision: null`` is accepted and audited with the null from
    revision; once the digest is available the null base is refused
    ``stale_revision`` — the binding can never be bypassed."""
    proc, home, repo = _start_channel_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-chan-2", "v1.0")
        run_id = "run-chan-pre-1"
        _seed_run(repo, run_id, "v1.0")
        # pre-approval premise: the version dir carries the story only —
        # spec/acceptance do not exist, so the trio digest is unavailable
        vdir = _write_docs(repo, "v1.0", ("story.md",))

        status, body = _post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {
                "base_revision": None,
                "content": "---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nfirst draft\n",
            },
            cookie,
            csrf,
            "chan-pre-null-1",
        )
        assert status == 202, (
            f"the pre-approval null-base save must not be blocked: "
            f"{status} {body[:200]}"
        )
        spec = vdir / "spec.md"
        assert _wait_content(spec, "first draft"), (
            "the accepted null-base save must apply (the review round starts "
            "from an empty baseline; §1n.2 creates the document)"
        )
        # the null base is recorded on the command-plane event (§1n.2: the
        # material.edited from_revision is null) — the service.db outlet
        deadline = time.time() + 15
        edited_payloads: list[dict] = []
        while time.time() < deadline:
            conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT payload FROM service_events WHERE type = 'material.edited'"
                ).fetchall()
            finally:
                conn.close()
            edited_payloads = [json.loads(row[0]) for row in rows]
            if any(payload.get("from_revision") is None for payload in edited_payloads):
                break
            time.sleep(0.3)
        assert any(
            payload.get("from_revision") is None for payload in edited_payloads
        ), (
            f"the null base must be recorded as the from revision on the "
            f"material.edited event (§1n.2): {edited_payloads!r}"
        )

        # once the trio digest exists, the null base is refused
        from tracks.baseline import revision_digest

        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if revision_digest(vdir):
                    break
            except OSError:
                pass
            time.sleep(0.2)
        status, body = _post(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {
                "base_revision": None,
                "content": "---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nsecond draft\n",
            },
            cookie,
            csrf,
            "chan-pre-null-2",
        )
        assert status == 409, (
            f"an available digest must refuse the null base: {status} {body[:200]}"
        )
        assert json.loads(body)["error"]["reason"] == "stale_revision", body[:200]
    finally:
        stop_serve(proc)
