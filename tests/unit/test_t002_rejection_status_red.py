"""T-002 RED (RULING seq 10924): the rejection-status contract of the new
write faces (§2b #18/#39/#40/#41; NFR-0157 single-path pipeline).

Unit pins of the five ruling clauses — the status an HTTP rejection must
answer, and what a rejection may never leave behind (a command row, a
written discussion):

- R1: a missing Idempotency-Key answers 400 on EVERY kind — the endpoint
  shape overrides (422) never apply to the missing-credential refusal.
- R2: submit_intake shape refusals (unknown source) answer 422.
- R3: dispose_backlog shape refusals answer 422; an unknown entry 404.
- R4: decide_intake shape refusals (unknown action; remove_backlog on a
  non-backlog source) answer 422.
- R5: the clarification XOR (thread_token/anchor_line both given / both
  missing) answers 422 and writes NOTHING (no command row, no discussion
  byte moves).

The fixed codes stay fixed (constraint of the ruling): #25/#26 409,
awaiting_mismatch/dirty_tree 422, unmerged/issue_fetch_failed 409.

AC: FR-0344, NFR-0157 — TRACKS-TRACE IF-DOCSWRITE-001, IF-DOCSAVE-001,
IF-CMDSVC-001, IF-GATEUI-001.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tracks import paths
from tracks.server import api_command, auth
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService

_VERSION = "v0.11"
_PID = "proj-1"
_ACTOR = "local-user"
_RUN = "run-review"
_TS = "2026-10-11T00:00:00+00:00"
_STORY = "---\nstory_id: S-001\n---\n\nfirst line\nsecond line\n"


# -- the handler seam: request double + guarded invocation --------------------


class _Headers:
    """Case-insensitive header mapping (the Starlette-compatible .get seam)."""

    def __init__(self, raw: dict | None) -> None:
        self._raw = {str(key).lower(): value for key, value in (raw or {}).items()}

    def get(self, name: str, default: object = None) -> object:
        return self._raw.get(str(name).lower(), default)


class _Request:
    """Duck-typed request exposing exactly the api_command handler seam."""

    def __init__(
        self,
        home: Path,
        service: object,
        *,
        path_params: dict | None = None,
        cookies: dict | None = None,
        headers: dict | None = None,
        body: object = None,
    ) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(home=home, service=service, permitted_repos=[])
        )
        self.path_params = dict(path_params or {})
        self.cookies = dict(cookies or {})
        self.headers = _Headers(headers)
        self._body = body

    async def json(self) -> object:
        return self._body


def _call(handler, request: _Request) -> tuple[int, object]:
    """Invoke one mutation handler; ANY server-side exception becomes a real
    assertion (a crashing handler is the 500 class this suite guards)."""
    try:
        result = asyncio.run(handler(request))
    except Exception as exc:  # noqa: BLE001 — the guard IS the contract pin
        raise AssertionError(
            f"{handler.__name__} must answer, not raise:"
            f" {type(exc).__name__}: {exc}"
        ) from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), f"{handler.__name__} must return a CommandResponse"
    return status, getattr(result, "payload", None)


def _seed(tmp_path: Path, docs: dict[str, str] | None = None):
    """A registered project over a version dir + one run + a real service."""
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True)
    for name, body in (docs or {}).items():
        (vdir / name).write_text(body, encoding="utf-8")
    store = Store(paths.tracks_home(repo))
    try:
        store.append(_RUN, _VERSION, "story.requested", {"raw_chars": 3})
        store.append(_RUN, _VERSION, "stage.entered", {"stage": "M-REVIEW"})
    finally:
        store.close()
    home = tmp_path / "service-home"
    sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?,?,?,?,?)",
        (_PID, str(repo), _VERSION, _ACTOR, _TS),
    )
    conn.commit()
    issued = auth.issue_session(conn, _ACTOR)
    conn.close()
    return home, CommandService(home, sdb.ServiceDB(home), {}), vdir, issued


def _signed(home: Path, service: object, *, idem: str | None, **kwargs) -> _Request:
    """A request double with a real session; ``idem=None`` omits the header."""
    conn = sqlite3.connect(home / "service.db")
    try:
        issued = auth.issue_session(conn, _ACTOR)
    finally:
        conn.close()
    headers = {"X-Trac-CSRF": issued.csrf_token}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    kwargs.setdefault("cookies", {"trac_session": issued.token})
    return _Request(home, service, headers=headers, **kwargs)


def _reason_of(payload: object) -> str:
    assert isinstance(payload, dict) and isinstance(payload.get("error"), dict), (
        f"expected the unified error envelope, got {payload!r}"
    )
    return payload["error"]["reason"]


def _commands_and_audits(home: Path, kind: str) -> tuple[int, int]:
    """(persisted command rows, command.rejected audits) for one kind."""
    conn = sqlite3.connect(home / "service.db")
    try:
        rows = conn.execute("SELECT COUNT(*) FROM commands WHERE kind = ?", (kind,)).fetchone()[0]
        audits = conn.execute(
            "SELECT COUNT(*) FROM service_events WHERE type = 'command.rejected'"
            " AND json_extract(payload, '$.kind') = ?",
            (kind,),
        ).fetchone()[0]
    finally:
        conn.close()
    return rows, audits


# -- R1: the missing-credential refusal is 400 on every kind ------------------


def test_missing_idempotency_key_answers_400_on_every_kind(tmp_path: Path):
    home, service, _vdir, _issued = _seed(tmp_path, {"story.md": _STORY})
    faces = [
        (
            api_command.submit_intake,
            {"pid": _PID},
            {"source": "text", "content": "hello"},
            "submit_intake",
        ),
        (
            api_command.dispose_backlog,
            {"pid": _PID, "entry_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ"},
            {"disposition": "removed"},
            "dispose_backlog",
        ),
        (api_command.pause_run, {"run_id": _RUN}, {}, "pause_run"),
    ]
    for handler, path_params, body, kind in faces:
        request = _signed(
            home, service, idem=None, path_params=path_params, body=body
        )
        status, payload = _call(handler, request)
        assert status == 400, (
            f"R1: a missing Idempotency-Key answers 400 on every kind — the"
            f" {kind} endpoint-shape override must not reach the missing-"
            f"credential refusal; got {status}: {payload}"
        )
        assert _reason_of(payload) == "validation_failed"
        rows, audits = _commands_and_audits(home, kind)
        assert rows == 0, f"{kind}: a rejection never persists a command row"
        assert audits >= 1, f"{kind}: the refusal lands the command.rejected audit"


# -- R2/R3: the intake/backlog shape refusals stay 422 / 404 ------------------


def test_submit_intake_shape_refusal_answers_422(tmp_path: Path):
    home, service, _vdir, _issued = _seed(tmp_path)
    request = _signed(
        home,
        service,
        idem="idem-r2",
        path_params={"pid": _PID},
        body={"source": "carrier-pigeon", "content": "hello"},
    )

    status, payload = _call(api_command.submit_intake, request)

    assert status == 422, f"R2: unknown source answers 422; got {status}: {payload}"
    assert _reason_of(payload) == "validation_failed"


def test_dispose_backlog_refusals_answer_422_and_404(tmp_path: Path):
    home, service, _vdir, _issued = _seed(tmp_path)
    shape = _signed(
        home,
        service,
        idem="idem-r3-shape",
        path_params={"pid": _PID, "entry_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ"},
        body={"disposition": "teleported"},
    )
    status, payload = _call(api_command.dispose_backlog, shape)
    assert status == 422, f"R3: unknown disposition answers 422; got {status}"
    assert _reason_of(payload) == "validation_failed"

    unknown_entry = _signed(
        home,
        service,
        idem="idem-r3-entry",
        path_params={"pid": _PID, "entry_id": "01JZZZZZZZZZZZZZZZZZZZZZZZ"},
        body={"disposition": "removed"},
    )
    status, payload = _call(api_command.dispose_backlog, unknown_entry)
    assert status == 404, f"R3: an unknown entry answers 404; got {status}"
    assert _reason_of(payload) == "not_found"


# -- R4: decide_intake shape refusals answer 422 ------------------------------


def _seed_evaluated_intake(home: Path, *, source: str) -> str:
    intake_id = "01INTAKE0000000000000000000"
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO intakes (intake_id, project_id, source, content, issue,"
        " origin_entry, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (intake_id, _PID, source, "content", None, None, "evaluated", _TS, _TS),
    )
    conn.commit()
    conn.close()
    return intake_id


def test_decide_intake_unknown_action_answers_422(tmp_path: Path):
    home, service, _vdir, _issued = _seed(tmp_path)
    intake_id = _seed_evaluated_intake(home, source="text")
    request = _signed(
        home,
        service,
        idem="idem-r4-action",
        path_params={"intake_id": intake_id},
        body={"action": "procrastinate"},
    )

    status, payload = _call(api_command.decide_intake, request)

    assert status == 422, (
        f"R4: an unknown action answers 422 (the decision shape refusal); got"
        f" {status}: {payload}"
    )
    assert _reason_of(payload) == "validation_failed"
    rows, _audits = _commands_and_audits(home, "decide_intake")
    assert rows == 0, "a shape refusal never persists a command row"


def test_decide_intake_remove_backlog_on_text_source_answers_422(tmp_path: Path):
    home, service, _vdir, _issued = _seed(tmp_path)
    intake_id = _seed_evaluated_intake(home, source="text")
    request = _signed(
        home,
        service,
        idem="idem-r4-source",
        path_params={"intake_id": intake_id},
        body={"action": "remove_backlog"},
    )

    status, payload = _call(api_command.decide_intake, request)

    assert status == 422, (
        "R4: remove_backlog on a non-backlog source answers 422 (SM-01.5 shape"
        f" refusal); got {status}: {payload}"
    )
    assert _reason_of(payload) == "validation_failed"


# -- R5: the XOR refusal is 422 and writes nothing -----------------------------


def test_clarification_xor_refusals_answer_422_and_never_write(tmp_path: Path):
    home, service, vdir, _issued = _seed(tmp_path, {"story.md": _STORY})
    both = _signed(
        home,
        service,
        idem="idem-r5-both",
        path_params={"run_id": _RUN},
        body={"doc": "story", "thread_token": "T-9", "anchor_line": "second line", "body": "x"},
    )
    status, payload = _call(api_command.submit_clarification, both)
    assert status == 422, (
        f"R5: thread_token AND anchor_line both given answers 422; got"
        f" {status}: {payload}"
    )
    assert _reason_of(payload) == "validation_failed"

    neither = _signed(
        home,
        service,
        idem="idem-r5-neither",
        path_params={"run_id": _RUN},
        body={"doc": "story", "body": "x"},
    )
    status, payload = _call(api_command.submit_clarification, neither)
    assert status == 422, (
        f"R5: neither thread_token nor anchor_line answers 422; got"
        f" {status}: {payload}"
    )

    assert (vdir / "story.md").read_text(encoding="utf-8") == _STORY, (
        "a rejected clarification never writes a discussion byte"
    )
    rows, audits = _commands_and_audits(home, "submit_clarification")
    assert rows == 0, "the XOR refusal never persists a command row"
    assert audits >= 2, "each refusal lands its command.rejected audit"
