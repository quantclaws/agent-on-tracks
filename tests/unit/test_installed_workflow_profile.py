"""Installed projects keep product versions separate from workflow capabilities."""

import pytest

from tests.unit.test_t015_phase0_repair_evidence import _contract
from tracks import paths
from tracks.capabilities import FULL_WORKFLOW_PROFILE
from tracks.cli.validate_cmd import _trace_capability
from tracks.executor.executor import Executor
from tracks.executor.version_extensions import CapabilityBlockedError, resolve_capability
from tracks.kernel.events import Command
from tracks.kernel.machine import decide
from tracks.store import Store


# AC-FR0348-01@v0.12 TRACKS-TRACE installed project has the full workflow at any product version
@pytest.mark.parametrize("version", ["v0.1", "v1.0", "v2.3"])
def test_full_profile_reaches_implementation_and_release(host_repo, version):
    store = Store(paths.tracks_home(host_repo))
    try:
        store.append("fresh", version, "story.requested", {
            "raw_chars": 1, "workflow_profile": FULL_WORKFLOW_PROFILE,
        })
        store.append("fresh", version, "stage.entered", {"stage": "M-TEST"})
        executor = Executor(store, host_repo, "fresh")
        assert not executor._is_boundary_transition("M-TEST", "M-IMPL")
        command = decide(store.state("fresh"))
        assert command.kind == "prepare_host_environment"
        store.append("fresh", version, "environment.prepared", {})
        assert decide(store.state("fresh")).kind == "phase0_validate"

        store.append("fresh", version, "stage.entered", {"stage": "M-IMPL"})
        store.append("fresh", version, "stage.exited", {"stage": "M-IMPL"})
        command = executor._release_boundary_route(store.state("fresh"))
        assert command.kind == "freeze_candidate"
        assert command.params["stage"] == "M-VERIFY"
        assert store.state("fresh").version == version, "document identities must retain the product version"
        store.rebuild_projections()
        assert store.state("fresh").workflow_profile == FULL_WORKFLOW_PROFILE
        assert _trace_capability(store.home, version) == FULL_WORKFLOW_PROFILE
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE historical runs retain their recorded workflow
def test_historical_run_without_profile_keeps_its_boundary(host_repo):
    store = Store(paths.tracks_home(host_repo))
    try:
        store.append("legacy", "v0.1", "story.requested", {"raw_chars": 1})
        executor = Executor(store, host_repo, "legacy")
        assert executor._is_boundary_transition("M-TEST", "M-IMPL")
        assert store.state("legacy").workflow_profile is None
        assert _trace_capability(store.home, "v0.1") == "v0.1"
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE unsupported future profiles cannot silently use legacy behavior
def test_unknown_workflow_profile_blocks():
    with pytest.raises(CapabilityBlockedError, match="unsupported workflow profile"):
        resolve_capability("full-v999", "after_m_impl")


# AC-FR0348-01@v0.12 TRACKS-TRACE baseline selection uses actual released runs
def test_baseline_uses_real_release_history(host_repo):
    store = Store(paths.tracks_home(host_repo))
    try:
        for run_id, version, terminal in (
            ("published", "v1.0", "released"),
            ("unfinished", "v1.1", "boundary"),
            ("patched", "v1.0-hotfix-5", "released"),
        ):
            store.append(run_id, version, "story.requested", {"raw_chars": 1})
            store.append(run_id, version, "run.completed", {"terminal_state": terminal})
        assert store.latest_released_version("v2.0") == "v1.0"
        assert store.latest_released_version("v1.0") is None
    finally:
        store.close()


# AC-FR0348-01@v0.12 TRACKS-TRACE first release validates real empty baseline without fictitious old specs
@pytest.mark.parametrize("registry_present", [True, False])
def test_first_release_phase0_keeps_real_guard_validation(host_repo, registry_present):
    from pathlib import Path

    home = paths.tracks_home(host_repo)
    contract = paths.project_toml_path(home)
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(_contract(), encoding="utf-8")
    for layer in ("unit", "integration", "e2e"):
        (host_repo / "tests" / layer).mkdir(parents=True, exist_ok=True)
    if registry_present:
        from tracks import __file__ as package_file

        architecture = home / "projects" / "v1.0" / "architecture.md"
        architecture.parent.mkdir(parents=True, exist_ok=True)
        architecture.write_bytes(
            (Path(package_file).parent / "assets" / "demo_host" / "architecture.md").read_bytes()
        )
    store = Store(home)
    try:
        store.append("fresh", "v1.0", "story.requested", {
            "raw_chars": 1, "workflow_profile": FULL_WORKFLOW_PROFILE,
        })
        store.append("fresh", "v1.0", "stage.entered", {"stage": "M-TEST"})
        executor = Executor(store, host_repo, "fresh")
        executor.issue(Command(kind="phase0_validate", params={}))
        state = store.state("fresh")
        assert state.phase0_status == ("SEALED" if registry_present else "BLOCKED")
        events = list(store.events("fresh"))
        assert not any(event.type == "phase0.baseline_repaired" for event in events)
        if registry_present:
            sealed = next(event for event in events if event.type == "phase0.sealed")
            assert sealed.payload["baseline_version"] == "initial"
        else:
            assert state.phase0_blocked_reason == "guard_registry_invalid"
    finally:
        store.close()
