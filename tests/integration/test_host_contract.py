"""Integration: host contract materialization (FR-0281, IF-HOSTCONTRACT-001/002).

b93 §8.1 bootstrap contract: the materialization half drives the shared
``walk_to_awaiting_release`` (bare ``trac run`` bootstrap is forbidden) with
the contract declared through the walker ``pre_seed_hook``; the execute-only
and failure halves drive the production ``run_local_gates`` handler directly
over the real Store (a chain-level failure opens a repair round whose re-arm
owns the next move). The release chain needs the loopback CI stand-in.
"""

from __future__ import annotations

import subprocess

import pytest

from tests._support.host_contracts import (
    TEST_EXECUTION_CONTRACT,
    declare_host_contract,
    declared_contract_toml,
)
from tests.e2e.helpers import walk_to_awaiting_release
from tracks.executor.host_contract import (
    LocalGateDecl,
    execute_gate,
    load_host_contract,
    validate_host_contract,
)

pytestmark = pytest.mark.integration


def _commit_contract(host_repo, body: str, *, with_test_sections: bool = True):
    """Write the declared contract, commit it and return (path, candidate)."""
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    text = body if not with_test_sections else TEST_EXECUTION_CONTRACT + "\n" + body
    contract_path.write_text(text, encoding="utf-8")
    subprocess.run(
        ["git", "add", "-f", str(contract_path.relative_to(host_repo))],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-qm", "declare host contract"],
        cwd=host_repo,
        check=True,
        capture_output=True,
    )
    candidate = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=host_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return contract_path, candidate


def _events(host_repo, run_id):
    from tracks import paths
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    try:
        return list(store.events(run_id))
    finally:
        store.close()


def _replay_local_gates(host_repo, run_id, candidate):
    """Production reconcile replay of the M-VERIFY local-gate command."""
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.kernel.events import Command
    from tracks.store import Store

    store = Store(paths.tracks_home(host_repo))
    try:
        Executor(store, host_repo, run_id)._execute(
            Command(kind="run_local_gates", params={"candidate_sha": candidate}),
            store.state(run_id),
            None,
            True,
        )
    finally:
        store.close()
    return _events(host_repo, run_id)


def _drive_local_gates(host_repo, trac, body: str):
    """init/start + declared contract + one production run_local_gates."""
    from tracks import paths
    from tracks.executor.executor import Executor
    from tracks.kernel.events import Command
    from tracks.store import Store

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    contract_path, candidate = _commit_contract(host_repo, body)
    store = Store(paths.tracks_home(host_repo))
    try:
        run_id = store.active_run()
        assert run_id
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-VERIFY"})
        store.append(
            run_id,
            "v0.8",
            "candidate.frozen",
            {
                "candidate_sha": candidate,
                "clean_tree": True,
                "branch": "main",
                "frozen_at_seq": 0,
            },
        )
        Executor(store, host_repo, run_id)._execute(
            Command(kind="run_local_gates", params={"candidate_sha": candidate}),
            store.state(run_id),
            None,
            False,
        )
        events = list(store.events(run_id))
    finally:
        store.close()
    return run_id, candidate, contract_path, events


# AC-FR0281-01@v0.8 TRACKS-TRACE materialized contract valid
def test_materialized_contract_valid(host_repo, trac, event_log, ci_echo_standin):
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    # Archer materializes the declared contract through the walker pre-seed
    # hook; the real M-VERIFY chain materializes and consumes it.
    walk_to_awaiting_release(trac, pre_seed_hook=declare_host_contract)
    loaded = load_host_contract(contract_path)
    assert loaded.contract_version == 1
    assert loaded.language == "python"
    assert validate_host_contract(loaded, host_repo) == ()
    events = event_log()
    materialized = [e for e in events if e["type"] == "host_contract.materialized"]
    assert materialized, "host_contract.materialized must appear"
    p = materialized[0]["payload"]
    assert "contract_path" in p
    assert "contract_digest" in p
    assert p["version"] == 1
    assert p["source"] == "host"
    assert p["contract_path"].endswith(".tracks/projects/project.toml")
    # Validate must pass for the materialized contract.
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode == 0, v.stdout + v.stderr


# AC-FR0281-02@v0.8 TRACKS-TRACE execute per contract only and unknown language blocked
def test_execute_per_contract_only(host_repo, trac, event_log, tmp_path):
    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): unknown
    # host-contract tables fail closed at load time — only declared gates run.
    bad_path = tmp_path / "project.toml"
    bad_path.write_text(
        "[host-contract]\nversion = 1\n[host-contract.bogus]\nkey = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown host-contract table"):
        load_host_contract(bad_path)

    # The runtime executes ONLY the declared commands: the passed gate's
    # command_echo is byte-equal to the declaration (never a guessed host
    # toolchain invocation). The language declaration is opaque data
    # (NFR-0147: the runtime never enumerates host languages).
    run_id, _candidate, contract_path, events = _drive_local_gates(
        host_repo, trac, declared_contract_toml()
    )
    passed = [e for e in events if e.type == "local_gate.passed"]
    assert passed, "declared gate must execute"
    assert list(passed[0].payload["command_echo"]) == ["true"]
    assert passed[0].payload["normalized_result"]["schema"] == "tracks-gate-result"
    replay = trac("replay")
    assert "host_contract" in replay.stdout.lower() or "local_gate" in replay.stdout.lower()

    # Declared-but-unknown vocabulary is fail-closed: host_contract.invalid
    # + blocked, and no gate command is guessed for the unknown declaration.
    contract_path.write_text(
        TEST_EXECUTION_CONTRACT
        + "\n"
        + declared_contract_toml()
        + "\n[host-contract.bogus]\nkey = 1\n",
        encoding="utf-8",
    )
    events2 = _replay_local_gates(host_repo, run_id, passed[0].payload["candidate_sha"])
    invalid = [e for e in events2 if e.type == "host_contract.invalid"]
    assert invalid, "unknown host-contract table must land host_contract.invalid"
    assert invalid[-1].payload.get("reason") == "missing_contract"
    assert "blocked" in trac("status").stdout


# AC-FR0281-03@v0.8 TRACKS-TRACE failed gate machine evidence revision loop
def test_failed_gate_machine_evidence_revision_loop(host_repo, trac, event_log, tmp_path):
    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): a failing declared
    # gate yields failed machine evidence (exit code + captured streams).
    decl = LocalGateDecl(
        kind="quality",
        source="command",
        command="false",
        categories=(),
        result_channel="exit_code",
        timeout_seconds=60,
    )
    result = execute_gate(decl, tmp_path, {})
    assert result.status == "failed"
    assert result.exit_code == 1
    assert {"exit", "stdout", "stderr"} <= set(result.summary)

    run_id, candidate, contract_path, events = _drive_local_gates(
        host_repo, trac, declared_contract_toml(gate_command="false")
    )
    failed = [e for e in events if e.type == "host_contract.failed"]
    assert failed, "host_contract.failed must appear"
    p = failed[0].payload
    assert "exit_code" in p
    assert "stdout_tail" in p or "stderr_tail" in p
    assert p["normalized_result"]["schema"] == "tracks-gate-result"
    status = trac("status")
    assert "needs_attention" in status.stdout.lower()
    assert "blocked" in status.stdout.lower()
    # Revision loop: the repaired contract re-materializes, validates and the
    # affected verification re-runs to green (never skip-ahead on the old
    # failure).
    contract_path.write_text(
        TEST_EXECUTION_CONTRACT + "\n" + declared_contract_toml(),
        encoding="utf-8",
    )
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode == 0, v.stdout + v.stderr
    events2 = _replay_local_gates(host_repo, run_id, candidate)
    assert any(e.type == "local_gate.passed" for e in events2)
    assert [e for e in events2 if e.type == "host_contract.materialized"]
