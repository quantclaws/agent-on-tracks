"""Packaged reference-host hotfix plans must target its active release branch."""
from pathlib import Path

import pytest
import tomllib

from tracks.executor.guard_registry import load_guard_registry, validate_guard_registry
from tracks.executor.reference_host import _deploy_closed_set
from tracks.executor.release_gate import build_operation_plan, version_facts


@pytest.mark.parametrize("journey", ["post_release", "dev"])
def test_deployed_hotfix_plan_targets_active_release_branch(tmp_path, journey):
    # AC-FR0277-02/03 and FR-0282: consume the shipped contract after deployment.
    assets = Path(__file__).resolve().parents[2] / "tracks/assets/reference_host"
    _deploy_closed_set(assets, tmp_path)
    contract_path = tmp_path / ".tracks/projects/project.toml"
    contract = tomllib.loads(contract_path.read_text())["host-contract"]
    plan = build_operation_plan(
        contract, journey, version_facts("v0.8-hotfix-42"),
        active_release_branch="releases/v0.8",
    )
    merges = [step for step in plan["steps"] if step.startswith("merge:")]
    expected = ["merge:main", "merge:releases/v0.8"] if journey == "post_release" else [
        "merge:releases/v0.8"
    ]
    assert merges == expected
    registry = load_guard_registry(tmp_path / ".tracks/projects/v0.1/architecture.md")
    assert validate_guard_registry(registry, tmp_path) == ()
