"""First reference host adapter declarations (IF-ADAPTER-002)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from tracks.adapters.base import Adapter, Layer, TestNode, TestRunResult


class ReferencePytestAdapter(Adapter):
    adapter_id = "reference-pytest"
    protocol = "tracks-test-result"
    protocol_version = 1

    def collect(self, collect_command: str, layer: Layer, cwd: Path) -> list[TestNode]:
        raise NotImplementedError("IF-ADAPTER-002")

    def run_selected(
        self,
        command_template: str,
        nodes: Sequence[str],
        result_path: Path,
        cwd: Path,
    ) -> tuple[str, ...]:
        raise NotImplementedError("IF-ADAPTER-002")

    def normalize_result(
        self, result_path: Path, selected: Sequence[str]
    ) -> list[TestRunResult]:
        raise NotImplementedError("IF-ADAPTER-002")


def collect_reference_nodes(
    collect_command: str, layer: Layer, cwd: Path
) -> list[TestNode]:
    raise NotImplementedError("IF-ADAPTER-002")


def resolve_reference_command(
    command_template: str,
    nodes: Sequence[str],
    result_path: Path,
    cwd: Path,
) -> tuple[str, ...]:
    raise NotImplementedError("IF-ADAPTER-002")


def normalize_reference_result(
    result_path: Path, selected: Sequence[str]
) -> list[TestRunResult]:
    raise NotImplementedError("IF-ADAPTER-002")
