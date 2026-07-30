"""Event contracts (AC-28a surface) + validate_document (FR-11/FR-19/FR-20)."""
import dataclasses

import pytest

from tests.unit.helpers import ev
from tracks.executor.validate import validate_document
from tracks.kernel.events import COMMAND_KINDS, EVENT_TYPES, EventEnvelope


def test_envelope_fields_match_interfaces_section_2():
    fields = {f.name for f in dataclasses.fields(EventEnvelope)}
    assert fields == {
        "seq", "ts", "run_id", "version", "type",
        "schema_version", "command_id", "task_id", "payload",
    }


def test_envelope_is_frozen():
    e = ev(1, "stage.entered", {"stage": "M-START"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.seq = 2


def test_closed_sets_cover_v01_usage():
    for t in ("command.issued", "outcome.received", "verdict.passed",
              "verdict.failed", "story.committed", "spec.committed",
              "human.triage", "human.review", "sage.verdict", "lex.verdict",
              "backlog.recorded", "run.completed", "stage.rolled_back"):
        assert t in EVENT_TYPES
    for k in ("dispatch_agent", "validate_document", "commit_document",
              "write_frontmatter", "record_backlog", "complete_run",
              "rollback_stage"):
        assert k in COMMAND_KINDS


# -- validate_document (D-16 minimal checks + FR-20 gate) --------------------

FM = "---\nspec_id: SPEC-001\nsha:\n---\n\n"


def test_missing_file_fails_schema(tmp_path):
    check, reason = validate_document(tmp_path / "story.md", "story.md")
    assert check == "schema" and "missing" in reason


def test_missing_frontmatter_fails_schema(tmp_path):
    p = tmp_path / "story.md"
    p.write_text("# no frontmatter\n", encoding="utf-8")
    check, _ = validate_document(p, "story.md")
    assert check == "schema"


def test_valid_document_passes(tmp_path):
    p = tmp_path / "story.md"
    p.write_text(FM + "# 目标\n", encoding="utf-8")
    assert validate_document(p, "story.md") is None


def test_spec_fr_count_gate(tmp_path):
    rows_30 = "\n".join(f"| FR-{i:02d} | x | y |" for i in range(1, 31))
    rows_31 = "\n".join(f"| FR-{i:02d} | x | y |" for i in range(1, 32))
    p = tmp_path / "spec.md"

    p.write_text(FM + rows_30 + "\n", encoding="utf-8")
    assert validate_document(p, "spec.md") is None  # 30 is within scope

    p.write_text(FM + rows_31 + "\n", encoding="utf-8")
    check, reason = validate_document(p, "spec.md")
    assert check == "scope_overflow" and "31" in reason
