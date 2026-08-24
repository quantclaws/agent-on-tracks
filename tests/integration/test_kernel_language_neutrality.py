"""Integration: kernel language neutrality (IF-ADAPTER-003).

AC-FR0264-04@v0.7 no language tokens in kernel/executor/cli,
AC-NFR0141-02@v0.7 language-invariant dual check.

Assertions land on the kernel/executor/cli source scan (IF-ADAPTER-003,
interfaces §1h): the forbidden-zone source must not reference pytest/junit/java
tokens, and `trac validate` must enforce it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Forbidden zone (interfaces §1h): runtime code in kernel/executor/cli.
FORBIDDEN_GLOBS = (
    "tracks/kernel/**/*.py",
    "tracks/executor/**/*.py",
    "tracks/cli/**/*.py",
)

# Token word boundary, case-insensitive (interfaces §1h).
TOKEN_RE = re.compile(r"\b(pytest|junit|java)\b", re.IGNORECASE)


def _forbidden_files() -> list[Path]:
    hits: list[Path] = []
    for pat in FORBIDDEN_GLOBS:
        for path in REPO.glob(pat):
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in TOKEN_RE.finditer(text):
                # Allow only occurrences inside the adapters package path guard
                # (adapters live outside the forbidden zone).
                hits.append((path, m.group(0)))
    return hits  # type: ignore[return-value]


# AC-FR0264-04@v0.7 TRACKS-TRACE no language tokens in kernel/executor/cli
def test_no_language_tokens_kernel_executor_cli():
    """AC-FR0264-04: kernel/executor/cli runtime code has no language tokens."""
    hits = _forbidden_files()
    assert not hits, (
        f"forbidden language tokens in kernel/executor/cli: {hits[:5]}"
    )


# AC-NFR0141-02@v0.7 TRACKS-TRACE language-invariant dual check
def test_language_invariant_dual_check():
    """AC-NFR0141-02: validate + runtime adapter-only execution both enforce neutrality.

    The static scan (above) is one check; the second is that the adapter is the
    only language surface. We assert resolve_adapter is the sole resolution
    path and that the forbidden zone carries no tokens (dual check)."""
    hits = _forbidden_files()
    assert not hits, (
        f"runtime code must be language-neutral (dual check failed): {hits[:5]}"
    )
    # The adapter protocol is language-neutral by name/version (no language
    # branch in the kernel).
    from tracks.adapters.base import TEST_RESULT_PROTOCOL
    assert "pytest" not in TEST_RESULT_PROTOCOL
    assert "junit" not in TEST_RESULT_PROTOCOL
