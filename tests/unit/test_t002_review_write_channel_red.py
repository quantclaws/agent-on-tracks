"""T-002 RED: review-round write channel (IF-DOCSWRITE-001 / IF-DOCSAVE-001).

Devon-owned unit RED for the T-002 review-round write slice the task declares
(interfaces §1n; IF-DOCSWRITE-001 main contract, IF-DOCSAVE-001 as the reused
explicit-save face):

- ``docs_revision`` globalization (§1n.1): a version directory with missing
  six-piece documents still yields a token — a missing piece degrades to the
  fixed ``label:missing`` marker (the same convention ``baseline.py`` already
  uses for absent inputs), while a complete directory keeps the v0.10
  construction byte-identical (pure extension; the #35 face base token is
  always derivable after globalization);
- #17 pre-approval creation window (§1n.2 as revised by PLANNING round 1,
  DIAGNOSE plan_defect seq 10722): binding AND writing both support creation —
  a null-base save of a document that does not exist yet CREATES it and
  records ``from_revision: null`` (no 404, no silent write skip); once the
  document exists, null is refused with the existing ``stale_revision``
  unconditionally (an acceptance.md that is still missing does not reopen the
  window — the second POST answers 409);
- inline comments (§1n.3): ``submit_clarification`` takes ``thread_token``
  XOR ``anchor_line`` — both given or both missing answer 422
  ``validation_failed``; the anchor_line form opens a new open root thread
  under the session actor, parsable from the stored document (落盘可查).

RED discipline: every failing node guards the absent behavior into a real
``AssertionError`` (getattr/OSError/Rejection guards — no stub tokens, no
assembly errors, no ``pytest.fail``). Fixtures seed the documented storage
for real (run plane through Store, projects row in service.db, CommandService
over ServiceDB); nothing mocks the system under test. The full gate/edit
matrix of the two acceptance anchors stays with the frozen integration
assets; this unit RED pins the service-plane contracts above.

AC: FR-0344 — TRACKS-TRACE IF-DOCSWRITE-001 / IF-DOCSAVE-001.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from tracks import paths
from tracks.discuss.parser import parse_threads
from tracks.frontmatter import split_frontmatter
from tracks.store import Store
from tracks.supervisor import db as sdb
from tracks.supervisor import service as service_mod
from tracks.supervisor.service import CommandService, Rejection

_VERSION = "v0.11"
_PID = "proj-1"
_ACTOR = "local-user"
_RUN = "run-review"
_TS = "2026-10-11T00:00:00+00:00"
_DOCS_ORDER = (
    ("story", "story.md"),
    ("spec", "spec.md"),
    ("acc", "acceptance.md"),
    ("architecture", "architecture.md"),
    ("interfaces", "interfaces.md"),
    ("test_plan", "test-plan.md"),
)


# -- contract guards: the symbols / behaviors the revision introduces -------


def _docs_revision_fn():
    fn = getattr(service_mod, "docs_revision", None)
    assert callable(fn), "supervisor.service must keep the docs_revision(vdir) identity (§1n.1)"
    return fn


def _expected_token(vdir: Path) -> str:
    """Independent §1n.1 recomputation: label:body_sha, missing files -> label:missing."""
    pieces = []
    for label, name in _DOCS_ORDER:
        doc = vdir / name
        if doc.is_file():
            _head, body = split_frontmatter(doc.read_text(encoding="utf-8"))
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        else:
            digest = "missing"
        pieces.append(f"{label}:{digest}")
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


def _accept_edit(
    service: CommandService, doc: str, base_revision: str | None, content: str, *, key: str
):
    """Accept one #17 edit; a Rejection becomes a real assertion."""
    try:
        return service.accept(
            "edit_material",
            {"run_id": _RUN, "doc": doc, "base_revision": base_revision, "content": content},
            actor=_ACTOR,
            actor_class="human",
            surface="http",
            idempotency_key=key,
        )
    except Rejection as rejection:
        raise AssertionError(
            f"edit_material must accept doc={doc!r} base_revision={base_revision!r} here;"
            f" rejected with {rejection.reason}: {rejection.detail}"
        ) from None


def _refuse_edit(
    service: CommandService, doc: str, base_revision: str | None, content: str, *, key: str
) -> Rejection | None:
    """Submit one edit expecting refusal; the Rejection when refused, None
    when accepted (the caller asserts the expected side — a refusal helper
    that never swallows keeps pytest.raises out of the picture)."""
    try:
        service.accept(
            "edit_material",
            {"run_id": _RUN, "doc": doc, "base_revision": base_revision, "content": content},
            actor=_ACTOR,
            actor_class="human",
            surface="http",
            idempotency_key=key,
        )
    except Rejection as rejection:
        return rejection
    return None


def _accept_clarification(service: CommandService, *, key: str, **params):
    """Accept one clarification; a Rejection becomes a real assertion."""
    request = {"run_id": _RUN, "doc": "story", **params}
    try:
        return service.accept(
            "submit_clarification", request,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    except Rejection as rejection:
        raise AssertionError(
            f"submit_clarification must accept {params!r} here;"
            f" rejected with {rejection.reason}: {rejection.detail}"
        ) from None


def _try_clarification(service: CommandService, *, key: str, **params) -> Rejection | None:
    """Submit one clarification; the Rejection when refused, None when accepted."""
    request = {"run_id": _RUN, "doc": "story", **params}
    try:
        service.accept(
            "submit_clarification", request,
            actor=_ACTOR, actor_class="human", surface="http", idempotency_key=key,
        )
    except Rejection as rejection:
        return rejection
    return None


def _outcome_of(rejection: Rejection | None) -> str:
    return "acceptance (no refusal)" if rejection is None else rejection.reason


def _service_events(home: Path, etype: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{home / 'service.db'}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT payload FROM service_events WHERE type = ?", (etype,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def _seed_edit_face(tmp_path: Path, docs: dict[str, str]) -> tuple[Path, Path, CommandService, Path]:
    """A registered project over a partially-filled version dir + one run.

    The run plane carries story.requested + stage.entered (the events-only
    seeding the Store projects into the runs row), the service plane carries
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
        store.append(_RUN, _VERSION, "stage.entered", {"stage": "M-IMPL"})
    finally:
        store.close()
    home = tmp_path / "service-home"
    db = sdb.ServiceDB(home)
    with sqlite3.connect(home / "service.db") as conn:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PID, str(repo), _VERSION, _ACTOR, _TS),
        )
    return repo, home, CommandService(home, db, {}), vdir


# -- docs_revision globalization (§1n.1) --------------------------------------


# AC-FR0344-02@v0.11 TRACKS-TRACE IF-DOCSWRITE-001 missing docs degrade to label:missing
def test_docs_revision_missing_pieces_degrade_to_missing_label(tmp_path: Path):
    docs_revision = _docs_revision_fn()
    vdir = tmp_path / _VERSION
    vdir.mkdir(parents=True)
    (vdir / "story.md").write_text("---\nstory_id: S-001\n---\n\nstory body\n", encoding="utf-8")
    (vdir / "spec.md").write_text("---\nspec_id: SPEC-1\n---\n\nspec body\n", encoding="utf-8")

    try:
        partial = docs_revision(vdir)
    except OSError:
        raise AssertionError(
            "docs_revision must be globalized (§1n.1): a version dir with missing"
            " six-piece documents still has a token — the missing piece degrades to"
            " the fixed label:missing marker, never an OSError -> None -> rejection"
        ) from None

    assert partial == _expected_token(vdir), (
        "the token of a partial dir is the six-piece join with missing pieces as"
        " label:missing (same convention as baseline.m_impl digest inputs)"
    )
    (vdir / "acceptance.md").write_text("# acc\n\nbody\n", encoding="utf-8")
    grown = docs_revision(vdir)
    assert grown != partial, "materializing a missing document moves the token"
    assert grown == _expected_token(vdir)

    for _label, name in _DOCS_ORDER[3:]:
        (vdir / name).write_text(f"# {name}\n\nbody\n", encoding="utf-8")
    complete = docs_revision(vdir)
    assert complete == _expected_token(vdir), (
        "a complete six-piece dir keeps the v0.10 construction byte-identical"
        " (§1n.1 is a pure extension of the docs_revision domain)"
    )


# -- #17 pre-approval creation window (§1n.2, PLANNING round 1 revision) ------


# AC-FR0344-02@v0.11 TRACKS-TRACE IF-DOCSWRITE-001 creation window: null-base
# saves create missing documents; an existing document must be bound
def test_pre_approval_creation_window_creates_then_binds(tmp_path: Path):
    story_head = (
        "---\nstory_id: S-001\ntitle: t\ncreated: 2026-10-11\nstatus: draft\nsha:\n---\n\n"
    )
    _repo, home, service, vdir = _seed_edit_face(
        tmp_path, {"story.md": story_head + "story body\n"}
    )
    spec_draft = "---\nspec_id: SPEC-1\n---\n\nreviewed spec body\n"

    receipt = _accept_edit(service, "spec", None, spec_draft, key="idem-create-1")

    assert receipt.status == "accepted"
    assert (vdir / "spec.md").read_text(encoding="utf-8") == spec_draft, (
        "the creation-window save writes the missing document (不 404、不静默"
        "跳过写入 — the review round creates the document it is reviewing)"
    )
    edits = _service_events(home, "material.edited")
    assert edits, "the creation save lands material.edited (§1a #22)"
    assert edits[-1]["from_revision"] is None, (
        "the created document had no prior revision — from_revision records null"
    )
    assert edits[-1]["to_revision"] == _docs_revision_fn()(vdir), (
        "to_revision is the globalized docs_revision of the saved state (§1n.1)"
    )

    unbound = _refuse_edit(service, "spec", None, "stale attempt\n", key="idem-create-2")
    assert unbound is not None and unbound.reason == "stale_revision", (
        "once the document exists null must be refused unconditionally — the"
        " missing acceptance.md does not reopen the creation window (the second"
        f" POST answers 409; got {_outcome_of(unbound)})"
    )

    (vdir / "acceptance.md").write_text("# acc\n\nbody\n", encoding="utf-8")
    complete = _refuse_edit(service, "story", None, "no base either\n", key="idem-create-3")
    assert complete is not None and complete.reason == "stale_revision", (
        "with the trio complete null stays refused — the reviewed revision has"
        f" to be bound (FR-0308); got {_outcome_of(complete)}"
    )


# -- inline comments via submit_clarification (§1n.3) -------------------------


# AC-FR0344-01@v0.11 TRACKS-TRACE IF-DOCSWRITE-001 anchor_line opens a root thread
def test_submit_clarification_anchor_line_xor_thread_token(tmp_path: Path):
    story = (
        "---\nstory_id: S-001\ntitle: t\n---\n\n# Story\n\n"
        "first line\nsecond line\nthird line\n\nfourth line\n"
    )
    _repo, _home, service, vdir = _seed_edit_face(tmp_path, {"story.md": story})
    assert parse_threads(story) == [], "the fixture story starts without threads"

    both = _try_clarification(service, key="clar-both", thread_token="T-9", anchor_line=2, body="x")
    assert both is not None and both.reason == "validation_failed", (
        "thread_token and anchor_line are mutually exclusive (§1n.3): both given"
        f" must answer 422 validation_failed; got {_outcome_of(both)}"
    )

    neither = _try_clarification(service, key="clar-none", body="x")
    assert neither is not None and neither.reason == "validation_failed", (
        "one of thread_token / anchor_line is required (§1n.3): both missing"
        f" must answer 422 validation_failed; got {_outcome_of(neither)}"
    )

    _accept_clarification(service, key="clar-anchor", anchor_line=2, body="inline comment")

    stored = (vdir / "story.md").read_text(encoding="utf-8")
    threads = parse_threads(stored)
    assert len(threads) == 1, (
        f"the anchor_line form opens exactly one new root thread (落盘可查), got {len(threads)}"
    )
    assert threads[0].status == "open", "the new root thread starts open (§1n.3)"
    assert threads[0].initiator == _ACTOR, (
        "the thread is initiated by the session actor that submitted the clarification"
    )
    assert "inline comment" in stored, "the clarification body lands in the stored thread"
