"""Document format validation (FR-11/FR-19/FR-20).

v0.1 checks (D-16): file exists + frontmatter parseable ("schema"), and for
spec.md the FR-count scope gate (> 30 rows -> "scope_overflow", FR-20).
Pure with respect to state: reads only the given file.
"""
from __future__ import annotations

import re
from pathlib import Path

from tracks.frontmatter import split_frontmatter

FR_LIMIT = 30
_FR_ROW = re.compile(r"^\|\s*FR-\d+", re.MULTILINE)


def validate_document(path: Path, doc: str) -> tuple[str, str] | None:
    """Return (check, reason) on failure, None when the document is valid."""
    if not path.exists():
        return ("schema", "missing file")
    head, body = split_frontmatter(path.read_text(encoding="utf-8"))
    if not head:
        return ("schema", "no frontmatter")
    if doc == "spec.md":
        n = len(_FR_ROW.findall(body))
        if n > FR_LIMIT:
            return ("scope_overflow", f"{n} FRs > {FR_LIMIT}")
    return None
