"""Minimal host-neutral protocol contract for the Prism prompt assets (b91).

Only machine-protocol assertions live here (A-class whitelist): source/
effective parity, parseable frontmatter + well-formed version, canonical
host-protocol path spelling, the `tracks-envelope:v2` token, and the
existing 3-skill routing wiring. No section/sentence/forbidden-token/line-
count/duplication assertions — prose shape is not a long-term unit pin.
"""

from pathlib import Path

from tracks.deliverables import check_deliverables
from tracks.frontmatter import split_frontmatter

REPO = Path(__file__).resolve().parents[2]

SOURCE = {
    "core": REPO / "tracks" / "agents" / "Prism.md",
    "design": REPO / "tracks" / "skills" / "tracks-prism-design" / "SKILL.md",
    "impl": REPO / "tracks" / "skills" / "tracks-prism-impl" / "SKILL.md",
    "test": REPO / "tracks" / "skills" / "tracks-prism-test" / "SKILL.md",
}

EFFECTIVE = {
    "core": REPO / ".opencode" / "agents" / "Prism.md",
    "design": REPO / ".opencode" / "skills" / "tracks-prism-design" / "SKILL.md",
    "impl": REPO / ".opencode" / "skills" / "tracks-prism-impl" / "SKILL.md",
    "test": REPO / ".opencode" / "skills" / "tracks-prism-test" / "SKILL.md",
}

# Existing Prism stage routing (b91 keeps it untouched, asserts it exists):
# machine_decide.py -> M-DESIGN, m_test.py -> M-TEST, m_impl.py -> M-IMPL.
ROUTING = {
    REPO / "tracks" / "kernel" / "machine_decide.py": "tracks-prism-design",
    REPO / "tracks" / "kernel" / "m_test.py": "tracks-prism-test",
    REPO / "tracks" / "kernel" / "m_impl.py": "tracks-prism-impl",
}


def _normalized(text: str) -> str:
    return "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").splitlines()
    ).strip() + "\n"


def test_source_effective_identical():
    for key in SOURCE:
        assert _normalized(SOURCE[key].read_text(encoding="utf-8")) == _normalized(
            EFFECTIVE[key].read_text(encoding="utf-8")
        ), f"{key} source/effective copies diverged"


def test_frontmatter_parseable_and_version_wellformed():
    for key, path in SOURCE.items():
        head, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        fields = {}
        for line in head.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields[k.strip()] = v.strip()
        assert fields.get("name") or fields.get("description"), f"{key} lacks identity"
        assert fields.get("version", "").startswith(("0.", "1.", "2.")), f"{key} version malformed"
        if key == "core":
            assert fields.get("IQ") in ("S", "A", "B"), "Prism agent IQ malformed"


def test_deliverables_cover_prism_assets():
    issues = check_deliverables(list(SOURCE.values()))
    assert issues == [], f"Prism deliverables failed existence/version gate: {issues}"


def test_canonical_host_protocol_path_spelling():
    text = SOURCE["design"].read_text(encoding="utf-8")
    assert ".tracks/projects/project.toml" in text
    assert ".tracks/project/project.toml" not in text


def test_envelope_v2_token_present_in_all_declared_faces():
    # IF-ENVELOPE-002 (FR-0279): every parity face the Runtime materializes
    # declares the envelope token in its frontmatter — the pre-spawn gate
    # binds face→token from the actual bytes. Supersedes the v0.7 staged
    # rollout invariant ("token in core only").
    core = SOURCE["core"].read_text(encoding="utf-8")
    assert "tracks-envelope:v2" in core
    for key in ("design", "impl", "test"):
        assert "tracks-envelope:v2" in SOURCE[key].read_text(encoding="utf-8")


def test_three_skill_routing_wiring_exists():
    for kernel_file, skill_name in ROUTING.items():
        assert skill_name in kernel_file.read_text(encoding="utf-8"), (
            f"{kernel_file.name} no longer wires {skill_name}"
        )
