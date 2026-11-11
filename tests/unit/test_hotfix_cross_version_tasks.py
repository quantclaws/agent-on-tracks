"""Cross-version hotfix anchors must retain their source-version identity."""

from tracks.executor.test_tasks import (
    check_design_trace_file,
    check_test_tasks_contract_file,
    parse_test_tasks,
    required_ac_ids,
)


def _fixture(tmp_path):
    projects = tmp_path / "projects"
    for version, ac in (("v0.8", "AC-FR0001-01"), ("v0.12", "AC-FR0002-01")):
        directory = projects / version
        directory.mkdir(parents=True)
        (directory / "acceptance.md").write_text(f"### {ac}\n\n- observable\n")
    (projects / "v0.12" / "interfaces.md").write_text(
        "## 5. IF Registry\n\n### IF-BASE-001\n"
    )
    delta = projects / "v0.12-hotfix-262"
    delta.mkdir()
    (delta / "interfaces.md").write_text("## 5. IF Registry\n\n### IF-DELTA-001\n")
    plan = delta / "test-plan.md"
    plan.write_text(
        "## 8. AC Coverage\n\n| AC id | layer | test | IF |\n"
        "|---|---|---|---|\n"
        "| AC-FR0001-01@v0.8 | integration | test_one | IF-DELTA-001 |\n"
        "| AC-FR0002-01@v0.12 | e2e | test_two | IF-BASE-001 |\n"
    )
    return projects, plan


def test_cross_version_contract_and_shield_tasks(tmp_path):
    projects, plan = _fixture(tmp_path)
    acceptance = projects / "v0.12" / "acceptance.md"
    assert check_test_tasks_contract_file(plan) == []
    assert check_design_trace_file(plan) == []
    assert parse_test_tasks(acceptance, plan) == [
        {"ac_id": "AC-FR0001-01", "layers": ["integration"], "if_ids": ["IF-DELTA-001"]},
        {"ac_id": "AC-FR0002-01", "layers": ["e2e"], "if_ids": ["IF-BASE-001"]},
    ]
    assert required_ac_ids(acceptance, plan) == {"AC-FR0001-01", "AC-FR0002-01"}


def test_wrong_version_fails_even_when_id_exists_in_another_source(tmp_path):
    projects, plan = _fixture(tmp_path)
    plan.write_text(plan.read_text().replace("AC-FR0001-01@v0.8", "AC-FR0001-01@v0.12"))
    issues = check_test_tasks_contract_file(plan)
    assert any("AC-FR0001-01@v0.12" in issue for issue in issues)
    assert parse_test_tasks(projects / "v0.12" / "acceptance.md", plan) == []


def test_missing_version_and_unregistered_interface_fail_closed(tmp_path):
    _, plan = _fixture(tmp_path)
    plan.write_text(plan.read_text().replace("@v0.8", "@v9.9").replace("IF-BASE-001", "IF-FAKE-999"))
    issues = check_test_tasks_contract_file(plan)
    assert any("AC-FR0001-01@v9.9" in issue for issue in issues)
    assert any("IF-FAKE-999" in issue for issue in issues)
