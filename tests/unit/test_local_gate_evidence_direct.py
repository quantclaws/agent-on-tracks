"""Direct RED coverage for candidate-bound local-gate evidence (FR-0269)."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import tracks.executor.verify_gates as verify_gates_module
from tests.unit.helpers import git_repo
from tracks.executor.executor import Executor
from tracks.executor.host_contract import (
    GATE_RESULT_PROTOCOL,
    HostContract,
    LocalGateDecl,
    NormalizedGateResult,
    VersionDecl,
    execute_gate,
)
from tracks.executor.local_gate_evidence import (
    has_complete_passed_gates,
    normalized_result_payload,
)
from tracks.kernel.events import Command
from tracks.store import Store

_CANDIDATE = "a" * 40
_FOREIGN = "f" * 40
_DIGEST = "contract-current"


def _contract() -> HostContract:
    return HostContract(
        contract_version=1,
        language="python",
        toolchain="cpython",
        install="",
        local_gates=(
            LocalGateDecl("quality", "command", "quality-check", (), "exit_code", 5),
            LocalGateDecl("trace", "command", "trace-check", (), "exit_code", 5),
        ),
        version=VersionDecl("v{minor}.0", "v{minor}.{n}", "v{minor}.{n}-pre.{ulid}"),
        build_command="",
        build_artifact="",
        smoke=(),
        security_scans=(),
        ci={},
        tracker={},
    )


def _executor(tmp_path: Path, monkeypatch) -> tuple[Executor, Store, HostContract]:
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    executor = Executor(store, repo, "RUN")
    contract = _contract()
    monkeypatch.setattr(
        executor,
        "_load_or_default_contract",
        lambda _cmd, _candidate: (contract, _DIGEST, "fixture"),
    )
    monkeypatch.setattr(verify_gates_module, "validate_host_contract", lambda *_args: ())
    monkeypatch.setattr(executor, "_release_version_facts", lambda _state: {})
    return executor, store, contract


def _result(gate_id: str, status: str, exit_code: int) -> NormalizedGateResult:
    return NormalizedGateResult(
        gate_id=gate_id,
        result_version=1,
        status=status,
        exit_code=exit_code,
        summary={"gate": gate_id, "observed": True},
        command_echo=(f"{gate_id}-check",),
    )


def test_execute_gate_result_records_expanded_command_echo(tmp_path):
    gate = LocalGateDecl("quality", "command", "printf {version}", (), "exit_code", 5)

    result = execute_gate(gate, tmp_path, {"version": "1.2"})

    assert result.status == "passed"
    assert result.command_echo == ("printf", "1.2")


def test_file_result_cannot_override_nonzero_process_exit(tmp_path):
    script = tmp_path / "gate.py"
    script.write_text(
        "import json, sys\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as out:\n"
        "    json.dump({'schema': 'tracks-gate-result', 'version': 1, "
        "'status': 'passed', 'exit_code': 0, 'summary': {}}, out)\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    gate = LocalGateDecl(
        "quality",
        "command",
        f"{sys.executable} {script} {{result}}",
        (),
        "file",
        5,
    )

    result = execute_gate(gate, tmp_path, {})

    assert result.status == "failed"
    assert result.exit_code == 3


def test_missing_executable_file_gate_keeps_real_command_echo(tmp_path):
    gate = LocalGateDecl(
        "quality",
        "command",
        "definitely-missing-gate {result}",
        (),
        "file",
        5,
    )

    result = execute_gate(gate, tmp_path, {})

    assert result.status == "failed"
    assert result.command_echo[0] == "definitely-missing-gate"
    assert result.command_echo[-1].endswith("result.json")


def test_each_declared_gate_event_carries_complete_normalized_result(tmp_path, monkeypatch):
    """Both pass and fail events must preserve the protocol result object."""
    executor, store, _contract_value = _executor(tmp_path, monkeypatch)
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        executor,
        "_emit",
        lambda event_type, payload, **_kwargs: emitted.append((event_type, payload)),
    )
    results = iter((_result("quality", "passed", 0), _result("trace", "failed", 7)))
    monkeypatch.setattr(verify_gates_module, "execute_gate", lambda *_args: next(results))

    ok = executor._execute_verify_gates(
        Command("run_local_gates", command_id="CMD-GATES"),
        _CANDIDATE,
        _DIGEST,
        _contract_value,
        SimpleNamespace(),
    )

    assert ok is False
    assert [event_type for event_type, _payload in emitted] == [
        "local_gate.passed",
        "local_gate.failed",
    ]
    for _event_type, payload in emitted:
        normalized = payload["normalized_result"]
        assert normalized["schema"] == GATE_RESULT_PROTOCOL
        assert normalized["version"] == 1
        assert normalized["status"] in {"passed", "failed"}
        assert isinstance(normalized["exit_code"], int)
        assert isinstance(normalized["summary"], dict)
        assert payload["command_echo"]
        assert payload["candidate_sha"] == _CANDIDATE
        assert payload["contract_digest"] == _DIGEST


def test_reconcile_does_not_skip_gates_for_foreign_passed_evidence(tmp_path, monkeypatch):
    """An unrelated candidate's pass cannot satisfy this reconcile command."""
    executor, store, contract = _executor(tmp_path, monkeypatch)
    store.append(
        "RUN",
        "v0.8",
        "local_gate.passed",
        {"kind": "quality", "candidate_sha": _FOREIGN, "contract_digest": _DIGEST},
    )
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )
    monkeypatch.setattr(executor, "issue", lambda _command: None)

    executor._do_run_local_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        SimpleNamespace(),
        None,
        True,
    )

    assert seen == [gate.kind for gate in contract.local_gates]


@pytest.mark.parametrize(
    "old_events",
    [
        (
            {
                "kind": "quality",
                "candidate_sha": _CANDIDATE,
                "contract_digest": "contract-old",
            },
        ),
        (
            {
                "kind": "quality",
                "candidate_sha": _CANDIDATE,
                "contract_digest": _DIGEST,
            },
        ),
    ],
    ids=("changed-contract", "partial-gate-coverage"),
)
def test_resume_requires_current_contract_and_all_declared_gates(
    tmp_path, monkeypatch, old_events
):
    """One old/partial pass must not bypass the real two-gate execution."""
    executor, store, contract = _executor(tmp_path, monkeypatch)
    for payload in old_events:
        store.append("RUN", "v0.8", "local_gate.passed", payload)
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )

    resumed, digest = executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )

    assert resumed == contract
    assert digest == _DIGEST
    assert seen == [gate.kind for gate in contract.local_gates]


def test_resume_does_not_hide_later_failure_behind_an_earlier_pass(tmp_path, monkeypatch):
    executor, store, contract = _executor(tmp_path, monkeypatch)
    for ordinal, gate in enumerate(contract.local_gates):
        result = _result(gate.kind, "passed", 0)
        store.append(
            "RUN",
            "v0.8",
            "local_gate.passed",
            {
                "kind": gate.kind,
                "gate_identity": f"{gate.kind}[{ordinal}]",
                "candidate_sha": _CANDIDATE,
                "contract_digest": _DIGEST,
                "command_echo": list(result.command_echo),
                "normalized_result": normalized_result_payload(result),
            },
        )
    failed = _result("quality", "failed", 9)
    store.append(
        "RUN",
        "v0.8",
        "local_gate.failed",
        {
            "kind": "quality",
            "gate_identity": "quality[0]",
            "candidate_sha": _CANDIDATE,
            "contract_digest": _DIGEST,
            "command_echo": list(failed.command_echo),
            "normalized_result": normalized_result_payload(failed),
        },
    )
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )

    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )

    assert seen == [gate.kind for gate in contract.local_gates]


@pytest.mark.parametrize("exit_code", [None, 7])
def test_resume_rejects_passed_result_without_zero_exit(tmp_path, monkeypatch, exit_code):
    executor, store, contract = _executor(tmp_path, monkeypatch)
    for ordinal, gate in enumerate(contract.local_gates):
        result = NormalizedGateResult(
            gate_id=gate.kind,
            result_version=1,
            status="passed",
            exit_code=exit_code,
            summary={},
            command_echo=(gate.command,),
        )
        store.append(
            "RUN",
            "v0.8",
            "local_gate.passed",
            {
                "kind": gate.kind,
                "gate_identity": f"{gate.kind}[{ordinal}]",
                "candidate_sha": _CANDIDATE,
                "contract_digest": _DIGEST,
                "command_echo": [gate.command],
                "normalized_result": normalized_result_payload(result),
            },
        )
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )

    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )

    assert seen == [gate.kind for gate in contract.local_gates]


def test_legacy_evidence_reruns_once_then_new_complete_evidence_reuses(
    tmp_path, monkeypatch
):
    executor, store, contract = _executor(tmp_path, monkeypatch)
    store.append(
        "RUN",
        "v0.8",
        "local_gate.passed",
        {"kind": "quality", "candidate_sha": _CANDIDATE, "contract_digest": _DIGEST},
    )
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )

    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )
    assert seen == [gate.kind for gate in contract.local_gates]


def test_empty_contract_never_counts_as_complete_evidence(tmp_path, monkeypatch):
    executor, store, contract = _executor(tmp_path, monkeypatch)
    empty_contract = replace(contract, local_gates=())

    assert not has_complete_passed_gates(
        store.events("RUN"), _CANDIDATE, _DIGEST, empty_contract
    )


def test_legacy_failure_requires_all_gates_to_be_newer(tmp_path, monkeypatch):
    executor, store, contract = _executor(tmp_path, monkeypatch)
    quality = _result("quality", "passed", 0)
    trace = _result("trace", "passed", 0)
    store.append(
        "RUN",
        "v0.8",
        "local_gate.passed",
        {
            "kind": "quality",
            "gate_identity": "quality[0]",
            "candidate_sha": _CANDIDATE,
            "contract_digest": _DIGEST,
            "command_echo": list(quality.command_echo),
            "normalized_result": normalized_result_payload(quality),
        },
    )
    store.append(
        "RUN",
        "v0.8",
        "local_gate.failed",
        {"kind": "quality", "candidate_sha": _CANDIDATE, "contract_digest": _DIGEST},
    )
    store.append(
        "RUN",
        "v0.8",
        "local_gate.passed",
        {
            "kind": "trace",
            "gate_identity": "trace[1]",
            "candidate_sha": _CANDIDATE,
            "contract_digest": _DIGEST,
            "command_echo": list(trace.command_echo),
            "normalized_result": normalized_result_payload(trace),
        },
    )
    seen: list[str] = []
    monkeypatch.setattr(
        verify_gates_module,
        "execute_gate",
        lambda gate, *_args: (seen.append(gate.kind) or _result(gate.kind, "passed", 0)),
    )

    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )

    assert seen == [gate.kind for gate in contract.local_gates]

    seen.clear()
    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )
    assert seen == []

    store.append(
        "RUN",
        "v0.8",
        "local_gate.failed",
        {"kind": "quality", "candidate_sha": _CANDIDATE, "contract_digest": _DIGEST},
    )
    executor._run_contract_gates(
        Command("run_local_gates", params={"candidate_sha": _CANDIDATE}),
        _CANDIDATE,
        SimpleNamespace(),
        resume=True,
    )
    assert seen == [gate.kind for gate in contract.local_gates]


def test_missing_registry_command_blocks_with_bound_failure(tmp_path, monkeypatch):
    """FR-0269: an unavailable declared quality gate cannot count as passed."""
    executor, _store, contract = _executor(tmp_path, monkeypatch)
    contract = replace(contract, local_gates=(
        LocalGateDecl("quality", "guard_registry", "", (), "exit_code", 5),
    ))
    monkeypatch.setattr(verify_gates_module, "lint_check_command", lambda _repo: None)
    emitted = []
    monkeypatch.setattr(executor, "_emit", lambda kind, payload, **_kw: emitted.append(
        (kind, payload)
    ))
    ok = executor._execute_verify_gates(
        Command("run_local_gates", command_id="CMD-MISSING-GUARD"),
        _CANDIDATE, _DIGEST, contract, SimpleNamespace(),
    )
    assert ok is False
    assert [kind for kind, _payload in emitted] == ["local_gate.failed"]
    payload = emitted[0][1]
    assert payload["candidate_sha"] == _CANDIDATE
    assert payload["contract_digest"] == _DIGEST
    assert payload["kind"] == "quality"
    assert payload["normalized_result"]["status"] == "failed"
    assert "command" in payload["detail"]
