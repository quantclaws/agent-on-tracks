"""Versioned host contract (FR-0281, IF-HOSTCONTRACT-001/002).

The ``[host-contract.*]`` namespaced sections of the host's
``.tracks/projects/project.toml`` are the machine truth Archer materializes
per host (architecture §1.0.3/§3.1): language/toolchain declaration,
dependency install, local gates for M-VERIFY, build/artifact, post-install
smoke, version scheme, M-SECURITY scan policy, required-CI binding and
M-PUBLISH operation plans.

The Runtime only executes declared commands and consumes versioned
``tracks-gate-result`` normalized results; it never interprets host language
semantics (NFR-0147). Unknown language/framework/tool or malformed result is
fail-closed with machine evidence for the M-DESIGN revision loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

GATE_RESULT_PROTOCOL = "tracks-gate-result"
GATE_RESULT_VERSION = 1

LocalGateKind = Literal[
    "quality", "trace", "reach", "anti_slop", "version", "build", "smoke"
]
ResultChannel = Literal["exit_code", "file"]


@dataclass(frozen=True)
class LocalGateDecl:
    kind: LocalGateKind
    source: str
    command: str
    categories: tuple[str, ...]
    result_channel: ResultChannel
    timeout_seconds: int


@dataclass(frozen=True)
class VersionDecl:
    feature_tag: str
    patch_line: str
    prerelease_tag: str
    file: str | None = None
    key: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class SecurityScanDecl:
    scan_id: str
    tool: str
    tool_version: str
    install: str
    command: str
    result_channel: ResultChannel
    threshold: str
    timeout_seconds: int


@dataclass(frozen=True)
class OperationPlanDecl:
    journey: str
    steps: tuple[str, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True)
class HostContract:
    contract_version: int
    language: str
    toolchain: str
    install: str
    local_gates: tuple[LocalGateDecl, ...]
    version: VersionDecl
    build_command: str
    build_artifact: str
    smoke: tuple[str, ...]
    security_scans: tuple[SecurityScanDecl, ...]
    ci: dict
    tracker: dict
    operations: dict[str, OperationPlanDecl] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedGateResult:
    gate_id: str
    result_version: int
    status: Literal["passed", "failed", "malformed"]
    exit_code: int | None
    summary: dict


def load_host_contract(path: Path) -> HostContract:
    raise NotImplementedError("IF-HOSTCONTRACT-001")


def validate_host_contract(contract: HostContract, repo: Path) -> tuple[str, ...]:
    raise NotImplementedError("IF-HOSTCONTRACT-001")


def execute_gate(
    decl: LocalGateDecl, repo: Path, placeholders: dict
) -> NormalizedGateResult:
    raise NotImplementedError("IF-HOSTCONTRACT-001")


def parse_gate_result(
    raw: bytes | None, channel: ResultChannel, exit_code: int | None
) -> NormalizedGateResult:
    raise NotImplementedError("IF-HOSTCONTRACT-001")


def resolve_placeholders(version_facts: dict, artifact: str, prefix: str) -> dict:
    raise NotImplementedError("IF-HOSTCONTRACT-001")
