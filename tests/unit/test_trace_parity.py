"""Ground-truth parity tests for trace tool (FR-0080).

Compares tracks.checks.trace.check_trace_full_file output against the
independent reference implementation tests/ground_truth/trace_reference.py
on fixture corpora under tests/assets/trace_fixtures/.

AC-FR0080-01@v0.4 full chain + ground truth,
AC-FR0080-02@v0.4 FR<->AC hard errors + ground truth,
AC-FR0080-03@v0.4 AC<->test hard errors + ground truth,
AC-FR0080-05@v0.4 no short circuit + ground truth,
AC-FR0100-02@v0.4 baseline exemption + ground truth.
"""

import sys
from pathlib import Path

from tracks.checks.trace import check_trace_full_file

GROUND_TRUTH_DIR = Path(__file__).resolve().parent.parent / "ground_truth"
sys.path.insert(0, str(GROUND_TRUTH_DIR))
from trace_reference import compute_trace  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "assets" / "trace_fixtures"

_TEST_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".java",
        ".go",
        ".rs",
        ".cs",
        ".rb",
        ".php",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".kt",
        ".swift",
    }
)


def _read_fixture(name: str) -> tuple[str, str, str, dict[str, str]]:
    """Read fixture files: (story, spec, acc, {test_file_path: content})."""
    d = FIXTURES / name
    story = (d / "story.md").read_text(encoding="utf-8")
    spec = (d / "spec.md").read_text(encoding="utf-8")
    acc = (d / "acceptance.md").read_text(encoding="utf-8")
    test_files = {}
    tests_dir = d / "tests"
    if tests_dir.exists():
        for tf in sorted(tests_dir.rglob("*")):
            if tf.is_file() and tf.suffix in _TEST_SUFFIXES:
                test_files[str(tf)] = tf.read_text(encoding="utf-8")
    return story, spec, acc, test_files


def _compare(name: str, baseline: dict | None = None) -> None:
    """Compare check_trace_full_file vs compute_trace on a fixture."""
    story, spec, acc, test_files = _read_fixture(name)
    d = FIXTURES / name
    tests_dir = d / "tests"

    # Implementation output
    impl = check_trace_full_file(d, tests_dir, baseline)

    # Oracle output
    oracle = compute_trace(story, spec, acc, test_files)

    assert impl.status == oracle["status"], (
        f"{name}: status mismatch impl={impl.status} oracle={oracle['status']}"
    )
    assert tuple(sorted(impl.hard_errors)) == tuple(sorted(oracle["hard_errors"])), (
        f"{name}: hard_errors mismatch\n  impl={impl.hard_errors}\n  oracle={oracle['hard_errors']}"
    )
    assert tuple(sorted(impl.warnings)) == tuple(sorted(oracle["warnings"])), (
        f"{name}: warnings mismatch\n  impl={impl.warnings}\n  oracle={oracle['warnings']}"
    )


# AC-FR0080-01@v0.4 TRACKS-TRACE clean fixture parity
def test_clean_parity():
    """AC-FR0080-01@v0.4 clean fixture parity (full coverage pass)."""
    _compare("clean")


# AC-FR0080-02@v0.4 TRACKS-TRACE orphans fixture parity
def test_orphans_parity():
    """AC-FR0080-02@v0.4 AC-FR0080-03@v0.4 AC-FR0080-05@v0.4 orphans fixture parity."""
    _compare("orphans")


# AC-FR0080-09@v0.4 TRACKS-TRACE tombstone fixture parity
def test_tombstone_parity():
    """AC-FR0080-09@v0.4 tombstone fixture parity."""
    _compare("tombstone")


# AC-FR0080-08@v0.4 TRACKS-TRACE duplicates fixture parity
def test_duplicates_parity():
    """AC-FR0080-08@v0.4 duplicates fixture parity."""
    _compare("duplicates")


# AC-FR0080-06@v0.4 TRACKS-TRACE short marker fixture parity
def test_short_marker_parity():
    """AC-FR0080-06@v0.4 short-format marker fixture parity."""
    _compare("short_marker")


# AC-FR0100-02@v0.4 TRACKS-TRACE baseline exemption parity
def test_baseline_exemption_parity():
    """AC-FR0100-02@v0.4 baseline exemption: exempted IDs not in output."""
    story, spec, acc, test_files = _read_fixture("baseline")
    d = FIXTURES / "baseline"
    tests_dir = d / "tests"

    # Without baseline
    no_baseline = check_trace_full_file(d, tests_dir, None)
    oracle_no_baseline = compute_trace(story, spec, acc, test_files)
    assert no_baseline.status == oracle_no_baseline["status"]
    assert tuple(sorted(no_baseline.hard_errors)) == tuple(
        sorted(oracle_no_baseline["hard_errors"])
    )

    # With baseline: FR-0020 is exempted
    baseline = {"trace_exemptions": {"ids": ["FR-0020"]}}
    with_baseline = check_trace_full_file(d, tests_dir, baseline)
    assert not any("FR-0020" in e for e in with_baseline.hard_errors)
    # FR-0020 should be in the no-baseline output
    assert any("FR-0020" in e for e in no_baseline.hard_errors)
