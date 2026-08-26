"""IF-ADAPTER-003 language-neutrality validation (T-003 RED unit).

Pins the language-neutrality invariant from interfaces.md §IF-ADAPTER-003
and the ``trac validate`` contract from architecture.md §1.2:

- ``validate_document`` (or a new ``scan_language_tokens`` helper) must
  reject language tokens (``pytest``, ``junit``, ``java``) found in
  kernel/executor/cli runtime source (AC-FR0264-04: no language tokens).
- The dual check combines static token scanning with runtime adapter-only
  execution path (AC-NFR0141-02: language-invariant dual check).

The scan function is not yet implemented in ``tracks/executor/validate.py``
-- these tests fail because the validate module does not yet expose the
``scan_language_tokens`` function.
"""

from __future__ import annotations

import pytest

from tracks.executor import validate


def test_validate_has_scan_language_tokens():
    """validate module must expose scan_language_tokens (IF-ADAPTER-003)."""
    assert hasattr(validate, "scan_language_tokens"), (
        "scan_language_tokens not implemented in tracks/executor/validate.py"
    )


def test_scan_language_tokens_detects_pytest_token():
    """scan_language_tokens raises NotImplementedError when called."""
    fn = getattr(validate, "scan_language_tokens", None)
    assert fn is not None, "scan_language_tokens not implemented"
    with pytest.raises(NotImplementedError, match="IF-ADAPTER-003"):
        fn(["pytest"])


def test_scan_language_tokens_detects_junit_token():
    """scan_language_tokens reports junit token."""
    fn = getattr(validate, "scan_language_tokens", None)
    assert fn is not None, "scan_language_tokens not implemented"
    with pytest.raises(NotImplementedError, match="IF-ADAPTER-003"):
        fn(["junit"])


def test_scan_language_tokens_detects_java_token():
    """scan_language_tokens reports java token."""
    fn = getattr(validate, "scan_language_tokens", None)
    assert fn is not None, "scan_language_tokens not implemented"
    with pytest.raises(NotImplementedError, match="IF-ADAPTER-003"):
        fn(["java"])


def test_validate_language_accepts_source_paths():
    """scan_language_tokens accepts source paths."""
    fn = getattr(validate, "scan_language_tokens", None)
    assert fn is not None, "scan_language_tokens not implemented"
    with pytest.raises(NotImplementedError, match="IF-ADAPTER-003"):
        fn(
            forbidden_tokens=["pytest", "junit", "java"],
            source_paths=["tracks/kernel/", "tracks/executor/", "tracks/cli/"],
        )
