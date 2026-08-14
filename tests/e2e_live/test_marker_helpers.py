"""Deterministic coverage for ``assert_test_markers``'s R-1 marker scan.

Regression for run011: the marker regex lacked ``re.MULTILINE``, so ``^``
only anchored at the start of the file and a marker sitting at line ~40
(behind a docstring + helpers) was missed, raising a false
"no R-1 TRACKS-TRACE marker" failure. The product ``check_trace`` scans
line-by-line and was unaffected; only this e2e_live helper was wrong."""

from __future__ import annotations

import pytest

from tests.e2e_live.m_test_helpers import assert_test_markers

_MARKER = "# AC-FR0010-01@v0.4 TRACKS-TRACE integration\n"


def _write(tmp_path, name, content):
    test_file = tmp_path / "tests" / "integration" / name
    test_file.parent.mkdir(parents=True)
    test_file.write_text(content, encoding="utf-8")
    return test_file


def test_marker_on_first_line_passes(tmp_path):
    tests_dir = tmp_path / "tests"
    _write(tmp_path, "test_marker.py", _MARKER + "def test_marker():\n    pass\n")
    assert_test_markers(tests_dir, "v0.4")


def test_marker_after_docstring_and_helpers_passes(tmp_path):
    """Regression (run011): a marker past line 1 (here ~line 40, behind a
    docstring/helpers preamble) must still match -- the regex needs
    ``re.MULTILINE`` to anchor ``^`` per line, not only at the file start."""
    tests_dir = tmp_path / "tests"
    prelude = "\n".join(["# preamble line"] * 39) + "\n"
    test_file = _write(
        tmp_path, "test_marker.py", prelude + _MARKER + "def test_marker():\n    pass\n"
    )
    assert test_file.read_text(encoding="utf-8").splitlines().index(_MARKER.strip()) + 1 >= 40
    assert_test_markers(tests_dir, "v0.4")


def test_file_without_any_marker_fails(tmp_path):
    tests_dir = tmp_path / "tests"
    _write(
        tmp_path,
        "test_no_marker.py",
        '"""No marker here."""\ndef test_something():\n    pass\n',
    )
    with pytest.raises(AssertionError, match="no R-1 TRACKS-TRACE marker"):
        assert_test_markers(tests_dir, "v0.4")
