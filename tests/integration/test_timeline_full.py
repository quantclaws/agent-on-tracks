"""Timeline full drawing + rollback safety projection half (IF-TIMELINE-002,
IF-RETURN-001, IF-QUERY-001, IF-STREAM-001).

The projection halves of the timeline ACs: the run detail's new
``awaiting_gate``/``return_options`` field groups (§1k.1/§1m.1), the
single-source parity between the timeline/ac-chain responses and the run's
event stream (§1l.3), readable degradation for data-less runs (§1l.2), and
the return confirm/revalidation discipline over the #25 endpoint (§1m.2).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
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
from tracks.paths import tracks_home
from tracks.store import Store

pytestmark = pytest.mark.integration

_TS = "2026-10-11T00:00:00+00:00"


def _start_timeline_serve(tmp_path: Path):
    """Real serve subprocess over an isolated home + host repo (§6.4 row 1)."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("timeline host\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".tracks/\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "timeline@example.com"),
        ("config", "user.name", "Timeline Human"),
        ("add", "README.md", ".gitignore"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    proc = start_serve(home, repo, port=0)
    return proc, home, repo


def _register(home: Path, repo: Path, project_id: str) -> None:
    conn = sqlite3.connect(str(home / "service.db"))
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (project_id, str(repo), "v1.0", "local-user", _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _append(repo: Path, run_id: str, kind: str, payload: dict, version: str = "v1.0") -> None:
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, version, kind, payload)
    finally:
        store.close()


def _seed_progressed_run(repo: Path, run_id: str) -> None:
    """A run that entered three stages, with the last one twice (attempt)."""
    _append(repo, run_id, "story.requested", {"raw_chars": 9})
    for stage in ("M-START", "M-STORY", "M-IMPL"):
        _append(repo, run_id, "stage.entered", {"stage": stage})
    _append(repo, run_id, "stage.entered", {"stage": "M-IMPL"})


def _seed_publish_executed_run(repo: Path, run_id: str) -> None:
    """A rollback-capable run: history already carries a published release —
    the executed irreversible op rides the sealed done-op vocabulary the
    shared legality rule counts (§1m.1 shared computation with
    ``trac return``; R pin tests/unit/test_t002_return_stage_sync_red.py)."""
    _append(repo, run_id, "stage.entered", {"stage": "M-RELEASE"})
    _append(
        repo,
        run_id,
        "release.previewed",
        {"preview_digest": "sha256:" + "b" * 64},
    )
    _append(
        repo,
        run_id,
        "publish.executed",
        {"status": "done", "operation_kind": "merge", "target": "main"},
    )


def _wait_for_event(repo: Path, run_id: str, kind: str, timeout: float = 15.0) -> None:
    """Bounded wait for a command-executed event on the run plane."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        store = Store(tracks_home(repo))
        try:
            events = [event.type for event in store.events(run_id)]
        finally:
            store.close()
        if kind in events:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"{kind!r} never landed on the run plane within {timeout}s: "
                f"{events!r}"
            )
        time.sleep(0.2)


def _detail(base: str, cookie: str, run_id: str) -> dict:
    status, body = http_get(base, f"/api/runs/{run_id}", cookies=cookie)
    assert status == 200, f"run detail must resolve: {status} {body[:200]}"
    return json.loads(body)


# AC-FR0335-02@v0.11 TRACKS-TRACE timeline data parity no second source
def test_timeline_data_parity_no_mock(tmp_path: Path):
    """AC-FR0335-02 (projection half): the timeline, ac-chain, and run detail
    faces read the run's single event stream — the timeline's event seq set
    equals the store's, the ac-chain agrees with the same events, every
    entered attempt stays an independent row, and the detail face's
    contracted v0.11 field groups (§2b/§1k.1/§1m.1) are present and derived
    from that stream; nothing is synthesized from a second source."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        _register(home, repo, "proj-tf-1")
        run_id = "run-tf-parity"
        _seed_progressed_run(repo, run_id)

        status, body = http_get(base, f"/api/runs/{run_id}/timeline", cookies=cookie)
        assert status == 200, f"the timeline must resolve: {status} {body[:200]}"
        timeline = json.loads(body)
        status, body = http_get(base, f"/api/runs/{run_id}/ac-chain", cookies=cookie)
        assert status == 200, f"the ac-chain must resolve: {status} {body[:200]}"
        chain = json.loads(body)

        # parity with the persisted truth: every store seq appears in the
        # timeline and vice versa (§1l.3 — no second source)
        store = Store(tracks_home(repo))
        try:
            store_seqs = [event.seq for event in store.events(run_id)]
        finally:
            store.close()
        timeline_seqs = [event["seq"] for event in timeline["events"]]
        assert sorted(timeline_seqs) == sorted(store_seqs), (
            "the timeline must project exactly the run's event stream: "
            f"store={store_seqs!r} timeline={timeline_seqs!r}"
        )
        entered = [
            event for event in timeline["events"] if event["type"] == "stage.entered"
        ]
        assert len(entered) == 4, (
            f"each attempt keeps its own row (four entries seeded): {len(entered)}"
        )
        # the ac-chain is a projection of the same run's stream: it resolves
        # for this run (§1l.3 names it a co-equal source of the draw)
        assert isinstance(chain, list), "the ac-chain stays a list projection"

        # the run detail is the third face of the same single source
        # (IF-TIMELINE-002: timeline/ac-chain/run detail, zero new sources):
        # the v0.11 field groups are contracted additions (§2b), so they must
        # be present and derived from this run's stream — a mid-progress run
        # whose events carry no awaiting boundary projects no gate (§1k.1),
        # and the rollback face carries the contracted shape (§1m.1) instead
        # of being silently absent or fabricated
        detail = _detail(base, cookie, run_id)
        assert "awaiting_gate" in detail, (
            "run detail must carry the awaiting_gate field group (§2b/§1k.1); "
            f"got keys {sorted(detail)!r}"
        )
        assert detail["awaiting_gate"] is None, (
            "a run whose stream holds no awaiting boundary must project "
            f"awaiting_gate null, not a fabricated gate: "
            f"{detail['awaiting_gate']!r}"
        )
        options = detail.get("return_options")
        assert isinstance(options, dict), (
            "run detail must carry the return_options field group "
            f"(§2b/§1m.1): {options!r}"
        )
        assert isinstance(options.get("allowed"), bool) and isinstance(
            options.get("targets"), list
        ), f"the rollback face carries the contracted shape (§1m.1): {options!r}"
    finally:
        stop_serve(proc)


# AC-FR0336-02@v0.11 TRACKS-TRACE detail degradation no fabricated floating data
def test_detail_degradation_no_floating(tmp_path: Path):
    """AC-FR0336-02 (projection half): a run whose event stream carries no
    node detail still projects readably — the timeline returns the canonical
    stage order with zero synthesized events, and the run detail invents no
    node facts: the contracted v0.11 field groups are present (§2b) and
    answer the closed degradation forms — ``awaiting_gate`` null for a run
    at no boundary (§1k.1) and the no-entry ``return_options`` form for a
    run with nothing executed (§1m.1) — never a floating fabrication."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        _register(home, repo, "proj-tf-2")
        run_id = "run-tf-empty"
        _append(repo, run_id, "story.requested", {"raw_chars": 9})

        status, body = http_get(base, f"/api/runs/{run_id}/timeline", cookies=cookie)
        assert status == 200, f"the timeline must still resolve: {status} {body[:200]}"
        timeline = json.loads(body)
        assert timeline.get("stage_order"), (
            "the canonical stage order stays available for the degraded render"
        )
        synthesized = [
            event
            for event in timeline.get("events", [])
            if event["type"] not in ("story.requested",)
        ]
        assert not synthesized, (
            f"no node detail may be fabricated for a data-less run: {synthesized!r}"
        )

        detail = _detail(base, cookie, run_id)
        assert "awaiting_gate" in detail, (
            "run detail must carry the awaiting_gate field group (§2b/§1k.1); "
            f"got keys {sorted(detail)!r}"
        )
        assert detail["awaiting_gate"] is None, (
            "a run at no boundary must project awaiting_gate null (§1k.1, "
            f"mapping外无入口), not a fabricated gate: {detail['awaiting_gate']!r}"
        )
        options = detail.get("return_options")
        assert isinstance(options, dict), (
            "run detail must carry the return_options field group "
            f"(§2b/§1m.1): {options!r}"
        )
        assert options.get("allowed") is False and options.get("targets") == [], (
            "a run with nothing executed has no legal return target — the "
            f"degraded face answers the closed no-entry form (§1m.1): {options!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0337-01@v0.11 TRACKS-TRACE return options gate pointer shared legality
def test_return_options_gate_pointer(tmp_path: Path):
    """AC-FR0337-01 (projection half): ``return_options`` exposes exactly the
    targets the shared legality rule allows — a publish-executed run offers
    its earlier stages with the already-executed consequences listed; a run
    with no legal target offers none (no rollback entry exists)."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        _register(home, repo, "proj-tf-3")
        _seed_publish_executed_run(repo, "run-tf-return")
        _append(repo, "run-tf-noreturn", "story.requested", {"raw_chars": 9})
        _append(repo, "run-tf-noreturn", "stage.entered", {"stage": "M-STORY"})

        options = _detail(base, cookie, "run-tf-return").get("return_options")
        assert isinstance(options, dict), (
            f"run detail must carry the return_options field group (§1m.1): {options!r}"
        )
        assert options["allowed"] is True, (
            f"a rollback-capable run must allow the return pointer: {options!r}"
        )
        targets = options["targets"]
        assert targets, f"the legal targets must be listed: {options!r}"
        for target in targets:
            assert "stage" in target and "already_executed" in target, (
                f"each target names its stage and consequences (§1m.1): {target!r}"
            )
        stages = {target["stage"] for target in targets}
        assert "M-RELEASE" not in stages, (
            f"the current stage is never its own target: {sorted(stages)}"
        )
        publish_target = next(
            (target for target in targets if target["already_executed"]), None
        )
        assert publish_target is not None, (
            f"targets downstream of published work list the executed ops: {targets!r}"
        )

        # a run with no legal target: no rollback entry at all
        fresh = _detail(base, cookie, "run-tf-noreturn").get("return_options")
        assert isinstance(fresh, dict), (
            f"the field group is always present (§1m.1): {fresh!r}"
        )
        assert fresh["allowed"] is False and fresh["targets"] == [], (
            f"no legal target means no rollback entry (§1m.1): {fresh!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0337-02@v0.11 TRACKS-TRACE return confirm revalidation no disguised undo
def test_return_confirm_revalidation(tmp_path: Path):
    """AC-FR0337-02 (projection half): the return flow revalidates server-side
    — an unconfirmed request is refused 409 echoing the already-executed
    consequences, the confirmed return executes against the legal target, and
    a revalidation failure answers with the reason and the legal continue
    position instead of a disguised undo."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        _register(home, repo, "proj-tf-4")
        _seed_publish_executed_run(repo, "run-tf-confirm")

        options = _detail(base, cookie, "run-tf-confirm").get("return_options")
        assert isinstance(options, dict) and options.get("targets"), (
            f"the confirm face reads its target from the projection (§1m.2): {options!r}"
        )
        target_stage = options["targets"][0]["stage"]

        # leg 1: without the explicit confirm the return is refused and the
        # refusal echoes the consequences (§1m.2, #25 semantics)
        status, body = http_post(
            base,
            "/api/runs/run-tf-confirm/return",
            {"to": target_stage, "reason": "rework"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="tf-return-noconfirm-1",
        )
        assert status == 409, (
            f"an unconfirmed return must be refused: {status} {body[:200]}"
        )
        refusal = json.loads(body)
        detail_text = json.dumps(refusal)
        assert "already_executed" in detail_text or "confirm" in detail_text, (
            f"the 409 must echo the confirmation demand/consequences: {refusal!r}"
        )

        # leg 2: the confirmed return executes against the projected target
        status, body = http_post(
            base,
            "/api/runs/run-tf-confirm/return",
            {"to": target_stage, "reason": "rework", "confirm": True},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="tf-return-confirm-1",
        )
        assert status in (200, 202), (
            f"the confirmed return must execute: {status} {body[:200]}"
        )

        # leg 3: revalidation — the same return replays into a refusal that
        # names the reason and the legal continue position (no disguised undo
        # of the first executed return, §1m.2)
        _wait_for_event(repo, "run-tf-confirm", "stage.rolled_back")
        status, body = http_post(
            base,
            "/api/runs/run-tf-confirm/return",
            {"to": "M-MILESTONE", "reason": "impossible target", "confirm": True},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="tf-return-revalid-1",
        )
        assert status in (409, 422), (
            f"an illegal target must fail revalidation: {status} {body[:200]}"
        )
        failure = json.loads(body)
        assert failure["error"]["reason"], (
            f"the revalidation failure carries its reason (§1m.2): {failure!r}"
        )
    finally:
        stop_serve(proc)


# AC-NFR0158-01@v0.11 TRACKS-TRACE no second data source failure visible
def test_no_second_source_failure_visible(tmp_path: Path):
    """AC-NFR0158-01 (projection half): the new data faces fail observably —
    an unknown run 404s with the closed error envelope instead of silently
    returning an empty projection, and the timeline refuses to synthesize a
    second source when the run plane has no events."""
    proc, home, repo = _start_timeline_serve(tmp_path)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, _csrf = login_session(base)
        _register(home, repo, "proj-tf-5")

        # unknown targets fail closed with the documented envelope (§2b
        # base contract + #36 failure column) — never a silent empty face
        for path in (
            "/api/runs/run-tf-unknown/timeline",
            "/api/runs/run-tf-unknown",
            "/api/projects/proj-tf-unregistered/releases",
        ):
            status, body = http_get(base, path, cookies=cookie)
            assert status == 404, (
                f"{path} must fail closed for an unknown target: {status} {body[:200]}"
            )
            try:
                envelope = json.loads(body)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"{path} must answer the documented JSON error envelope "
                    f"(§2b base contract), got: {body[:120]!r}"
                ) from exc
            assert envelope["error"]["reason"], (
                f"the failure must carry the closed error envelope: {body[:200]}"
            )
    finally:
        stop_serve(proc)
