"""FR-0090 trac check reach - module-level import-graph island detection.

Stub (IF-REACH-*): signatures frozen by interfaces.md §1f; Devon implements the
behavior body. Shield's contract tests collect/import against these signatures
before Devon's implementation lands (ATDD foundation).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ReachReport:
    """FR-0090 reach report (interfaces.md §1f)."""

    status: Literal["pass", "fail"]
    islands: tuple[str, ...]       # unreachable production modules (sorted stable)
    entrypoints: tuple[str, ...]   # discovered entry module names
    errors: tuple[str, ...]        # no-entrypoint-declared etc.


def check_reach(
    entrypoints: list[str],             # entry module names (e.g. ["tracks.cli.main"])
    import_graph: dict[str, set[str]],  # {module: {imported_module, ...}}
    production_modules: set[str],       # production module set (excludes tests/)
    baseline: dict | None = None,       # legacy exemption modules
) -> ReachReport:
    """FR-0090 module-level import-graph island detection (pure).

    BFS from entrypoints; unreachable production modules are islands.
    No entrypoint declaration -> errors non-empty, status=fail.
    baseline modules not counted as islands. Order stable (NFR-0020).
    """
    raise NotImplementedError("IF-REACH-001 check_reach")


def check_reach_file(
    repo: Path,
    baseline: dict | None = None,
) -> ReachReport:
    """FR-0090 file-reading wrapper: parses pyproject.toml [project.scripts],
    scans package dirs for __main__.py, reads .tracks/reach-entries.txt whitelist,
    ast-parses .py files to build import graph, calls check_reach.
    """
    raise NotImplementedError("IF-REACH-002 check_reach_file")
