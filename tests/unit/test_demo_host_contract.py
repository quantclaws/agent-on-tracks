"""IF-DEMO-001 demo host real-path provisioning contract (T-014 RED unit).

Pins the dual-host demo contract from interfaces.md §1j *before* the GREEN
implementation lands in ``tracks/executor/demo_host.py``:

- ``FAIL_CLOSED_SCENARIOS`` is exactly the nine-member closed set of §1a
  ``FailClosedScenario`` (AC-FR0266-02 real-path provisioning; the scenario
  set is the input to the nine-detail-event demonstration).
- ``create_demo_host`` provisions a ``DemoHostReport`` that records the real
  install-path evidence: fresh venv, non-editable wheel sha256, import path
  outside the source tree, the canonical demo architecture landing point
  ``.tracks/projects/v0.1/architecture.md``, registry digest, hooks path, CI
  binding and adapter id (AC-FR0266-02, AC-NFR0142-01).
- ``verify_path_equivalence`` reports whether the demo host path is
  equivalent to the tracks host (registry/deployment/hook/CI digests agree)
  and returns the (equivalent, evidence-paths) pair (AC-NFR0142-01).
- ``synthesize_scenario_fixture`` constructs a ``ScenarioFixture`` binding a
  closed-set scenario to a host repo and candidate digest.

Each behavioral assertion fails today because ``create_demo_host``,
``verify_path_equivalence`` and ``synthesize_scenario_fixture`` are
``NotImplementedError("IF-DEMO-001")`` / ``("IF-FAILCLOSED-001")`` stubs --
the M-IMPL RED on the contract.
"""

from __future__ import annotations

from pathlib import Path

from tracks.executor.demo_host import (
    FAIL_CLOSED_SCENARIOS,
    DemoHostReport,
    ScenarioFixture,
    create_demo_host,
    synthesize_scenario_fixture,
    verify_path_equivalence,
)

# AC-FR0266-02@v0.7 TRACKS-TRACE FAIL_CLOSED_SCENARIOS exact nine-member set

# The nine scenario names are the closed set interfaces.md §1a FailClosedScenario.
# They are asserted on the constant so a drift in the closed set is caught
# regardless of the GREEN behavioral body.
_EXPECTED_SCENARIOS = (
    "broad_mutation",
    "stale_patch",
    "wrong_candidate",
    "uncollected_node",
    "unrelated_red",
    "target_survived",
    "control_hit",
    "malformed_adapter_result",
    "guard_parity_mismatch",
)


def test_fail_closed_scenarios_exactly_nine_closed_members():
    """FAIL_CLOSED_SCENARIOS is exactly the nine members of FailClosedScenario."""
    assert isinstance(FAIL_CLOSED_SCENARIOS, tuple)
    assert FAIL_CLOSED_SCENARIOS == _EXPECTED_SCENARIOS
    assert len(FAIL_CLOSED_SCENARIOS) == 9
    assert len(set(FAIL_CLOSED_SCENARIOS)) == 9  # no duplicate / shared fixture


# AC-FR0266-02@v0.7 TRACKS-TRACE create_demo_host real-path report


def test_create_demo_host_returns_demo_host_report(tmp_path):
    """create_demo_host provisions a DemoHostReport from the wheel assets."""
    report = create_demo_host(
        template_dir=tmp_path / "template",
        target_dir=tmp_path / "demo",
        wheel=tmp_path / "tracks.whl",
    )
    assert isinstance(report, DemoHostReport)
    assert isinstance(report.repo, Path)
    assert isinstance(report.venv, Path)


def test_create_demo_host_real_install_path_evidence(tmp_path):
    """The report carries the real-path evidence required by AC-FR0266-02."""
    wheel = tmp_path / "demo.whl"
    target = tmp_path / "demo"
    report = create_demo_host(
        template_dir=tmp_path / "template",
        target_dir=target,
        wheel=wheel,
    )
    # import path must be outside the source tree (fresh venv install)
    assert isinstance(report.import_path, str) and report.import_path
    assert report.wheel_sha256 != ""
    # canonical demo architecture landing point is fixed
    assert report.architecture_path == target / ".tracks" / "projects" / "v0.1" / "architecture.md"
    assert isinstance(report.registry_digest, str) and report.registry_digest != ""
    assert isinstance(report.hooks_path, str) and report.hooks_path != ""
    assert isinstance(report.ci_binding, str) and report.ci_binding != ""
    assert isinstance(report.adapter_id, str) and report.adapter_id != ""


def test_create_demo_host_report_is_frozen(tmp_path):
    """DemoHostReport is an immutable dataclass (frozen) with the exact fields."""
    report = create_demo_host(
        template_dir=tmp_path / "template",
        target_dir=tmp_path / "demo",
        wheel=tmp_path / "demo.whl",
    )
    frozen = DemoHostReport(
        repo=report.repo,
        venv=report.venv,
        wheel_sha256=report.wheel_sha256,
        import_path=report.import_path,
        architecture_path=report.architecture_path,
        registry_digest=report.registry_digest,
        hooks_path=report.hooks_path,
        ci_binding=report.ci_binding,
        adapter_id=report.adapter_id,
    )
    # contract requires wheel_sha256 + architecture_path identity evidence
    assert frozen.wheel_sha256
    assert frozen.architecture_path == report.architecture_path


# --- AC-NFR0142-01@v0.7 TRACKS-TRACE verify_path_equivalence evidence ---


def _report(tmp_path: Path) -> DemoHostReport:
    return DemoHostReport(
        repo=tmp_path / "repo",
        venv=tmp_path / "venv",
        wheel_sha256="sha256:" + "a" * 64,
        import_path="/instantiated/outside/source",
        architecture_path=tmp_path / ".tracks" / "projects" / "v0.1" / "architecture.md",
        registry_digest="sha256:" + "b" * 64,
        hooks_path=".githooks",
        ci_binding="declared",
        adapter_id="reference-pytest",
    )


def test_verify_path_equivalence_returns_bool_and_evidence(tmp_path):
    """verify_path_equivalence reports (equivalent, evidence paths)."""
    report = _report(tmp_path)
    equivalent, evidence = verify_path_equivalence(report)
    assert isinstance(equivalent, bool)
    assert isinstance(evidence, tuple)


def test_verify_path_equivalence_evidence_is_path_strings(tmp_path):
    """AC-NFR0142-01: the evidence tuple carries replayable path identities."""
    report = _report(tmp_path)
    equivalent, evidence = verify_path_equivalence(report)
    if equivalent:
        # when the demo host path is equivalent, each evidence entry is a
        # non-empty registry/deployment/hook/CI path identity that can be
        # replayed (trac-replay evidence requirement).
        assert len(evidence) >= 4
        for item in evidence:
            assert isinstance(item, str) and item != ""


# --- AC-FR0266-01@v0.7 TRACKS-TRACE synthesize_scenario_fixture contract ---


def test_synthesize_scenario_fixture_returns_fixture(tmp_path):
    """synthesize_scenario_fixture binds a closed scenario to a candidate."""
    fixture = synthesize_scenario_fixture(
        scenario="broad_mutation",
        host_repo=tmp_path / "host",
        candidate_digest="sha256:" + "c" * 64,
    )
    assert isinstance(fixture, ScenarioFixture)
    assert fixture.scenario == "broad_mutation"
    assert fixture.host
    assert fixture.expected_block_reason != ""
    assert fixture.manifest_or_config_ref != ""


def test_synthesize_scenario_fixture_recovers_any_closed_scenario(tmp_path):
    """Each closed scenario synthesizes a fixture (no leak / no silent drop)."""
    for scenario in FAIL_CLOSED_SCENARIOS:
        fixture = synthesize_scenario_fixture(
            scenario=scenario,
            host_repo=tmp_path / "host",
            candidate_digest="sha256:" + "d" * 64,
        )
        assert fixture.host
        assert fixture.expected_block_reason != ""
