"""T-004 RED: docs_revision edit faces + top-level 409 current_revision.

Devon-owned unit RED for the T-004 delivery slice under the 2026-10-01
spec_gap revision (interfaces §1o v3, §2b #17/#35; IF-DOCSAVE-001 main
contract, IF-DOCREV-001 / IF-WEBGATE-001 as the reused revision and web-gate
binding faces):

- ``tracks/supervisor/service.py`` defines the pure ``docs_revision(vdir)``
  identity (§1o.1a): the six-piece fixed order
  story/spec/acc/architecture/interfaces/test_plan joined as
  ``label:body_sha`` + sha256, isomorphic to ``baseline.revision_digest`` —
  any six-piece body change moves it while the trio digest (FR-0308 approval
  binding) stays untouched;
- ``edit_material`` carries the optional closed ``revision_kind`` enum
  (``"trio"`` default / ``"docs"``, §1o.1b);
- the run-domain edit face #17 converges to the trio domain: ``design`` alias
  and the three design document names answer 422 pointing at the docs-centre
  endpoint;
- the docs-centre edit face #35 (``POST /api/projects/{pid}/docs/{version}/
  {doc}/edits``) binds ``base_revision``/``new_revision`` to ``docs_revision``
  for all six documents, answers 422 ``no_editable_run`` when the version has
  no editable run, and records ``material.edited`` from/to as docs_revision
  values;
- either edit face answers 409 with the live token **top-level** as
  ``current_revision`` (never inside the error object, §1o.2) so the editor's
  reload-discard / force-overwrite options are contract-bound.

RED discipline: ``docs_revision`` and the #35 route do not exist on the
current tree; every failing node guards those absences into a real
``AssertionError`` (callable/route getattr guards, status/schema asserts — no
KeyError, no stub tokens, no assembly errors). Fixtures seed the documented
storage contract for real (ServiceDB + projects row + the run's tracks.db
through Store + the six documents under ``.tracks/projects/<version>/``);
nothing mocks the system under test.

AC: FR-0323 — TRACKS-TRACE IF-DOCSAVE-001 / IF-DOCREV-001 / IF-WEBGATE-001.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracks.baseline import revision_digest
from tracks.frontmatter import split_frontmatter
from tracks.paths import tracks_home
from tracks.server import api_command, auth
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor import service as service_mod
from tracks.supervisor.service import CommandService, Rejection

_VERSION = "v0.10"
_PID = "proj-1"
_ACTOR = "local-user"
_SESSION_COOKIE = "trac_session"
_DOCS_EDIT_PATH = "/api/projects/{pid}/docs/{version}/{doc}/edits"
# docs_revision construction order (interfaces §1o.1a): fixed labels + files.
_DOCS_ORDER = (
    ("story", "story.md"),
    ("spec", "spec.md"),
    ("acc", "acceptance.md"),
    ("architecture", "architecture.md"),
    ("interfaces", "interfaces.md"),
    ("test_plan", "test-plan.md"),
)
_DESIGN_DOCS = ("architecture", "interfaces", "test-plan")
_BASE_BODIES = {
    "story.md": "story body\n",
    "spec.md": "spec body\n",
    "acceptance.md": "acceptance body\n",
    "architecture.md": "architecture body\n",
    "interfaces.md": "interfaces body\n",
    "test-plan.md": "test plan body\n",
}


# -- the handler seam: request double + response extraction ------------------


class _Headers:
    """Case-insensitive header mapping (the Starlette-compatible .get seam)."""

    def __init__(self, raw: dict) -> None:
        self._raw = {str(key).lower(): value for key, value in (raw or {}).items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._raw.get(str(name).lower(), default)


class _Request:
    """Duck-typed request exposing exactly the api_command handler seam."""

    def __init__(
        self,
        home: Path,
        service: Any,
        *,
        path_params: dict | None = None,
        cookies: dict | None = None,
        headers: dict | None = None,
        body: Any = None,
    ) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(home=home, service=service, permitted_repos=[])
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
    assert isinstance(payload, dict) and "error" in payload, (
        f"expected the unified error envelope, got {payload!r}"
    )
    error = payload["error"]
    return error["reason"] if isinstance(error, dict) else error


# -- contract guards: the new symbols + route the revision introduces --------


def _docs_revision_fn():
    fn = getattr(service_mod, "docs_revision", None)
    assert callable(fn), (
        "supervisor.service must define the pure docs_revision(vdir) identity"
        " (interfaces §1o.1a)"
    )
    return fn


def _docs_center_handler():
    routes = getattr(api_command, "EXTENSION_ROUTES", None)
    assert isinstance(routes, (list, tuple)) and routes, (
        "api_command.EXTENSION_ROUTES must declare the §2b #35 docs-centre edit"
        " route for the create_app assembly seam"
    )
    for entry in routes:
        assert isinstance(entry, (list, tuple)) and len(entry) == 3, (
            f"extension route entries are (path, handler, methods): {entry!r}"
        )
        path, handler, methods = entry
        if path == _DOCS_EDIT_PATH:
            assert tuple(methods) == ("POST",), f"{path} is a POST write endpoint"
            assert callable(handler), f"{path} must bind the api_command edit handler"
            return handler
    raise AssertionError(
        f"api_command.EXTENSION_ROUTES must declare {_DOCS_EDIT_PATH!r} (§2b #35)"
    )


# -- fixtures: real stores seeded through the documented contract ------------


def _write_six_piece(vdir: Path) -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    for name, body in _BASE_BODIES.items():
        (vdir / name).write_text(body, encoding="utf-8")


def _expected_docs_revision(vdir: Path) -> str:
    """Independent recomputation of §1o.1a (same construction as baseline)."""
    pieces = []
    for label, name in _DOCS_ORDER:
        _head, body = split_frontmatter((vdir / name).read_text(encoding="utf-8"))
        pieces.append(f"{label}:{hashlib.sha256(body.encode('utf-8')).hexdigest()}")
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


def _open_service_db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(home / "service.db"))


def _seed_project(tmp_path: Path, *, active_run: str | None) -> tuple[Path, Path, Any, Path]:
    """A registered project over the six-piece version dir.

    ``active_run`` pins a non-terminal run of the version (the docs tree's
    editable_run_id); None leaves the version without an editable run.
    """
    repo = tmp_path / "repo"
    vdir = repo / ".tracks" / "projects" / _VERSION
    _write_six_piece(vdir)
    store = Store(tracks_home(repo))
    try:
        if active_run is not None:
            store.append(active_run, _VERSION, "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()
    if active_run is not None:
        store = Store(tracks_home(repo))
        try:
            store.conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, version, status, stage, substate,"
                " awaiting, updated_ts) VALUES (?,?,?,?,?,?,?)",
                (
                    active_run,
                    _VERSION,
                    "active",
                    "M-IMPL",
                    "DRAFT",
                    None,
                    "2026-10-02T09:00:00+00:00",
                ),
            )
            store.conn.commit()
        finally:
            store.close()
    home = tmp_path / "service"
    sdb.ServiceDB(home)
    conn = _open_service_db(home)
    try:
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, _ACTOR, "2026-10-02T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    service = CommandService(home, sdb.ServiceDB(home), SimpleNamespace())
    return repo, home, service, vdir


def _session(home: Path) -> tuple[str, str]:
    """Real session row through auth.issue_session; returns (token, csrf)."""
    conn = _open_service_db(home)
    try:
        issued = auth.issue_session(conn, _ACTOR)
    finally:
        conn.close()
    return issued.token, issued.csrf_token


def _signed_request(home: Path, service: Any, *, idem: str, **kwargs) -> _Request:
    token, csrf = _session(home)
    headers = {"X-Trac-CSRF": csrf, "Idempotency-Key": idem}
    kwargs.setdefault("cookies", {_SESSION_COOKIE: token})
    kwargs.setdefault("headers", headers)
    return _Request(home, service, **kwargs)


def _run_edit(
    home: Path,
    service: Any,
    *,
    run_id: str,
    doc: str,
    base_revision: str,
    content: str,
    idem: str,
) -> tuple[int, Any]:
    """POST one run-domain #17 edit through the real handler."""
    request = _signed_request(
        home,
        service,
        idem=idem,
        path_params={"run_id": run_id, "doc": doc},
        body={"base_revision": base_revision, "content": content},
    )
    return _call(api_command.edit_material, request)


def _docs_edit(
    home: Path,
    service: Any,
    handler: Any,
    *,
    doc: str,
    base_revision: str,
    content: str,
    idem: str,
    version: str = _VERSION,
) -> tuple[int, Any]:
    """POST one docs-centre #35 edit through the declared route handler."""
    request = _signed_request(
        home,
        service,
        idem=idem,
        path_params={"pid": _PID, "version": version, "doc": doc},
        body={"base_revision": base_revision, "content": content},
    )
    return _call(handler, request)


def _approve(
    home: Path,
    service: Any,
    *,
    run_id: str,
    expected_revision: str,
    idem: str,
) -> tuple[int, Any]:
    """POST one approval bound to ``expected_revision`` through the handler."""
    request = _signed_request(
        home,
        service,
        idem=idem,
        path_params={"run_id": run_id},
        body={
            "object": "requirements",
            "expected_revision": expected_revision,
            "decision": "approve",
        },
    )
    return _call(api_command.record_approval, request)


def _draft(doc: str) -> str:
    return f"# {doc} draft\n\nSAVED-DRAFT-MARKER-{doc}\n"


def _accept_edit(service: Any, request: dict) -> Any:
    """Accept one edit_material command directly; Rejection -> AssertionError."""
    try:
        return service.accept(
            "edit_material",
            request,
            actor=_ACTOR,
            actor_class="human",
            surface="cli",
            idempotency_key=None,
        )
    except Rejection as rejection:
        raise AssertionError(
            f"the edit must be accepted, rejected with {rejection.reason}: {rejection.detail}"
        ) from None


# -- audit readers (service.db / tracks.db read-only) ------------------------


def _accepted_commands(home: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM commands").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _material_edited(home: Path) -> list[dict]:
    """material.edited is a §1a service event: it lands in service_events."""
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT payload FROM service_events WHERE type = 'material.edited' ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


# -- docs_revision identity (§1o.1a) -----------------------------------------


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 docs_revision six-piece identity
def test_docs_revision_is_six_piece_and_moves_on_any_doc(tmp_path: Path):
    docs_revision = _docs_revision_fn()
    _repo, _home, _service, vdir = _seed_project(tmp_path, active_run=None)
    initial = docs_revision(vdir)

    assert initial == _expected_docs_revision(vdir), (
        "docs_revision is the six-piece label:body_sha join in fixed order"
        " (story/spec/acc/architecture/interfaces/test_plan), isomorphic to"
        " baseline.revision_digest (§1o.1a)"
    )
    trio_before = revision_digest(vdir)
    (vdir / "architecture.md").write_text("architecture edited\n", encoding="utf-8")
    design_moved = docs_revision(vdir)

    assert design_moved != initial, (
        "a design-piece body change moves docs_revision (the v0.9 trio digest"
        " could not, §1o.1a)"
    )
    assert design_moved == _expected_docs_revision(vdir), (
        "docs_revision stays the six-piece recomputation after a design edit"
    )
    assert revision_digest(vdir) == trio_before, (
        "docs_revision must not touch tracks/baseline.py semantics — the trio"
        " digest stays the approval-bound token (FR-0308 unchanged, §1o.1a)"
    )

    for label, name in _DOCS_ORDER:
        before = docs_revision(vdir)
        (vdir / name).write_text(f"{label} body moved\n", encoding="utf-8")
        assert docs_revision(vdir) != before, (
            f"changing {name} must move the docs_revision identity (§1o.1a)"
        )
        assert docs_revision(vdir) == _expected_docs_revision(vdir)


# -- revision_kind closed enum (§1o.1b) --------------------------------------


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCREV-001 revision_kind closed enum
def test_edit_material_rejects_unknown_revision_kind(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-enum")
    current = revision_digest(vdir)

    try:
        service.accept(
            "edit_material",
            {
                "run_id": "run-enum",
                "doc": "spec",
                "base_revision": current,
                "content": _draft("spec"),
                "revision_kind": "frame",
            },
            actor=_ACTOR,
            actor_class="human",
            surface="cli",
            idempotency_key=None,
        )
    except Rejection as rejection:
        assert rejection.reason == "validation_failed", (
            f"the closed revision_kind enum rejects with validation_failed: {rejection!r}"
        )
    else:
        raise AssertionError(
            "edit_material revision_kind is a closed enum {trio, docs} (§1o.1b);"
            " an unknown value must be rejected"
        )
    assert (vdir / "spec.md").read_text(encoding="utf-8") == "spec body\n", (
        "a rejected revision_kind produces no material write"
    )
    assert not [row for row in _accepted_commands(home) if row["status"] == "accepted"]


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCREV-001 docs kind binds docs_revision
def test_edit_material_docs_kind_binds_and_records_docs_revision(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-docs-kind")
    docs_before = _expected_docs_revision(vdir)
    trio_before = revision_digest(vdir)
    content = _draft("interfaces")

    _accept_edit(
        service,
        {
            "run_id": "run-docs-kind",
            "doc": "interfaces",
            "base_revision": docs_before,
            "content": content,
            "revision_kind": "docs",
        },
    )

    assert (vdir / "interfaces.md").read_text(encoding="utf-8") == content
    docs_after = _expected_docs_revision(vdir)
    assert docs_after != docs_before
    assert _docs_revision_fn()(vdir) == docs_after, (
        "the accepted docs-kind edit moves the docs_revision identity"
    )
    assert revision_digest(vdir) == trio_before, (
        "a design-doc docs-kind edit leaves the trio digest untouched (§1o.1a)"
    )
    edits = _material_edited(home)
    assert edits, "the accepted edit lands material.edited (§1a #22)"
    assert edits[-1]["doc"] == "interfaces"
    assert edits[-1]["from_revision"] == docs_before
    assert edits[-1]["to_revision"] == docs_after, (
        "revision_kind='docs' records docs_revision values in material.edited"
        " from_revision/to_revision (§1o.1b)"
    )


# -- run-domain edit face #17 converges to the trio domain -------------------


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #17 design docs answer 422
@pytest.mark.parametrize("doc", (*_DESIGN_DOCS, "design"))
def test_run_domain_edit_rejects_design_docs_pointing_to_docs_center(tmp_path: Path, doc: str):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run=f"run-{doc}")
    current = revision_digest(vdir)
    target = "architecture.md" if doc == "design" else f"{doc}.md"
    before = (vdir / target).read_text(encoding="utf-8")

    status, payload = _run_edit(
        home,
        service,
        run_id=f"run-{doc}",
        doc=doc,
        base_revision=current,
        content=_draft(doc),
        idem=f"idem-{doc}",
    )

    assert status == 422, (
        f"the run-domain face #17 is trio-only; {doc!r} must answer 422 pointing"
        f" at the docs-centre endpoint (§1o.1b); got {status} {payload!r}"
    )
    error = payload.get("error") if isinstance(payload, dict) else None
    assert isinstance(error, dict) and isinstance(error.get("detail"), str), (
        f"the rejection must carry the unified error envelope, got {payload!r}"
    )
    detail = error["detail"]
    assert "/api/projects/" in detail and "edits" in detail, (
        "the rejection must point at the docs-centre edit endpoint (§1o.1b)"
    )
    assert (vdir / target).read_text(encoding="utf-8") == before, (
        "a rejected design-doc edit makes no material write"
    )


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #17 trio save moves both identities
def test_run_domain_trio_save_moves_trio_and_stale_approval_rejected(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-trio")
    trio_before = revision_digest(vdir)
    docs_before = _expected_docs_revision(vdir)

    status, payload = _run_edit(
        home,
        service,
        run_id="run-trio",
        doc="spec",
        base_revision=trio_before,
        content=_draft("spec"),
        idem="idem-trio-save",
    )

    assert status == 202, f"the trio save must be accepted; got {status} {payload!r}"
    trio_after = revision_digest(vdir)
    assert trio_after != trio_before
    assert payload.get("new_revision") == trio_after, (
        "#17 new_revision is the trio digest (v0.9 approval-bound token)"
    )
    docs_after = _docs_revision_fn()(vdir)
    assert docs_after == _expected_docs_revision(vdir) and docs_after != docs_before, (
        "a trio save moves both identities: the trio digest and docs_revision"
        " (§1o.1a: the save is reflected by either token)"
    )

    status, payload = _approve(
        home,
        service,
        run_id="run-trio",
        expected_revision=trio_before,
        idem="idem-trio-approval",
    )

    assert status == 409, f"an approval bound to the pre-save trio must be stale; got {status}"
    assert _reason_of(payload) == "stale_revision", (
        "FR-0308 approval binding is not bypassed: the old trio revision is"
        " rejected with the existing stale_revision (§1o.3)"
    )


# AC-FR0323-02@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #17 stale 409 top-level trio token
def test_run_domain_stale_409_carries_top_level_trio_token(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-17-conflict")
    first = _draft("spec-first")

    status, _payload = _run_edit(
        home,
        service,
        run_id="run-17-conflict",
        doc="spec",
        base_revision=revision_digest(vdir),
        content=first,
        idem="idem-17-1",
    )
    assert status == 202
    advanced = revision_digest(vdir)

    status, payload = _run_edit(
        home,
        service,
        run_id="run-17-conflict",
        doc="spec",
        base_revision="0" * 64,
        content=_draft("spec-stale"),
        idem="idem-17-2",
    )

    assert status == 409
    assert _reason_of(payload) == "stale_revision"
    assert payload.get("current_revision") == advanced, (
        "the run-domain 409 carries the live trio digest as the response-body"
        " TOP-LEVEL current_revision field (§1o.2 authoritative form)"
    )
    assert "current_revision" not in payload["error"], (
        "current_revision is never nested inside the error object (§1o.2"
        " pins the top-level placement)"
    )
    assert (vdir / "spec.md").read_text(encoding="utf-8") == first, (
        "the stale rejection never silently overwrites the server content"
    )


# -- docs-centre edit face #35 (six-piece, docs_revision token) --------------


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #35 route declaration
def test_docs_center_edit_route_declared_on_api_command_extension_seam():
    routes = getattr(api_command, "EXTENSION_ROUTES", None)
    assert isinstance(routes, (list, tuple)) and routes, (
        "api_command must declare the docs-centre edit route on the"
        " EXTENSION_ROUTES seam so the composition root can bind §2b #35"
    )
    methods_by_path = {path: tuple(methods) for path, _handler, methods in routes}
    assert methods_by_path.get(_DOCS_EDIT_PATH) == ("POST",), (
        f"the extension seam must declare {_DOCS_EDIT_PATH!r} as POST (§2b #35)"
    )
    assert callable(_docs_center_handler())


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #35 no editable run -> 422
def test_docs_center_edit_without_editable_run_is_no_editable_run(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run=None)
    handler = _docs_center_handler()

    status, payload = _docs_edit(
        home,
        service,
        handler,
        doc="interfaces",
        base_revision=_expected_docs_revision(vdir),
        content=_draft("interfaces"),
        idem="idem-no-editable-run",
    )

    assert status == 422, (
        f"a version without an editable run answers 422 (§1o.1b); got {status} {payload!r}"
    )
    assert _reason_of(payload) == "no_editable_run", (
        "the #35 failure reason is the closed-set token no_editable_run (§2b #35)"
    )
    assert (vdir / "interfaces.md").read_text(encoding="utf-8") == "interfaces body\n"


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #35 design save moves docs_revision
def test_docs_center_edit_design_doc_moves_docs_revision(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-35-design")
    handler = _docs_center_handler()
    docs_before = _expected_docs_revision(vdir)
    content = _draft("interfaces")

    status, payload = _docs_edit(
        home,
        service,
        handler,
        doc="interfaces",
        base_revision=docs_before,
        content=content,
        idem="idem-35-design",
    )

    assert status == 202, (
        f"the docs-centre face accepts a design-doc save with base=docs_revision"
        f" (§1o.1b); got {status} {payload!r}"
    )
    docs_after = _expected_docs_revision(vdir)
    assert docs_after != docs_before, (
        "a design-doc save through #35 moves docs_revision (§1o.1a) — the"
        " structural impossibility the v0.9 face had"
    )
    assert payload.get("new_revision") == docs_after, (
        "#35 returns new_revision as the new docs_revision (§2b #35)"
    )
    assert _docs_revision_fn()(vdir) == docs_after
    assert (vdir / "interfaces.md").read_text(encoding="utf-8") == content
    edits = _material_edited(home)
    assert edits, "the accepted #35 save lands material.edited (§1a #22)"
    assert edits[-1]["doc"] == "interfaces"
    assert edits[-1]["from_revision"] == docs_before
    assert edits[-1]["to_revision"] == docs_after, (
        "#35 material.edited from/to are docs_revision values (§1o.1b)"
    )
    accepted = [row for row in _accepted_commands(home) if row["status"] == "accepted"]
    assert len(accepted) == 1 and accepted[0]["kind"] == "edit_material"
    assert accepted[0]["run_id"] == "run-35-design", (
        "#35 resolves the version's editable_run_id and submits edit_material on it"
    )


# AC-FR0323-02@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #35 409 docs token + force overwrite
def test_docs_center_stale_409_carries_docs_token_and_force_overwrite(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-35-conflict")
    handler = _docs_center_handler()
    docs_before = _expected_docs_revision(vdir)
    first = _draft("interfaces-first")

    status, _payload = _docs_edit(
        home,
        service,
        handler,
        doc="interfaces",
        base_revision=docs_before,
        content=first,
        idem="idem-35-1",
    )
    assert status == 202, "the first docs-centre save must advance docs_revision"
    advanced = _expected_docs_revision(vdir)
    assert advanced != docs_before

    draft = _draft("interfaces-draft")
    status, payload = _docs_edit(
        home,
        service,
        handler,
        doc="interfaces",
        base_revision=docs_before,
        content=draft,
        idem="idem-35-2",
    )

    assert status == 409, f"a stale base docs_revision must answer 409; got {status} {payload!r}"
    assert _reason_of(payload) == "stale_revision"
    assert payload.get("current_revision") == advanced, (
        "the #35 409 carries the live docs_revision as the top-level"
        " current_revision field (§1o.2)"
    )
    assert (vdir / "interfaces.md").read_text(encoding="utf-8") == first, (
        "the stale rejection never silently overwrites the server content"
    )

    status, payload = _docs_edit(
        home,
        service,
        handler,
        doc="interfaces",
        base_revision=advanced,
        content=draft,
        idem="idem-35-3",
    )

    assert status == 202, (
        f"force overwrite rebinding base to current_revision must be accepted"
        f" (§1o.2); got {status} {payload!r}"
    )
    assert (vdir / "interfaces.md").read_text(encoding="utf-8") == draft
    assert payload.get("new_revision") == _expected_docs_revision(vdir), (
        "the force overwrite returns the new docs_revision"
    )


# AC-FR0323-01@v0.10 TRACKS-TRACE IF-DOCSAVE-001 #35 trio save moves trio + stale approval
def test_docs_center_trio_save_moves_trio_and_rejects_stale_approval(tmp_path: Path):
    _repo, home, service, vdir = _seed_project(tmp_path, active_run="run-35-trio")
    handler = _docs_center_handler()
    trio_before = revision_digest(vdir)
    docs_before = _expected_docs_revision(vdir)

    status, payload = _docs_edit(
        home,
        service,
        handler,
        doc="spec",
        base_revision=docs_before,
        content=_draft("spec"),
        idem="idem-35-trio-save",
    )

    assert status == 202, f"a trio save through #35 must be accepted; got {status} {payload!r}"
    assert revision_digest(vdir) != trio_before, (
        "a trio save through #35 also moves the trio digest (structural"
        " guarantee, §1o.1a/§1o.3)"
    )
    assert _docs_revision_fn()(vdir) != docs_before

    status, payload = _approve(
        home,
        service,
        run_id="run-35-trio",
        expected_revision=trio_before,
        idem="idem-35-trio-approval",
    )

    assert status == 409, f"the pre-save trio approval must be stale; got {status} {payload!r}"
    assert _reason_of(payload) == "stale_revision", (
        "FR-0308 is structurally preserved: trio saves through either edit"
        " face move the approval-bound token (§1o.3)"
    )
