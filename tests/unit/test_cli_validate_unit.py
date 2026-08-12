"""Unit tests for CLI tasks.json validation (IF-VALIDATE-001).

Covers missing-file error, missing acceptance.md / interfaces.md context
errors, and unparseable interfaces.md.
"""
from __future__ import annotations

from pathlib import Path

from tracks.cli.main import _validate_tasksjson

FIXTURES = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"

_VALID = (FIXTURES / "valid.json").read_text(encoding="utf-8")
_ACC = (FIXTURES / "acceptance.md").read_text(encoding="utf-8")
_IF = (FIXTURES / "interfaces.md").read_text(encoding="utf-8")


def test_validate_tasksjson_missing_file(tmp_path):
    path = tmp_path / "nonexistent" / "tasks.json"
    issues = _validate_tasksjson(path)
    assert len(issues) == 1
    assert "missing file" in issues[0].lower()


def test_validate_tasksjson_missing_acceptance(tmp_path):
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(_VALID, encoding="utf-8")
    issues = _validate_tasksjson(tasks_json)
    assert any("missing acceptance.md" in i for i in issues)


def test_validate_tasksjson_missing_interfaces(tmp_path):
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(_VALID, encoding="utf-8")
    (tmp_path / "acceptance.md").write_text(_ACC, encoding="utf-8")
    issues = _validate_tasksjson(tasks_json)
    assert any("missing interfaces.md" in i for i in issues)


def test_validate_tasksjson_unparseable_interfaces(tmp_path):
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(_VALID, encoding="utf-8")
    (tmp_path / "acceptance.md").write_text(_ACC, encoding="utf-8")
    (tmp_path / "interfaces.md").write_text(
        "---\nversion: v0.5\n---\n\nNo IF Registry section\n",
        encoding="utf-8",
    )
    issues = _validate_tasksjson(tasks_json)
    assert any("IF Registry" in i for i in issues)


def test_validate_tasksjson_valid_no_context_errors(tmp_path):
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(_VALID, encoding="utf-8")
    (tmp_path / "acceptance.md").write_text(_ACC, encoding="utf-8")
    (tmp_path / "interfaces.md").write_text(_IF, encoding="utf-8")
    issues = _validate_tasksjson(tasks_json)
    assert issues == []
