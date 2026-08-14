"""tasks.json five-check validation contract (FR-0180, IF-IMPL-003/IF-VALIDATE-001).

Each of the five structural checks must fail individually with a non-zero exit
and a position indicator (task ID / AC ID / issue number) on stderr.
"""

import json

import pytest

from tests.integration.v05_contract_helpers import (
    validate_tasksjson,
)

# Each fixture triggers exactly one of the five checks; the other four pass.
# (fixture_name, expected_keyword_in_output, expected_position_token)
_FIVE_CHECKS = [
    ("dag_cycle.json", "cycle", "T-001"),
    ("scope_overlap.json", "overlap", "T-001"),
    ("ac_coverage_gap.json", "not covered", "AC-FR0030-01"),
    ("if_invalid.json", "IF-IMPL-999", "T-001"),
    ("issue_invalid.json", "issue", "T-001"),
]


@pytest.mark.integration
# AC-FR0180-04@v0.5 TRACKS-TRACE five checks fail individually with position
def test_five_checks_individual_failures(trac, host_repo):
    """Each of the 5 tasks.json validation checks fails independently and
    reports the offending position (task ID / AC ID / issue number)."""
    for fixture_name, keyword, position in _FIVE_CHECKS:
        result, output = validate_tasksjson(trac, host_repo, fixture_name)
        assert result.returncode != 0, f"{fixture_name}: expected non-zero exit for check failure"
        assert keyword.lower() in output, (
            f"{fixture_name}: expected '{keyword}' in validate output, got: {output}"
        )
        assert position.lower() in output, (
            f"{fixture_name}: expected position '{position}' in validate output, got: {output}"
        )


@pytest.mark.integration
# AC-FR0180-04@v0.5 TRACKS-TRACE valid tasks.json passes all five checks
def test_five_checks_valid_passes(trac, host_repo):
    """A valid tasks.json with no defects passes all five checks (exit 0)."""
    result, _ = validate_tasksjson(trac, host_repo, "valid.json")
    assert result.returncode == 0, (
        f"valid tasks.json should pass all checks, got: {result.stdout}\n{result.stderr}"
    )


@pytest.mark.integration
# AC-FR0180-02@v0.5 TRACKS-TRACE DAG cycle detected with cycle description
def test_validate_dag(trac, host_repo):
    """DAG with a cycle is detected and reported with the cycle path."""
    result, output = validate_tasksjson(trac, host_repo, "dag_cycle.json")
    assert result.returncode != 0
    assert "cycle" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0180-02@v0.5 TRACKS-TRACE scope overlap detected with conflicting tasks
def test_validate_scope(trac, host_repo):
    """Scope boundary overlap between two tasks is detected and reported."""
    result, output = validate_tasksjson(trac, host_repo, "scope_overlap.json")
    assert result.returncode != 0
    assert "overlap" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0180-02@v0.5 TRACKS-TRACE required AC coverage gap detected with AC ID
def test_validate_ac_coverage(trac, host_repo):
    """Required AC not covered by any task is detected and reported with AC ID."""
    result, output = validate_tasksjson(trac, host_repo, "ac_coverage_gap.json")
    assert result.returncode != 0
    assert "not covered" in output
    assert "ac-fr0030-01" in output


@pytest.mark.integration
# AC-FR0180-02@v0.5 TRACKS-TRACE invalid IF- identifier detected with task ID
def test_validate_if_validity(trac, host_repo):
    """IF- identifier not in interfaces.md registry is detected and reported."""
    result, output = validate_tasksjson(trac, host_repo, "if_invalid.json")
    assert result.returncode != 0
    assert "if-impl-999" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0180-02@v0.5 TRACKS-TRACE invalid issue number detected with task ID
def test_validate_issue_numbers(trac, host_repo):
    """Issue number that is not a positive integer is detected and reported."""
    result, output = validate_tasksjson(trac, host_repo, "issue_invalid.json")
    assert result.returncode != 0
    assert "issue" in output
    assert "t-001" in output


@pytest.mark.integration
# AC-FR0180-01@v0.5 TRACKS-TRACE tasks.json parsed by Runtime to drive DAG scheduling
def test_tasksjson_parsed_by_runtime(trac, host_repo):
    """Runtime parses tasks.json to drive DAG scheduling; the taskgraph.committed
    event fires with parsed task list."""
    from tests.integration.helpers import walk_to_m_test

    walk_to_m_test(trac, version="v0.5")
    assert trac("run").returncode == 0
    # After M-IMPL PLANNING, tasks.json should exist and be parsed
    tasks_json = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    assert tasks_json.exists()
    data = json.loads(tasks_json.read_text(encoding="utf-8"))
    assert "tasks" in data
    assert len(data["tasks"]) > 0
    for task in data["tasks"]:
        assert "task_id" in task
        assert "issue_number" in task
        assert "if_ids" in task
        assert "scope_boundary" in task
        assert "depends_on" in task
        assert "budget" in task


@pytest.mark.integration
# AC-FR0180-01@v0.5 TRACKS-TRACE tasks.md projected from tasks.json deterministically
def test_tasksmd_projected(trac, host_repo):
    """tasks.md is a human-readable projection generated from tasks.json by Runtime."""
    from tests.integration.helpers import walk_to_m_test

    walk_to_m_test(trac, version="v0.5")
    assert trac("run").returncode == 0
    tasks_json = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.json"
    tasks_md = host_repo / ".tracks" / "projects" / "v0.5" / "tasks.md"
    assert tasks_json.exists()
    assert tasks_md.exists()
    json_data = json.loads(tasks_json.read_text(encoding="utf-8"))
    md_text = tasks_md.read_text(encoding="utf-8")
    for task in json_data["tasks"]:
        assert task["task_id"] in md_text


@pytest.mark.integration
# AC-FR0180-01@v0.5 TRACKS-TRACE trac report rebuilds per-task progress from events
def test_report_rebuild(trac, host_repo):
    """trac report reconstructs per-task progress from the event stream."""
    from tests.integration.helpers import walk_to_m_test

    walk_to_m_test(trac, version="v0.5")
    result = trac("run")
    assert result.returncode == 0
    # Extract run_id from status
    status = trac("status")
    assert status.returncode == 0
    report = trac("report", "--run-id", "latest", "--output", str(host_repo / "report_out"))
    assert report.returncode == 0


@pytest.mark.integration
# AC-FR0180-05@v0.5 TRACKS-TRACE Runtime validation is structural only
def test_runtime_validation_is_structural_only(trac, host_repo):
    """Runtime validate only checks deterministic structural invariants (DAG/scope/
    AC coverage/IF- validity/issue number); it does not perform semantic review."""
    result, _ = validate_tasksjson(trac, host_repo, "valid.json")
    # Structural validation passes (exit 0); semantic review is Prism's job
    assert result.returncode == 0
    output = result.stdout.lower()
    # Runtime does NOT emit semantic judgments like "good decomposition"
    assert "semantic" not in output
    assert "quality" not in output
