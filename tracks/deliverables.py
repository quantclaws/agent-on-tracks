"""Deliverable consistency gate (FR-040/FR-130, AC-1303).

A pre-commit / CI gate (NOT Runtime): the spec deliverables (all shipped agent
prompts — Scribe/Sage/Lex/Archer/Prism — + tracks-discuz skill) must ship with
tracks and carry a well-formed frontmatter ``version``; agent prompts must also
carry a well-formed ``IQ`` grade. Existence + version + IQ only — no
digest/manifest. Any failure exits non-zero to block merge.
"""
from __future__ import annotations

from pathlib import Path

from tracks.frontmatter import split_frontmatter

_PKG = Path(__file__).resolve().parent

AGENT_DELIVERABLES = (
    _PKG / "agents" / "Scribe.md",
    _PKG / "agents" / "Sage.md",
    _PKG / "agents" / "Lex.md",
    _PKG / "agents" / "Archer.md",
    _PKG / "agents" / "Prism.md",
    _PKG / "agents" / "Shield.md",  # v0.4 M-TEST (FR-0120)
    _PKG / "agents" / "Devon.md",   # v0.5 M-IMPL phase-separated RGR (FR-0170)
)

DELIVERABLES = AGENT_DELIVERABLES + (
    _PKG / "skills" / "tracks-discuz" / "SKILL.md",
    _PKG / "skills" / "tracks-prism-test" / "SKILL.md",  # v0.4 D-29 (FR-0040)
    _PKG / "skills" / "tracks-prism-design" / "SKILL.md",  # v0.5 D-29 design criteria
    _PKG / "skills" / "tracks-prism-impl" / "SKILL.md",  # v0.5 D-29 impl criteria
)

IQ_GRADES = ("S", "A", "B")


def _frontmatter_value(path: Path, key: str):
    head, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    for line in head.splitlines():
        if line.startswith(f"{key}:"):
            val = line.split(":", 1)[1].strip()
            if val:
                return val
    return None


def _version(path: Path):
    """The frontmatter version if well-formed (starts with a digit), else None."""
    val = _frontmatter_value(path, "version")
    if val and val[0].isdigit():
        return val
    return None


def _iq(path: Path):
    """The frontmatter IQ grade if well-formed (one of S/A/B), else None."""
    val = _frontmatter_value(path, "IQ")
    if val in IQ_GRADES:
        return val
    return None


def check_deliverables(paths=None) -> list:
    """Return failure messages ([] = consistent). AC-1303 existence + version + IQ."""
    issues = []
    for path in DELIVERABLES if paths is None else paths:
        if not path.exists():
            issues.append(f"missing deliverable: {path}")
            continue
        if _version(path) is None:
            issues.append(f"missing or malformed version in {path}")
        if path in AGENT_DELIVERABLES and _iq(path) is None:
            issues.append(f"missing or malformed IQ in {path}")
    return issues
