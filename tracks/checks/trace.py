"""FR-0080 trac check trace - BS->FR->AC->test full-chain orphan detection.

Pure-function core (check_trace_full) + file-reading wrapper (check_trace_full_file).
Reuses executor/validate.py scanning helpers (_spec_items/_acc_scan) per
architecture.md §3.1 / interfaces.md §1d.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.executor.validate import _acc_scan, _spec_items

# BS-XX heading: two-digit, story `### BS-XX` (interfaces.md §1d / FR-0130).
_BS_HEADING = re.compile(r"^###\s+BS-(\d{2})\s", re.M)

# Long-format test marker: AC-FRXXXX-YY@vX.Y (interfaces.md §3d / FR-0080).
_TEST_MARKER = re.compile(r"AC-((?:N?FR)\d{4})-(\d{2})(@\S+)?")

# tombstone: HTML comment `<!-- tombstone: ID -->` (interfaces.md §3e / FR-0130).
_TOMBSTONE = re.compile(
    r"<!--\s*tombstone:\s*((?:N?FR)-\d{4}|BS-\d{2}|AC-(?:N?FR)\d{4}-\d{2})\s*-->"
)


@dataclass(frozen=True)
class TraceReport:
    """FR-0080 trace report (interfaces.md §1d)."""

    status: Literal["pass", "fail"]
    hard_errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _scan_bs(story_text: str) -> list[tuple[str, int]]:
    """Return [(BS-XX, line)] from story, skipping fenced code."""
    items: list[tuple[str, int]] = []
    fence = False
    for i, line in enumerate(story_text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence and (m := _BS_HEADING.match(line)):
            items.append((f"BS-{m.group(1)}", i))
    return items


def _scan_tombstones(*texts: str) -> set[str]:
    """Collect tombstone IDs from all texts (HTML comment form)."""
    ids: set[str] = set()
    for text in texts:
        ids.update(m.group(1) for m in _TOMBSTONE.finditer(text))
    return ids


def _scan_fr(spec_text: str) -> list[tuple[str, int]]:
    """Return [(FR-XXXX|NFR-XXXX, line)] from spec."""
    return [
        (re.match(r"^###\s+((?:N?FR)-\d+)", heading, re.I).group(1).upper(), line_no)
        for line_no, heading, _ in _spec_items(spec_text)
    ]


def _detect_duplicates(
    items: list[tuple[str, int]], label: str,
) -> list[str]:
    """Detect duplicate IDs in a list of (id, line) tuples."""
    errors: list[str] = []
    seen: dict[str, int] = {}
    for item_id, line in items:
        if item_id in seen:
            errors.append(
                f"duplicate {item_id}: {label} line:{seen[item_id]} "
                f"and {label} line:{line}"
            )
        else:
            seen[item_id] = line
    return errors


def _check_fr_ac_errors(
    fr_items: list[tuple[str, int]],
    ac_items: list[tuple[str, str, int]],
    fr_ids: set[str],
    fr_to_acs: dict[str, list[str]],
    tombstones: set[str],
) -> list[str]:
    """FR<->AC bidirectional hard error detection."""
    errors: list[str] = []
    for fr_id, line in fr_items:
        if fr_id not in tombstones and fr_id not in fr_to_acs:
            errors.append(f"spec line:{line} {fr_id} has no AC item")
    for ac_id, fr_id, line in ac_items:
        if ac_id in tombstones or fr_id in tombstones:
            continue
        if fr_id not in fr_ids:
            errors.append(
                f"acceptance line:{line} {ac_id} references non-existent {fr_id}"
            )
    return errors


def _check_ac_test_errors(
    ac_items: list[tuple[str, str, int]],
    ac_ids: set[str],
    test_markers: dict[str, list[str]],
    tombstones: set[str],
) -> list[str]:
    """AC<->test bidirectional hard error detection + short-format detection."""
    errors: list[str] = []
    marker_ac_ids_long = {
        ac_id for ac_id, markers in test_markers.items()
        if any("@" in m for m in markers)
    }
    for ac_id, _, line in ac_items:
        if ac_id not in tombstones and ac_id not in marker_ac_ids_long:
            errors.append(f"acceptance line:{line} {ac_id} has no test marker bound")
    for ac_id, markers in test_markers.items():
        for marker_str in markers:
            if "@" in marker_str and ac_id not in ac_ids:
                errors.append(
                    f"test marker {marker_str} references non-existent {ac_id}"
                )
            elif "@" not in marker_str:
                errors.append(
                    f"test marker {marker_str} short format (missing @version)"
                )
    return errors


def _check_bs_warnings(
    bs_items: list[tuple[str, int]], tombstones: set[str],
) -> list[str]:
    """BS->FR weak-link warnings (do not change exit code)."""
    return [
        f"story line:{line} {bs_id} has no FR承接 (BS->FR weak link)"
        for bs_id, line in bs_items
        if bs_id not in tombstones
    ]


def check_trace_full(
    story_text: str,
    spec_text: str,
    acc_text: str,
    test_markers: dict[str, list[str]],
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
    tombstones = _scan_tombstones(story_text, spec_text, acc_text)
    fr_items = _scan_fr(spec_text)
    _, acs = _acc_scan(acc_text)
    ac_items = [(ac_id, ref_id, ln) for ac_id, ref_id, ln, _ in acs]
    fr_to_acs: dict[str, list[str]] = {}
    for ac_id, fr_id, _ in ac_items:
        fr_to_acs.setdefault(fr_id, []).append(ac_id)

    hard_errors = _detect_duplicates(fr_items, "spec")
    hard_errors += _detect_duplicates(
        [(ac_id, ln) for ac_id, _, ln in ac_items], "acceptance"
    )
    hard_errors += _check_fr_ac_errors(
        fr_items, ac_items,
        {fr_id for fr_id, _ in fr_items}, fr_to_acs, tombstones,
    )
    hard_errors += _check_ac_test_errors(
        ac_items, {ac_id for ac_id, _, _ in ac_items},
        test_markers, tombstones,
    )
    warnings = _check_bs_warnings(_scan_bs(story_text), tombstones)
    return TraceReport(
        status="fail" if hard_errors else "pass",
        hard_errors=tuple(sorted(hard_errors)),
        warnings=tuple(sorted(warnings)),
    )


_DATA_DIRS = frozenset({"assets", "ground_truth"})


def _scan_test_markers(tests_dir: Path) -> dict[str, list[str]]:
    """Scan .py files in tests_dir for AC markers.

    Skips data directories (``assets`` and ``ground_truth``) by project
    convention -- those contain fixture trees and oracle reference files,
    not test assets to bind ACs to.
    """
    markers: dict[str, list[str]] = {}
    for py_file in sorted(tests_dir.rglob("*.py")):
        rel_parts = py_file.relative_to(tests_dir).parts
        if _DATA_DIRS & set(rel_parts):
            continue
        content = py_file.read_text(encoding="utf-8")
        for m in _TEST_MARKER.finditer(content):
            ac_id = f"AC-{m.group(1)}-{m.group(2)}"
            markers.setdefault(ac_id, []).append(m.group(0))
    return markers


def _apply_baseline(report: TraceReport, baseline: dict | None) -> TraceReport:
    """Filter out hard_errors/warnings mentioning baseline-exempted IDs."""
    if baseline is None:
        return report
    exempted = set(baseline.get("trace_exemptions", {}).get("ids", []))
    if not exempted:
        return report
    hard = tuple(
        e for e in report.hard_errors
        if not any(eid in e for eid in exempted)
    )
    warns = tuple(
        w for w in report.warnings
        if not any(eid in w for eid in exempted)
    )
    return TraceReport(
        status="fail" if hard else "pass",
        hard_errors=hard,
        warnings=warns,
    )


def check_trace_full_file(
    version_dir: Path,
    tests_dir: Path,
    baseline: dict | None = None,
) -> TraceReport:
    """FR-0080 file-reading wrapper: reads story/spec/acceptance from
    version_dir, scans .py files in tests_dir for markers, calls
    check_trace_full. baseline is the legacy exemption (FR-0100).
    """
    story = (version_dir / "story.md")
    spec = (version_dir / "spec.md")
    acc = (version_dir / "acceptance.md")
    story_text = story.read_text(encoding="utf-8") if story.exists() else ""
    spec_text = spec.read_text(encoding="utf-8") if spec.exists() else ""
    acc_text = acc.read_text(encoding="utf-8") if acc.exists() else ""
    test_markers = _scan_test_markers(tests_dir)
    report = check_trace_full(story_text, spec_text, acc_text, test_markers)
    return _apply_baseline(report, baseline)
