"""FR-0080 trac check trace - BS->FR->AC->test full-chain orphan detection.

Stub (IF-TRACE-*): signatures frozen by interfaces.md §1d; Devon implements the
behavior body. Shield's contract tests collect/import against these signatures
before Devon's implementation lands (ATDD foundation).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class TraceReport:
    """FR-0080 trace report (interfaces.md §1d)."""

    status: Literal["pass", "fail"]
    hard_errors: tuple[str, ...]   # full orphan list with line:N (no short-circuit)
    warnings: tuple[str, ...]      # BS->FR weak-link warnings


def check_trace_full(
    story_text: str,
    spec_text: str,
    acc_text: str,
    test_markers: dict[str, list[str]],  # {ac_id: [test_file_path, ...]}
) -> TraceReport:
    """FR-0080 BS->FR->AC->test full-chain bidirectional orphan detection (pure).

    - FR<->AC hard errors: spec FR with no acceptance AC; acceptance AC pointing
      at a non-existent FR.
    - AC<->test hard errors: AC with no long-format marker; marker pointing at a
      non-existent AC.
    - BS->FR warning: BS with no FR承接 (does not change exit code).
    - Short-format marker (missing @version) -> hard error.
    - Duplicate FR/AC ID -> hard error (both conflicting line:N listed).
    - tombstone ID not counted as orphan.
    Full list reported without short-circuit; order stable and reproducible
    (NFR-0020).
    """
    raise NotImplementedError("IF-TRACE-001 check_trace_full")


def check_trace_full_file(
    version_dir: Path,
    tests_dir: Path,
    baseline: dict | None = None,
) -> TraceReport:
    """FR-0080 file-reading wrapper: reads story/spec/acceptance from
    version_dir, scans .py files in tests_dir for long-format markers, calls
    check_trace_full. baseline is the legacy exemption (FR-0100): IDs/documents
    listed in the baseline are not counted as orphans.
    """
    raise NotImplementedError("IF-TRACE-002 check_trace_full_file")
