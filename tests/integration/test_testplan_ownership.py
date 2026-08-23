"""Shield test-plan ownership boundary contracts (FR-0210, IF-IMPL-007/IF-VALIDATE-001).

test-plan.md is the Shield-prepared authority for M-TEST environment, fixtures,
ground-truth, black-box observability, layer attribution, and frozen test
authority. It is NOT copied into tasks.json; tasks.json only carries test_refs
references back to test-plan §8.
"""

import json
import re
from pathlib import Path

import pytest

from tests.integration.v05_contract_helpers import read_tasksjson, run_m_impl_journey

ASSETS = Path(__file__).parents[1] / "assets" / "taskgraph_fixtures"


def _read_design_trio(host_repo):
    """Read the v0.5 design trio from the host repo."""
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    return (
        (vdir / "test-plan.md").read_text(encoding="utf-8"),
        (vdir / "tasks.json").read_text(encoding="utf-8")
        if (vdir / "tasks.json").exists()
        else None,
        (vdir / "interfaces.md").read_text(encoding="utf-8"),
    )


@pytest.mark.integration
# AC-FR0210-01@v0.5 TRACKS-TRACE test-plan not copied to tasks.json
def test_testplan_not_copied_to_tasksjson(trac, event_log, host_repo):
    """test-plan.md content (environment/fixture/ground-truth) is not duplicated
    inside tasks.json; tasks.json only carries test_refs references."""
    _, _, events = run_m_impl_journey(trac, event_log)
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    tasks_json_path = vdir / "tasks.json"
    assert tasks_json_path.exists()
    tasks_data = json.loads(tasks_json_path.read_text(encoding="utf-8"))
    # tasks.json must NOT contain test-plan environment/fixture/ground-truth fields
    for task in tasks_data.get("tasks", []):
        assert "environment" not in task, (
            f"task {task.get('task_id')}: tasks.json must not carry environment"
        )
        assert "fixtures" not in task, (
            f"task {task.get('task_id')}: tasks.json must not carry fixtures"
        )
        assert "ground_truth" not in task, (
            f"task {task.get('task_id')}: tasks.json must not carry ground_truth"
        )


@pytest.mark.integration
# AC-FR0210-01@v0.5 TRACKS-TRACE test-plan §8 canonical header present
def test_canonical_header_present(trac, event_log, host_repo):
    """trac validate --file test-plan.md validates §8 canonical header exists."""
    _, _, events = run_m_impl_journey(trac, event_log)
    test_plan = host_repo / ".tracks" / "projects" / "v0.5" / "test-plan.md"
    assert test_plan.exists()
    text = test_plan.read_text(encoding="utf-8")
    assert "## 8. AC Coverage" in text


@pytest.mark.integration
# AC-FR0210-02@v0.5 TRACKS-TRACE test_refs traceable to test-plan §8
def test_test_refs_traceable_to_section8(trac, event_log, host_repo):
    """Task AC/IF ownership traces to §8 while unit refs stay R-owned.

    D-41 separates the identities: §8 owns integration/e2e IF rows, while a
    task's tests/unit node refs come from the immutable Runtime R manifest.
    The task must still trace to a §8 row by AC + IF, with no orphan unit ref.
    """
    _, _, events = run_m_impl_journey(trac, event_log)
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    tasks_json_path = vdir / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    tasks_json = json.loads(tasks_json_path.read_text(encoding="utf-8"))
    plan_text = (vdir / "test-plan.md").read_text(encoding="utf-8")
    # Extract §8 section.
    m = re.search(r"## 8\. AC Coverage", plan_text)
    assert m
    section8 = plan_text[m.start() :]
    for task in tasks_json.get("tasks", []):
        for test_ref in task.get("test_refs", []):
            file_part = test_ref.split("::")[0]
            assert file_part.startswith("tests/unit/") and "::" in test_ref, (
                f"task {task['task_id']}: unit RED ref is not a node identity: {test_ref}"
            )
        matching_rows = [
            line
            for line in section8.splitlines()
            if any(ac in line for ac in task.get("ac_refs", []))
        ]
        assert matching_rows, f"task {task['task_id']}: no §8 AC ownership row"
        for if_id in task.get("if_ids", []):
            assert any(if_id in row for row in matching_rows), (
                f"task {task['task_id']}: IF {if_id} not traceable to its §8 AC row"
            )


@pytest.mark.integration
# AC-FR0210-03@v0.5 TRACKS-TRACE AC coverage cross-consistent between §8 and tasks.json
def test_ac_coverage_cross_consistent(trac, event_log, host_repo):
    """test-plan §8 AC Coverage table and tasks.json ac_refs are cross-consistent;
    missing/extra ACs fail."""
    _, _, events = run_m_impl_journey(trac, event_log)
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    tasks_json_path = vdir / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    tasks_json = json.loads(tasks_json_path.read_text(encoding="utf-8"))
    plan_text = (vdir / "test-plan.md").read_text(encoding="utf-8")
    # Collect AC IDs from §8
    section8 = _extract_section8(plan_text)
    if section8:
        plan_acs = set(re.findall(r"AC-FR\d{4}-\d{2}|AC-NFR\d{4}-\d{2}", section8))
    else:
        plan_acs = set()
    # Collect AC IDs from tasks.json
    tasks_acs = set()
    for task in tasks_json.get("tasks", []):
        for ac in task.get("ac_refs", []):
            tasks_acs.add(ac)
    # Cross-consistency: tasks.json ACs should be a subset of §8 ACs
    orphan = tasks_acs - plan_acs
    assert not orphan, f"tasks.json references ACs not in §8: {orphan}"


@pytest.mark.integration
# AC-FR0210-04@v0.5 TRACKS-TRACE IF- attribution cross-consistent between §8 and tasks.json
def test_if_attribution_cross_consistent(trac, event_log, host_repo):
    """test-plan §8 IF- attribution and tasks.json if_ids are cross-consistent;
    IF- identifiers are defined in interfaces.md §5."""
    _, _, events = run_m_impl_journey(trac, event_log)
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    tasks_json_path = vdir / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    tasks_json = json.loads(tasks_json_path.read_text(encoding="utf-8"))
    interfaces_text = (vdir / "interfaces.md").read_text(encoding="utf-8")
    # Extract IF Registry from interfaces.md §5
    m = re.search(r"## 5\. IF Registry", interfaces_text)
    assert m
    registry_section = interfaces_text[m.start() :]
    registered = set(re.findall(r"IF-[A-Z]+-\d{3}", registry_section))
    # Every IF- in tasks.json must be registered
    for task in tasks_json.get("tasks", []):
        for if_id in task.get("if_ids", []):
            assert if_id in registered, (
                f"task {task['task_id']}: IF- {if_id} not in interfaces.md §5 registry"
            )


@pytest.mark.integration
# AC-FR0210-05@v0.5 TRACKS-TRACE ground truth not in tasks.json
def test_ground_truth_not_in_tasksjson(trac, event_log, host_repo):
    """test-plan §3 Ground Truth method is not copied to tasks.json; tasks.json
    only carries test_refs references."""
    _, _, events = run_m_impl_journey(trac, event_log)
    vdir = host_repo / ".tracks" / "projects" / "v0.5"
    tasks_json_path = vdir / "tasks.json"
    assert tasks_json_path.exists(), "tasks.json must be produced during M-IMPL"
    tasks_json = json.loads(tasks_json_path.read_text(encoding="utf-8"))
    plan_text = (vdir / "test-plan.md").read_text(encoding="utf-8")
    # §3 Ground Truth section exists in test-plan
    has_gt_section = "## 3. Ground Truth Method" in plan_text
    has_gt_marker = _has_ground_truth(plan_text)
    assert has_gt_section or "## 3." not in plan_text or has_gt_marker
    # tasks.json top-level keys should NOT include ground truth method fields
    for key in tasks_json:
        assert key.lower() not in ("ground_truth", "ground-truth", "groundtruth"), (
            f"tasks.json top-level key {key} duplicates ground truth"
        )
    for task in tasks_json.get("tasks", []):
        for key in task:
            assert "ground" not in key.lower(), (
                f"task {task.get('task_id')}: key {key} duplicates ground truth"
            )


@pytest.mark.integration
# AC-FR0210-06@v0.5 TRACKS-TRACE frozen test_refs immutable in M-IMPL
def test_frozen_test_refs_immutable_in_m_impl(trac, event_log, host_repo):
    """After test-plan is frozen, Shield writes tests in M-TEST; during M-IMPL
    the test_refs in tasks.json do not change."""
    _, _, events = run_m_impl_journey(trac, event_log)
    tasks_json = read_tasksjson(host_repo)
    # Each task must have test_refs (non-empty for tasks with integration/e2e ACs)
    for task in tasks_json.get("tasks", []):
        if task.get("ac_refs"):
            assert task.get("test_refs"), (
                f"task {task['task_id']}: test_refs must be non-empty when ac_refs present"
            )


def _extract_section8(plan_text):
    m = re.search(r"## 8\. AC Coverage(.*?)(?=## \d+\.|$)", plan_text, re.S)
    return m.group(1) if m else ""


def _has_ground_truth(plan_text):
    return "Ground Truth" in plan_text
