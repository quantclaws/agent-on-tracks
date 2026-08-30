"""Shield-owned seed data: e2e v0.7 journey Phase-0 data premise.

Test-plan §2.4/§11.1 fixture duty for ``tests/e2e/test_v07_journey.py``:
the v0.7-A main journey's Phase 0 gap-repair outlet (``phase0.baseline_repaired``
for AC-FR0250-03@v0.6 / AC-NFR0130-01@v0.6 / AC-NFR0130-02@v0.6) is only
reachable when the fake host repo carries a REAL v0.6 baseline directory
(``.tracks/projects/v0.6/``) whose test-plan §8 rows bind the three gap ACs
(unversioned AC keys, matching the shipped v0.6 baseline) to REAL collectable
integration nodes present in the host tree (`_phase0_planned_bindings` /
`_phase0_approved_acs`, executor.py §1d scan inputs).

The seed also carries the two other Phase-0 premises the journey needs:

- ``[adapter] id="reference-pytest"`` on the host ``project.toml``
  (interfaces §1h: ``_repair_phase0_gap`` fail-closes with UnknownAdapterError
  when the declaration is absent — T-016 locks that fail-closed behaviour).
- the §4.2 ``[quality_registry]`` machine block on the post-M-DESIGN
  ``architecture.md`` (shared ``_seed_guard_registry`` from
  ``test_failclosed_scenarios``), which the Phase-0 guard-hardening gate
  consumes.

Fixture data is the truth source (test-plan §3): every digest stems from the
node file bytes / the §4.2 TOML block, never from implementation output.  The
seeded node file is committed to the host git tree so the B59 freeze expects a
clean ``tests/`` at commit_tests (test.written -> controlled commit; a dirty
historical seed would trip the freeze residue gate).  T-017 re-uses this seed
for the candidate-identity fail-closed demonstration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GAP_ACS = (
    "AC-FR0250-03",
    "AC-NFR0130-01",
    "AC-NFR0130-02",
)
_GAP_NODE_FILE = "tests/integration/test_v06_gap_seed.py"
_GAP_NODE = "tests/integration/test_v06_gap_seed.py::test_gap_anchor"

_GAP_NODE_BODY = """# Seed gap anchors for the v0.7-A Phase 0 baseline repair (test-plan §2.4).
#
# The three v0.6 gap ACs bind (via the seeded v0.6 test-plan §8 rows) to this
# REAL collected node.  The node carries the R-1 TRACKS-TRACE markers so the
# trace scanner can attribute it to the baseline gap ACs; its body is a
# plain PASSING node so it survives the M-IMPL FULL chain (FULL runs every
# collected node -- a failing node would park the run at DIAGNOSE).

# AC-FR0250-03@v0.6 TRACKS-TRACE Phase 0 gap node
# AC-NFR0130-01@v0.6 TRACKS-TRACE Phase 0 gap node
# AC-NFR0130-02@v0.6 TRACKS-TRACE Phase 0 gap node
def test_gap_anchor():
    assert True
"""


def seed_v06_baseline(host_repo: Path) -> None:
    """Seed ``.tracks/projects/v0.6/`` trio + the collectable gap node.

    The three-gap test-plan §8 rows use UNVERSIONED AC keys exactly as the
    shipped v0.6 baseline does (``_AC_REF_IN_ROW`` captures them, and
    ``_acc_scan`` reads unversioned ``### AC-…`` headings), so
    ``_phase0_planned_bindings``/``_phase0_approved_acs`` resolve them.
    """
    vdir = host_repo / ".tracks" / "projects" / "v0.6"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text("# v0.6 story\n\nbaseline story.\n", encoding="utf-8")
    (vdir / "spec.md").write_text("# v0.6 spec\n\nbaseline spec.\n", encoding="utf-8")
    (vdir / "acceptance.md").write_text(
        "# v0.6 acceptance\n\n"
        + "\n\n".join(f"### {ac}\n\nbaseline AC.\n" for ac in _GAP_ACS)
        + "\n",
        encoding="utf-8",
    )
    rows = "\n".join(
        f"| {ac} | integration | {_GAP_NODE} | IF-TRACE-002 |" for ac in _GAP_ACS
    )
    (vdir / "test-plan.md").write_text(
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        f"{rows}\n",
        encoding="utf-8",
    )
    node = host_repo / _GAP_NODE_FILE
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text(_GAP_NODE_BODY, encoding="utf-8")
    subprocess.run(
        ["git", "add", ".tracks/projects/v0.6", _GAP_NODE_FILE],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed v0.6 Phase 0 baseline + gap nodes"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )


def add_adapter_declaration(host_repo: Path) -> None:
    """Append the ``[adapter]`` section (interfaces §1h) to the host contract."""
    toml = host_repo / ".tracks" / "projects" / "project.toml"
    text = toml.read_text(encoding="utf-8")
    if "[adapter]" in text:
        return
    text = text.rstrip("\n") + (
        "\n\n[adapter]\n"
        "id = 'reference-pytest'\n"
        "protocol = 'tracks-test-result'\n"
        "version = 1\n"
    )
    toml.write_text(text, encoding="utf-8")


def seed_guard_registry(host_repo) -> None:
    """Append the §4.2 ``[quality_registry]`` block post-M-DESIGN (shared)."""
    from tests.integration.test_failclosed_scenarios import _seed_guard_registry

    _seed_guard_registry(host_repo)


__all__ = [
    "seed_v06_baseline",
    "add_adapter_declaration",
    "seed_guard_registry",
]
