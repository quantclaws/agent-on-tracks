"""T-013 RED: mutating HTTP routes -> CommandService (IF-WEBGATE-001).

Devon-owned unit RED for the T-013 delivery slice
(``tracks/server/api_command.py``): every mutating route of interfaces §2b
resolves the authenticated session (§1f.2-3), checks the ``X-Trac-CSRF``
credential, validates the structured payload through the command guard
(§1g.1), applies the web-gate business preflight (approval revision binding,
preview digest binding, controlled-retry position) and delegates to
``CommandService.accept`` with ``surface="http"`` (§1b). Rejections map onto
the §2b status codes with the unified error envelope; rejected requests
produce no execution effects (SM-01.5) and an auditable ``command.rejected``
(or ``access.denied``) record.

The unit seam is the handler contract the app factory (T-014) binds to: each
handler receives a request exposing

    request.app.state.home      -> Path of the service home (service.db)
    request.app.state.service   -> CommandService (composition root injects)
    request.app.state.permitted_repos -> realpath scope of serve --repo (§1g.2)
    request.path_params         -> pid / run_id / doc
    request.cookies             -> {"trac_session": <token>}
    request.headers             -> case-insensitive .get (X-Trac-CSRF,
                                   Idempotency-Key)
    await request.json()        -> structured JSON body

and returns ``api_command.CommandResponse(status_code, payload)`` — the
starlette-free response envelope (the runtime venv does not carry starlette
yet; the composition root maps the envelope onto JSONResponse), mirroring the
T-011 query seam.

Fixtures seed the documented storage contract for real (interfaces §1c
service.db through ``ServiceDB`` plus the projects row, each run's tracks.db
through ``Store``, the material trio under ``.tracks/projects/<version>/``)
— nothing here mocks the system under test. The handler bodies still raise
their IF- stub tokens; every failing node guards that stub state into a real
``AssertionError`` (no stub_token, no assembly errors). The fresh-preview
release fixture follows the proven candidate-bound authorization harness
(contract + frozen candidate + bound preview blob) so the delay/return
binding anchor exercises the real read-time gate.

AC: FR-0308/0309/0311 — TRACKS-TRACE IF-WEBGATE-001.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.unit.helpers import git_repo, git_strip
from tracks.baseline import revision_digest
from tracks.executor.host_contract import load_host_contract
from tracks.executor.release_preview import assemble_preview
from tracks.paths import tracks_home
from tracks.server import api_command, auth
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService

_VERSION = "v0.9"
_RELEASE_VERSION = "v0.8"  # release fixture rides the proven v0.8 harness facts
_ACTOR = "local-user"
_SESSION_COOKIE = "trac_session"
_CONTRACT_TOML = (
    "[host-contract]\n"
    "version = 1\n"
    "language = 'python'\n"
    "toolchain = 'cpython'\n"
    "install = ''\n"
    "\n[host-contract.version_scheme]\n"
    "feature_tag = 'v{minor}.0'\n"
    "patch_line = 'v{minor}.{n}'\n"
    "prerelease_tag = 'v{minor}.{n}-pre.{ulid}'\n"
    "\n[[host-contract.local_gate]]\n"
    "kind = 'quality'\n"
    "source = 'command'\n"
    "command = 'quality'\n"
    "result_channel = 'exit_code'\n"
    "\n[[host-contract.security_scan]]\n"
    "id = 'security'\ntool = 'fixture'\ntool_version = '1'\n"
    "install = ''\ncommand = 'true'\nresult_channel = 'exit_code'\n"
    "threshold = '0'\ntimeout_seconds = 5\n"
    "\n[host-contract.ci]\n"
    "repo_env = 'CI_REPO'\n"
    "workflow = 'ci.yml'\n"
    "required_checks = ['quality']\n"
    "\n[host-contract.operations.feature]\n"
    "steps = ['tag:{feature_tag}']\n"
)


# -- the handler seam: request double + response extraction ------------------


class _Headers:
    """Case-insensitive header mapping (Starlette-compatible .get)."""

    def __init__(self, raw: dict) -> None:
        self._raw = {str(key).lower(): value for key, value in (raw or {}).items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._raw.get(str(name).lower(), default)


class _Request:
    """Duck-typed request exposing exactly the handler seam (see docstring)."""

    def __init__(
        self,
        home: Path,
        service: Any,
        *,
        permitted_repos: list[Path] | None = None,
        path_params: dict | None = None,
        cookies: dict | None = None,
        headers: dict | None = None,
        body: Any = None,
    ) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                home=home,
                service=service,
                permitted_repos=list(permitted_repos or []),
            )
        )
        self.path_params = dict(path_params or {})
        self.cookies = dict(cookies or {})
        self.headers = _Headers(headers)
        self._body = body

    async def json(self) -> Any:
        return self._body


def _call(handler, request: _Request) -> tuple[int, Any]:
    """Call one mutation handler; a stub token becomes a real assertion."""
    try:
        result = asyncio.run(handler(request))
    except NotImplementedError as exc:
        raise AssertionError(f"{handler.__name__} is still a stub: {exc}") from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), (
        f"{handler.__name__} must return api_command.CommandResponse(status_code, payload)"
    )
    return status, getattr(result, "payload", None)


def _reason_of(payload: Any) -> str:
    """Closed-set reason from the unified error envelope (§2b / §1f.3)."""
    assert isinstance(payload, dict) and "error" in payload, (
        f"expected the unified error envelope, got {payload!r}"
    )
    error = payload["error"]
    if isinstance(error, dict):
        return error["reason"]
    return error


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# -- fixtures: real stores seeded through the documented contract ------------


def _write_trio(vdir: Path, *, story: str = "story body\n") -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text(story, encoding="utf-8")
    (vdir / "spec.md").write_text("spec body\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text("acceptance body\n", encoding="utf-8")


def _register_project(conn: sqlite3.Connection, repo: Path, version: str) -> None:
    conn.execute(
        "INSERT INTO projects VALUES (?,?,?,?,?)",
        ("proj-1", str(repo), version, _ACTOR, "2026-09-21T00:00:00+00:00"),
    )
    conn.commit()


def _open_service_db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(home / "service.db"))


def _make_service(home: Path) -> CommandService:
    return CommandService(home, sdb.ServiceDB(home), SimpleNamespace())


def _session(home: Path) -> tuple[str, str]:
    """Real session row through auth.issue_session; returns (token, csrf)."""
    conn = _open_service_db(home)
    try:
        issued = auth.issue_session(conn, _ACTOR)
    finally:
        conn.close()
    return issued.token, issued.csrf_token


def _request(
    home: Path,
    service: Any,
    *,
    path_params: dict | None = None,
    body: Any = None,
    token: str | None = "token-1",
    csrf: str | None = "csrf-1",
    idem: str | None = "idem-1",
    permitted_repos: list[Path] | None = None,
) -> _Request:
    cookies = {_SESSION_COOKIE: token} if token else {}
    headers: dict = {}
    if csrf:
        headers["X-Trac-CSRF"] = csrf
    if idem:
        headers["Idempotency-Key"] = idem
    return _Request(
        home,
        service,
        permitted_repos=permitted_repos,
        path_params=path_params,
        cookies=cookies,
        headers=headers,
        body=body,
    )


def _seed_approval_run(tmp_path: Path, run_id: str):
    """A run at the M-REQ-APPROVAL human gate with the trio digest bound.

    Returns (repo, home, service, current_digest).
    """
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / _VERSION
    _write_trio(vdir)
    current = revision_digest(vdir)
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-REQ-APPROVAL"})
        store.append(run_id, _VERSION, "preview.generated", {"digest": current})
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _VERSION)
    finally:
        conn.close()
    return repo, home, _make_service(home), current


def _sessioned(home: Path, service: Any, **kwargs) -> _Request:
    """A request carrying a real session cookie + matching CSRF credential."""
    token, csrf = _session(home)
    return _request(home, service, token=token, csrf=csrf, **kwargs)


# -- audit readers (service.db / tracks.db read-only) ------------------------


def _service_events(home: Path, event_type: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT payload FROM service_events WHERE type = ? ORDER BY seq", (event_type,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def _accepted_commands(home: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM commands").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _tracks_event_types(repo: Path) -> set[str]:
    conn = sqlite3.connect(
        f"file:{repo / '.tracks' / 'runtime' / 'tracks.db'}?mode=ro", uri=True
    )
    try:
        return {row[0] for row in conn.execute("SELECT DISTINCT type FROM events")}
    finally:
        conn.close()


def _state_awaiting(repo: Path, run_id: str) -> tuple:
    from tracks.kernel.events import event_envelope_from_row
    from tracks.server.projections import project_state

    conn = sqlite3.connect(
        f"file:{repo / '.tracks' / 'runtime' / 'tracks.db'}?mode=ro", uri=True
    )
    try:
        rows = conn.execute(
            "SELECT run_id, seq, ts, version, type, schema_version, command_id,"
            " task_id, payload FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()
    state = project_state(
        [event_envelope_from_row(row, json.loads(row[8])) for row in rows]
    )
    return state.stage, state.awaiting


# -- release fixture: candidate-bound fresh preview (proven harness) ---------


def _seed_release_run(tmp_path: Path, run_id: str, *, stale: bool = False):
    """A run at M-RELEASE with a bound fresh preview (or a staled one).

    Returns (repo, home, service, preview_digest).
    """
    repo = git_repo(tmp_path, gitignore=True)
    contract_path = repo / ".tracks" / "projects" / "project.toml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(_CONTRACT_TOML, encoding="utf-8")
    git_strip(repo, "add", "-f", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-m", "release contract")
    candidate = git_strip(repo, "rev-parse", "HEAD")
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _RELEASE_VERSION, "story.requested", {"raw_chars": 1})
        store.append(run_id, _RELEASE_VERSION, "stage.entered", {"stage": "M-RELEASE"})
        store.append(
            run_id,
            _RELEASE_VERSION,
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": "main",
                "frozen_at_seq": 3,
            },
        )
        contract = load_host_contract(contract_path)
        facts = {"version": _RELEASE_VERSION, "major": "0", "minor": "0.8"}
        preview = assemble_preview(
            repo,
            contract,
            candidate,
            "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest(),
            facts,
            list(store.events(run_id)),
            journey="feature",
        )
        assert preview is not None, "fixture must assemble a fresh preview"
        blob = store.write_audit_blob(preview)
        assert blob is not None
        stored = dict(preview)
        stored["blob_ref"] = f".tracks/runtime/blobs/{blob}"
        store.append(run_id, _RELEASE_VERSION, "release.previewed", stored)
        if stale:
            store.append(
                run_id,
                _RELEASE_VERSION,
                "evidence.staled",
                {"candidate_sha": candidate},
            )
    finally:
        store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _RELEASE_VERSION)
    finally:
        conn.close()
    return repo, home, _make_service(home), preview["preview_digest"]


# -- approvals: POST /api/runs/{run_id}/approvals -----------------------------


def _approval_body(current: str, decision: str = "approve") -> dict:
    return {
        "object": "requirements",
        "expected_revision": current,
        "decision": decision,
    }


# AC-FR0308-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 approval accepted bound to the reviewed revision
def test_approval_binds_revision_and_accepts_with_surface_http(tmp_path: Path):
    run_id = "run-approve"
    repo, home, service, current = _seed_approval_run(tmp_path, run_id)
    token, csrf = _session(home)
    request = _request(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body(current),
        token=token,
        csrf=csrf,
        idem="idem-approve-1",
    )

    status, payload = _call(api_command.record_approval, request)

    assert status == 202
    assert isinstance(payload.get("command_id"), str) and payload["command_id"]
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    row = accepted[0]
    assert row["kind"] == "record_stage_approval"
    assert row["surface"] == "http"
    assert row["actor"] == _ACTOR
    assert row["actor_class"] == "human"
    params = json.loads(row["params_json"])
    assert params["run_id"] == run_id
    assert params["expected_revision"] == current
    assert params["decision"] == "approve"
    events = _service_events(home, "command.accepted")
    assert events and events[-1]["surface"] == "http"


# AC-FR0308-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 stale revision approval rejected 409
def test_stale_revision_approval_rejected_409_without_state_change(tmp_path: Path):
    run_id = "run-stale-approval"
    repo, home, service, current = _seed_approval_run(tmp_path, run_id)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body("0" * 64),
        idem="idem-stale-approval",
    )

    status, payload = _call(api_command.record_approval, request)

    assert status == 409
    assert _reason_of(payload) == "stale_revision"
    assert current in payload["error"]["detail"], "rejection must surface the current revision"
    assert not [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    rejections = _service_events(home, "command.rejected")
    assert any(item.get("reason") == "stale_revision" for item in rejections)
    assert "human.approval" not in _tracks_event_types(repo)
    assert _state_awaiting(repo, run_id) == ("M-REQ-APPROVAL", "approval")


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 unauthenticated mutation rejected 401
def test_approval_without_session_is_unauthenticated_401(tmp_path: Path):
    _, home, service, current = _seed_approval_run(tmp_path, "run-anon")
    request = _request(
        home,
        service,
        path_params={"run_id": "run-anon"},
        body=_approval_body(current),
        token=None,
    )

    status, payload = _call(api_command.record_approval, request)

    assert status == 401
    assert _reason_of(payload) == "unauthenticated"
    assert _accepted_commands(home) == []
    assert _service_events(home, "command.accepted") == []


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 csrf mismatch rejected 403 audited
def test_approval_with_wrong_csrf_rejected_403_and_audited(tmp_path: Path):
    run_id = "run-csrf"
    _, home, service, current = _seed_approval_run(tmp_path, run_id)
    token, _csrf = _session(home)
    request = _request(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body(current),
        token=token,
        csrf="wrong-csrf-credential",
    )

    status, payload = _call(api_command.record_approval, request)

    assert status == 403
    assert _reason_of(payload) in {"unauthenticated", "forbidden_actor"}
    assert _service_events(home, "access.denied")
    assert _accepted_commands(home) == []


# AC-FR0314-03@v0.9 TRACKS-TRACE IF-WEBGATE-001 shell payload rejected 400 without execution
def test_shell_payload_on_approval_rejected_400_without_effects(tmp_path: Path):
    run_id = "run-shell"
    _, home, service, current = _seed_approval_run(tmp_path, run_id)
    body = _approval_body(current)
    body["shell"] = "rm -rf /"
    request = _sessioned(home, service, path_params={"run_id": run_id}, body=body)

    status, payload = _call(api_command.record_approval, request)

    assert status == 400
    assert _reason_of(payload) in {"guard_blocked", "validation_failed"}
    assert not [row for row in _accepted_commands(home) if row["status"] == "accepted"]


# AC-FR0295-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 missing idempotency key -> 400
def test_mutation_without_idempotency_key_rejected_400(tmp_path: Path):
    run_id = "run-no-idem"
    _, home, service, current = _seed_approval_run(tmp_path, run_id)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body(current),
        idem=None,
    )

    status, payload = _call(api_command.record_approval, request)

    assert status == 400
    assert _reason_of(payload) == "validation_failed"
    assert _accepted_commands(home) == []


# AC-FR0295-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 same key different payload -> 409 conflict
def test_idempotency_conflict_maps_to_409(tmp_path: Path):
    run_id = "run-conflict"
    _, home, service, current = _seed_approval_run(tmp_path, run_id)
    first = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body(current),
        idem="idem-dup",
    )
    ok_status, ok_payload = _call(api_command.record_approval, first)
    assert ok_status == 202
    conflict = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_approval_body(current, decision="revise"),
        idem="idem-dup",
    )

    status, payload = _call(api_command.record_approval, conflict)

    assert status == 409
    assert _reason_of(payload) == "idempotency_conflict"


# AC-FR0310-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 unknown run -> 404 not_found
def test_mutation_for_unknown_run_is_not_found_404(tmp_path: Path):
    _, home, service, _ = _seed_approval_run(tmp_path, "run-known")
    request = _sessioned(home, service, path_params={"run_id": "run-ghost"}, body={})

    status, payload = _call(api_command.pause_run, request)

    assert status == 404
    assert _reason_of(payload) == "not_found"
    assert _accepted_commands(home) == []


# -- release decision: POST /api/runs/{run_id}/release-decision ----------------


def _decision_body(action: str, digest: str) -> dict:
    body: dict = {"action": action, "preview_digest": digest}
    if action == "return":
        body["reason"] = "re-walk verification"
        body["target"] = "M-VERIFY"
    return body


# AC-FR0309-03@v0.9 TRACKS-TRACE IF-WEBGATE-001 delay decision binds the preview digest
def test_delay_decision_binds_digest_and_accepts(tmp_path: Path):
    run_id = "run-delay"
    repo, home, service, digest = _seed_release_run(tmp_path, run_id)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_decision_body("delay", digest),
        idem="idem-delay",
    )

    status, payload = _call(api_command.record_release_decision, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    row = accepted[0]
    assert row["kind"] == "record_release_decision"
    assert row["surface"] == "http"
    params = json.loads(row["params_json"])
    assert params["run_id"] == run_id
    assert params["action"] == "delay"
    assert params["preview_digest"] == digest


# AC-FR0309-03@v0.9 TRACKS-TRACE IF-WEBGATE-001 return decision binds the preview digest
def test_return_decision_binds_digest_and_accepts(tmp_path: Path):
    run_id = "run-return"
    repo, home, service, digest = _seed_release_run(tmp_path, run_id)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_decision_body("return", digest),
        idem="idem-return",
    )

    status, payload = _call(api_command.record_release_decision, request)

    assert status == 202
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    params = json.loads(accepted[0]["params_json"])
    assert params["action"] == "return"
    assert params["preview_digest"] == digest


# AC-FR0309-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 stale preview decision rejected 409
def test_stale_preview_decision_rejected_409_without_effects(tmp_path: Path):
    run_id = "run-stale-preview"
    repo, home, service, digest = _seed_release_run(tmp_path, run_id, stale=True)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body=_decision_body("delay", digest),
        idem="idem-stale-preview",
    )

    status, payload = _call(api_command.record_release_decision, request)

    assert status == 409
    assert _reason_of(payload) == "stale_preview"
    assert not [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    rejections = _service_events(home, "command.rejected")
    assert any(item.get("reason") == "stale_preview" for item in rejections)
    assert "release.decided" not in _tracks_event_types(repo)


# -- controlled retry: POST /api/runs/{run_id}/retry ---------------------------


# AC-FR0311-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 legal retry accepted from escalation gate
def test_retry_from_legal_position_accepts(tmp_path: Path):
    run_id = "run-retry"
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            _VERSION,
            "run.breaker_tripped",
            {"condition": "attempts exhausted", "report": {"failures": 3}},
        )
    finally:
        store.close()
    assert _state_awaiting(repo, run_id) == ("M-IMPL", "escalation")
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _VERSION)
    finally:
        conn.close()
    service = _make_service(home)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body={"clear_evidence": False},
        idem="idem-retry",
    )

    status, payload = _call(api_command.retry_run, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    row = accepted[0]
    assert row["kind"] == "retry_run"
    assert row["surface"] == "http"
    params = json.loads(row["params_json"])
    assert params["run_id"] == run_id
    assert params["clear_evidence"] is False


# AC-FR0311-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 retry outside legal position rejected with reason
def test_stale_evidence_retry_rejected_409_with_reason(tmp_path: Path):
    run_id = "run-retry-stale"
    repo = tmp_path / "repo"
    store = Store(tracks_home(repo))
    try:
        store.append(run_id, _VERSION, "stage.entered", {"stage": "M-IMPL"})
        store.append(
            run_id,
            _VERSION,
            "verdict.failed",
            {"check": "red_classifier", "ac_refs": ["AC-FR0311-02"], "attempt": 1},
        )
    finally:
        store.close()
    assert _state_awaiting(repo, run_id)[1] != "escalation"
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _VERSION)
    finally:
        conn.close()
    service = _make_service(home)
    request = _sessioned(
        home,
        service,
        path_params={"run_id": run_id},
        body={"clear_evidence": False},
        idem="idem-retry-stale",
    )

    status, payload = _call(api_command.retry_run, request)

    assert status == 409
    assert _reason_of(payload) == "validation_failed"
    assert payload["error"]["detail"], "the rejection must carry a readable reason"
    assert not [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    rejections = _service_events(home, "command.rejected")
    assert rejections, "the controlled-retry rejection must be auditable"


# -- wiring sweep: every mutating route accepts through the command service ----


# AC-FR0289-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 in-scope registration completes synchronously
def test_register_project_completes_and_persists_row(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    service = _make_service(home)
    request = _sessioned(
        home,
        service,
        body={"repo_path": str(repo)},
        idem="idem-register",
        permitted_repos=[tmp_path],
    )

    status, payload = _call(api_command.register_project, request)

    assert status == 201
    assert payload.get("project_id")
    assert payload.get("repo_path") == str(repo)
    assert payload.get("version")
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT repo_path FROM projects").fetchall()
    finally:
        conn.close()
    assert [row[0] for row in rows] == [str(repo)]
    assert _service_events(home, "project.registered")


# AC-FR0289-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 out-of-scope registration rejected 403 audited
def test_register_project_outside_scope_rejected_403(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    service = _make_service(home)
    outsider = tmp_path / "elsewhere"
    outsider.mkdir()
    request = _sessioned(
        home,
        service,
        body={"repo_path": str(outsider)},
        idem="idem-register-oos",
        permitted_repos=[repo],
    )

    status, payload = _call(api_command.register_project, request)

    assert status == 403
    assert _reason_of(payload) == "outside_permitted_scope"
    rejections = _service_events(home, "project.registration_rejected")
    assert any(item.get("reason") == "outside_permitted_scope" for item in rejections)
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT repo_path FROM projects").fetchall()
    finally:
        conn.close()
    assert rows == []


# AC-FR0290-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 readiness route reports four closed probes
def test_readiness_route_reports_closed_check_set(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _VERSION)
    finally:
        conn.close()
    service = _make_service(home)
    request = _sessioned(home, service, path_params={"pid": "proj-1"}, body={}, idem=None)

    status, payload = _call(api_command.run_readiness, request)

    assert status == 200
    assert isinstance(payload.get("ok"), bool)
    checks = payload.get("checks")
    assert isinstance(checks, dict)
    assert set(checks) == {"contract", "harness_model", "credentials_ref", "tools"}
    for probe in checks.values():
        assert isinstance(probe["ok"], bool)
        assert "reason" in probe


# AC-FR0291-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 create_run accepted returns both ids
def test_create_run_route_accepts_and_returns_ids(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        _register_project(conn, repo, _VERSION)
    finally:
        conn.close()
    service = _make_service(home)
    body = {
        "project_id": "proj-1",
        "journey": "feature",
        "version": _VERSION,
        "story": "as a user",
        "issue": None,
        "target": None,
        "preempt": False,
    }
    request = _sessioned(home, service, path_params={"pid": "proj-1"}, body=body)

    status, payload = _call(api_command.create_run, request)

    assert status == 202
    assert payload.get("command_id")
    assert payload.get("run_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    row = accepted[0]
    assert row["kind"] == "create_run"
    assert row["surface"] == "http"
    assert row["project_id"] == "proj-1"


# AC-FR0293-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 clarification reply accepted on the same run
def test_clarification_route_accepts(tmp_path: Path):
    repo, home, service, _ = _seed_approval_run(tmp_path, "run-clarify")
    assert repo is not None
    body = {"doc": "story", "thread_token": "tok-1", "body": "clarified in place"}
    request = _sessioned(
        home, service, path_params={"run_id": "run-clarify"}, body=body
    )

    status, payload = _call(api_command.submit_clarification, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "submit_clarification"
    assert accepted[0]["surface"] == "http"


# AC-FR0294-03@v0.9 TRACKS-TRACE IF-WEBGATE-001 material edit accepted with revision response
def test_edit_material_route_accepts_with_new_revision_key(tmp_path: Path):
    _, home, service, current = _seed_approval_run(tmp_path, "run-edit")
    body = {"base_revision": current, "content": "# Story\n\nrevised\n"}
    request = _sessioned(
        home,
        service,
        path_params={"run_id": "run-edit", "doc": "story"},
        body=body,
    )

    status, payload = _call(api_command.edit_material, request)

    assert status == 202
    assert payload.get("command_id")
    assert payload.get("new_revision")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "edit_material"
    assert accepted[0]["surface"] == "http"


# AC-FR0310-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 pause accepted as two-phase request
def test_pause_route_accepts_with_pause_requested_state(tmp_path: Path):
    _, home, service, _ = _seed_approval_run(tmp_path, "run-pause")
    request = _sessioned(
        home, service, path_params={"run_id": "run-pause"}, body={}
    )

    status, payload = _call(api_command.pause_run, request)

    assert status == 202
    assert payload.get("command_id")
    assert payload.get("state") == "pause_requested"
    assert _service_events(home, "run.pause_requested")


# AC-FR0310-02@v0.9 TRACKS-TRACE IF-WEBGATE-001 resume accepted on the paused run
def test_resume_route_accepts(tmp_path: Path):
    _, home, service, _ = _seed_approval_run(tmp_path, "run-resume")
    request = _sessioned(
        home, service, path_params={"run_id": "run-resume"}, body={}
    )

    status, payload = _call(api_command.resume_run, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "resume_run"
    assert accepted[0]["surface"] == "http"


# AC-FR0313-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 abandon accepted with reason
def test_abandon_route_accepts(tmp_path: Path):
    _, home, service, _ = _seed_approval_run(tmp_path, "run-abandon")
    request = _sessioned(
        home,
        service,
        path_params={"run_id": "run-abandon"},
        body={"reason": "no longer needed"},
    )

    status, payload = _call(api_command.abandon_run, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "abandon_run"
    assert accepted[0]["surface"] == "http"


# AC-FR0312-01@v0.9 TRACKS-TRACE IF-WEBGATE-001 return accepted toward an allowed upstream stage
def test_return_route_accepts_with_confirm(tmp_path: Path):
    _, home, service, _ = _seed_approval_run(tmp_path, "run-return-stage")
    body = {"to": "M-SPEC", "reason": "requirements fix", "confirm": True}
    request = _sessioned(
        home,
        service,
        path_params={"run_id": "run-return-stage"},
        body=body,
    )

    status, payload = _call(api_command.return_stage, request)

    assert status == 202
    assert payload.get("command_id")
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["kind"] == "return_stage"
    assert accepted[0]["surface"] == "http"
