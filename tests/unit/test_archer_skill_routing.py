"""Archer skill routing + packaging regression guards (OOB b90, G10).

G10a-G10d from the locked implementation contract: stage skills injected into
the M-DESIGN and M-IMPL PLANNING assignments, deliverables coverage, and
materialized effective copies. Static AST/regex/file checks only — no
subprocess, no ``trac``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tracks.deliverables import DELIVERABLES, check_deliverables
from tracks.frontmatter import split_frontmatter

REPO = Path(__file__).resolve().parents[2]

MACHINE = REPO / "tracks" / "kernel" / "machine_decide.py"
M_IMPL = REPO / "tracks" / "kernel" / "m_impl_decide.py"

DESIGN_SRC = REPO / "tracks" / "skills" / "tracks-archer-design" / "SKILL.md"
DESIGN_EFF = REPO / ".opencode" / "skills" / "tracks-archer-design" / "SKILL.md"
PLANNING_SRC = REPO / "tracks" / "skills" / "tracks-archer-planning" / "SKILL.md"
PLANNING_EFF = REPO / ".opencode" / "skills" / "tracks-archer-planning" / "SKILL.md"
CORE_SRC = REPO / "tracks" / "agents" / "Archer.md"

SKILL_NAME = re.compile(r'"(tracks-[a-z-]+)"')


def _func_source(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(text, node)
            if segment is not None:
                return segment
    raise AssertionError(f"function {name} not found in {path}")


def _skill_literals(source: str) -> set[str]:
    return set(SKILL_NAME.findall(source))


def _frontmatter_fields(path: Path) -> dict[str, str]:
    head, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    fields: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


# -- G10a: M-DESIGN routing ------------------------------------------------------


def test_m_design_skills_include_archer_design():
    skills = _skill_literals(_func_source(MACHINE, "_decide_design_draft"))
    assert "tracks-archer-design" in skills, f"M-DESIGN skills = {skills}"
    assert "tracks-discuz" in skills, "M-DESIGN must keep the discussion skill"


# -- G10b: M-IMPL PLANNING routing ------------------------------------------------


def test_planning_skills_include_archer_planning_without_quality_guards():
    skills = _skill_literals(_func_source(M_IMPL, "_m_impl_archer_dispatch"))
    assert "tracks-archer-planning" in skills, f"PLANNING skills = {skills}"
    assert "tracks-quality-guards" not in skills, (
        "PLANNING must not default-inject the large guard-stack catalog"
    )


# -- G10c: deliverables coverage ---------------------------------------------------


def test_deliverables_include_both_skills():
    names = {p.parent.name for p in DELIVERABLES}
    assert {"tracks-archer-design", "tracks-archer-planning"} <= names
    assert check_deliverables() == []


def test_check_deliverables_validates_both_skill_paths():
    issues = check_deliverables(paths=[DESIGN_SRC, PLANNING_SRC])
    assert issues == [], f"skill deliverables not valid: {issues}"


# -- G10d: materialized copies + frontmatter ---------------------------------------


def test_skill_files_exist_with_frontmatter():
    for path in (DESIGN_SRC, DESIGN_EFF, PLANNING_SRC, PLANNING_EFF):
        assert path.exists(), f"missing skill asset: {path}"
    for src, eff, expected_name in (
        (DESIGN_SRC, DESIGN_EFF, "tracks-archer-design"),
        (PLANNING_SRC, PLANNING_EFF, "tracks-archer-planning"),
    ):
        assert src.read_bytes() == eff.read_bytes(), f"source/effective drift: {src} vs {eff}"
        fields = _frontmatter_fields(src)
        assert fields.get("name") == expected_name, f"{src} frontmatter name mismatch"
        assert fields.get("version", "").split(".")[0].isdigit(), f"{src} bad version"
        assert fields.get("description"), f"{src} missing description"


def test_materializer_copies_both_skills(tmp_path):
    """The generic skill materializer (_materialize_skills, driven by
    assignment.skills) replicates both source skills into .opencode/skills."""
    from tracks.effects.opencode import OpencodeBackend

    backend = OpencodeBackend(tmp_path, "v1.0")
    for name, src in (
        ("tracks-archer-design", DESIGN_SRC),
        ("tracks-archer-planning", PLANNING_SRC),
    ):
        info = backend._materialize_skill(name)
        assert info is not None, f"materializer returned None for {name}"
        assert src.read_bytes() == info["dest"].read_bytes(), f"{name} materialized drift"
        info["dest"].unlink(missing_ok=True)


# -- parity / version basics --------------------------------------------------------


def test_core_version_and_envelope_parity():
    fields = _frontmatter_fields(CORE_SRC)
    version = tuple(int(part) for part in fields["version"].split("."))
    assert version >= (0, 4), f"core version {fields['version']} not bumped past 0.3"
    assert fields.get("IQ") == "S", f"core IQ must stay S, got {fields.get('IQ')}"

    _, body = split_frontmatter(CORE_SRC.read_text(encoding="utf-8"))
    assert "tracks-envelope:v2" in body
    for path in (DESIGN_SRC, PLANNING_SRC):
        fields = _frontmatter_fields(path)
        assert fields.get("version", "").split(".")[0].isdigit()
