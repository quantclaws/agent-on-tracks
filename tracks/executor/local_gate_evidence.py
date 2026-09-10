"""Pure validation and serialization for candidate-bound local gate evidence."""

from __future__ import annotations

from collections.abc import Iterable

from tracks.executor.host_contract import GATE_RESULT_PROTOCOL, GATE_RESULT_VERSION


def gate_identity(kind: str, ordinal: int) -> str:
    """Return the stable declaration identity, including duplicate kinds."""
    return f"{kind}[{ordinal}]"


def normalized_result_payload(result) -> dict:
    """Serialize one host-contract result into the event protocol shape."""
    return {
        "schema": GATE_RESULT_PROTOCOL,
        "version": getattr(result, "result_version", GATE_RESULT_VERSION),
        "status": getattr(result, "status", "malformed"),
        "exit_code": getattr(result, "exit_code", None),
        "summary": dict(getattr(result, "summary", None) or {}),
        "gate_id": getattr(result, "gate_id", ""),
    }


def _valid_normalized_result(
    payload: object, command_echo: object, *, status: str | None = None
) -> bool:
    return (
        isinstance(payload, dict)
        and _valid_protocol(payload)
        and _valid_status(payload, status)
        and _valid_exit_code(payload.get("exit_code"))
        and _valid_pass_exit(payload)
        and isinstance(payload.get("summary"), dict)
        and _valid_command_echo(command_echo)
    )


def _valid_protocol(payload: dict) -> bool:
    return (
        payload.get("schema") == GATE_RESULT_PROTOCOL
        and payload.get("version") == GATE_RESULT_VERSION
    )


def _valid_status(payload: dict, expected: str | None) -> bool:
    status = payload.get("status")
    return status in {"passed", "failed", "malformed"} and (
        expected is None or status == expected
    )


def _valid_exit_code(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _valid_pass_exit(payload: dict) -> bool:
    return payload.get("status") != "passed" or payload.get("exit_code") == 0


def _valid_command_echo(command_echo: object) -> bool:
    return bool(command_echo) and isinstance(command_echo, list) and all(
        isinstance(item, str) for item in command_echo
    )


def has_complete_passed_gates(
    events: Iterable,
    candidate_sha: str,
    contract_digest: str,
    contract,
) -> bool:
    """Check that the latest result for every declaration is a valid pass.

    Missing identity fields deliberately make old evidence non-reusable. The
    latest result wins per declaration, so a later failure cannot be hidden by
    an earlier pass.
    """
    latest = _latest_bound_gate_events(events, candidate_sha, contract_digest)
    expected = {
        gate_identity(gate.kind, ordinal)
        for ordinal, gate in enumerate(contract.local_gates)
    }
    if not expected:
        return False
    expected_events = [latest.get(identity) for identity in expected]
    if any(event is None for event in expected_events):
        return False
    barrier_seq = max(
        (_event_seq(event) for identity, event in latest.items() if identity not in expected),
        default=-1,
    )
    if any(_event_seq(event) <= barrier_seq for event in expected_events):
        return False
    return all(
        _is_valid_pass(latest.get(gate_identity(gate.kind, ordinal)), gate.kind)
        for ordinal, gate in enumerate(contract.local_gates)
    )


def _latest_bound_gate_events(
    events: Iterable, candidate_sha: str, contract_digest: str
) -> dict[str, object]:
    latest: dict[str, object] = {}
    for event in events:
        payload = event.payload or {}
        if _is_bound_gate_event(event, payload, candidate_sha, contract_digest):
            identity = payload.get("gate_identity")
            if isinstance(identity, str):
                latest[identity] = event
            else:
                latest["__legacy__"] = event
    return latest


def _event_seq(event) -> int:
    return int(getattr(event, "seq", 0) or 0)


def _is_bound_gate_event(event, payload: dict, candidate_sha: str, digest: str) -> bool:
    return (
        event.type in {"local_gate.passed", "local_gate.failed"}
        and payload.get("candidate_sha") == candidate_sha
        and payload.get("contract_digest") == digest
    )


def _is_valid_pass(event, gate_kind: str) -> bool:
    if event is None or event.type != "local_gate.passed":
        return False
    payload = event.payload or {}
    result = payload.get("normalized_result")
    return (
        _valid_normalized_result(result, payload.get("command_echo"), status="passed")
        and result.get("gate_id") == gate_kind
    )
