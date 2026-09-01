"""Minimal host-neutral machine protocol for the Devon prompt assets (b91).

A-class whitelist only: source/effective parity, parseable frontmatter +
well-formed version + no permission block, the `tracks-envelope:v2` token in
the core (and only there), and the single-RGR-phase skill routing wiring. No
sentence/section/line-count/forbidden-token/prose-pin assertions.
"""

from pathlib import Path

from tracks.deliverables import check_deliverables
from tracks.frontmatter import split_frontmatter

REPO = Path(__file__).resolve().parents[2]

CORE = REPO / "tracks" / "agents" / "Devon.md"
SKILL = REPO / "tracks" / "skills" / "tracks-devon-rgr" / "SKILL.md"
EFFECTIVE = {
    CORE: REPO / ".opencode" / "agents" / "Devon.md",
    SKILL: REPO / ".opencode" / "skills" / "tracks-devon-rgr" / "SKILL.md",
}

# b91 keeps the existing routing untouched: M-IMPL RED/GREEN/REFACTOR all
# dispatch role=devon with the tracks-devon-rgr skill (tracks/kernel/m_impl.py).
ROUTING_KERNEL = REPO / "tracks" / "kernel" / "m_impl.py"


def _normalized(text: str) -> str:
    return "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").splitlines()
    ).strip() + "\n"


def _frontmatter(path: Path) -> dict:
    head, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    fields = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def test_source_effective_identical():
    for source, effective in EFFECTIVE.items():
        assert _normalized(source.read_text(encoding="utf-8")) == _normalized(
            effective.read_text(encoding="utf-8")
        ), f"{source.name} source/effective copies diverged"


def test_frontmatter_parseable_and_permission_free():
    for path in (CORE, SKILL):
        fields = _frontmatter(path)
        assert fields.get("name") or fields.get("description"), f"{path.name} lacks identity"
        assert fields.get("version", "").startswith(("0.", "1.", "2.")), (
            f"{path.name} version malformed"
        )
    core_fields = _frontmatter(CORE)
    assert core_fields.get("IQ") in ("S", "A", "B"), "Devon agent IQ malformed"
    assert "permission" not in core_fields, "host permission belongs in runtime config"


def test_deliverables_cover_devon_core():
    issues = check_deliverables([CORE])
    assert issues == [], f"Devon deliverables failed existence/version/IQ gate: {issues}"


def test_envelope_v2_token_present_in_core_only():
    core = CORE.read_text(encoding="utf-8")
    assert "tracks-envelope:v2" in core
    assert "tracks-envelope:v2" not in SKILL.read_text(encoding="utf-8")


def test_single_phase_skill_routing_wiring_exists():
    kernel = ROUTING_KERNEL.read_text(encoding="utf-8")
    assert "tracks-devon-rgr" in kernel, "m_impl.py no longer wires tracks-devon-rgr"
