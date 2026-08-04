"""Independent reference implementation for trace tool (FR-0080).

Ground truth (test-plan §3.3): recomputes the BS->FR->AC->test orphan list
independently of tracks.checks.trace, using only the standard library.
Shield's tests compare tracks.checks.trace.check_trace_full output against
this script's output on the same fixture data.

Isolation rules (test-plan §3.2):
- Must NOT import tracks.* (CI static check blocks merge on violation).
- Only standard library (re/pathlib/json) + test data files.
- Read fixture files directly from tests/assets/trace_fixtures/.
"""
from __future__ import annotations

import re

# --- ID patterns --------------------------------------------------------

# BS-XX: two-digit, story `### BS-XX` heading
_BS_HEADING = re.compile(r"^###\s+BS-(\d{2})\s", re.M)
# FR-XXXX / NFR-XXXX: four-digit, spec `### FR-XXXX` / `### NFR-XXXX` heading
_FR_HEADING = re.compile(r"^###\s+(N?FR)-(\d{4})\s", re.M)
# AC-FRXXXX-YY: acceptance `### AC-FRXXXX-YY` heading
_AC_HEADING = re.compile(
    r"^###\s+AC-((?:N?FR)\d{4})-(\d{2})\s", re.M)
# Long-format test marker: AC-FRXXXX-YY@vX.Y in test file docstrings/comments
_TEST_MARKER_LONG = re.compile(
    r"AC-((?:N?FR)\d{4})-(\d{2})@(v\d+\.\d+)")
# Short-format marker (missing @version) - detected as hard error
_TEST_MARKER_SHORT = re.compile(r"AC-(?:N?FR)\d{4}-\d{2}(?!@)")
# tombstone: HTML comment or frontmatter
_TOMBSTONE = re.compile(r"<!--\s*tombstone:\s*((?:N?FR)-\d{4}|BS-\d{2})\s*-->")


def _line_of(text: str, offset: int) -> int:
    """1-indexed line number of a character offset in text."""
    return text.count("\n", 0, offset) + 1


def _strip_discussion_and_code(text: str) -> str:
    """Remove inline-discussion blockquote lines and fenced code blocks so
    IDs inside them are not scanned as real IDs."""
    lines = text.split("\n")
    result: list[str] = []
    in_fence = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue
        # discussion blockquote: lines starting with '>' are discussion
        if stripped.startswith(">"):
            result.append(line)
            continue
        result.append(line)
    return "\n".join(result)


def _scan_bs(story_text: str) -> list[tuple[str, int]]:
    """Return [(BS-XX, line)] from story."""
    clean = _strip_discussion_and_code(story_text)
    return [(f"BS-{m.group(1)}", _line_of(story_text, m.start()))
            for m in _BS_HEADING.finditer(clean)]


def _scan_fr(spec_text: str) -> list[tuple[str, int]]:
    """Return [(FR-XXXX|NFR-XXXX, line)] from spec."""
    clean = _strip_discussion_and_code(spec_text)
    return [(f"{m.group(1)}-{m.group(2)}", _line_of(spec_text, m.start()))
            for m in _FR_HEADING.finditer(clean)]


def _scan_ac(acc_text: str) -> list[tuple[str, str, int]]:
    """Return [(AC-FRXXXX-YY, FR-XXXX, line)] from acceptance.

    AC heading is `### AC-FRXXXX-YY` (no dash between FR and digits);
    the referenced FR ID is `FR-XXXX` (with dash, matching spec heading).
    """
    clean = _strip_discussion_and_code(acc_text)
    result: list[tuple[str, str, int]] = []
    for m in _AC_HEADING.finditer(clean):
        fr_no_dash = m.group(1)  # e.g. "FR0010" (no dash)
        fr_id = f"{fr_no_dash[:2]}-{fr_no_dash[2:]}"  # -> "FR-0010"
        ac_id = f"AC-{fr_no_dash}-{m.group(2)}"  # -> "AC-FR0010-01"
        line = _line_of(acc_text, m.start())
        result.append((ac_id, fr_id, line))
    return result


def _scan_test_markers(
    test_files: dict[str, str],
) -> list[tuple[str, str, int, bool]]:
    """Return [(marker_str, ac_id, line, is_long_format)] from test files.

    test_files: {file_path: file_content}
    """
    result: list[tuple[str, str, int, bool]] = []
    for _fpath, content in test_files.items():
        for m in re.finditer(r"AC-((?:N?FR)\d{4})-(\d{2})(@\S+)?", content):
            ac_id = f"AC-{m.group(1)}-{m.group(2)}"
            line = _line_of(content, m.start())
            is_long = m.group(3) is not None
            result.append((m.group(0), ac_id, line, is_long))
    return result


def _tombstones(*texts: str) -> set[str]:
    """Collect tombstone IDs from all texts."""
    ids: set[str] = set()
    for text in texts:
        for m in _TOMBSTONE.finditer(text):
            ids.add(m.group(1))
    return ids


def compute_trace(
    story_text: str,
    spec_text: str,
    acc_text: str,
    test_files: dict[str, str],
) -> dict:
    """Recompute the trace orphan list independently.

    Returns {"status": "pass"|"fail", "hard_errors": [...], "warnings": [...]}.

    hard_errors and warnings are sorted lists of strings with line:N references.
    """
    bs_items = _scan_bs(story_text)
    fr_items = _scan_fr(spec_text)
    ac_items = _scan_ac(acc_text)
    markers = _scan_test_markers(test_files)
    tombstones = _tombstones(story_text, spec_text, acc_text)

    fr_ids = {fr_id for fr_id, _ in fr_items}
    ac_ids = {ac_id for ac_id, _, _ in ac_items}
    fr_to_acs: dict[str, list[str]] = {}
    for ac_id, fr_id, _ in ac_items:
        fr_to_acs.setdefault(fr_id, []).append(ac_id)

    hard_errors: list[str] = []
    warnings: list[str] = []

    # --- duplicate detection ---
    seen_fr: dict[str, int] = {}
    for fr_id, line in fr_items:
        if fr_id in seen_fr:
            hard_errors.append(
                f"duplicate {fr_id}: spec line:{seen_fr[fr_id]} and spec line:{line}")
        else:
            seen_fr[fr_id] = line
    seen_ac: dict[str, int] = {}
    for ac_id, _, line in ac_items:
        if ac_id in seen_ac:
            hard_errors.append(
                f"duplicate {ac_id}: acceptance line:{seen_ac[ac_id]} and acceptance line:{line}")
        else:
            seen_ac[ac_id] = line

    # --- FR<->AC hard errors ---
    for fr_id, line in fr_items:
        if fr_id in tombstones:
            continue
        if fr_id not in fr_to_acs:
            hard_errors.append(f"spec line:{line} {fr_id} has no AC item")

    for ac_id, fr_id, line in ac_items:
        if ac_id in tombstones or fr_id in tombstones:
            continue
        if fr_id not in fr_ids:
            hard_errors.append(
                f"acceptance line:{line} {ac_id} references non-existent {fr_id}")

    # --- AC<->test hard errors ---
    marker_ac_ids = {m[1] for m in markers if m[3]}  # long-format only
    for ac_id, _, line in ac_items:
        if ac_id in tombstones:
            continue
        if ac_id not in marker_ac_ids:
            hard_errors.append(
                f"acceptance line:{line} {ac_id} has no test marker bound")

    for marker_str, ac_id, _line, is_long in markers:
        if is_long and ac_id not in ac_ids:
            hard_errors.append(
                f"test marker {marker_str} references non-existent {ac_id}")

    # --- short-format marker hard error ---
    for marker_str, _ac_id, _line, is_long in markers:
        if not is_long:
            hard_errors.append(
                f"test marker {marker_str} short format (missing @version)")

    # --- BS->FR warning (does not change exit code) ---
    for bs_id, line in bs_items:
        if bs_id in tombstones:
            continue
        # BS->FR is a weak link; just warn
        warnings.append(f"story line:{line} {bs_id} has no FR承接 (BS->FR weak link)")

    status = "fail" if hard_errors else "pass"
    return {
        "status": status,
        "hard_errors": sorted(hard_errors),
        "warnings": sorted(warnings),
    }
