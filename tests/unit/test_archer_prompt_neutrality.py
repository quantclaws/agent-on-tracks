"""Archer minimal machine-protocol guards (OOB b91 Q0).

Human ruling: prompt/skill prose is judged by behavior, not pinned verbatim.
Only the minimal machine protocol stays as static text tests. Skill routing,
materialization and deliverables are covered by test_archer_skill_routing.py;
FakeBackend closure behavior is covered by test_fake_island_closure.py.

No subprocess, no ``trac``; file checks only.
"""

from __future__ import annotations

from pathlib import Path

from tracks.frontmatter import split_frontmatter

REPO = Path(__file__).resolve().parents[2]

CORE_SRC = REPO / "tracks" / "agents" / "Archer.md"
CORE_EFF = REPO / ".opencode" / "agents" / "Archer.md"
DESIGN_SRC = REPO / "tracks" / "skills" / "tracks-archer-design" / "SKILL.md"
DESIGN_EFF = REPO / ".opencode" / "skills" / "tracks-archer-design" / "SKILL.md"
PLANNING_SRC = REPO / "tracks" / "skills" / "tracks-archer-planning" / "SKILL.md"
PLANNING_EFF = REPO / ".opencode" / "skills" / "tracks-archer-planning" / "SKILL.md"

ASSETS = (
    (CORE_SRC, CORE_EFF),
    (DESIGN_SRC, DESIGN_EFF),
    (PLANNING_SRC, PLANNING_EFF),
)


def _normalize(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    return "\n".join(lines).strip("\n") + "\n"


def _fields(path: Path) -> dict[str, str]:
    head, _ = split_frontmatter(_normalize(path.read_text(encoding="utf-8")))
    fields: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


def test_copies_consistent():
    """Core + both skills: source == effective, verbatim after newline /
    trailing-whitespace normalization."""
    for src, eff in ASSETS:
        assert src.exists(), f"missing source asset: {src}"
        assert eff.exists(), f"missing effective copy: {eff}"
        assert _normalize(src.read_text(encoding="utf-8")) == _normalize(
            eff.read_text(encoding="utf-8")
        ), f"source/effective drift: {src} vs {eff}"


def test_frontmatter_parseable():
    """Frontmatter parses with a well-formed version for every asset; skills
    additionally carry their ``name``."""
    for src, _ in ASSETS:
        fields = _fields(src)
        assert fields.get("version", "").split(".")[0].isdigit(), f"{src} bad version"
        assert fields.get("description"), f"{src} missing description"
        if src != CORE_SRC:
            assert fields.get("name", "").startswith("tracks-archer-"), (
                f"{src} frontmatter missing skill name"
            )


def test_core_envelope_token():
    """The core carries the machine token ``tracks-envelope:v2`` verbatim."""
    _, body = split_frontmatter(CORE_SRC.read_text(encoding="utf-8"))
    assert "tracks-envelope:v2" in body
