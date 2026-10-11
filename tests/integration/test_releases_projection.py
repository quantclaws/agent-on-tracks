"""Releases window aggregation projection (IF-RELEASES-001, IF-BACKLOG-001,
IF-INTAKE-001).

Covers the v0.11 Releases read model through the documented outlet (§2b #36
``GET /api/projects/{pid}/releases``) over a real serve subprocess: the
version-grouped run registry, the backlog fold-over group derived from
``backlog.recorded`` events (§1d.3), the physically separate archive group
backed by the service-plane intakes rows (§1h.2), and the disposition
flow's ``backlog.dispositioned`` truth (§1d.1/§1h.3).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from tests._support.intake_fence import wait_intake_terminal
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
from tracks.paths import tracks_home
from tracks.store import Store, new_ulid

pytestmark = pytest.mark.integration

_TS = "2026-10-11T00:00:00+00:00"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _start_releases_serve(tmp_path: Path):
    """Real serve subprocess over an isolated home + host repo (§6.4 row 1)."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("releases host\n", encoding="utf-8")
    # the runtime area stays out of VCS so the M-START clean-tree premise (§1g.1)
    # holds no matter how many fixture events the store appends
    (repo / ".gitignore").write_text(".tracks/\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "releases@example.com"),
        ("config", "user.name", "Releases Human"),
        ("add", "README.md", ".gitignore"),
        ("commit", "-m", "initial"),
    ):
        _git(repo, *args)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _register(home: Path, repo: Path, project_id: str, version: str) -> None:
    """The service-plane projects row the projection resolves (§1c table 2)."""
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), version, "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _append(repo: Path, run_id: str, version: str, kind: str, payload: dict) -> None:
    """Append one run-plane event through the real store (§1.1 outlet)."""
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, kind, payload)
    finally:
        store.close()


def _seed_registry_runs(repo: Path) -> list[str]:
    """Two runs across two versions (§1e.2 versions)."""
    run_a = new_ulid()
    run_b = new_ulid()
    _append(repo, run_a, "v1.0", "story.requested", {"raw_chars": 9})
    _append(repo, run_a, "v1.0", "stage.entered", {"stage": "M-STORY"})
    _append(repo, run_b, "v2.0", "story.requested", {"raw_chars": 9})
    _append(repo, run_b, "v2.0", "stage.entered", {"stage": "M-IMPL"})
    return [run_a, run_b]


def _seed_backlog_entries(repo: Path) -> list[str]:
    """Two backlog entries in the v0.11 payload form (§1d.2 optional keys)."""
    entry_a = new_ulid()
    entry_b = new_ulid()
    _append(
        repo,
        entry_a,
        "v1.0",
        "backlog.recorded",
        {"reason": "active_run", "source": "text", "content": "排队条目甲", "intake_id": None},
    )
    _append(
        repo,
        entry_b,
        "v1.0",
        "backlog.recorded",
        {"reason": "active_run", "source": "issue", "content": "排队条目乙", "intake_id": None},
    )
    return [entry_a, entry_b]


def _releases(base_url: str, cookie: str, project_id: str) -> dict:
    status, body = http_get(
        base_url, f"/api/projects/{project_id}/releases", cookies=cookie
    )
    assert status == 200, f"the releases aggregation must resolve: {status} {body[:200]}"
    return json.loads(body)


# AC-FR0333-01@v0.11 TRACKS-TRACE registry and backlog groups with summary row
def test_registry_backlog_groups(tmp_path: Path):
    """AC-FR0333-01: the releases aggregation presents the version-grouped run
    registry (newest version first) and the backlog fold-over group, while the
    registered project and version info stay a summary row — never a sidebar
    listing of other tabs' sub-content."""
    proc, home, repo = _start_releases_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        _register(home, repo, "proj-rel-1", "v2.0")
        run_a, run_b = _seed_registry_runs(repo)
        entry_a, entry_b = _seed_backlog_entries(repo)

        payload = _releases(base, cookie, "proj-rel-1")
        # summary row: the window's identity facts only (§1e.2)
        assert payload["summary"] == {
            "project_id": "proj-rel-1",
            "registered_version": "v2.0",
            "repo_name": repo.name,
        }, f"the summary row must carry exactly the window facts: {payload.get('summary')!r}"
        # versions descend by version number; runs carry the run-face fields
        versions = payload["versions"]
        assert [row["version"] for row in versions] == ["v2.0", "v1.0"], (
            f"versions must sort descending by version number: {versions!r}"
        )
        by_version = {row["version"]: row for row in versions}
        assert [run["run_id"] for run in by_version["v1.0"]["runs"]] == [run_a]
        assert [run["run_id"] for run in by_version["v2.0"]["runs"]] == [run_b]
        v2_run = by_version["v2.0"]["runs"][0]
        assert v2_run["stage"] == "M-IMPL", f"run rows project the stage: {v2_run!r}"
        for field in ("status", "awaiting", "updated_ts"):
            assert field in v2_run, f"run rows carry {field} (§1e.2): {v2_run!r}"
        # backlog fold-over group: entries aggregate by ULID with disposition
        backlog = payload["backlog"]
        assert backlog["count"] == 2, f"both seeded entries project: {backlog!r}"
        entries = {row["entry_id"]: row for row in backlog["entries"]}
        assert set(entries) == {entry_a, entry_b}, (
            f"backlog entries aggregate from backlog.recorded events: {entries!r}"
        )
        assert entries[entry_a]["source"] == "text", (
            f"the v0.11 payload source must surface: {entries[entry_a]!r}"
        )
        assert entries[entry_a]["content"] == "排队条目甲", (
            f"the v0.11 payload content must surface: {entries[entry_a]!r}"
        )
        assert entries[entry_b]["source"] == "issue"
        for row in entries.values():
            assert row["disposition"] is None, (
                f"an undisposed entry has no disposition yet: {row!r}"
            )
    finally:
        stop_serve(proc)


# AC-FR0333-02@v0.11 TRACKS-TRACE archive group distinct and queryable
def test_archive_group_distinct_queryable(tmp_path: Path):
    """AC-FR0333-02: archived entries surface in their own archive group,
    distinct from the backlog fold-over group, queryable but outside
    development (no promote/develop entry exists for them)."""
    proc, home, repo = _start_releases_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-rel-2", "v1.0")
        _seed_backlog_entries(repo)

        # the archive fixture walks the public intake face: submit then archive
        status, body = http_post(
            base,
            "/api/projects/proj-rel-2/intakes",
            {"source": "text", "content": "归档候选：仅存档不开发"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-archive-submit-1",
        )
        assert status == 202, (
            f"the unified intake entry must accept a text submission: {status} {body[:200]}"
        )
        submitted = json.loads(body)
        intake_id = submitted["intake_id"]

        # decide_intake's precondition is an evaluated intake (§1b #15):
        # fence the supervisor evaluation to its terminal state first
        evaluated = wait_intake_terminal(base, cookie, "proj-rel-2", intake_id)
        assert evaluated["status"] == "evaluated", (
            f"the fake-backend evaluation must land evaluated: {evaluated!r}"
        )

        status, body = http_post(
            base,
            f"/api/intakes/{intake_id}/decisions",
            {"action": "archive"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-archive-decide-1",
        )
        assert status == 202, (
            f"the archive action must be accepted: {status} {body[:200]}"
        )

        payload = _releases(base, cookie, "proj-rel-2")
        archive = payload["archive"]
        assert isinstance(archive, list) and archive, (
            f"the archive group must list the archived intake: {archive!r}"
        )
        assert any(row.get("intake_id") == intake_id for row in archive), (
            f"the archived intake must be queryable in the archive group: {archive!r}"
        )
        archived_row = next(row for row in archive if row["intake_id"] == intake_id)
        assert archived_row.get("content") == "归档候选：仅存档不开发", (
            f"the archive row carries the intake content: {archived_row!r}"
        )
        # distinct storage: the archived intake never joins the backlog group
        backlog_ids = {row["entry_id"] for row in payload["backlog"]["entries"]}
        assert intake_id not in backlog_ids, (
            "the archive group and the backlog fold-over group stay distinct (§1h.2)"
        )
    finally:
        stop_serve(proc)


# AC-FR0340-01@v0.11 TRACKS-TRACE backlog storage and archive distinct
def test_backlog_storage_and_archive_distinct(tmp_path: Path):
    """AC-FR0340-01: the backlog truth is the run-plane ``backlog.recorded``
    event stream and the archive truth lives on the service plane (intakes
    rows + the ``intake.decided`` audit) — two physical stores, one
    aggregation face, no second backlog storage."""
    proc, home, repo = _start_releases_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-rel-3", "v1.0")
        entry_a, _entry_b = _seed_backlog_entries(repo)

        # an archived intake exists only on the service plane
        status, body = http_post(
            base,
            "/api/projects/proj-rel-3/intakes",
            {"source": "text", "content": "归档侧条目"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-storage-submit-1",
        )
        assert status == 202, f"submit must be accepted: {status} {body[:200]}"
        intake_id = json.loads(body)["intake_id"]
        wait_intake_terminal(base, cookie, "proj-rel-3", intake_id)
        status, body = http_post(
            base,
            f"/api/intakes/{intake_id}/decisions",
            {"action": "archive"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-storage-decide-1",
        )
        assert status == 202, f"archive decision must be accepted: {status} {body[:200]}"

        # the run-plane backlog truth: the event store carries the recorded
        # entries and no disposition has touched them
        store = Store(tracks_home(repo))
        try:
            recorded = [
                event for event in store.events(entry_a) if event.type == "backlog.recorded"
            ]
        finally:
            store.close()
        assert recorded, "the backlog entry's truth is its backlog.recorded event"
        assert recorded[0].payload.get("source") == "text", (
            f"the v0.11 payload keys ride the event: {recorded[0].payload!r}"
        )

        payload = _releases(base, cookie, "proj-rel-3")
        backlog_ids = {row["entry_id"] for row in payload["backlog"]["entries"]}
        assert entry_a in backlog_ids, "the event-backed entry projects in backlog"
        assert intake_id not in backlog_ids, (
            "the archived intake must not appear in the backlog group (physical split)"
        )
        assert any(
            row.get("intake_id") == intake_id for row in payload["archive"]
        ), "the archived intake is queryable only in the archive group"
    finally:
        stop_serve(proc)


# AC-FR0340-02@v0.11 TRACKS-TRACE disposition recorded and promote flow
def test_disposition_recorded_and_promote_flow(tmp_path: Path):
    """AC-FR0340-02: removing or promoting a backlog entry records the
    disposition (``backlog.dispositioned``: removed/promoted + to); the
    retained row carries the marker while the folded count drops to the
    undispositioned only (§1d.3 — the whereabouts stay queryable); the
    promote path re-enters the new-release flow through the unified entry."""
    proc, home, repo = _start_releases_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-rel-4", "v1.0")
        _seed_registry_runs(repo)
        entry_a, entry_b = _seed_backlog_entries(repo)

        # remove entry_a through the disposition endpoint (§2b #41)
        status, body = http_post(
            base,
            f"/api/projects/proj-rel-4/backlog/{entry_a}/dispositions",
            {"disposition": "removed"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-dispose-1",
        )
        assert status == 202, (
            f"the remove disposition must be accepted: {status} {body[:200]}"
        )
        # the removal rides the worker — poll the public #36 face, then pin
        # the locked fold: the retained row carries the marker (§1d.3 R pin:
        # entries keep disposed rows, count folds to undispositioned only)
        deadline = time.monotonic() + 15.0
        while True:
            payload = _releases(base, cookie, "proj-rel-4")
            rows = {row["entry_id"]: row for row in payload["backlog"]["entries"]}
            removed_row = rows.get(entry_a)
            if removed_row is not None and removed_row["disposition"] == "removed":
                break
            if time.monotonic() > deadline:
                raise AssertionError(
                    "the removal never landed on the backlog projection within "
                    f"15s: count={payload['backlog'].get('count')} rows={rows!r}"
                )
            time.sleep(0.2)
        assert rows[entry_b]["disposition"] is None, (
            f"the untouched entry stays the open control row: {rows[entry_b]!r}"
        )
        assert payload["backlog"]["count"] == 1, (
            "the folded count only counts undispositioned entries (§1d.3): "
            f"{payload['backlog']!r}"
        )

        # promote half: the develop outcome needs a boundary-free project —
        # an in-flight seeded run would make the §1g.1a active-run
        # backlog_recorded outcome the legal develop terminal, while the
        # promoted disposition only rides develop success (§1f.3 SM-01), so
        # the unified-entry flow (§1f.1 转正 = submit_intake(source=backlog,
        # origin_entry)) runs on its own repo with no runs in flight
        repo_b = tmp_path / "repo-promote"
        repo_b.mkdir()
        (repo_b / "README.md").write_text("promote host\n", encoding="utf-8")
        (repo_b / ".gitignore").write_text(".tracks/\n", encoding="utf-8")
        for args in (
            ("init", "-b", "main"),
            ("config", "user.email", "releases@example.com"),
            ("config", "user.name", "Releases Human"),
            ("add", "README.md", ".gitignore"),
            ("commit", "-m", "initial"),
        ):
            _git(repo_b, *args)
        _register(home, repo_b, "proj-rel-5", "v1.0")
        entry_p = new_ulid()
        _append(
            repo_b,
            entry_p,
            "v1.0",
            "backlog.recorded",
            {
                "reason": "active_run",
                "source": "issue",
                "content": "转正候选条目",
                "intake_id": None,
            },
        )

        # the unified entry re-submits the backlog row as a backlog-source
        # intake and the develop action rides the evaluated precondition
        # (§1b #14/#15)
        status, body = http_post(
            base,
            "/api/projects/proj-rel-5/intakes",
            {"source": "backlog", "origin_entry": entry_p},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-promote-submit-1",
        )
        assert status == 202, (
            f"the backlog-source resubmission must be accepted: {status} {body[:200]}"
        )
        promote_intake = json.loads(body)["intake_id"]
        wait_intake_terminal(base, cookie, "proj-rel-5", promote_intake)
        status, body = http_post(
            base,
            f"/api/intakes/{promote_intake}/decisions",
            {"action": "develop"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="rel-promote-decide-1",
        )
        assert status == 202, (
            f"the develop decision must be accepted: {status} {body[:200]}"
        )
        # develop success disposes the origin entry promoted (§1f.3 SM-01:
        # 转正 = submit_intake(backlog) 走新建流程, develop 成功后处置原条目) —
        # the disposition rides the worker, so poll the public #36 face
        deadline = time.monotonic() + 90.0
        while True:
            promote_payload = _releases(base, cookie, "proj-rel-5")
            promote_rows = {
                row["entry_id"]: row for row in promote_payload["backlog"]["entries"]
            }
            promoted_row = promote_rows.get(entry_p)
            if promoted_row is not None and promoted_row["disposition"] == "promoted":
                break
            if time.monotonic() > deadline:
                raise AssertionError(
                    "the promoted disposition never landed on the backlog "
                    "projection within 90s although develop completed (decided "
                    "+ run created): count="
                    f"{promote_payload['backlog'].get('count')} rows={promote_rows!r}"
                )
            time.sleep(0.5)

        # the removed entry keeps its row with the removal marker — the
        # whereabouts stay queryable, only the folded count drops it (§1d.3)
        assert promoted_row["to"], (
            "the promoted entry keeps its whereabouts queryable (§1d.3): "
            f"{promoted_row!r}"
        )
        assert promote_payload["backlog"]["count"] == 0, (
            "the folded count only counts undispositioned entries (§1d.3): "
            f"{promote_payload['backlog']!r}"
        )
    finally:
        stop_serve(proc)
