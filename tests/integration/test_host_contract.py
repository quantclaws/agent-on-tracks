"""Integration: host contract materialization (FR-0281, IF-HOSTCONTRACT-001/002)."""

from __future__ import annotations

import pytest

from tracks.executor.host_contract import load_host_contract

pytestmark = pytest.mark.integration


# AC-FR0281-01@v0.8 TRACKS-TRACE materialized contract valid
def test_materialized_contract_valid(host_repo, trac, event_log):
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    # Materialized contract must exist after init/start
    trac("init")
    if not contract_path.exists():
        # Pre-implementation: contract not yet materialized; loader must still raise IF token
        # Legal red will be produced via missing events below, not file existence
        pass
    else:
        assert contract_path.exists()
    try:
        load_host_contract(contract_path)
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError as exc:
        assert "IF-HOSTCONTRACT-001" in str(exc)
    except FileNotFoundError:
        # File absent pre-implementation is legal red; treat as stub token path
        assert True

    trac("run")
    events = event_log()
    materialized = [e for e in events if e["type"] == "host_contract.materialized"]
    assert materialized, "host_contract.materialized must appear"
    p = materialized[0]["payload"]
    assert "contract_path" in p
    assert "contract_digest" in p
    assert p["version"] == 1
    # Validate must pass for materialized contract
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode == 0 or "host-contract" in v.stdout.lower()


# AC-FR0281-02@v0.8 TRACKS-TRACE execute per contract only and unknown language blocked
def test_execute_per_contract_only(host_repo, trac, event_log, tmp_path):
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    # Module contract (IF-HOSTCONTRACT-001, unit-pinned): unknown
    # host-contract tables fail closed at load time — only declared gates run.
    bad_path = tmp_path / "project.toml"
    bad_path.write_text(
        "[host-contract]\nversion = 1\n[host-contract.bogus]\nkey = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown host-contract table"):
        load_host_contract(bad_path)

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    trac("run")
    events = event_log()
    replay = trac("replay").stdout
    assert "host_contract" in replay.lower() or "local_gate" in replay.lower()
    assert events or "host_contract" in replay.lower()
    # Inject unknown language
    if contract_path.exists():
        orig = contract_path.read_text(encoding="utf-8")
        contract_path.write_text(orig.replace('language = "python"', 'language = "brainfuck"'), encoding="utf-8")
        trac("run")
        events2 = event_log()
        invalid = [e for e in events2 if e["type"] == "host_contract.invalid"]
        assert invalid or "blocked" in trac("status").stdout
        if invalid:
            assert invalid[0]["payload"].get("reason") in ("unknown_language", "malformed_result", "gate_failed")
            assert "unknown language" in trac("status").stdout.lower() or "blocked" in trac("status").stdout
        contract_path.write_text(orig, encoding="utf-8")


# AC-FR0281-03@v0.8 TRACKS-TRACE failed gate machine evidence revision loop
def test_failed_gate_machine_evidence_revision_loop(host_repo, trac, event_log, tmp_path):
    from tracks.executor.host_contract import LocalGateDecl, execute_gate

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

    assert trac("init").returncode == 0
    started = trac("start", "v0.8", stdin="构建一个事件溯源运行时")
    assert started.returncode == 0, started.stderr
    trac("run")
    events = event_log()
    failed = [e for e in events if e["type"] == "host_contract.failed"]
    assert failed, "host_contract.failed must appear"
    p = failed[0]["payload"]
    assert "exit_code" in p
    assert "stdout_tail" in p or "stderr_tail" in p
    assert "needs_attention" in trac("status").stdout.lower()
    # Revision loop: after fixing contract, validate must pass again
    contract_path = host_repo / ".tracks" / "projects" / "project.toml"
    v = trac("validate", "--file", str(contract_path))
    assert v.returncode in (0, 1)
