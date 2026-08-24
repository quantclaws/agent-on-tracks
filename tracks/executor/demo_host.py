"""Dynamic demo-host and fail-closed demonstration declarations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FAIL_CLOSED_SCENARIOS = (
    "broad_mutation",
    "stale_patch",
    "wrong_candidate",
    "uncollected_node",
    "unrelated_red",
    "target_survived",
    "control_hit",
    "malformed_adapter_result",
    "guard_parity_mismatch",
)


@dataclass(frozen=True)
class DemoHostReport:
    repo: Path
    venv: Path
    wheel_sha256: str
    import_path: str
    architecture_path: Path
    registry_digest: str
    hooks_path: str
    ci_binding: str
    adapter_id: str


@dataclass(frozen=True)
class ScenarioFixture:
    scenario: str
    host: str
    manifest_or_config_ref: str
    expected_block_reason: str


def create_demo_host(
    template_dir: Path, target_dir: Path, wheel: Path
) -> DemoHostReport:
    raise NotImplementedError("IF-DEMO-001")


def verify_path_equivalence(
    report: DemoHostReport,
) -> tuple[bool, tuple[str, ...]]:
    raise NotImplementedError("IF-DEMO-001")


def synthesize_scenario_fixture(
    scenario: str, host_repo: Path, candidate_digest: str
) -> ScenarioFixture:
    raise NotImplementedError("IF-FAILCLOSED-001")
