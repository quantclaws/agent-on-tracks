"""Language-neutral host test adapter declarations (IF-ADAPTER-001)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

TEST_RESULT_PROTOCOL = "tracks-test-result"
TEST_RESULT_PROTOCOL_VERSION = 1

Layer = Literal["unit", "integration", "e2e"]
NodeStatus = Literal["passed", "failed", "error", "skipped"]


@dataclass(frozen=True)
class TestNode:
    node_id: str
    layer: Layer
    source_digest: str


@dataclass(frozen=True)
class TestRunResult:
    node_id: str
    status: NodeStatus
    detail: str | None


class UnknownAdapterError(Exception):
    """IF-ADAPTER-001 unknown adapter/protocol/version."""


class AdapterResultError(Exception):
    """IF-ADAPTER-001 malformed or inexact normalized result."""


class Adapter(Protocol):
    adapter_id: str
    protocol: str
    protocol_version: int

    def collect(self, collect_command: str, layer: Layer, cwd: Path) -> list[TestNode]:
        raise NotImplementedError("IF-ADAPTER-001")

    def run_selected(
        self,
        command_template: str,
        nodes: Sequence[str],
        result_path: Path,
        cwd: Path,
    ) -> tuple[str, ...]:
        raise NotImplementedError("IF-ADAPTER-001")

    def normalize_result(
        self, result_path: Path, selected: Sequence[str]
    ) -> list[TestRunResult]:
        raise NotImplementedError("IF-ADAPTER-001")


def resolve_adapter(adapter_id: str, protocol: str, version: int) -> Adapter:
    """Resolve the declared adapter; unknown declarations fail closed."""
    raise NotImplementedError("IF-ADAPTER-001")
