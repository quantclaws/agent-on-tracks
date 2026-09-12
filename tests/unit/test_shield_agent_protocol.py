"""Minimal host-neutral machine protocol for the Shield prompt assets (b91).

A-class whitelist only: source/effective parity, parseable frontmatter +
well-formed version + no permission block, the `tracks-envelope:v2` token in
the core (and only there), and the M-TEST WRITE + M-IMPL SHIELD_FIX skill
routing wiring. No sentence/section/line-count/forbidden-token/prose-pin
assertions.
"""

from pathlib import Path

from tracks.deliverables import check_deliverables
from tracks.frontmatter import split_frontmatter

REPO = Path(__file__).resolve().parents[2]

CORE = REPO / "tracks" / "agents" / "Shield.md"
SKILL = REPO / "tracks" / "skills" / "tracks-shield" / "SKILL.md"
EFFECTIVE = {
    CORE: REPO / ".opencode" / "agents" / "Shield.md",
    SKILL: REPO / ".opencode" / "skills" / "tracks-shield" / "SKILL.md",
}

# b91 routing: both Shield dispatch sites (M-TEST WRITE and M-IMPL SHIELD_FIX)
# declare [tracks-discuz, tracks-shield] with discussion first.
ROUTING_KERNELS = (
    REPO / "tracks" / "kernel" / "m_test.py",
    REPO / "tracks" / "kernel" / "m_impl_decide.py",
)
SHIELD_SKILLS = ["tracks-discuz", "tracks-shield"]


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
        ), f"{source.parent.name} source/effective copies diverged"


def test_frontmatter_parseable_and_permission_free():
    for path in (CORE, SKILL):
        fields = _frontmatter(path)
        assert fields.get("name") or fields.get("description"), f"{path.name} lacks identity"
        assert fields.get("version", "").startswith(("0.", "1.", "2.")), (
            f"{path.name} version malformed"
        )
    core_fields = _frontmatter(CORE)
    assert core_fields.get("IQ") in ("S", "A", "B"), "Shield agent IQ malformed"
    assert "permission" not in core_fields, "host permission belongs in runtime config"


def test_deliverables_cover_shield_core_and_skill():
    issues = check_deliverables([CORE, SKILL])
    assert issues == [], f"Shield deliverables failed existence/version gate: {issues}"


def test_envelope_v2_token_present_in_core_only():
    core = CORE.read_text(encoding="utf-8")
    assert "tracks-envelope:v2" in core
    assert "tracks-envelope:v2" not in SKILL.read_text(encoding="utf-8")


def test_shield_skill_routing_wiring_exists():
    for kernel in ROUTING_KERNELS:
        text = kernel.read_text(encoding="utf-8")
        assert "tracks-shield" in text, f"{kernel.name} no longer wires tracks-shield"
        assert "tracks-discuz" in text, f"{kernel.name} dropped the discussion protocol"
