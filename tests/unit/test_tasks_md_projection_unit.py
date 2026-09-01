"""OB-4 unit tests: render migration contract, idempotence, guard pure logic."""

from __future__ import annotations

from pathlib import Path

from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.taskgraph import (
    classify_tasks_md_guard,
    parse_tasks_json,
    render_tasks_md,
)

# ---------------------------------------------------------------------------
# Migration contract: render_tasks_md vs real v0.8 sample (byte-identical)
# ---------------------------------------------------------------------------


def test_render_migration_contract_byte_identical():
    repo = Path(__file__).resolve().parents[2]
    tasks_json_path = repo / ".tracks" / "projects" / "v0.8" / "tasks.json"
    tasks_md_path = repo / ".tracks" / "projects" / "v0.8" / "tasks.md"
    # These files are the runtime projection's authoritative sample — read-only
    assert tasks_json_path.exists(), f"missing {tasks_json_path}"
    assert tasks_md_path.exists(), f"missing {tasks_md_path}"
    raw = tasks_json_path.read_text(encoding="utf-8")
    tasks, err = parse_tasks_json(raw)
    assert err is None, err
    rendered = render_tasks_md(tasks)
    expected = tasks_md_path.read_text(encoding="utf-8")
    assert rendered == expected, "render_tasks_md must be byte-identical to .tracks/projects/v0.8/tasks.md"
    # Also ensure the old delegating method stays identical
    legacy = MImplRuntimeMixin._tasks_md(tasks)
    assert legacy == rendered
    assert legacy == expected


def test_render_idempotent():
    """render(parse(render(...))) == render — deterministic and stable."""
    raw = (Path(__file__).resolve().parents[2] / ".tracks" / "projects" / "v0.8" / "tasks.json").read_text(encoding="utf-8")
    tasks, err = parse_tasks_json(raw)
    assert err is None
    first = render_tasks_md(tasks)
    # Re-parse the same tasks.json text and re-render must be identical
    tasks2, err2 = parse_tasks_json(raw)
    assert err2 is None
    second = render_tasks_md(tasks2)
    assert first == second
    # Also calling render twice on same list is idempotent
    assert render_tasks_md(tasks) == render_tasks_md(tasks)


# ---------------------------------------------------------------------------
# Guard pure logic three branches (violation / system_repaired / ok)
# ---------------------------------------------------------------------------


def test_guard_violation_when_in_diff_and_mismatch():
    assert classify_tasks_md_guard(True, "rendered", "tampered") == "violation"
    assert classify_tasks_md_guard(True, "a", "b") == "violation"


def test_guard_system_repaired_when_not_in_diff_but_mismatch():
    assert classify_tasks_md_guard(False, "rendered", "tampered") == "system_repaired"
    assert classify_tasks_md_guard(False, "expected", "different") == "system_repaired"


def test_guard_ok_when_match_regardless_of_diff():
    assert classify_tasks_md_guard(True, "same", "same") == "ok"
    assert classify_tasks_md_guard(False, "same", "same") == "ok"
    # rendered == post_md → ok even if content is empty
    assert classify_tasks_md_guard(True, "", "") == "ok"


def test_guard_skipped_when_render_none():
    # tasks.json unparsable → guard skips (taskgraph channel reports)
    assert classify_tasks_md_guard(True, None, "whatever") == "skipped"
    assert classify_tasks_md_guard(False, None, "whatever") == "skipped"
    assert classify_tasks_md_guard(False, None, None) == "skipped"


def test_guard_inherited_dirty_not_counted_as_violation():
    # The critical F-02 incremental attribution property: a mismatch that is
    # NOT in this turn's diff must heal but must NOT be counted as a violation.
    # This is the "inherited dirty" self-heal path.
    assert classify_tasks_md_guard(False, "rendered", "old_projection") == "system_repaired"
    assert classify_tasks_md_guard(False, "rendered", "old_projection") != "violation"


def test_guard_empty_post_md_treated_as_mismatch():
    # Missing tasks.md (post_md None) vs rendered content is a mismatch
    assert classify_tasks_md_guard(True, "rendered", None) == "violation"
    assert classify_tasks_md_guard(False, "rendered", None) == "system_repaired"
