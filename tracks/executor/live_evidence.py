"""Interface stub for the canonical live release-evidence producer.

Contract: IF-LIVE-001.  Devon replaces only the function bodies during M-IMPL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    raise NotImplementedError("IF-LIVE-001")


def write_live_evidence(
    repo: str,
    bundle: LiveEvidenceBundle,
    referenced_blobs: dict[str, bytes],
) -> str:
    """Atomically persist an IF-LIVE-001 bundle and content-addressed blobs."""
    raise NotImplementedError("IF-LIVE-001")
