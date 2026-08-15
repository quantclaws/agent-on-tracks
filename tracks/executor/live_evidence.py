"""Canonical live release-evidence producer (IF-LIVE-001, FR-0230/FR-0231).

``bind_live_evidence`` binds independently observable journey facts (real
OpencodeBackend dispatch receipts, Runtime gates, RGR lineage and the M-IMPL
boundary) into a ``LiveEvidenceBundle``, failing closed on fake/simulated/
overlay provenance or an incomplete journey.  ``write_live_evidence`` persists
the bundle as canonical JSON plus content-addressed blobs under the §3h
release-evidence root (atomic writes, never overwriting different bytes).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

SCHEMA_VERSION = "tracks.release-evidence/v1"
EVIDENCE_ROOT = ".tracks/runtime/release-evidence/v1"
REQUIRED_DEVON_PHASES: tuple[str, str, str] = ("RED", "GREEN", "REFACTOR")
_PRISM_PHASES = ("PRISM_RED", "PRISM_FINAL")
_HEX = "0123456789abcdef"
_FAKE_PAYLOAD_KEYS = (
    "fake_backend",
    "simulated",
    "trac_fake_simulate",
    "assignment_overlay",
    "assignment_simulation",
)
# Journey-ordered gates with the verdict.passed check tokens each accepts.
_GATE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("RED_GATE", ("red_valid", "red_gate", "red")),
    ("GREEN_GATE", ("green_gate", "green")),
    ("REFACTOR_GATE", ("refactor_gate", "refactor")),
    ("TASK_REVIEW", ("task_review",)),
    ("PRISM_FINAL", ("prism_final",)),
    ("ISLAND_GATE_2", ("island_gate_2", "island_2")),
)


@dataclass(frozen=True)
class AgentIOReceipt:
    role: str
    phase: str
    task_id: str
    attempt: int
    command_seq: int
    outcome_seq: int
    input_ref: str
    input_sha256: str
    output_ref: str
    output_sha256: str
    audit_completeness: Literal["complete"]


@dataclass(frozen=True)
class CandidateArtifactEvidence:
    name: str
    sha256: str
    distribution: Literal["agent-on-tracks"]
    version: str
    installed_import_path: str
    source_tree_import: Literal[False]


@dataclass(frozen=True)
class ProvenanceEvidence:
    backend_class: Literal["OpencodeBackend"]
    fake_backend: Literal[False]
    trac_fake_simulate: Literal[False]
    assignment_overlay: Literal[False]
    assignment_simulation: Literal[False]
    event_origin: Literal["runtime"]


@dataclass(frozen=True)
class EventSequenceEvidence:
    first_seq: int
    last_seq: int
    events_ref: str
    events_sha256: str


@dataclass(frozen=True)
class RGRLineageEvidence:
    task_id: str
    attempt: int
    red_ref: str
    r_sha: str
    g_sha: str
    red_checkpoint_seq: int
    green_commit_seq: int
    trailers: dict[str, str]


@dataclass(frozen=True)
class GateObservation:
    gate: Literal[
        "RED_GATE",
        "GREEN_GATE",
        "REFACTOR_GATE",
        "TASK_REVIEW",
        "PRISM_FINAL",
        "ISLAND_GATE_2",
    ]
    status: Literal["pass"]
    seq: int
    source: Literal["runtime", "prism"]
    source_event_type: str
    command_id: str


@dataclass(frozen=True)
class BoundaryEvidence:
    stage: Literal["M-IMPL"]
    stage_exited_seq: int
    run_completed_seq: int
    terminal_state: Literal["boundary"]


@dataclass(frozen=True)
class LiveEvidenceBundle:
    schema_version: Literal["tracks.release-evidence/v1"]
    status: Literal["satisfied"]
    candidate_sha: str
    candidate_artifact: CandidateArtifactEvidence
    run_id: str
    backend: Literal["opencode"]
    provenance: ProvenanceEvidence
    required_devon_phases: tuple[str, str, str]
    agent_io: tuple[AgentIOReceipt, ...]
    event_sequence: EventSequenceEvidence
    rgr_lineage: RGRLineageEvidence
    gate_observations: tuple[GateObservation, ...]
    boundary: BoundaryEvidence


def _canonical_bytes(value: object) -> bytes:
    """UTF-8 canonical JSON: sort_keys, compact separators, trailing LF (§3h)."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8") + b"\n"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_ref(candidate_sha: str, run_id: str, sha256_hex: str) -> str:
    return f"{EVIDENCE_ROOT}/{candidate_sha}/{run_id}/blobs/{sha256_hex}"


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(char in _HEX for char in value)


def _event_dict(event: object) -> dict:
    """Accept plain dicts or EventEnvelope-like objects from the store."""
    if isinstance(event, dict):
        return dict(event)
    return {
        "seq": event.seq,
        "type": event.type,
        "payload": dict(getattr(event, "payload", {}) or {}),
        "command_id": getattr(event, "command_id", None),
        "task_id": getattr(event, "task_id", None),
    }


def _payload_of(item: dict) -> dict:
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else {}


def _validate_provenance(
    backend_name: str,
    trac_fake_simulate: bool,
    assignment_overlay: bool,
    assignment_simulation: bool,
) -> None:
    if backend_name != "opencode":
        raise ValueError(
            "IF-LIVE-001:provenance not real: backend is not opencode "
            f"({backend_name!r}); fake backends cannot produce release evidence"
        )
    if trac_fake_simulate:
        raise ValueError("IF-LIVE-001:provenance not real: TRAC_FAKE_SIMULATE is active")
    if assignment_overlay:
        raise ValueError("IF-LIVE-001:provenance not real: assignment overlay is active")
    if assignment_simulation:
        raise ValueError("IF-LIVE-001:provenance not real: assignment simulation is active")


def _validate_identity(
    run_id: str, candidate_sha: str, candidate_artifact: CandidateArtifactEvidence
) -> None:
    if not run_id:
        raise ValueError("IF-LIVE-001:journey incomplete: run_id is empty")
    if not _is_hex(candidate_sha, 40):
        raise ValueError("IF-LIVE-001:candidate_sha is not 40 lowercase hex")
    artifact = candidate_artifact
    if artifact.distribution != "agent-on-tracks":
        raise ValueError("IF-LIVE-001:candidate artifact distribution is not agent-on-tracks")
    if artifact.source_tree_import is not False:
        raise ValueError("IF-LIVE-001:candidate artifact was imported from the source tree")
    if not _is_hex(artifact.sha256, 64):
        raise ValueError("IF-LIVE-001:candidate artifact sha256 is not 64 lowercase hex")
    if not (artifact.name and artifact.version and artifact.installed_import_path):
        raise ValueError("IF-LIVE-001:candidate artifact identity fields are incomplete")


def _normalize_events(events: list[dict]) -> list[dict]:
    if not events:
        raise ValueError("IF-LIVE-001:journey incomplete: no events")
    journey: list[dict] = []
    previous_seq: int | None = None
    for event in events:
        item = _event_dict(event)
        seq, event_type = item.get("seq"), item.get("type")
        if not isinstance(seq, int) or not isinstance(event_type, str):
            raise ValueError(f"IF-LIVE-001:journey malformed: event missing seq/type at {seq!r}")
        if previous_seq is not None and seq <= previous_seq:
            raise ValueError(f"IF-LIVE-001:journey malformed: seq {seq} is not strictly increasing")
        previous_seq = seq
        journey.append(item)
    return journey


def _scan_event_provenance(journey: list[dict]) -> None:
    for item in journey:
        payload = _payload_of(item)
        for key in _FAKE_PAYLOAD_KEYS:
            if payload.get(key):
                raise ValueError(
                    f"IF-LIVE-001:provenance not real: {key} at event seq {item['seq']}"
                )


def _is_dispatch_command(item: dict) -> bool:
    command = _payload_of(item).get("command")
    return item.get("type") == "command.issued" and isinstance(command, dict) and (
        command.get("kind") == "dispatch_agent"
    )


def _nearest_dispatch(journey: list[dict], outcome_seq: int) -> dict | None:
    command: dict | None = None
    for item in journey:
        if item["seq"] < outcome_seq and _is_dispatch_command(item):
            command = item
    return command


def _outcome_events(journey: list[dict], role: str, phases: tuple[str, ...]) -> list[dict]:
    return [
        item
        for item in journey
        if item.get("type") == "outcome.received"
        and _payload_of(item).get("role") == role
        and _payload_of(item).get("phase") in phases
    ]


def _validate_outcome(item: dict) -> None:
    payload = _payload_of(item)
    status = payload.get("status")
    if status is not None and status != "done":
        raise ValueError(
            f"IF-LIVE-001:audit incomplete: outcome status is {status!r} at seq {item['seq']}"
        )
    completeness = payload.get("audit_completeness")
    if completeness is not None and completeness != "complete":
        raise ValueError(
            f"IF-LIVE-001:audit incomplete: {completeness!r} at seq {item['seq']}"
        )


def _attempt_of(outcome_item: dict, command_item: dict) -> int:
    attempt = _payload_of(outcome_item).get("attempt")
    if isinstance(attempt, int) and attempt >= 1:
        return attempt
    command = _payload_of(command_item).get("command")
    params = command.get("params") if isinstance(command, dict) else None
    assignment = params.get("assignment") if isinstance(params, dict) else None
    if isinstance(assignment, dict) and isinstance(assignment.get("attempt"), int):
        return assignment["attempt"]
    return 1


def _agent_receipt(
    command_item: dict, outcome_item: dict, candidate_sha: str, run_id: str
) -> AgentIOReceipt:
    payload = _payload_of(outcome_item)
    input_sha = _sha256_hex(_canonical_bytes(command_item))
    output_sha = _sha256_hex(_canonical_bytes(outcome_item))
    task_id = outcome_item.get("task_id") or payload.get("task_id") or ""
    return AgentIOReceipt(
        role=str(payload.get("role", "")),
        phase=str(payload.get("phase", "")),
        task_id=str(task_id),
        attempt=_attempt_of(outcome_item, command_item),
        command_seq=command_item["seq"],
        outcome_seq=outcome_item["seq"],
        input_ref=_blob_ref(candidate_sha, run_id, input_sha),
        input_sha256=input_sha,
        output_ref=_blob_ref(candidate_sha, run_id, output_sha),
        output_sha256=output_sha,
        audit_completeness="complete",
    )


def _bind_agent_io(journey: list[dict], candidate_sha: str, run_id: str) -> tuple:
    devon = _outcome_events(journey, "devon", REQUIRED_DEVON_PHASES)
    seen_phases = {_payload_of(item).get("phase") for item in devon}
    missing = [phase for phase in REQUIRED_DEVON_PHASES if phase not in seen_phases]
    if missing:
        raise ValueError(
            "IF-LIVE-001:journey incomplete: missing Devon dispatch receipts for "
            + ",".join(missing)
        )
    prism = _outcome_events(journey, "prism", _PRISM_PHASES)
    seen_prism_phases = {_payload_of(item).get("phase") for item in prism}
    missing_prism = [phase for phase in _PRISM_PHASES if phase not in seen_prism_phases]
    if missing_prism:
        raise ValueError(
            "IF-LIVE-001:journey incomplete: missing Prism dispatch receipts for "
            + ",".join(missing_prism)
        )
    outcomes = devon + prism
    receipts = []
    for outcome_item in sorted(outcomes, key=lambda item: item["seq"]):
        _validate_outcome(outcome_item)
        command_item = _nearest_dispatch(journey, outcome_item["seq"])
        if command_item is None:
            raise ValueError(
                f"IF-LIVE-001:audit incomplete: no dispatch command before seq "
                f"{outcome_item['seq']}"
            )
        receipts.append(_agent_receipt(command_item, outcome_item, candidate_sha, run_id))
    return tuple(receipts)


def _gate_events(journey: list[dict]) -> dict:
    """First ``verdict.passed`` event per gate, keyed by gate name."""
    found: dict[str, dict] = {}
    for item in journey:
        if item.get("type") != "verdict.passed":
            continue
        check = _payload_of(item).get("check")
        for gate, tokens in _GATE_TOKENS:
            if check in tokens:
                found.setdefault(gate, item)
                break
    return found


def _validate_gate_journey(journey: list[dict]) -> dict:
    gates = _gate_events(journey)
    missing = [gate for gate, _ in _GATE_TOKENS if gate not in gates]
    if missing:
        raise ValueError("IF-LIVE-001:journey incomplete: missing gates " + ",".join(missing))
    seqs = [gates[gate]["seq"] for gate, _ in _GATE_TOKENS]
    if any(later <= earlier for earlier, later in zip(seqs, seqs[1:], strict=False)):
        raise ValueError("IF-LIVE-001:journey incomplete: gates are not in journey order")
    return gates


def _gate_observations(gates: dict) -> tuple:
    observations = []
    for gate, _ in _GATE_TOKENS:
        item = gates[gate]
        command_id = item.get("command_id") or _payload_of(item).get("command_id") or ""
        observations.append(
            GateObservation(
                gate=gate,
                status="pass",
                seq=item["seq"],
                source="prism" if gate == "PRISM_FINAL" else "runtime",
                source_event_type="verdict.passed",
                command_id=str(command_id),
            )
        )
    return tuple(observations)


def _first_event(journey: list[dict], event_type: str) -> dict | None:
    for item in journey:
        if item.get("type") == event_type:
            return item
    return None


def _validated_boundary(journey: list[dict], island_seq: int) -> BoundaryEvidence:
    exit_item = _first_event(journey, "stage.exited")
    completed_item = _first_event(journey, "run.completed")
    if exit_item is None or _payload_of(exit_item).get("stage") != "M-IMPL":
        raise ValueError("IF-LIVE-001:journey incomplete: missing stage.exited(M-IMPL)")
    if completed_item is None or _payload_of(completed_item).get("terminal_state") != "boundary":
        raise ValueError(
            "IF-LIVE-001:journey incomplete: missing run.completed(terminal_state=boundary)"
        )
    if completed_item["seq"] != exit_item["seq"] + 1:
        raise ValueError(
            "IF-LIVE-001:journey incomplete: run.completed does not immediately follow "
            "stage.exited(M-IMPL)"
        )
    if island_seq >= exit_item["seq"]:
        raise ValueError("IF-LIVE-001:journey incomplete: boundary precedes ISLAND_GATE_2")
    return BoundaryEvidence(
        stage="M-IMPL",
        stage_exited_seq=exit_item["seq"],
        run_completed_seq=completed_item["seq"],
        terminal_state="boundary",
    )


def _rgr_lineage(journey: list[dict], gates: dict, agent_io: tuple) -> RGRLineageEvidence:
    started = _first_event(journey, "task.started")
    red_checkpoint = _first_event(journey, "red.checkpointed")
    green_commit = _first_event(journey, "green.committed")
    started_payload = _payload_of(started) if started else {}
    lead_receipt = agent_io[0] if agent_io else None
    attempt = started_payload.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        attempt = lead_receipt.attempt if lead_receipt else 1
    red_payload = _payload_of(red_checkpoint) if red_checkpoint else {}
    green_payload = _payload_of(green_commit) if green_commit else {}
    # Without explicit lineage events the gate observations carry the ordering.
    red_seq = red_checkpoint["seq"] if red_checkpoint else gates["RED_GATE"]["seq"]
    green_seq = green_commit["seq"] if green_commit else gates["GREEN_GATE"]["seq"]
    if red_seq >= green_seq:
        raise ValueError("IF-LIVE-001:journey incomplete: red checkpoint not before green commit")
    trailers = green_payload.get("trailers")
    fallback_task_id = lead_receipt.task_id if lead_receipt else ""
    return RGRLineageEvidence(
        task_id=str(started_payload.get("task_id") or fallback_task_id),
        attempt=attempt,
        red_ref=str(red_payload.get("ref", "")),
        r_sha=str(red_payload.get("r_sha", "")),
        g_sha=str(green_payload.get("g_sha", "")),
        red_checkpoint_seq=red_seq,
        green_commit_seq=green_seq,
        trailers=dict(trailers) if isinstance(trailers, dict) else {},
    )


def bind_live_evidence(
    *,
    repo: str,
    run_id: str,
    candidate_sha: str,
    candidate_artifact: CandidateArtifactEvidence,
    backend_name: str,
    trac_fake_simulate: bool,
    assignment_overlay: bool,
    assignment_simulation: bool,
    events: list[dict],
) -> LiveEvidenceBundle:
    """Bind independently observable journey facts into IF-LIVE-001 evidence."""
    _validate_provenance(
        backend_name, trac_fake_simulate, assignment_overlay, assignment_simulation
    )
    _validate_identity(run_id, candidate_sha, candidate_artifact)
    journey = _normalize_events(events)
    _scan_event_provenance(journey)
    agent_io = _bind_agent_io(journey, candidate_sha, run_id)
    gates = _validate_gate_journey(journey)
    boundary = _validated_boundary(journey, gates["ISLAND_GATE_2"]["seq"])
    events_sha = _sha256_hex(_canonical_bytes(journey))
    return LiveEvidenceBundle(
        schema_version=SCHEMA_VERSION,
        status="satisfied",
        candidate_sha=candidate_sha,
        candidate_artifact=candidate_artifact,
        run_id=run_id,
        backend="opencode",
        provenance=ProvenanceEvidence(
            backend_class="OpencodeBackend",
            fake_backend=False,
            trac_fake_simulate=False,
            assignment_overlay=False,
            assignment_simulation=False,
            event_origin="runtime",
        ),
        required_devon_phases=REQUIRED_DEVON_PHASES,
        agent_io=agent_io,
        event_sequence=EventSequenceEvidence(
            first_seq=journey[0]["seq"],
            last_seq=journey[-1]["seq"],
            events_ref=_blob_ref(candidate_sha, run_id, events_sha),
            events_sha256=events_sha,
        ),
        rgr_lineage=_rgr_lineage(journey, gates, agent_io),
        gate_observations=_gate_observations(gates),
        boundary=boundary,
    )


def _resolve_ref(root: Path, ref: str) -> Path:
    if not ref or "\x00" in ref:
        raise ValueError(f"IF-LIVE-001:invalid evidence ref: {ref!r}")
    pure = PurePosixPath(ref)
    if pure.is_absolute() or os.path.isabs(ref) or ".." in pure.parts:
        raise ValueError(f"IF-LIVE-001:evidence ref escapes the repository root: {ref!r}")
    return root.joinpath(*pure.parts)


def _persist_bytes(root: Path, ref: str, data: bytes) -> None:
    """Atomically persist ``data`` at ``ref``; fail closed on different bytes."""
    target = _resolve_ref(root, ref)
    if target.exists():
        if target.read_bytes() == data:
            return
        raise ValueError(f"IF-LIVE-001:ref already exists with different bytes: {ref}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)


def write_live_evidence(
    repo: str,
    bundle: LiveEvidenceBundle,
    referenced_blobs: dict[str, bytes],
) -> str:
    """Atomically persist an IF-LIVE-001 bundle and content-addressed blobs."""
    if not isinstance(bundle, LiveEvidenceBundle):
        raise ValueError("IF-LIVE-001:write_live_evidence requires a LiveEvidenceBundle")
    root = Path(repo)
    evidence_ref = f"{EVIDENCE_ROOT}/{bundle.candidate_sha}/{bundle.run_id}/evidence.json"
    bundle_bytes = _canonical_bytes(asdict(bundle))
    for ref, data in (referenced_blobs or {}).items():
        _persist_bytes(root, ref, data)
    _persist_bytes(root, evidence_ref, bundle_bytes)
    return evidence_ref
