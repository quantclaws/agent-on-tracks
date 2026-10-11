"""T-006 RED: the fake channel's intake evaluation outlet (task-claimed
IF-INTAKE-001 report schema §1f.2; the evaluation outlet half).

Devon-owned unit RED for the DIAGNOSE-mandated GREEN fix (plan_defect seq
11312, GREEN_GATE seq 11305 / forensics 01M4NQYHEZCCZ762QBH41GZ1ZW): four
frozen anchor nodes strand in ``submitted`` and never reach a terminal
state because ``FakeBackend.act`` routes the ``scribe``/``INTAKE_EVAL``
dispatch into the legacy verdict branch (``status: done`` / ``verdict:
pass``, no ``intake_report`` key) — ``tracks/supervisor/intake.py``'s
``_coerce_report`` rightly judges that non-conforming, so the evaluation
never lands ``evaluated``. The fix face is ``tracks/effects/fake_act.py``
(now inside this task's allowed_paths); this RED pins its contract:

- the ``scribe``/``INTAKE_EVAL`` dispatch returns an outcome carrying an
  ``intake_report`` conforming to the §1f.2 closed schema:
  feasibility{verdict in {feasible, blocked}, blockers},
  duplicates{found, items[{ref, summary, suggestion in {no_go, enhance}}]},
  conflicts{found, pairs[{new_item, existing_ref}]}, a non-empty title, a
  summary, and input_form in {raw, complete};
- the branch is additive: unrelated legacy dispatches keep their shape and
  never leak an ``intake_report`` key;
- input_form derives from the story frontmatter presence in the
  assignment brief's verbatim input body (frontmatter -> complete, plain
  -> raw, unreadable brief -> raw);
- through the real consumer — ``run_intake_evaluation`` over a real
  service.db with the fake agent channel — a submitted intake reaches
  ``evaluated`` with the report landed on the row and exactly the
  ``intake.evaluated`` audit (actor scribe-evaluator): the unit-level
  reproduction of the four stranded anchor nodes.

RED discipline: every failing node is a plain AssertionError on the
contract above (no stub tokens, no assembly errors, no pytest.fail). The
dispatch face is the real ``FakeBackend``; the brief is materialized by
the real ``materialize_assignment`` producer; the end-to-end node drives
the real ``tracks/supervisor/intake.py`` consumer over the real store —
nothing mocks the system under test, and no frozen integration asset is
read or run. The supervisor-claim tick delivery (serve_cmd.py) and the
workbench JS faces are untouched by this RED.

AC: FR-0338 — TRACKS-TRACE IF-INTAKE-001; the report's duplicate/conflict
columns are the §1f.2 schema the FR-0346 anchors consume.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.unit.helpers import git_repo
from tracks.effects import FakeBackend
from tracks.supervisor import intake as intake_mod
from tracks.supervisor.db import ServiceDB

_PID = "proj-1"
_ACTOR = "local-user"
_TS = "2026-10-11T00:00:00+00:00"
_RAW_CONTENT = "v0.11 统一入口的一句话需求"
_COMPLETE_CONTENT = (
    "---\n"
    "story_id: S-001\n"
    "title: 一个标题\n"
    "---\n"
    "\n"
    "# S-001: 一个标题\n"
    "\n"
    "## 1. 原始输入\n"
)


# -- fixtures: the real brief producer over the real store ---------------------


def _intake_row(intake_id: str, content: str) -> dict:
    return {
        "intake_id": intake_id,
        "project_id": _PID,
        "source": "text",
        "content": content,
    }


def _brief_for(home: Path, intake_id: str, content: str) -> Path:
    return intake_mod.materialize_assignment(home, _intake_row(intake_id, content))


def _service_home(tmp_path: Path) -> Path:
    home = tmp_path / "service-home"
    ServiceDB(home)
    return home


def _project_row(home: Path, repo: Path) -> None:
    conn = sqlite3.connect(home / "service.db")
    conn.execute(
        "INSERT INTO projects (project_id, repo_path, version, registered_by,"
        " registered_at) VALUES (?,?,?,?,?)",
        (_PID, str(repo), "v0.11", _ACTOR, _TS),
    )
    conn.commit()
    conn.close()


def _seed_submitted(home: Path, intake_id: str, content: str) -> None:
    stamp = _TS
    ServiceDB(home).insert_intake(
        {
            "intake_id": intake_id,
            "project_id": _PID,
            "source": "text",
            "content": content,
            "issue": None,
            "origin_entry": None,
            "status": "submitted",
            "report_json": None,
            "decision": None,
            "decided_at": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
    )


def _act(backend: FakeBackend, doc_path: Path) -> dict:
    """The exact dispatch ``_dispatch_scribe`` issues (interfaces §1f.2)."""
    outcome = backend.act("scribe", "INTAKE_EVAL", doc=None, doc_path=doc_path)
    assert isinstance(outcome, dict), "act() must return an outcome object"
    return outcome


# -- the §1f.2 closed schema (asserted, never taken from the implementation) ---


def _assert_conforming(report: object) -> None:
    assert isinstance(report, dict), "intake_report must be an object"
    feasibility = report.get("feasibility")
    assert isinstance(feasibility, dict), "feasibility is a §1f.2 object"
    assert feasibility.get("verdict") in {"feasible", "blocked"}, (
        "feasibility.verdict stays in the closed set {feasible, blocked}"
    )
    assert isinstance(feasibility.get("blockers"), list), (
        "feasibility.blockers is a list (empty when nothing blocks)"
    )
    duplicates = report.get("duplicates")
    assert isinstance(duplicates, dict), "duplicates is a §1f.2 object"
    assert isinstance(duplicates.get("found"), bool), (
        "duplicates.found is a boolean (无发现即 false，栏目显式记无)"
    )
    items = duplicates.get("items")
    assert isinstance(items, list), "duplicates.items is a list"
    for item in items:
        assert isinstance(item, dict), "each duplicate item is an object"
        assert isinstance(item.get("ref"), str) and item["ref"], (
            "duplicate refs are non-empty cross-version references (FR-XXXX@vN)"
        )
        assert isinstance(item.get("summary"), str), (
            "each duplicate item carries a summary"
        )
        assert item.get("suggestion") in {"no_go", "enhance"}, (
            "duplicate suggestions stay in {no_go, enhance}"
        )
    conflicts = report.get("conflicts")
    assert isinstance(conflicts, dict), "conflicts is a §1f.2 object"
    assert isinstance(conflicts.get("found"), bool), (
        "conflicts.found is a boolean (无发现即 false，栏目显式记无)"
    )
    pairs = conflicts.get("pairs")
    assert isinstance(pairs, list), "conflicts.pairs is a list"
    for pair in pairs:
        assert isinstance(pair, dict), "each conflict pair is an object"
        assert isinstance(pair.get("new_item"), str) and pair["new_item"], (
            "conflict pairs name the new demand item"
        )
        assert isinstance(pair.get("existing_ref"), str) and pair["existing_ref"], (
            "conflict pairs cite the existing FR reference"
        )
    assert isinstance(report.get("title"), str) and report["title"].strip(), (
        "the suggested title is a non-empty string (§1f.2)"
    )
    assert isinstance(report.get("summary"), str), "the summary is a string"
    assert report.get("input_form") in {"raw", "complete"}, (
        "input_form stays in the closed set {raw, complete}"
    )


# -- the fake channel's INTAKE_EVAL outlet (DIAGNOSE fix face) -----------------


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 conforming report + additive
def test_fake_intake_eval_returns_conforming_report(tmp_path: Path):
    backend = FakeBackend(git_repo(tmp_path), "v0.11")
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    brief = _brief_for(briefs, "I1", _RAW_CONTENT)

    outcome = _act(backend, brief)

    report = outcome.get("intake_report")
    assert isinstance(report, dict), (
        "the scribe/INTAKE_EVAL dispatch must carry an intake_report — the "
        "legacy verdict branch (status done / verdict pass, no report key) "
        "leaves every evaluation non-conforming, stranding intakes in "
        "submitted forever (the four stranded anchor nodes' root cause)"
    )
    _assert_conforming(report)
    legacy = backend.act("scribe", "LEGACY_PROBE", doc=None, doc_path=brief)
    assert isinstance(legacy, dict) and "intake_report" not in legacy, (
        "the INTAKE_EVAL branch is additive — unrelated legacy dispatches "
        "keep their shape and never leak an intake_report key"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 input_form from frontmatter
def test_fake_intake_eval_input_form_from_frontmatter(tmp_path: Path):
    backend = FakeBackend(git_repo(tmp_path), "v0.11")
    briefs = tmp_path / "briefs"
    briefs.mkdir()
    raw_brief = _brief_for(briefs, "I-raw", _RAW_CONTENT)
    complete_brief = _brief_for(briefs, "I-full", _COMPLETE_CONTENT)

    raw_report = _act(backend, raw_brief).get("intake_report")
    assert isinstance(raw_report, dict), (
        "a plain (raw seed) input still must yield its conforming report"
    )
    _assert_conforming(raw_report)
    assert raw_report["input_form"] == "raw", (
        "a verbatim input body without story frontmatter is a raw seed"
    )
    complete_report = _act(backend, complete_brief).get("intake_report")
    assert isinstance(complete_report, dict), (
        "a complete-story input still must yield its conforming report"
    )
    _assert_conforming(complete_report)
    assert complete_report["input_form"] == "complete", (
        "a verbatim input body carrying story frontmatter is a complete story"
    )
    missing = _act(backend, briefs / "nope" / "assignment.md").get("intake_report")
    assert isinstance(missing, dict), "an unreadable brief still reports"
    _assert_conforming(missing)
    assert missing["input_form"] == "raw", (
        "an unreadable brief falls back to raw (never crashes the channel)"
    )


# AC-FR0338-01@v0.11 TRACKS-TRACE IF-INTAKE-001 evaluation reaches evaluated
def test_fake_intake_evaluation_reaches_evaluated(tmp_path: Path):
    repo = git_repo(tmp_path)
    home = _service_home(tmp_path)
    _project_row(home, repo)
    _seed_submitted(home, "I1", _RAW_CONTENT)

    row = intake_mod.run_intake_evaluation(home, "I1")

    assert row.get("status") == "evaluated", (
        "through the real consumer on the fake channel a submitted intake "
        "must reach evaluated — a conforming report lands the row and "
        "appends the intake.evaluated audit (the stranded-anchor failure "
        "mode: retrying in submitted never terminates)"
    )
    report = json.loads(row["report_json"])
    _assert_conforming(report)
    audits = [
        event
        for event in ServiceDB(home).read_events()
        if event.get("type") == "intake.evaluated"
    ]
    assert audits, "the evaluation lands its intake.evaluated service audit"
    for event in audits:
        payload = event.get("payload") or {}
        assert payload.get("intake_id") == "I1", (
            "the audit names the intake it evaluated"
        )
        assert payload.get("actor") == "scribe-evaluator", (
            "the evaluation audit carries the scribe-evaluator actor (§1a #27)"
        )
