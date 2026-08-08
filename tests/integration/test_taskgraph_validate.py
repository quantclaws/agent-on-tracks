"""Task-plan parsing, DAG, scope, and AC closure through IF-IMPL-003."""

from pathlib import Path

import pytest

from tracks.executor.taskgraph import (
    parse_taskgraph,
    validate_ac_coverage,
    validate_dag,
    validate_scope,
)

ASSETS = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


@pytest.mark.integration
# AC-FR0030-01@v0.5 TRACKS-TRACE task graph is parsed content truth
# AC-FR0180-01@v0.5 TRACKS-TRACE runtime parses Task List and dependencies
def test_taskgraph_happy_path_from_public_file_contract():
    text = (ASSETS / "valid.md").read_text(encoding="utf-8")
    tasks, error = parse_taskgraph(text)
    assert error is None
    assert [task.task_id for task in tasks] == ["T-001", "T-002"]
    assert tasks[1].depends_on == ("T-001",)
    assert tasks[1].parallel is True


@pytest.mark.integration
# AC-FR0030-02@v0.5 TRACKS-TRACE cycle scope and AC gaps fail closed
# AC-FR0180-02@v0.5 TRACKS-TRACE task-plan validation errors are observable
def test_taskgraph_key_error_paths_fail_closed():
    cyclic, error = parse_taskgraph(
        (ASSETS / "cycle_overlap.md").read_text(encoding="utf-8")
    )
    assert error is None
    acyclic, cycle = validate_dag(cyclic)
    no_overlap, overlap_errors = validate_scope(cyclic)
    covered, coverage_errors = validate_ac_coverage(
        cyclic, ["AC-FR0030-02", "AC-FR9999-01"], {"IF-IMPL-003"}
    )
    assert acyclic is False and cycle.startswith("cycle:")
    assert no_overlap is False and "tracks/executor/taskgraph.py" in overlap_errors[0]
    assert covered is False and "AC-FR9999-01" in coverage_errors[0]
