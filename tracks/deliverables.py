"""Deliverable consistency gate (FR-040/FR-130, AC-1303).

A pre-commit / CI gate (NOT Runtime): the spec deliverables (Scribe/Sage agents
+ tracks-discuz skill) must ship with tracks and carry a well-formed frontmatter
``version``. Existence + version only — no digest/manifest. Any failure exits
non-zero to block merge.
"""
from __future__ import annotations

from pathlib import Path

from tracks.frontmatter import split_frontmatter

_PKG = Path(__file__).resolve().parent

DELIVERABLES = (
    _PKG / "agents" / "Scribe.md",
    _PKG / "agents" / "Sage.md",
    _PKG / "skills" / "tracks-discuz" / "SKILL.md",
)


def _version(path: Path):
    """The frontmatter version if well-formed (starts with a digit), else None."""
    head, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    for line in head.splitlines():
        if line.startswith("version:"):
            val = line.split(":", 1)[1].strip()
            if val and val[0].isdigit():
                return val
    return None


def check_deliverables(paths=None) -> list:
    """Return failure messages ([] = consistent). AC-1303 existence + version."""
    issues = []
    for path in DELIVERABLES if paths is None else paths:
        if not path.exists():
            issues.append(f"missing deliverable: {path}")
        elif _version(path) is None:
            issues.append(f"missing or malformed version in {path}")
    return issues
