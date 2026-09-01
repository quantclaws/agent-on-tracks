"""Focused ISLAND_GATE_1 contract tests for Fake M-DESIGN."""

from __future__ import annotations

import re
from pathlib import Path

from tracks import paths, templating
from tracks.effects.fake import FakeBackend
from tracks.executor.taskgraph import _requirement_ref, parse_tasks_json
from tracks.executor.test_tasks import _extract_if_registry
from tracks.executor.validate import check_template

_CLOSURE_LINE = re.compile(
    r"^- \*\*((?:N?FR)-\d{4})\*\* "
    r"owner=(\S+) surface=(\S+) composition=(\S+) wiring=(\S+) "
    r"test=(\S+) evidence=(\S+) "
    r"((?:IF-[A-Z]+-\d{3})(?:\s+IF-[A-Z]+-\d{3})*)$"
)

_ACCEPTANCE = """---
acc_id: ACC-001
status: draft
sha:
---

# v0.5 - acceptance

## FR-0010 Deterministic output

### AC-FR0010-01

- observable output is deterministic

## NFR-0020 Stable interface

### AC-NFR0020-01

- the interface remains registered
"""


def _version_dir(tmp_path: Path) -> Path:
    vdir = paths.version_dir(paths.tracks_home(tmp_path), "v0.5")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "acceptance.md").write_text(_ACCEPTANCE, encoding="utf-8")
    return vdir


def test_fake_design_closure_matches_planning_tasks(tmp_path):
    vdir = _version_dir(tmp_path)
    backend = FakeBackend(tmp_path, "v0.5")

    drafted = backend.act(
        "archer",
        "DRAFT",
        None,
        None,
        {"kind": "DRAFT"},
    )
    assert drafted["status"] == "done"

    planned = backend.act(
        "archer",
        "PLANNING",
        None,
        None,
        {"kind": "PLANNING"},
    )
    assert planned["status"] == "done"

    architecture = (vdir / "architecture.md").read_text(encoding="utf-8")
    interfaces = (vdir / "interfaces.md").read_text(encoding="utf-8")
    assert check_template(vdir / "architecture.md") == []
    closure_lines = [
        line for line in architecture.splitlines() if line.startswith("- **") and ("owner=" in line)
    ]
    assert len(closure_lines) == 2

    tasks_raw = (vdir / "tasks.json").read_text(encoding="utf-8")
    tasks, error = parse_tasks_json(tasks_raw)
    assert error is None
    assert [
        match.group(1) for line in closure_lines if (match := _CLOSURE_LINE.fullmatch(line))
    ] == [_requirement_ref(task.ac_refs[0]) for task in tasks]
    for line, task in zip(closure_lines, tasks, strict=True):
        match = _CLOSURE_LINE.fullmatch(line)
        assert match is not None
        assert all(match.group(index) for index in range(1, 8))
        assert not any(
            "{" in match.group(index) or "}" in match.group(index) for index in range(1, 8)
        )
        assert set(match.group(8).split()) == set(task.if_ids)

    registry = _extract_if_registry(interfaces)
    assert registry is not None
    assert all(set(task.if_ids) <= registry for task in tasks)

    from tracks.executor.taskgraph import validate_island_closure

    ok, errors = validate_island_closure(tasks, architecture)
    assert ok, errors


def test_architecture_template_defines_closure_syntax():
    """Machine syntax of the architecture template only: the section header
    and the exact closure-line shape stay meaningful in a template fixture.

    OOB b91 Q0: the former skill-prose assertions (``exact in prose``,
    ``DRAFT / RESPOND``, AC tokens, fixed phrases) were prose pins on
    tracks-archer-design/SKILL.md and are removed per the Human ruling --
    design-skill routing is asserted behaviorally in
    test_archer_skill_routing.py::test_m_design_skills_include_archer_design.
    """
    template = templating.load_template("architecture")
    exact = (
        "- **FR-0010** owner=<...> surface=<...> composition=<...> "
        "wiring=<...> test=<...> evidence=<...> IF-MTEST-001"
    )
    assert "### 1.2 Required AC closure (ISLAND_GATE_1)" in template
    assert exact in template
