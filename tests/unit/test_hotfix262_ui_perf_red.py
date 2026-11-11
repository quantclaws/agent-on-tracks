"""T-008 RED: IF-UIPERF-001 投影与 UI 收口（AC-NFR0150-02 / FR-0333 / FR-0340 / FR-0307）.

Devon-owned unit RED for the remaining IF-UIPERF-001 delivery slice:

- ``projections_core._chain_rows`` returns before any event replay / state
  projection when the version registry has no ACs (interfaces §1g.1);
- ``projections_core.project_todos`` is one inbox covering the awaiting gate
  set, unresolved ``attention.required`` blocks and pending ``evaluated``
  intakes, each item carrying the §1g.5 read fields;
- ``projections_run._backlog_group`` presents a readable ``display_title``
  (event title, first non-empty content line, localized fallback) and never
  the raw ULID entry id (interfaces §1g.4);
- the static view layer consumes those projections: ``releases.js`` renders
  ``display_title``, ``views.js`` renders the readable Todo card and keeps the
  ``data-testid="todo-*"``/navigation contract, ``styles.css`` keeps only the
  outline ring on the selected stage node, and ``timeline.js`` exposes the
  ``aria-selected``/``aria-current`` marks (interfaces §1g.3/§1g.5).

Assertions land on the documented read/UI outlets; fixtures write the real
stores through ``Store`` / ``ServiceDB``. No collection or assembly error is
used as Red: every missing behaviour surfaces as a failed assertion.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tracks import paths
from tracks.server import api_query, projections, projections_core
from tracks.server.redaction import SecretRedactor
from tracks.store.store import Store
from tracks.supervisor import db as sdb

_VERSION = "v0.12"
_STORY_TITLE = "Hotfix UI closure"
_PROJECT_ID = "proj-1"
_APP = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "app"
_CSS = Path(__file__).resolve().parents[2] / "tracks" / "server" / "static" / "styles.css"


# -- fixtures: real stores through the documented write contracts -----------


def _seed_project(
    tmp_path: Path,
    runs: dict[str, list[tuple[str, dict]]] | None = None,
    intakes: list[dict] | None = None,
) -> tuple[Path, Path]:
    """A registered project with real run-plane events and service rows."""
    repo = tmp_path / "repo"
    story = repo / ".tracks" / "projects" / _VERSION / "story.md"
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_text(f"---\ntitle: {_STORY_TITLE}\n---\n", encoding="utf-8")
    store = Store(paths.tracks_home(repo))
    try:
        for run_id, events in (runs or {}).items():
            for event_type, payload in events:
                store.append(run_id, _VERSION, event_type, payload)
    finally:
        store.close()
    home = tmp_path / "service"
    service = sdb.ServiceDB(home)
    conn = sqlite3.connect(home / "service.db")
    try:
        conn.execute(
            "INSERT INTO projects (project_id, repo_path, version, registered_by,"
            " registered_at) VALUES (?,?,?,?,?)",
            (_PROJECT_ID, str(repo), _VERSION, "alice", "2026-10-30T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    for row in intakes or []:
        service.insert_intake(row)
    return home, repo


def _request(home: Path) -> SimpleNamespace:
    state = SimpleNamespace(home=home, redactor=SecretRedactor({}))
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        path_params={"pid": _PROJECT_ID},
        query_params={},
    )


def _releases_payload(home: Path) -> dict:
    result = asyncio.run(api_query.releases(_request(home)))
    assert getattr(result, "status_code", None) == 200
    payload = getattr(result, "payload", None)
    assert isinstance(payload, dict)
    return payload


def _module(name: str) -> str:
    path = _APP / name
    assert path.is_file(), f"tracks/server/static/app/{name} must exist"
    return path.read_text(encoding="utf-8")


# The seven awaiting gate forms plus the AWAITING_RELEASE substate, each
# reached through the real kernel reducer (probe-verified state.awaiting /
# state.substate values), and one unresolved attention block.
_GATE_RUNS: dict[str, list[tuple[str, dict]]] = {
    "run-triage": [
        ("stage.entered", {"stage": "M-STORY"}),
        ("outcome.received", {"status": "done"}),
    ],
    "run-review": [
        ("stage.entered", {"stage": "M-SPEC"}),
        ("lex.verdict", {"verdict": "pass"}),
    ],
    "run-review-park": [
        ("stage.entered", {"stage": "M-SPEC"}),
        ("lex.verdict", {"verdict": "pass-pending-human-threads"}),
    ],
    "run-approval": [
        ("stage.entered", {"stage": "M-REQ-APPROVAL"}),
        ("preview.generated", {"digest": "d" * 64}),
    ],
    "run-release": [
        ("stage.entered", {"stage": "M-RELEASE"}),
        ("release.previewed", {"preview_digest": "p" * 64}),
    ],
    "run-escalation": [
        ("stage.entered", {"stage": "M-IMPL"}),
        ("run.breaker_tripped", {"condition": "stalled", "report": "loss report"}),
    ],
    "run-rollback": [
        ("stage.entered", {"stage": "M-TEST"}),
        ("prism.verdict", {"verdict": "revise", "defect_classification": "acceptance_defect"}),
    ],
    "run-hotfix": [
        ("stage.entered", {"stage": "M-HOTFIX-TRIAGE"}),
        (
            "outcome.received",
            {
                "status": "failed",
                "failure_class": "agent_error",
                "attempt": 3,
                "self_report": "anchor not found",
            },
        ),
    ],
}


# -- ac-chain short-circuit (AC-NFR0150-02, interfaces §1g.1) ---------------


def _write_acceptance(repo: Path, ac_ids: list[str]) -> None:
    blocks = [
        f"## {ac_id.split('-')[1]}\n\n### {ac_id}\n\n- criterion {ac_id}\n"
        for ac_id in ac_ids
    ]
    target = repo / ".tracks" / "projects" / _VERSION / "acceptance.md"
    target.write_text("\n".join(blocks), encoding="utf-8")


def _write_tasks(repo: Path, ac_id: str) -> None:
    payload = {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-1",
                "test_tasks": [
                    {
                        "ac_id": ac_id,
                        "anchors": [f"tests/integration/test_hotfix262_ui_perf.py::{ac_id}"],
                    }
                ],
            }
        ],
    }
    target = repo / ".tracks" / "projects" / _VERSION / "tasks.json"
    target.write_text(json.dumps(payload), encoding="utf-8")


# AC-NFR0150-02@v0.9 TRACKS-TRACE IF-UIPERF-001 empty registry returns before replay
def test_chain_rows_short_circuits_before_event_replay(tmp_path: Path, monkeypatch: Any):
    replayed: list[str] = []
    projected: list[list] = []

    def _spy_events(repo_path: str, run_id: str) -> list:
        replayed.append(run_id)
        return []

    def _spy_project(events: list) -> Any:
        projected.append(events)
        return SimpleNamespace(candidate_sha=None)

    monkeypatch.setattr(projections_core, "_run_events", _spy_events)
    monkeypatch.setattr(projections_core, "project_state", _spy_project)

    repo = tmp_path / "repo"
    (repo / ".tracks" / "projects" / _VERSION).mkdir(parents=True)
    assert projections_core._chain_rows(str(repo), _VERSION, "run-zero-ac") == [], (
        "a 0-AC registry yields no chain rows"
    )
    assert replayed == [], (
        "_chain_rows must read the registry and return before _run_events()/ "
        "project_state() for a 0-AC version (interfaces §1g.1)"
    )
    assert projected == [], "no event replay means no state projection either"

    _write_acceptance(repo, ["AC-NFR0150-02"])
    _write_tasks(repo, "AC-NFR0150-02")
    rows = projections_core._chain_rows(str(repo), _VERSION, "run-one-ac")
    assert [row["ac_id"] for row in rows] == ["AC-NFR0150-02"], (
        "a non-empty registry still produces its chain row"
    )
    assert replayed == ["run-one-ac"], "the populated registry does replay its run"


# -- unified Todos inbox (AC-FR0333-01 / AC-FR0307-01, interfaces §1g.5) ----


def _todo_fields(item: dict) -> None:
    for key in ("version", "title", "stage", "kind_label", "updated_ts", "action_ref"):
        assert isinstance(item.get(key), str) and item[key].strip(), (
            f"todo item must carry a readable {key!r} (interfaces §1g.5), got {item!r}"
        )
    assert item["version"] == _VERSION
    assert isinstance(item["action_ref"], str) and item["action_ref"].startswith("/"), (
        "action_ref navigates to the run timeline / decision face"
    )
    assert item["title"] != item.get("run_id"), (
        "titles never fall back to the raw run identity"
    )
    assert isinstance(item.get("context") or item.get("summary"), str), (
        "summary/context explains why this is a human todo"
    )
    assert item.get("context") or item.get("summary")


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-UIPERF-001 one inbox covers every awaiting gate
def test_project_todos_covers_every_awaiting_gate(tmp_path: Path):
    home, _repo = _seed_project(tmp_path, _GATE_RUNS)

    todos = projections.project_todos(home)

    by_run = {todo.get("run_id"): todo for todo in todos}
    missing = sorted(set(_GATE_RUNS) - set(by_run))
    assert not missing, (
        "the unified inbox covers the whole awaiting gate set + AWAITING_RELEASE"
        f" (interfaces §1g.5); missing {missing}"
    )
    for run_id, item in by_run.items():
        if run_id not in _GATE_RUNS:
            continue
        _todo_fields(item)


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-UIPERF-001 unresolved attention blocks enter the inbox
def test_project_todos_includes_unresolved_attention_block(tmp_path: Path):
    runs = {
        "run-attention": [
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "attention.required",
                {
                    "area": "issue_creation",
                    "reason": "missing_token",
                    "detail": "GITHUB_TOKEN is not set",
                    "next": "export GITHUB_TOKEN=...",
                },
            ),
        ]
    }
    home, _repo = _seed_project(tmp_path, runs)

    todo = next(
        (item for item in projections.project_todos(home)
         if item.get("run_id") == "run-attention"),
        None,
    )

    assert todo is not None, (
        "an unresolved attention.required block is a human todo even when the"
        " machine keeps awaiting=None (interfaces §1g.5)"
    )
    _todo_fields(todo)


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-UIPERF-001 evaluated intakes enter the inbox
def test_project_todos_includes_pending_evaluated_intake(tmp_path: Path):
    intake = {
        "intake_id": "in-evaluated",
        "project_id": _PROJECT_ID,
        "source": "text",
        "content": "raw intake content",
        "status": "evaluated",
        "report_json": json.dumps({"title": "Readable intake request"}),
        "created_at": "2026-10-30T01:00:00+00:00",
        "updated_at": "2026-10-30T01:00:00+00:00",
    }
    home, _repo = _seed_project(tmp_path, {}, [intake])

    todos = projections.project_todos(home)

    todo = next(
        (item for item in todos if item.get("title") == "Readable intake request"),
        None,
    )
    assert todo is not None, (
        "pending evaluated intakes are human decisions and must appear in the"
        " unified inbox (interfaces §1g.5)"
    )
    _todo_fields(todo)


# -- readable backlog titles (AC-FR0340-01, interfaces §1g.4) ----------------


# AC-FR0340-01@v0.11 TRACKS-TRACE IF-UIPERF-001 backlog display_title is readable and never a ULID
def test_backlog_display_title_falls_back_without_ulid(tmp_path: Path):
    runs = {
        "01ENTRY000000000000000000AA": [
            (
                "backlog.recorded",
                {
                    "source": "text",
                    "content": "raw body",
                    "title": "Typed backlog title",
                },
            )
        ],
        "01ENTRY000000000000000000BB": [
            (
                "backlog.recorded",
                {"source": "text", "content": "# Fix noisy release\nMore detail"},
            )
        ],
        "01ENTRY000000000000000000CC": [
            ("backlog.recorded", {"source": "issue", "content": ""})
        ],
    }
    home, _repo = _seed_project(tmp_path, runs)

    entries = _releases_payload(home)["backlog"]["entries"]

    by_id = {entry["entry_id"]: entry for entry in entries}
    for entry in entries:
        assert isinstance(entry.get("display_title"), str) and entry["display_title"].strip(), (
            "every backlog entry carries a display_title (interfaces §1g.4)"
        )
        assert entry["entry_id"] not in entry["display_title"], (
            "the rendered title never contains the raw ULID entry id"
        )
    assert by_id["01ENTRY000000000000000000AA"]["display_title"] == "Typed backlog title", (
        "the event title wins when present"
    )
    assert by_id["01ENTRY000000000000000000BB"]["display_title"] == "Fix noisy release", (
        "the first non-empty content line is stripped of leading #/-/*/> markers"
    )
    fallback = by_id["01ENTRY000000000000000000CC"]["display_title"]
    assert fallback.startswith("待办条目"), (
        f"the final fallback is the localized 待办条目 (<version/date>) label, got {fallback!r}"
    )


# -- static view layer (AC-FR0333-01 / AC-FR0340-01, interfaces §1g.3-5) ----


# AC-FR0340-01@v0.11 TRACKS-TRACE IF-UIPERF-001 releases sidebar renders display_title
def test_releases_module_renders_backlog_display_title():
    releases = _module("releases.js")

    assert "display_title" in releases, (
        "releases.js renders the projection's display_title for backlog entries"
        " (interfaces §1g.4)"
    )
    assert "entry.display_title" in releases, (
        "the backlog sidebar label consumes display_title"
    )
    assert not re.search(r"label:\s*entry\.content\s*\|\|\s*entry\.entry_id", releases), (
        "the sidebar label never falls back to the raw entry id / ULID"
    )


# AC-FR0333-01@v0.11 TRACKS-TRACE IF-UIPERF-001 todo cards are readable and clickable
def test_views_module_renders_readable_clickable_todo_cards():
    views = _module("views.js")

    assert "kind_label" in views, "the Todo card renders kind_label (interfaces §1g.5)"
    assert "action_ref" in views, "the Todo card navigates through action_ref"
    assert re.search(
        r"\$\{[^}]+\}\s*·\s*\$\{[^}]+\}\s*—\s*\$\{[^}]+\}\s*·\s*\$\{[^}]+\}", views
    ), (
        "the card text is `<version> · <title> — <stage> · <kind_label>`"
        " (interfaces §1g.5)"
    )
    assert "todo-${" in views, 'the data-testid="todo-${todo.run_id}" hook is preserved'
    assert "addEventListener" in views, "the card is clickable, not static text"


# AC-FR0307-01@v0.9 TRACKS-TRACE IF-UIPERF-001 todo items expose context and action_ref
def test_todo_items_expose_context_and_action_ref(tmp_path: Path):
    runs = {
        "run-attention": [
            ("stage.entered", {"stage": "M-IMPL"}),
            (
                "attention.required",
                {
                    "area": "issue_creation",
                    "reason": "missing_token",
                    "detail": "GITHUB_TOKEN is not set",
                    "next": "export GITHUB_TOKEN=...",
                },
            ),
        ]
    }
    intake = {
        "intake_id": "in-context",
        "project_id": _PROJECT_ID,
        "source": "text",
        "content": "raw intake content",
        "status": "evaluated",
        "report_json": json.dumps({"title": "Readable intake request"}),
        "created_at": "2026-10-30T01:00:00+00:00",
        "updated_at": "2026-10-30T01:00:00+00:00",
    }
    home, _repo = _seed_project(tmp_path, runs, [intake])

    todos = projections.project_todos(home)
    assert len(todos) >= 2, (
        "both the attention block and the evaluated intake are human todos"
        " (interfaces §1g.5)"
    )
    for todo in todos:
        context = todo.get("context") or todo.get("summary")
        assert isinstance(context, str) and context.strip(), (
            "each todo explains its context/reason (interfaces §1g.5, FR-0307)"
        )
        assert isinstance(todo.get("action_ref"), str) and todo["action_ref"].startswith("/"), (
            "each todo carries a navigable action_ref (FR-0307)"
        )


# -- selected stage ring (interfaces §1g.3) ---------------------------------


# AC-FR0307-01@v0.9 TRACKS-TRACE IF-UIPERF-001 selected stage keeps only an outline ring
def test_selected_stage_style_uses_outline_ring_only():
    css = _CSS.read_text(encoding="utf-8")
    start = css.index(".stage-node-selected,")
    end = css.index(".stage-node {", start)
    selected = css[start:end]

    assert "outline: 2px solid" in selected and "outline-offset" in selected, (
        "the selected stage node is marked by an outline ring (interfaces §1g.3)"
    )
    assert "background" not in selected, (
        "selected state must not overwrite the state background colour"
    )
    assert "color: var(--accent-fg)" not in selected, (
        "selected state must not overwrite the state foreground colour"
    )
    for state in ("state-done", "state-active", "state-attention", "state-void"):
        assert f".stage-node.{state}" in css, (
            f"the {state} colour rule stays authoritative (not overridden by selection)"
        )


# AC-FR0307-01@v0.9 TRACKS-TRACE IF-UIPERF-001 selected stage exposes aria marks
def test_timeline_selection_exposes_aria_marks():
    timeline = _module("timeline.js")

    assert "aria-selected" in timeline, (
        "selection appends aria-selected=true on the node (interfaces §1g.3)"
    )
    assert 'aria-current", "step"' in timeline, (
        "selection appends aria-current=step on the node (interfaces §1g.3)"
    )
