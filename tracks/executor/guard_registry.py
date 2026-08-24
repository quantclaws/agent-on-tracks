"""Canonical quality-guard registry declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

GUARD_CATEGORIES = (
    "lint_format",
    "static_analysis",
    "cognitive_complexity",
    "file_length",
    "method_length_locals",
    "duplication",
    "coverage_threshold",
    "hooks_runner_ci_required_checks",
)


@dataclass(frozen=True)
class GuardEntry:
    guard_id: str
    category: str
    tool: str
    tool_version: str
    command: tuple[str, ...]
    config_paths: tuple[str, ...]
    config_sections: tuple[str, ...]
    config_digest: str
    scope: tuple[str, ...]
    threshold: str
    timeout_seconds: int
    failure_policy: Literal["fail_closed"]
    execution_points: tuple[str, ...]
    required_check: str


@dataclass(frozen=True)
class GuardRegistry:
    version: int
    host: str
    entries: tuple[GuardEntry, ...]
    digest: str


@dataclass(frozen=True)
class GuardMismatch:
    place: Literal["runtime", "pre_commit", "ci"]
    guard_id: str
    kind: str
    detail: str


@dataclass(frozen=True)
class ParityReport:
    registry_digest: str
    runtime_match: bool
    pre_commit_match: bool
    ci_match: bool
    mismatches: tuple[GuardMismatch, ...]


@dataclass(frozen=True)
class GuardDeployment:
    registry_digest: str
    pre_commit_path: Path
    ci_workflow_path: Path
    artifact_digests: Mapping[str, str]


def load_guard_registry(architecture_path: Path) -> GuardRegistry:
    raise NotImplementedError("IF-GUARD-001")


def validate_guard_registry(registry: GuardRegistry, repo: Path) -> tuple[str, ...]:
    raise NotImplementedError("IF-GUARD-001")


def check_parity(
    registry: GuardRegistry,
    runtime_commands: Mapping[str, Sequence[str]],
    pre_commit_path: Path,
    ci_workflow_path: Path,
    cwd: Path,
) -> ParityReport:
    raise NotImplementedError("IF-GUARD-002")


def deploy_guard_configs(
    registry: GuardRegistry, target_repo: Path
) -> GuardDeployment:
    raise NotImplementedError("IF-GUARD-002")
