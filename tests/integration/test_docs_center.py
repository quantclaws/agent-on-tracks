"""Docs-center read model, save/conflict channel, and discussion projection
(IF-DOCCENTER-001, IF-DOCSAVE-001, IF-DOCREV-001, IF-DISCUSS-001).

Drives the documented HTTP outlets over a real serve subprocess
(interfaces §4a/§4c): the project-domain docs tree (version ordering,
six-piece set, editable_run_id), the versioned document read, the explicit
save channel through the revision binding, the 409 conflict face, and the
read-only discussion projection with its absent write endpoints.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests._support.v09_web import (
    http_get,
    http_post,
    login_session,
    parse_base_url,
    start_serve,
    stop_serve,
    wait_for_healthz,
    wait_for_port_line,
)

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS = "2026-09-27T00:00:00+00:00"
_TRIO = ("story.md", "spec.md", "acceptance.md")
_SIX_PIECE = _TRIO + ("architecture.md", "interfaces.md", "test-plan.md")

# A doc body carrying inline discussion threads (interfaces §1p / §3b): one
# open thread whose root asks @Human to adjudicate, one resolved thread, and
# one agent-rooted open thread — the parser is the server-side authority.
_DISCUSSION_DOC = """---
envelope: tracks-envelope:v2
---

# v1.0 spec

## 功能需求

### FR-0001 文档中心

文档中心读模型。

> **Lex:** 范围确认：六件套封闭集是否包含 test-plan？请 @Human 裁决。
> **Human:** 确认包含。

> **Sage:** 已按评审修订第二节。

> **Sage [RESOLVED]:** 术语统一完成。

> **Lex:** 请 @Sage 补充 diff 语义。
"""


def _start_docs_serve(tmp_path: Path):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("docs host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "docs@example.com"),
        ("config", "user.name", "Docs Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _seed_project(home: Path, repo: Path, project_id: str, version: str) -> None:
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_run(home: Path, repo: Path, run_id: str, version: str) -> None:
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, "story.requested", {"raw_chars": 1})
        store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()


def _write_version(repo: Path, version: str, docs) -> Path:
    vdir = repo / ".tracks" / "projects" / version
    vdir.mkdir(parents=True, exist_ok=True)
    for name in docs:
        (vdir / name).write_text(
            f"---\nenvelope: tracks-envelope:v2\n---\n\n"
            f"# {version} {name}\n\nfixture body for {name}\n",
            encoding="utf-8",
        )
    return vdir


def _trio_digest(vdir: Path) -> str:
    """The documented revision semantics recomputed from the fixture
    (``baseline.revision_digest``: sha256 over the labelled trio body hashes)."""
    from tracks.baseline import revision_digest

    return revision_digest(vdir)


def _post_json(base_url: str, path: str, payload: dict, *, cookies: str, csrf: str, key: str):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Cookie": cookies,
            "X-Trac-CSRF": csrf,
            "Idempotency-Key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# AC-FR0322-01@v0.10 TRACKS-TRACE tree version desc and six-piece read
def test_tree_version_desc_and_six_piece_read(tmp_path: Path):
    """AC-FR0322-01: the docs tree orders versions newest-first (a hotfix dir
    sorts after its baseline inside the same group), lists the six-piece
    closed set (missing members absent), recomputes each version's revision,
    and names the editable run; the versioned read returns content + history."""
    proc, home, repo = _start_docs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-docs-1"
        _seed_project(home, repo, "proj-docs-1", "v1.0")
        _seed_run(home, repo, run_id, "v1.0")
        _write_version(repo, "v1.0", _SIX_PIECE)
        _write_version(repo, "v0.9", _TRIO)
        _write_version(repo, "v0.9-hotfix-7", _TRIO)

        status, body = http_get(base, "/api/projects/proj-docs-1/docs/tree", cookies=cookie)
        assert status == 200, body[:200]
        tree = json.loads(body)
        versions = [row["version"] for row in tree["versions"]]
        assert versions == ["v1.0", "v0.9", "v0.9-hotfix-7"], (
            f"versions must sort newest-first with the hotfix dir after its baseline: {versions}"
        )
        top = tree["versions"][0]
        assert [doc["doc"] for doc in top["docs"]] == [
            "story",
            "spec",
            "acceptance",
            "architecture",
            "interfaces",
            "test-plan",
        ]
        assert top["docs"][0]["revision"] == _trio_digest(
            repo / ".tracks" / "projects" / "v1.0"
        )
        assert top["editable_run_id"] == run_id, (
            "the latest non-terminal run of the version is the editable entry"
        )
        hotfix = tree["versions"][2]
        assert [doc["doc"] for doc in hotfix["docs"]] == ["story", "spec", "acceptance"], (
            "missing six-piece members must not appear in the tree"
        )
        assert hotfix["editable_run_id"] is None

        # the versioned read: content from the fixture + revision + history
        status, body = http_get(
            base, "/api/projects/proj-docs-1/docs/v1.0/spec", cookies=cookie
        )
        assert status == 200, body[:200]
        read = json.loads(body)
        assert "fixture body for spec.md" in read["content"]
        assert read["revision"] == top["docs"][1]["revision"]
        assert isinstance(read["history"], list)

        # the six-piece domain: architecture/interfaces/test-plan resolve
        for doc in ("architecture", "interfaces", "test-plan"):
            status, body = http_get(
                base, f"/api/projects/proj-docs-1/docs/v1.0/{doc}", cookies=cookie
            )
            assert status == 200, f"{doc} must be readable: {body[:120]}"
    finally:
        stop_serve(proc)


# AC-FR0323-01@v0.10 TRACKS-TRACE save produces revision; stale approval rejected
def test_save_produces_revision_and_stale_approval_rejected(tmp_path: Path):
    """AC-FR0323-01: an explicit save through the revision channel produces a
    new revision for the six-piece doc set and audits the edit; an approval
    bound to the superseded revision is rejected — the binding is never
    bypassed."""
    proc, home, repo = _start_docs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-docs-save-1"
        _seed_project(home, repo, "proj-docs-1", "v1.0")
        _seed_run(home, repo, run_id, "v1.0")
        vdir = _write_version(repo, "v1.0", _SIX_PIECE)
        before = _trio_digest(vdir)

        status, body = _post_json(
            base,
            f"/api/runs/{run_id}/docs/interfaces/edits",
            {
                "base_revision": before,
                "content": "---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 interfaces\n\nedited body\n",
            },
            cookies=cookie,
            csrf=csrf,
            key="docs-save-1",
        )
        assert status == 202, f"the six-piece save must be accepted: {body[:200]}"
        receipt = json.loads(body)
        assert receipt["command_id"]
        assert receipt["new_revision"] and receipt["new_revision"] != before, (
            "the accepted edit must project a new revision"
        )

        # the supervisor applies the accepted command: the material moves and
        # the edit is audited on the service plane
        deadline = time.time() + 10
        applied = False
        while time.time() < deadline:
            if "edited body" in (vdir / "interfaces.md").read_text(encoding="utf-8"):
                applied = True
                break
            time.sleep(0.2)
        assert applied, "the accepted edit must be applied by the supervisor"
        assert _trio_digest(vdir) != before, "the revision must move past the base"

        # an approval bound to the superseded revision is rejected
        stale = _post_json(
            base,
            f"/api/runs/{run_id}/approvals",
            {
                "object": "spec",
                "expected_revision": before,
                "decision": "approve",
            },
            cookies=cookie,
            csrf=csrf,
            key="docs-save-stale-1",
        )
        assert stale[0] == 409, f"a stale approval must be refused: {stale[1][:200]}"
        assert json.loads(stale[1])["error"]["reason"] == "stale_revision"
    finally:
        stop_serve(proc)


# AC-FR0323-02@v0.10 TRACKS-TRACE conflict 409 carries current_revision, no overwrite
def test_conflict_409_two_options_and_draft_retained(tmp_path: Path):
    """AC-FR0323-02: a save whose base_revision went stale is refused with
    409 and the response carries the server's ``current_revision`` (the
    reload/force-overwrite decision input); nothing is silently overwritten."""
    proc, home, repo = _start_docs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-docs-conflict-1"
        _seed_project(home, repo, "proj-docs-1", "v1.0")
        _seed_run(home, repo, run_id, "v1.0")
        vdir = _write_version(repo, "v1.0", _TRIO)
        stale_base = "0" * 64

        status, body = _post_json(
            base,
            f"/api/runs/{run_id}/docs/spec/edits",
            {
                "base_revision": stale_base,
                "content": "---\nenvelope: tracks-envelope:v2\n---\n\n# v1.0 spec\n\nconflict probe\n",
            },
            cookies=cookie,
            csrf=csrf,
            key="docs-conflict-1",
        )
        assert status == 409, f"a stale base revision must be refused: {body[:200]}"
        error = json.loads(body)["error"]
        assert error["reason"] == "stale_revision"
        assert error.get("current_revision") == _trio_digest(vdir), (
            "the 409 must carry the server's current revision for the two "
            "conflict options (§1o.2)"
        )
        assert "conflict probe" not in (vdir / "spec.md").read_text(encoding="utf-8"), (
            "a refused save must never silently overwrite the material"
        )
    finally:
        stop_serve(proc)


# AC-FR0325-01@v0.10 TRACKS-TRACE discussions read model and nav state
def test_discussions_read_model_and_nav_state(tmp_path: Path):
    """AC-FR0325-01: the discussion projection returns the server-parsed
    thread read model — status closed set, initiator, anchors, summary and
    the derived awaiting party — for the versioned document."""
    proc, home, repo = _start_docs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-docs-discuss-1"
        _seed_project(home, repo, "proj-docs-1", "v1.0")
        _seed_run(home, repo, run_id, "v1.0")
        vdir = _write_version(repo, "v1.0", _SIX_PIECE)
        (vdir / "spec.md").write_text(_DISCUSSION_DOC, encoding="utf-8")

        status, body = http_get(
            base,
            "/api/projects/proj-docs-1/docs/v1.0/spec/discussions",
            cookies=cookie,
        )
        assert status == 200, body[:200]
        threads = json.loads(body)["threads"]
        by_status = {}
        for thread in threads:
            assert set(thread) >= {
                "thread_id",
                "status",
                "initiator",
                "anchor_line",
                "summary",
                "entry_line",
                "awaiting",
            }, f"thread schema drift: {thread!r}"
            assert thread["status"] in ("open", "resolved", "reopen")
            assert isinstance(thread["entry_line"], int) and thread["entry_line"] > 0
            by_status.setdefault(thread["status"], []).append(thread)

        assert by_status.get("resolved"), "the resolved thread must be projected"
        open_threads = by_status.get("open", [])
        assert open_threads, "the open threads must be projected"
        # the pending party is the protocol-derived awaiting value; a resolved
        # thread has no pending party
        human_waited = [
            t for t in open_threads if t["initiator"] == "Lex" and t["awaiting"] == "Human"
        ]
        assert human_waited, (
            "a thread whose root requests @Human must derive awaiting=Human"
        )
        for thread in by_status["resolved"]:
            assert thread["awaiting"] is None
    finally:
        stop_serve(proc)


# AC-FR0325-02@v0.10 TRACKS-TRACE no discussion mutation endpoint
def test_no_discussion_mutation_endpoint(tmp_path: Path):
    """AC-FR0325-02: the API exposes no discussion write-back endpoint — every
    discussion mutation path is refused — while the CLI write surface stays
    available (write-back belongs to ``trac discuss``)."""
    proc, home, repo = _start_docs_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        run_id = "run-docs-nowrite-1"
        _seed_project(home, repo, "proj-docs-1", "v1.0")
        _seed_run(home, repo, run_id, "v1.0")
        _write_version(repo, "v1.0", _SIX_PIECE)

        mutations = (
            ("/api/projects/proj-docs-1/docs/v1.0/spec/discussions/resolve", {"thread_id": "T-001"}),
            ("/api/projects/proj-docs-1/docs/v1.0/spec/discussions/reply", {"body": "hi"}),
            ("/api/projects/proj-docs-1/docs/v1.0/spec/discussions", {"body": "new"}),
        )
        for path, payload in mutations:
            status, body = http_post(base, path, payload, cookies=cookie, csrf=csrf, idempotency_key="nowrite-1")
            assert status in (404, 405), (
                f"{path} must not exist (no discussion write endpoint, §1p.3): {status} {body[:120]}"
            )

        # the CLI write surface remains the documented channel: the subcommand
        # is registered in the usage surface
        cli = subprocess.run(
            [sys.executable, "-m", "tracks.cli.main", "--help"],
            cwd=repo,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(_REPO_ROOT)},
        )
        assert "discuss" in (cli.stdout + cli.stderr).lower(), (
            "the CLI must keep the discuss write surface (§1p.3)"
        )
    finally:
        stop_serve(proc)
