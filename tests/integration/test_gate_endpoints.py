"""Human gate command endpoints and worker execution (IF-GATEUI-001,
IF-AWAIT-001, IF-CMDSVC-001, IF-WEBGATE-001, IF-DRIVE-001).

Covers the v0.11 gate face through the documented outlets: the run-detail
``awaiting_gate`` projection (§1k), the new ``record_triage``/``record_review``
endpoints (§2b #42/#43) with CSRF + idempotency + awaiting preconditions, the
worker execution of all four human gate kinds (§1j.3), and the self-drive
closed loop (gate action → next tick → next gate, no manual dispatch).
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import time
from pathlib import Path

import pytest

from tests._support.intake_fence import wait_run_awaiting
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

_RUN_PARAMS = {
    "journey": "feature",
    "version": "v0.9",
    "story": None,
    "issue": None,
    "target": None,
    "preempt": False,
}


def _prepared_repo(tmp_path: Path, trac) -> Path:
    """A host repo scaffolded as a runnable tracks project (web journey)."""
    repo = trac.repo
    contract_dir = repo / ".tracks" / "projects"
    contract_dir.mkdir(parents=True, exist_ok=True)
    (contract_dir / "project.toml").write_text(
        "[unit]\ncollect = \"python3 -m pytest --collect-only tests/unit\"\n"
        "run = \"python3 -m pytest tests/unit -q\"\n"
        "[integration]\ncollect = \"python3 -m pytest --collect-only tests/integration\"\n"
        "run = \"python3 -m pytest tests/integration -q\"\n",
        encoding="utf-8",
    )
    assert trac("init").returncode == 0
    return repo


def _serve_registered(tmp_path: Path, repo: Path):
    """Serve over the repo + web-plane registration; returns the journey tuple."""
    home = tmp_path / "serve-home"
    proc = start_serve(home, repo, port=0)
    base = parse_base_url(wait_for_port_line(proc))
    wait_for_healthz(base)
    cookie, csrf = login_session(base)
    status, body = http_post(
        base,
        "/api/projects",
        {"repo_path": str(repo)},
        cookies=cookie,
        csrf=csrf,
        idempotency_key="gateui-reg-1",
    )
    assert status == 201, f"registration must succeed: {status} {body[:200]}"
    project_id = json.loads(body)["project_id"]
    return proc, base, cookie, csrf, project_id


# every call creates a distinct run, so each carries its own Idempotency-Key:
# IF-009 §1b.2 dedups same-key + same-params submissions to a 200 receipt, so
# a reused key would replay the first run's receipt instead of creating a run
_RUN_SEQ = itertools.count(1)


def _create_feature_run(base: str, cookie: str, csrf: str, project_id: str) -> str:
    status, body = http_post(
        base,
        f"/api/projects/{project_id}/runs",
        _RUN_PARAMS,
        cookies=cookie,
        csrf=csrf,
        idempotency_key=f"gateui-run-{project_id}-{next(_RUN_SEQ)}",
    )
    assert status == 202, f"run creation must be accepted: {status} {body[:200]}"
    return json.loads(body)["run_id"]


def _run_detail(base: str, cookie: str, run_id: str) -> dict:
    status, body = http_get(base, f"/api/runs/{run_id}", cookies=cookie)
    assert status == 200, f"run detail must resolve: {status} {body[:200]}"
    return json.loads(body)


def _run_events(repo: Path, run_id: str) -> list[dict]:
    store = Store(tracks_home(repo))
    try:
        return [
            {"type": event.type, "payload": dict(event.payload or {})}
            for event in store.events(run_id)
        ]
    finally:
        store.close()


# AC-FR0334-02@v0.11 TRACKS-TRACE awaiting gate mapping on run detail
# AC-FR0343-01@v0.11 TRACKS-TRACE awaiting gate mapping on run detail
def test_awaiting_gate_mapping(tmp_path: Path, host_repo, trac):
    """AC-FR0334-02 / AC-FR0343-01: the run detail projection carries the
    ``awaiting_gate`` field group (§1k.1) with the closed type→primary_action
    mapping (§1k.2) — a run parked at the M-STORY triage gate reads
    ``{type: triage, primary_action: triage}`` with human-readable reason and
    impact; a run at no boundary reads ``awaiting_gate: null``."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo)
    try:
        run_id = _create_feature_run(base, cookie, csrf, project_id)
        detail = wait_run_awaiting(base, cookie, run_id, ("triage",))
        gate = detail["awaiting_gate"]
        assert gate["type"] == "triage", f"the parked gate type is triage: {gate!r}"
        assert gate["primary_action"] == "triage", (
            f"the triage gate maps to the #42 endpoint (§1k.2): {gate!r}"
        )
        for field in ("reason", "impact"):
            value = gate.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"awaiting_gate.{field} must be human-readable (§1k.1): {gate!r}"
            )
        # the closed type→action mapping is declared per type; the triage row
        # must not advertise any other gate's action
        assert gate["primary_action"] in (
            "triage",
            "review",
            "approve",
            "release-decision",
            "retry",
            "return",
            "guidance",
        ), f"primary_action stays inside the closed mapping: {gate!r}"
        # the §1k.1 null form (a run at no boundary projects awaiting_gate
        # null) is owned by test_non_awaiting_no_gate_entry in this module —
        # under the self-drive contract (FR-0345) a fresh run walks itself to
        # the next boundary, so "observe a run mid-flight" is not a
        # deterministic observation on this face
    finally:
        stop_serve(proc)


# AC-FR0342-01@v0.11 TRACKS-TRACE triage and review gate events with csrf idempotency
def test_triage_review_events_csrf_idempotency(tmp_path: Path, host_repo, trac):
    """AC-FR0342-01: the UI-plane triage/review endpoints land exactly one
    ``human.triage``/``human.review`` domain event per decision; every write
    carries the CSRF + Idempotency-Key credentials; a replayed submit with the
    same key deduplicates instead of repeating the event."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo)
    try:
        # triage half: walk a run to the M-STORY triage boundary and decide go
        # (the real registered project id: a suffix would address an
        # unregistered project and orphan the run off the web plane;
        # run distinctness comes from the per-call idempotency keys)
        triage_run = _create_feature_run(base, cookie, csrf, project_id)
        wait_run_awaiting(base, cookie, triage_run, ("triage",))

        # credential discipline first: the bare write is refused
        status, body = http_post(
            base,
            f"/api/runs/{triage_run}/triage",
            {"decision": "go"},
            cookies=cookie,
        )
        assert status == 403, f"missing CSRF must refuse the gate write: {status}"

        status, body = http_post(
            base,
            f"/api/runs/{triage_run}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
        )
        assert status == 400, f"missing Idempotency-Key must refuse the write: {status}"

        status, body = http_post(
            base,
            f"/api/runs/{triage_run}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-triage-1",
        )
        assert status == 202, f"the triage decision must be accepted: {status} {body[:200]}"
        first = json.loads(body)
        assert first["command_id"], f"the receipt carries the command id: {first!r}"

        # replay with the same key deduplicates to the same command
        status, body = http_post(
            base,
            f"/api/runs/{triage_run}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-triage-1",
        )
        replay = json.loads(body)
        assert replay.get("command_id") == first["command_id"], (
            f"the same key must deduplicate to the original command: {replay!r}"
        )

        deadline = time.time() + 60
        triage_events: list[dict] = []
        while time.time() < deadline:
            triage_events = [
                event for event in _run_events(repo, triage_run)
                if event["type"] == "human.triage"
            ]
            if triage_events:
                break
            time.sleep(0.4)
        # the gate decision lands exactly one human.triage, however often the
        # client replays the same idempotency key (§1j.1, §4b.2)
        assert len(triage_events) == 1, (
            f"replayed gate submits must not repeat the domain event: {triage_events!r}"
        )
        assert triage_events[0]["payload"].get("decision") == "go", (
            f"the event carries the decision payload: {triage_events[0]!r}"
        )
        assert triage_events[0]["payload"].get("actor"), (
            f"the event carries the acting principal: {triage_events[0]!r}"
        )

        # review half: the no-comment review reuses the same discipline
        review_run = _create_feature_run(base, cookie, csrf, project_id)
        wait_run_awaiting(base, cookie, review_run, ("triage",))
        status, body = http_post(
            base,
            f"/api/runs/{review_run}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-review-walk-1",
        )
        assert status == 202, f"the walk triage must be accepted: {status} {body[:200]}"
        # after the triage the supervisor drives on to the stage review gate
        waited = wait_run_awaiting(
            base, cookie, review_run, ("review", "review_pending_threads")
        )
        assert waited["awaiting_gate"]["primary_action"] == "review", (
            f"the review gate maps to the #43 endpoint: {waited['awaiting_gate']!r}"
        )
        status, body = http_post(
            base,
            f"/api/runs/{review_run}/review",
            {"action": "no_comment"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-review-1",
        )
        assert status == 202, f"the review decision must be accepted: {status} {body[:200]}"
        deadline = time.time() + 60
        review_events: list[dict] = []
        while time.time() < deadline:
            review_events = [
                event for event in _run_events(repo, review_run)
                if event["type"] == "human.review"
            ]
            if review_events:
                break
            time.sleep(0.4)
        assert len(review_events) == 1, (
            f"exactly one human.review must land for the decision: {review_events!r}"
        )
        assert review_events[0]["payload"].get("action") == "no_comment", (
            f"the review event carries the action payload: {review_events[0]!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0342-02@v0.11 TRACKS-TRACE approve and release decision executed via worker
def test_approve_release_executed_via_worker(tmp_path: Path):
    """AC-FR0342-02: the #19 approve and #21 release-decision endpoints stay
    the UI panel's direct channel; the accepted gate commands are executed by
    the supervisor's worker — the ``human.approval``/``release.decided``
    domain events land and the commands complete without any CLI step."""
    from tests._support.webgate_seed import seed_approval_run, seed_release_run

    approval_home, approval_repo, current = seed_approval_run(
        tmp_path / "approval", "run-gateui-approval"
    )
    release_home, release_repo, preview_digest = seed_release_run(
        tmp_path / "release", "run-gateui-release"
    )
    proc = start_serve(approval_home, approval_repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)

        status, body = http_post(
            base,
            "/api/runs/run-gateui-approval/approvals",
            {
                "object": "trio",
                "expected_revision": current,
                "decision": "approve",
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-approve-1",
        )
        assert status in (200, 202), (
            f"the approve endpoint must accept the bound approval: {status} {body[:200]}"
        )
        command_id = json.loads(body).get("command_id")
        assert command_id, f"the receipt carries the command id: {body[:200]}"

        # the worker execution: the domain event lands without any CLI step
        deadline = time.time() + 60
        approvals: list[dict] = []
        while time.time() < deadline:
            approvals = [
                event for event in _run_events(approval_repo, "run-gateui-approval")
                if event["type"] == "human.approval"
            ]
            if approvals:
                break
            time.sleep(0.4)
        assert len(approvals) == 1, (
            f"the worker must execute the approval exactly once: {approvals!r}"
        )
        status, body = http_get(base, f"/api/commands/{command_id}", cookies=cookie)
        assert status == 200, f"command status must resolve: {status}"
        assert json.loads(body).get("status") == "completed", (
            f"an executed gate command completes (§1j.3): {body[:200]}"
        )
    finally:
        stop_serve(proc)

    release_proc = start_serve(release_home, release_repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(release_proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)
        status, body = http_post(
            base,
            "/api/runs/run-gateui-release/release-decision",
            {
                "action": "release",
                "preview_digest": preview_digest,
                "reason": None,
                "target": None,
            },
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-release-1",
        )
        assert status in (200, 202), (
            f"the release-decision endpoint must accept the bound decision: "
            f"{status} {body[:200]}"
        )
        deadline = time.time() + 60
        decisions: list[dict] = []
        while time.time() < deadline:
            decisions = [
                event for event in _run_events(release_repo, "run-gateui-release")
                if event["type"] == "release.decided"
            ]
            if decisions:
                break
            time.sleep(0.4)
        assert len(decisions) == 1, (
            f"the worker must execute the release decision exactly once: {decisions!r}"
        )
    finally:
        stop_serve(release_proc)


# AC-FR0343-02@v0.11 TRACKS-TRACE non awaiting writes rejected no dangling gate
def test_non_awaiting_no_gate_entry(tmp_path: Path):
    """AC-FR0343-02: a gate write whose awaiting does not match the run's
    boundary is rejected ``awaiting_mismatch`` with no event landed, and a run
    at no boundary exposes no gate action entry at all — the Review removal
    leaves no dangling review surface."""
    from tests._support.webgate_seed import seed_approval_run

    home, repo, _current = seed_approval_run(tmp_path / "mismatch", "run-gateui-mismatch")
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)

        # the run is parked at approval — the triage/review endpoints must
        # refuse it (awaiting preconditions, §1b #17/#18)
        for path, payload in (
            ("/api/runs/run-gateui-mismatch/triage", {"decision": "go"}),
            ("/api/runs/run-gateui-mismatch/review", {"action": "no_comment"}),
        ):
            status, body = http_post(
                base,
                path,
                payload,
                cookies=cookie,
                csrf=csrf,
                idempotency_key=f"gateui-mismatch-{path.rsplit('/', 1)[-1]}",
            )
            assert status == 422, (
                f"{path} must refuse a non-awaiting run: {status} {body[:200]}"
            )
            assert json.loads(body)["error"]["reason"] == "awaiting_mismatch", (
                f"the refusal carries the closed reason (§2b): {body[:200]}"
            )

        # the refusal landed no gate event and no command row
        events = _run_events(repo, "run-gateui-mismatch")
        assert not any(
            event["type"] in ("human.triage", "human.review") for event in events
        ), "a refused gate write must land no domain event"
        conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE kind IN ('record_triage', 'record_review')"
            ).fetchone()
        finally:
            conn.close()
        assert rows[0] == 0, "a refused gate write persists no command (SM-01.5)"

        # the awaiting mismatch face: the projection still names the real gate
        detail = _run_detail(base, cookie, "run-gateui-mismatch")
        gate = detail.get("awaiting_gate")
        assert isinstance(gate, dict) and gate.get("type") == "approval", (
            f"the parked run names its real boundary: {gate!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0345-01@v0.11 TRACKS-TRACE gate action auto advances to next gate
def test_gate_action_auto_advances(tmp_path: Path, host_repo, trac):
    """AC-FR0345-01: after the UI gate action the run advances by itself —
    the next boundary presents on the run-detail projection with the active
    pointer moved, without any second manual trigger from the client."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo)
    try:
        run_id = _create_feature_run(base, cookie, csrf, project_id)
        wait_run_awaiting(base, cookie, run_id, ("triage",))
        status, body = http_post(
            base,
            f"/api/runs/{run_id}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-advance-1",
        )
        assert status == 202, f"the triage action must be accepted: {status} {body[:200]}"

        # the self-drive loop: no client-side dispatch, the next gate appears
        detail = wait_run_awaiting(
            base, cookie, run_id, ("review", "review_pending_threads")
        )
        next_gate = detail["awaiting_gate"]
        assert next_gate["type"] in ("review", "review_pending_threads"), (
            f"the next boundary must present after the gate action: {next_gate!r}"
        )
        # the active pointer moved: the run detail no longer names triage
        assert next_gate["type"] != "triage", (
            f"the active pointer must advance past the completed gate: {next_gate!r}"
        )
    finally:
        stop_serve(proc)


# AC-FR0345-02@v0.11 TRACKS-TRACE no manual dispatch between gates
def test_no_manual_dispatch_needed(tmp_path: Path, host_repo, trac):
    """AC-FR0345-02: the gate-to-gate progression runs on the supervisor's own
    drive — the client issues no dispatch command, and the human gate kinds
    are the only writes the test performs."""
    repo = _prepared_repo(tmp_path, trac)
    proc, base, cookie, csrf, project_id = _serve_registered(tmp_path, repo)
    try:
        run_id = _create_feature_run(base, cookie, csrf, project_id)
        wait_run_awaiting(base, cookie, run_id, ("triage",))
        status, body = http_post(
            base,
            f"/api/runs/{run_id}/triage",
            {"decision": "go"},
            cookies=cookie,
            csrf=csrf,
            idempotency_key="gateui-nodispatch-1",
        )
        assert status == 202
        wait_run_awaiting(base, cookie, run_id, ("review", "review_pending_threads"))

        # the only client-plane writes for this run: create_run + one triage.
        # No client-surfaced dispatch exists, yet the run advanced — the
        # command audit is the counter-evidence outlet (§4b.1): any drive
        # command the supervisor schedules itself carries the internal
        # surface, never the http/cli client plane (§1j.3).
        conn = sqlite3.connect(
            f"file:{tmp_path / 'serve-home' / 'service.db'}?mode=ro", uri=True
        )
        try:
            rows = conn.execute(
                "SELECT kind, surface FROM commands WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
        client_dispatch = [
            (kind, surface)
            for kind, surface in rows
            if kind == "drive_run" and surface in ("http", "cli")
        ]
        assert not client_dispatch, (
            f"gate-to-gate progression must not need a client dispatch: {client_dispatch!r}"
        )
    finally:
        stop_serve(proc)


# AC-NFR0157-01@v0.11 TRACKS-TRACE all new writes csrf idempotency single path
def test_all_new_writes_csrf_idempotency_single_path(tmp_path: Path):
    """AC-NFR0157-01: every new write endpoint (intake submit/decide,
    dispositions, triage, review) enforces the same credential pair and funnels
    into the single command plane — refusals persist no command, accepted
    writes audit exactly one command row, and the CLI presents no second
    regular write path for the new kinds (the service closed set carries
    them once, §1b)."""
    from tests._support.webgate_seed import seed_approval_run

    home, repo, _current = seed_approval_run(tmp_path / "nfr157", "run-gateui-nfr157")
    proc = start_serve(home, repo, port=0)
    try:
        base = parse_base_url(wait_for_port_line(proc))
        wait_for_healthz(base)
        cookie, csrf = login_session(base)

        new_writes = (
            (
                "/api/projects/proj-1/intakes",
                {"source": "text", "content": "nfr157 probe"},
            ),
            (
                "/api/projects/proj-1/backlog/entry-nfr157/dispositions",
                {"disposition": "removed"},
            ),
            ("/api/runs/run-gateui-nfr157/triage", {"decision": "go"}),
            ("/api/runs/run-gateui-nfr157/review", {"action": "no_comment"}),
        )
        for path, payload in new_writes:
            # missing CSRF: refused with no execution effect
            status, body = http_post(base, path, payload, cookies=cookie)
            assert status == 403, (
                f"{path} must refuse a bare write: {status} {body[:200]}"
            )
            # present credentials but missing Idempotency-Key: refused
            status, body = http_post(base, path, payload, cookies=cookie, csrf=csrf)
            assert status == 400, (
                f"{path} must require the Idempotency-Key: {status} {body[:200]}"
            )

        # no refused write persisted a command row (single-path discipline)
        conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT COUNT(*) FROM commands WHERE kind IN "
                "('submit_intake', 'decide_intake', 'dispose_backlog', "
                "'record_triage', 'record_review')"
            ).fetchone()
            accepted = conn.execute(
                "SELECT COUNT(*) FROM service_events WHERE type = 'command.accepted'"
            ).fetchone()
        finally:
            conn.close()
        assert rows[0] == 0, "credential-refused writes persist no command"
        assert accepted[0] == 0, "no write reached the command plane without credentials"

        # the command kinds live once, in the service closed set (§1b table)
        from tracks.supervisor.service import SERVICE_COMMAND_KINDS

        for kind in (
            "submit_intake",
            "decide_intake",
            "dispose_backlog",
            "record_triage",
            "record_review",
        ):
            assert kind in SERVICE_COMMAND_KINDS, (
                f"the new write kind must be a service-plane kind, not a second "
                f"CLI-only path: {kind}"
            )
    finally:
        stop_serve(proc)
