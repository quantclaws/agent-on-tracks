"""T-002 RED (revision round 2): the write-channel operative clauses at the
HTTP handler / service seams.

Unit pins of the three re-planned clauses plus the §2b #39-43 route surface
(the acceptance anchors stay with the frozen integration assets):

1. AC-FR0344-02 — the #17 ``edit_material`` respond must tolerate the
   creation window: projecting ``new_revision`` over a PARTIAL version dir
   (missing trio siblings) never raises; missing pieces degrade to the
   fixed ``missing`` label and a complete directory keeps the v0.10 trio
   digest byte-identical (PLANNING revision: “#17 响应 new_revision 在缺
   文档窗口容错……不得 FileNotFoundError→500”).
2. AC-FR0344-01 — ``submit_clarification`` takes the anchor_line in BOTH
   forms: a line number (int / numeric string) and a line TEXT (the string
   locates the anchor line by its content); each form lands exactly one new
   open root thread under the session actor (§1n.3, frozen test passes the
   line text).
3. AC-NFR0157-01 face — the §2b #39-43 endpoint group is declared on the
   shared ``_mutate`` pipeline (single CSRF+Idempotency-Key path), and the
   #25 return face maps ``validation_failed`` to 409 on both rejection
   routes (``_rejection_status`` / ``_rejection_audit``, the retry #26
   precedent).

AC: FR-0344, NFR-0157 — TRACKS-TRACE IF-DOCSWRITE-001, IF-DOCSAVE-001,
IF-CMDSVC-001, IF-GATEUI-001.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from tracks import paths
from tracks.discuss.parser import parse_threads
from tracks.frontmatter import split_frontmatter
from tracks.server import api_command, auth
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor.service import CommandService

_VERSION = "v0.11"
_PID = "proj-1"
_ACTOR = "local-user"
_RUN = "run-review"
_TS = "2026-10-11T00:00:00+00:00"
_TRIO = (("story", "story.md"), ("spec", "spec.md"), ("acc", "acceptance.md"))
_SPEC_DRAFT = "---\nspec_id: SPEC-1\n---\n\nreviewed spec body\n"


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
    assertion (a 500-shaped crash is the defect this RED pins, never an
    acceptable test error)."""
    try:
        result = asyncio.run(handler(request))
    except Exception as exc:  # noqa: BLE001 — the guard IS the contract pin
        raise AssertionError(
            f"{handler.__name__} must answer, not raise (a crashing respond is"
            f" the 500 the acceptance anchors saw): {type(exc).__name__}: {exc}"
        ) from None
    status = getattr(result, "status_code", None)
    assert isinstance(status, int), (
        f"{handler.__name__} must return api_command.CommandResponse(status_code,"
        " payload)"
    )
    return status, getattr(result, "payload", None)


def _seed(tmp_path: Path, docs: dict[str, str]) -> tuple[Path, Path, CommandService, Path]:
    """A registered project over a (possibly partial) version dir + one run.

    The run plane carries story.requested + stage.entered (the events-only
    seeding the Store projects into the runs row); the service plane carries
    the projects row; CommandService is real.
    """
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / _VERSION
    vdir.mkdir(parents=True)
    for name, body in docs.items():
        (vdir / name).write_text(body, encoding="utf-8")
    store = Store(paths.tracks_home(repo))
    try:
        store.append(_RUN, _VERSION, "story.requested", {"raw_chars": 3})
        store.append(_RUN, _VERSION, "stage.entered", {"stage": "M-REVIEW"})
    finally:
        store.close()
    home = tmp_path / "service-home"
    sdb.ServiceDB(home)
    with sqlite3.connect(home / "service.db") as conn:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, _ACTOR, _TS),
        )
    return repo, home, CommandService(home, sdb.ServiceDB(home), {}), vdir


def _signed(home: Path, service: object, *, idem: str, **kwargs) -> _Request:
    """A request double carrying a real session cookie + CSRF + idempotency."""
    conn = sqlite3.connect(home / "service.db")
    try:
        issued = auth.issue_session(conn, _ACTOR)
    finally:
        conn.close()
    headers = {"X-Trac-CSRF": issued.csrf_token, "Idempotency-Key": idem}
    kwargs.setdefault("cookies", {"trac_session": issued.token})
    return _Request(home, service, headers=headers, **kwargs)


def _expected_trio_token(vdir: Path, edited_name: str, edited_content: str) -> str:
    """Independent trio-token construction: body sha per present piece, the
    fixed ``missing`` label per absent piece (§1n.1 convention applied to the
    #17 face; a complete directory keeps the v0.10 value byte-identical).
    ``edited_name`` empty projects the directory as-is (no override)."""
    pieces = []
    for label, name in _TRIO:
        if name == edited_name and edited_name:
            body = split_frontmatter(edited_content)[1]
        elif (vdir / name).is_file():
            body = split_frontmatter((vdir / name).read_text(encoding="utf-8"))[1]
        else:
            pieces.append(f"{label}:missing")
            continue
        pieces.append(f"{label}:{hashlib.sha256(body.encode('utf-8')).hexdigest()}")
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


# -- clause 1: the #17 creation window never 500s (AC-FR0344-02) --------------


def test_creation_window_edit_projects_revision_without_raising(tmp_path: Path):
    docs = {"story.md": "---\nstory_id: S-001\n---\n\nstory body\n"}
    _repo, home, service, vdir = _seed(tmp_path, docs)
    request = _signed(
        home,
        service,
        idem="idem-window-1",
        path_params={"run_id": _RUN, "doc": "spec"},
        body={"base_revision": None, "content": _SPEC_DRAFT},
    )

    status, payload = _call(api_command.edit_material, request)

    assert status == 202, f"the null-base creation save must not be blocked: {payload}"
    assert isinstance(payload, dict) and payload.get("new_revision"), (
        "the creation-window respond carries a new_revision token (missing pieces"
        " degrade to the missing label — never a crash)"
    )
    assert payload["new_revision"] == _expected_trio_token(vdir, "spec.md", _SPEC_DRAFT)
    assert (vdir / "spec.md").read_text(encoding="utf-8") == _SPEC_DRAFT, (
        "the creation save writes the missing document (不 404、不静默跳过写入)"
    )


def test_existing_document_null_base_stays_409_after_window_save(tmp_path: Path):
    docs = {"story.md": "---\nstory_id: S-001\n---\n\nstory body\n"}
    _repo, home, service, vdir = _seed(tmp_path, docs)
    (vdir / "spec.md").write_text(_SPEC_DRAFT, encoding="utf-8")
    request = _signed(
        home,
        service,
        idem="idem-window-2",
        path_params={"run_id": _RUN, "doc": "spec"},
        body={"base_revision": None, "content": "second attempt\n"},
    )

    status, payload = _call(api_command.edit_material, request)

    assert status == 409, (
        "once the document exists null must be refused unconditionally — the"
        f" second POST answers 409; got {status}: {payload}"
    )
    error = payload.get("error") if isinstance(payload, dict) else None
    assert isinstance(error, dict) and error.get("reason") == "stale_revision"


def test_complete_trio_new_revision_stays_byte_identical(tmp_path: Path):
    docs = {
        "story.md": "---\nstory_id: S-001\n---\n\nstory body\n",
        "spec.md": "---\nspec_id: SP-0\n---\n\nold spec body\n",
        "acceptance.md": "# acc\n\nbody\n",
    }
    _repo, home, service, vdir = _seed(tmp_path, docs)
    revised = "---\nspec_id: SP-1\n---\n\nnew spec body\n"
    request = _signed(
        home,
        service,
        idem="idem-trio-1",
        path_params={"run_id": _RUN, "doc": "spec"},
        body={
            "base_revision": _expected_trio_token(vdir, "", ""),
            "content": revised,
        },
    )

    status, payload = _call(api_command.edit_material, request)

    assert status == 202, f"the bound edit over a complete trio must pass: {payload}"
    assert payload["new_revision"] == _expected_trio_token(vdir, "spec.md", revised), (
        "a complete directory keeps the v0.10 trio digest byte-identical — the"
        " window tolerance is a pure extension"
    )


# -- clause 2: anchor_line dual form (AC-FR0344-01) ---------------------------


def _accept_clarification(service: CommandService, *, key: str, **params) -> None:
    """Accept one clarification; a Rejection or a crash becomes an assertion
    (the anchor forms must be accepted and landed, never refused/raised)."""
    request = {"run_id": _RUN, "doc": "story", **params}
    try:
        service.accept(
            "submit_clarification",
            request,
            actor=_ACTOR,
            actor_class="human",
            surface="http",
            idempotency_key=key,
        )
    except Exception as exc:  # noqa: BLE001 — the guard IS the contract pin
        raise AssertionError(
            f"submit_clarification must accept {params!r} and land the thread;"
            f" got {type(exc).__name__}: {exc}"
        ) from None


def test_anchor_line_text_form_opens_root_thread(tmp_path: Path):
    story = (
        "---\nstory_id: S-001\ntitle: t\n---\n\n# Story\n\n"
        "first line\nsecond line\nthird line\n"
    )
    _repo, _home, service, vdir = _seed(tmp_path, {"story.md": story})

    _accept_clarification(
        service, key="clar-text", anchor_line="second line", body="anchored comment"
    )

    stored = (vdir / "story.md").read_text(encoding="utf-8")
    threads = parse_threads(stored)
    assert len(threads) == 1, (
        "the line-TEXT form opens exactly one new root thread (落盘可查); got"
        f" {len(threads)} threads"
    )
    assert threads[0].status == "open", "the new root thread starts open (§1n.3)"
    assert threads[0].initiator == _ACTOR, "the thread initiator is the session actor"
    assert "anchored comment" in stored, "the clarification body lands in the thread"


def test_anchor_line_numeric_string_form_keeps_working(tmp_path: Path):
    story = (
        "---\nstory_id: S-001\ntitle: t\n---\n\n# Story\n\n"
        "first line\nsecond line\nthird line\n"
    )
    _repo, _home, service, vdir = _seed(tmp_path, {"story.md": story})

    _accept_clarification(
        service, key="clar-num", anchor_line="3", body="numeric comment"
    )

    threads = parse_threads((vdir / "story.md").read_text(encoding="utf-8"))
    assert len(threads) == 1, (
        "the numeric-string form keeps opening exactly one root thread; got"
        f" {len(threads)} threads"
    )


# -- clause 3: #39-43 routes + the #25 409 mapping (AC-NFR0157-01) ------------


_REQUIRED_ROUTE_PATHS = frozenset(
    {
        "/api/projects/{pid}/intakes",
        "/api/intakes/{intake_id}/decisions",
        "/api/projects/{pid}/backlog/{entry_id}/dispositions",
        "/api/runs/{run_id}/triage",
        "/api/runs/{run_id}/review",
    }
)


def test_extension_routes_declare_the_five_new_write_endpoints():
    routes = getattr(api_command, "EXTENSION_ROUTES", None)
    assert isinstance(routes, (list, tuple)) and routes, (
        "api_command.EXTENSION_ROUTES must exist and carry the §2b extension"
        " routes"
    )
    declared = {path for path, _handler, _methods in routes}
    missing = sorted(_REQUIRED_ROUTE_PATHS - declared)
    assert not missing, (
        "the §2b #39-43 write endpoints must be declared on the shared route"
        f" seam (single _mutate pipeline for CSRF+Idempotency-Key); missing:"
        f" {missing}"
    )
    for path, handler, methods in routes:
        if path in _REQUIRED_ROUTE_PATHS:
            assert tuple(methods) == ("POST",), f"{path} is a POST write endpoint"
            assert callable(handler), f"{path} must bind an api_command handler"


def test_return_stage_validation_failed_maps_409_both_rejection_paths(tmp_path: Path):
    _repo, home, service, _vdir = _seed(
        tmp_path, {"story.md": "---\nstory_id: S-001\n---\n\nstory body\n"}
    )
    assert api_command._rejection_status("return_stage", "validation_failed") == 409, (
        "the #25 return face conveys its position via 409 (retry #26 precedent),"
        " not 400/422"
    )
    request = _signed(home, service, idem="idem-return-1")
    session = SimpleNamespace(actor=_ACTOR, actor_class="human")
    response = api_command._rejection_audit(
        request,
        session,
        "return_stage",
        {"run_id": _RUN, "to": "M-SPEC", "reason": "why"},
        "validation_failed",
        "already_executed operations this return would cross (re-submit with"
        " confirm=true): merge release/v0.11 (publish.executed)",
    )
    assert response.status_code == 409, (
        "the audit rejection route maps return_stage validation_failed to 409"
        f" (the already_executed confirmation position); got {response.status_code}"
    )
    error = response.payload.get("error")
    assert isinstance(error, dict) and error.get("reason") == "validation_failed"
