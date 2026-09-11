"""Unit: native ``source=version_decl`` local gate (FR-0269/NFR-0147).

The tracks/reference host contracts declare a ``kind="version"`` gate with
``source="version_decl"`` and no executable command: the Runtime derives the
declared operation-plan tag/release targets from the
``[host-contract.version_scheme]`` templates + run version facts, requires them
to be exact template renderings, and probes the remote for each derived tag
(``git ls-remote``). These anchors drive the real Executor/Store over real
temporary git repositories and a local bare remote — the loader/validator and
the shared release_gate parser are not replaced.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.executor.executor import Executor
from tracks.executor.host_contract import (
    GATE_RESULT_PROTOCOL,
    HostContract,
    LocalGateDecl,
    OperationPlanDecl,
    VersionDecl,
)
from tracks.kernel.events import Command
from tracks.store import Store

_CANDIDATE = "a" * 40
_DIGEST = "sha256:" + "b" * 64
_CMD_ID = "CMD-VERSION-GATE"

_FEATURE = OperationPlanDecl(
    journey="feature",
    steps=("merge:main", "tag:{feature_tag}", "artifact:{artifact}", "release:{feature_tag}"),
    requires=(),
)
_POST_RELEASE = OperationPlanDecl(
    journey="post_release",
    steps=(
        "merge:main",
        "tag:{patch_line}",
        "artifact:{artifact}",
        "release:{patch_line}",
    ),
    requires=(),
)


def _contract(*, feature_steps=None) -> HostContract:
    return HostContract(
        contract_version=1,
        language="python",
        toolchain="cpython",
        install="",
        local_gates=(
            LocalGateDecl("version", "version_decl", "", (), "exit_code", 60),
        ),
        version=VersionDecl(
            "v{minor}.0", "v{minor}.{n}", "v{minor}.{n}-pre.{ulid}"
        ),
        build_command="",
        build_artifact="",
        smoke=(),
        security_scans=(),
        ci={},
        tracker={},
        operations={
            "feature": (
                OperationPlanDecl("feature", tuple(feature_steps), ())
                if feature_steps is not None
                else _FEATURE
            ),
            "post_release": _POST_RELEASE,
        },
    )


@pytest.fixture()
def gate_env(tmp_path, monkeypatch):
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    executor = Executor(store, repo, "RUN")
    state = SimpleNamespace(version="v0.8", hotfix_scenario=None)

    def _run(contract=None, *, params=None):
        return executor._execute_verify_gates(
            Command("run_local_gates", params=params or {}, command_id=_CMD_ID),
            _CANDIDATE,
            _DIGEST,
            contract or _contract(),
            state,
        )

    def _events():
        return [
            event
            for event in store.events("RUN")
            if event.command_id == _CMD_ID
        ]

    yield SimpleNamespace(
        repo=repo, store=store, executor=executor, run=_run, events=_events
    )
    store.close()


def _bare_remote(repo: Path, name: str = "origin.git") -> Path:
    bare = repo.parent / name
    subprocess.run(
        ["git", "init", "--bare", str(bare)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(bare)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return bare


def _tag_payload(event) -> dict:
    payload = event.payload or {}
    assert payload["kind"] == "version"
    assert payload["candidate_sha"] == _CANDIDATE
    assert payload["contract_digest"] == _DIGEST
    assert payload["gate_identity"] == "version[0]"
    normalized = payload["normalized_result"]
    assert normalized["schema"] == GATE_RESULT_PROTOCOL
    assert normalized["version"] == 1
    assert normalized["gate_id"] == "version"
    assert payload["command_echo"], "resume completeness requires a command echo"
    return payload


def test_legal_derivation_passes_with_remote_tag_absent(gate_env):
    _bare_remote(gate_env.repo)
    assert gate_env.run() is True
    events = gate_env.events()
    assert [event.type for event in events] == ["local_gate.passed"]
    payload = _tag_payload(events[0])
    assert payload["status"] == "passed"
    assert payload["exit_code"] == 0
    assert payload["journey"] == "feature"
    assert payload["derived_tags"] == ["v0.8.0"]
    assert payload["remote_check"] == {
        "status": "verified_absent",
        "tags": ["v0.8.0"],
    }
    assert payload["remote_patch_n"] == {"requested": False, "n": None, "error": None}


def test_literal_target_not_derivable_fails_version_mismatch(gate_env):
    contract = _contract(feature_steps=("tag:v9.9.9", "release:v9.9.9"))
    assert gate_env.run(contract) is False
    events = gate_env.events()
    assert [event.type for event in events] == ["local_gate.failed"]
    payload = _tag_payload(events[0])
    assert payload["status"] == "failed"
    assert payload["reason"] == "version_mismatch"
    assert payload["mismatches"] == [
        {
            "step": "tag:v9.9.9",
            "kind": "tag",
            "target": "v9.9.9",
            "expected": "v0.8.0",
        },
        {
            "step": "release:v9.9.9",
            "kind": "release",
            "target": "v9.9.9",
            "expected": "v0.8.0",
        },
    ]
    assert payload["remote_check"]["status"] == "not_checked"


def test_cross_template_target_fails_version_mismatch(gate_env):
    # A feature journey may not tag the patch line / prerelease template.
    contract = _contract(feature_steps=("tag:{patch_line}",))
    assert gate_env.run(contract) is False
    payload = _tag_payload(gate_env.events()[0])
    assert payload["reason"] == "version_mismatch"
    assert payload["mismatches"][0]["target"] == "v0.8.1"
    assert payload["mismatches"][0]["expected"] == "v0.8.0"


def test_existing_remote_tag_fails_closed(gate_env):
    remote = _bare_remote(gate_env.repo)
    subprocess.run(
        ["git", "tag", "v0.8.0"], cwd=gate_env.repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "v0.8.0"],
        cwd=gate_env.repo,
        check=True,
        capture_output=True,
    )
    assert remote.exists()
    assert gate_env.run() is False
    payload = _tag_payload(gate_env.events()[0])
    assert payload["reason"] == "tag_already_exists"
    assert payload["remote_check"] == {
        "status": "exists",
        "tags": ["v0.8.0"],
        "existing": "v0.8.0",
    }


def test_unreachable_remote_fails_closed(gate_env):
    subprocess.run(
        ["git", "remote", "add", "origin", "/nonexistent/tracks-remote.git"],
        cwd=gate_env.repo,
        check=True,
        capture_output=True,
    )
    assert gate_env.run() is False
    payload = _tag_payload(gate_env.events()[0])
    assert payload["reason"] == "remote_unavailable"
    assert payload["remote_check"] == {
        "status": "unavailable",
        "tags": ["v0.8.0"],
        "failed_tag": "v0.8.0",
    }


def test_no_remote_records_attention_and_explicit_skip(gate_env):
    assert gate_env.run() is True
    events = gate_env.events()
    assert [event.type for event in events] == [
        "attention.required",
        "local_gate.passed",
    ]
    attention = events[0].payload
    assert attention["area"] == "version_gate"
    assert attention["reason"] == "remote_unavailable"
    assert attention["candidate_sha"] == _CANDIDATE
    payload = _tag_payload(events[1])
    assert payload["remote_check"] == {
        "status": "skipped_no_remote",
        "tags": ["v0.8.0"],
    }


def test_post_release_census_input_recorded_and_next_patch_derived(gate_env):
    remote = _bare_remote(gate_env.repo)
    subprocess.run(
        ["git", "tag", "v0.8.1"], cwd=gate_env.repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "v0.8.1"],
        cwd=gate_env.repo,
        check=True,
        capture_output=True,
    )
    assert remote.exists()
    params = {"candidate_sha": _CANDIDATE, "journey": "post_release"}
    assert gate_env.run(params=params) is True
    payload = _tag_payload(gate_env.events()[0])
    assert payload["journey"] == "post_release"
    assert payload["remote_patch_n"] == {"requested": True, "n": "2", "error": None}
    assert payload["derived_tags"] == ["v0.8.2"]
    assert payload["remote_check"] == {
        "status": "verified_absent",
        "tags": ["v0.8.2"],
    }
