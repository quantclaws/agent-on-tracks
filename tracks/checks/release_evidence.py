"""IF-RELEASE-001 current-candidate release-evidence check (FR-0232/NFR-0080).

``check_release_evidence`` deterministically classifies the observable facts of
the canonical live-evidence bundles (interfaces.md §2d/§3h/§1j): selection by
UTF-8 path byte order, fixed reason-code precedence (missing -> stale ->
malformed -> not_real -> audit_incomplete -> journey_incomplete), and strict
equality of the current Git HEAD with the bundle candidate SHA.  It never
reads clocks, mtimes, agent self-reports or human input, and it never writes
anything.  ``check_release_evidence_file`` is the read-only file wrapper that
supplies the Git/evidence facts for a repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from tracks.executor.live_evidence import EVIDENCE_ROOT, SCHEMA_VERSION

ReleaseEvidenceReason = Literal[
    "ok",
    "missing",
    "stale",
    "malformed",
    "not_real",
    "audit_incomplete",
    "journey_incomplete",
]

_REQUIRED_PHASES = frozenset(
    {
        ("devon", "RED"),
        ("devon", "GREEN"),
        ("devon", "REFACTOR"),
        ("prism", "PRISM_RED"),
        ("prism", "PRISM_FINAL"),
    }
)
_REQUIRED_DEVON_PHASES = ("RED", "GREEN", "REFACTOR")
_GATES_IN_ORDER = (
    "RED_GATE",
    "GREEN_GATE",
    "REFACTOR_GATE",
    "TASK_REVIEW",
    "PRISM_FINAL",
    "ISLAND_GATE_2",
)
_REQUIRED_TRAILERS = ("Tracks-Task", "Tracks-Attempt", "Tracks-R", "Tracks-Issue", "Tracks-AC")
_TRAILER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*): (.*)$")


@dataclass(frozen=True)
class ReleaseEvidenceReport:
    status: Literal["satisfied", "not_satisfied"]
    reason_code: ReleaseEvidenceReason
    candidate_sha: str | None
    run_id: str | None
    backend: str | None
    evidence_path: str | None
    event_bounds: tuple[int, int] | None
    branch: str | None


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_evidence_path(path: str) -> tuple[str, str] | None:
    """Return (candidate_sha, run_id) for a canonical §3h evidence.json path."""
    parts = PurePosixPath(path).parts
    root = PurePosixPath(EVIDENCE_ROOT).parts
    if len(parts) != len(root) + 3 or parts[: len(root)] != root:
        return None
    if parts[-1] != "evidence.json":
        return None
    return parts[-3], parts[-2]


def _select_current(
    current_head: str | None, evidence_candidates: list[tuple[str, bytes]]
) -> tuple[list[tuple[str, str, str, bytes]], bool]:
    """Split candidates into current-SHA bundles and a marker for any others."""
    current: list[tuple[str, str, str, bytes]] = []
    has_other = False
    for path, data in evidence_candidates:
        parsed = _parse_evidence_path(path)
        if parsed is None:
            continue
        sha, run_id = parsed
        if sha == current_head:
            current.append((path, run_id, sha, data))
        else:
            has_other = True
    return current, has_other


def _load_bundle(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _backend_of(bundle: dict) -> str | None:
    backend = bundle.get("backend")
    return backend if isinstance(backend, str) else None


def _provenance_ok(bundle: dict) -> bool:
    if bundle.get("backend") != "opencode":
        return False
    provenance = bundle.get("provenance")
    if not isinstance(provenance, dict):
        return False
    if provenance.get("backend_class") != "OpencodeBackend":
        return False
    if provenance.get("fake_backend") is not False:
        return False
    if provenance.get("trac_fake_simulate") is not False:
        return False
    if provenance.get("assignment_overlay") is not False:
        return False
    if provenance.get("assignment_simulation") is not False:
        return False
    return provenance.get("event_origin") == "runtime"


def _blob_digest(ref: object, expected: object, blobs: dict) -> bool:
    if not isinstance(ref, str) or not isinstance(expected, str):
        return False
    data = blobs.get(ref)
    return isinstance(data, bytes) and _sha256_hex(data) == expected


def _receipt_audit_ok(receipt: object, blobs: dict) -> bool:
    if not isinstance(receipt, dict):
        return False
    if receipt.get("audit_completeness") != "complete":
        return False
    command_seq = receipt.get("command_seq")
    outcome_seq = receipt.get("outcome_seq")
    if not isinstance(command_seq, int) or not isinstance(outcome_seq, int):
        return False
    if command_seq >= outcome_seq:
        return False
    if not _blob_digest(receipt.get("input_ref"), receipt.get("input_sha256"), blobs):
        return False
    return _blob_digest(receipt.get("output_ref"), receipt.get("output_sha256"), blobs)


def _audit_ok(bundle: dict, blobs: dict) -> bool:
    sequence = bundle.get("event_sequence")
    if not isinstance(sequence, dict):
        return False
    if not _blob_digest(sequence.get("events_ref"), sequence.get("events_sha256"), blobs):
        return False
    agent_io = bundle.get("agent_io")
    if not isinstance(agent_io, list):
        return False
    return all(_receipt_audit_ok(receipt, blobs) for receipt in agent_io)


def _event_bounds(bundle: dict) -> tuple[int, int] | None:
    sequence = bundle.get("event_sequence")
    if not isinstance(sequence, dict):
        return None
    first_seq = sequence.get("first_seq")
    last_seq = sequence.get("last_seq")
    if not isinstance(first_seq, int) or not isinstance(last_seq, int):
        return None
    return (first_seq, last_seq)


def _required_phases_ok(bundle: dict) -> bool:
    if tuple(bundle.get("required_devon_phases") or ()) != _REQUIRED_DEVON_PHASES:
        return False
    agent_io = bundle.get("agent_io")
    if not isinstance(agent_io, list):
        return False
    seen = {
        (receipt.get("role"), receipt.get("phase"))
        for receipt in agent_io
        if isinstance(receipt, dict)
    }
    return seen >= _REQUIRED_PHASES


def _seqs_from_items(items: list, keys: tuple[str, ...]) -> list[int]:
    seqs: list[int] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if isinstance(value, int):
                seqs.append(value)
    return seqs


def _collect_seqs(bundle: dict) -> list[int]:
    seqs = _seqs_from_items(bundle.get("agent_io") or [], ("command_seq", "outcome_seq"))
    lineage = bundle.get("rgr_lineage")
    if isinstance(lineage, dict):
        seqs.extend(_seqs_from_items([lineage], ("red_checkpoint_seq", "green_commit_seq")))
    seqs.extend(_seqs_from_items(bundle.get("gate_observations") or [], ("seq",)))
    boundary = bundle.get("boundary")
    if isinstance(boundary, dict):
        seqs.extend(_seqs_from_items([boundary], ("stage_exited_seq", "run_completed_seq")))
    return seqs


def _trailers_ok(lineage: dict) -> bool:
    trailers = lineage.get("trailers")
    if not isinstance(trailers, dict):
        return False
    return all(key in trailers for key in _REQUIRED_TRAILERS)


def _lineage_ok(bundle: dict, git_facts: dict) -> bool:
    lineage = bundle.get("rgr_lineage")
    if not isinstance(lineage, dict):
        return False
    red_seq = lineage.get("red_checkpoint_seq")
    green_seq = lineage.get("green_commit_seq")
    if not isinstance(red_seq, int) or not isinstance(green_seq, int):
        return False
    if red_seq >= green_seq:
        return False
    if not _trailers_ok(lineage):
        return False
    if git_facts.get("r_ref_sha") != lineage.get("r_sha"):
        return False
    g_parent = git_facts.get("g_parent")
    if not isinstance(g_parent, str) or g_parent in (lineage.get("g_sha"), lineage.get("r_sha")):
        return False
    return git_facts.get("g_trailers") == lineage.get("trailers")


def _gates_ok(bundle: dict) -> bool:
    gates = bundle.get("gate_observations")
    if not isinstance(gates, list):
        return False
    names = [gate.get("gate") for gate in gates if isinstance(gate, dict)]
    if names != list(_GATES_IN_ORDER):
        return False
    seqs = [gate.get("seq") for gate in gates]
    if any(not isinstance(seq, int) for seq in seqs):
        return False
    if any(later <= earlier for earlier, later in zip(seqs, seqs[1:], strict=False)):
        return False
    if any(gate.get("status") != "pass" for gate in gates):
        return False
    by_name = {gate["gate"]: gate for gate in gates}
    if by_name["TASK_REVIEW"].get("source") != "runtime":
        return False
    return by_name["PRISM_FINAL"].get("source") == "prism"


def _boundary_ok(bundle: dict) -> bool:
    boundary = bundle.get("boundary")
    if not isinstance(boundary, dict):
        return False
    if boundary.get("stage") != "M-IMPL":
        return False
    if boundary.get("terminal_state") != "boundary":
        return False
    exited = boundary.get("stage_exited_seq")
    completed = boundary.get("run_completed_seq")
    if not isinstance(exited, int) or not isinstance(completed, int):
        return False
    return exited < completed


def _journey_ok(bundle: dict, git_facts: dict, bounds: tuple[int, int]) -> bool:
    if bundle.get("status") != "satisfied":
        return False
    if not _required_phases_ok(bundle):
        return False
    first_seq, last_seq = bounds
    if first_seq > last_seq:
        return False
    if any(seq < first_seq or seq > last_seq for seq in _collect_seqs(bundle)):
        return False
    if not _lineage_ok(bundle, git_facts):
        return False
    if not _gates_ok(bundle):
        return False
    return _boundary_ok(bundle)


def _not_satisfied(
    *,
    branch: str,
    current_head: str | None,
    reason: ReleaseEvidenceReason,
    path: str | None = None,
    run_id: str | None = None,
    backend: str | None = None,
    event_bounds: tuple[int, int] | None = None,
) -> ReleaseEvidenceReport:
    return ReleaseEvidenceReport(
        status="not_satisfied",
        reason_code=reason,
        candidate_sha=current_head,
        run_id=run_id,
        backend=backend,
        evidence_path=path,
        event_bounds=event_bounds,
        branch=branch,
    )


def check_release_evidence(
    *,
    current_head: str,
    branch: str,
    evidence_candidates: list[tuple[str, bytes]],
    blobs: dict[str, bytes],
    git_facts: dict,
) -> ReleaseEvidenceReport:
    """Deterministically evaluate IF-RELEASE-001 from supplied observable facts."""
    current, has_other = _select_current(current_head, evidence_candidates)
    if not current:
        return _not_satisfied(
            branch=branch,
            current_head=current_head,
            reason="stale" if has_other else "missing",
        )
    path, run_id, _sha, raw = max(current, key=lambda item: (item[1], item[0]))
    bundle = _load_bundle(raw)
    if bundle is None or bundle.get("schema_version") != SCHEMA_VERSION:
        return _not_satisfied(
            branch=branch, current_head=current_head, reason="malformed",
            path=path, run_id=run_id,
        )
    if bundle.get("candidate_sha") != current_head or bundle.get("run_id") != run_id:
        return _not_satisfied(
            branch=branch, current_head=current_head, reason="malformed",
            path=path, run_id=run_id,
        )
    backend = _backend_of(bundle)
    if not _provenance_ok(bundle):
        return _not_satisfied(
            branch=branch, current_head=current_head, reason="not_real",
            path=path, run_id=run_id, backend=backend,
        )
    if not _audit_ok(bundle, blobs):
        return _not_satisfied(
            branch=branch, current_head=current_head, reason="audit_incomplete",
            path=path, run_id=run_id, backend=backend,
        )
    event_bounds = _event_bounds(bundle)
    if event_bounds is None or not _journey_ok(bundle, git_facts, event_bounds):
        return _not_satisfied(
            branch=branch, current_head=current_head, reason="journey_incomplete",
            path=path, run_id=run_id, backend=backend, event_bounds=event_bounds,
        )
    return ReleaseEvidenceReport(
        status="satisfied",
        reason_code="ok",
        candidate_sha=current_head,
        run_id=run_id,
        backend="opencode",
        evidence_path=path,
        event_bounds=event_bounds,
        branch=branch,
    )


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _run_dirs(root: Path) -> list[Path]:
    run_dirs: list[Path] = []
    if not root.is_dir():
        return run_dirs
    for sha_dir in sorted(root.iterdir(), key=lambda entry: entry.name):
        if not sha_dir.is_dir():
            continue
        for run_dir in sorted(sha_dir.iterdir(), key=lambda entry: entry.name):
            if run_dir.is_dir():
                run_dirs.append(run_dir)
    return run_dirs


def _enumerate_evidence(repo: Path) -> list[tuple[str, bytes]]:
    candidates: list[tuple[str, bytes]] = []
    for run_dir in _run_dirs(repo / EVIDENCE_ROOT):
        evidence = run_dir / "evidence.json"
        if not evidence.is_file():
            continue
        try:
            data = evidence.read_bytes()
        except OSError:
            data = b""
        candidates.append((evidence.relative_to(repo).as_posix(), data))
    return candidates


def _blob_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for run_dir in _run_dirs(root):
        blob_dir = run_dir / "blobs"
        if blob_dir.is_dir():
            files.extend(sorted(blob_dir.iterdir(), key=lambda entry: entry.name))
    return files


def _enumerate_blobs(repo: Path) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    for blob_file in _blob_files(repo / EVIDENCE_ROOT):
        if not blob_file.is_file():
            continue
        try:
            data = blob_file.read_bytes()
        except OSError:
            continue
        blobs[blob_file.relative_to(repo).as_posix()] = data
    return blobs


def _parse_trailers(message: str | None) -> dict[str, str]:
    trailers: dict[str, str] = {}
    if not message:
        return trailers
    for line in message.splitlines():
        match = _TRAILER_LINE.match(line)
        if match:
            trailers[match.group(1)] = match.group(2)
    return trailers


def _bundle_lineage(bundle: object) -> dict | None:
    if not isinstance(bundle, dict):
        return None
    lineage = bundle.get("rgr_lineage")
    return lineage if isinstance(lineage, dict) else None


def _git_facts(
    repo: Path, current_head: str | None, candidates: list[tuple[str, bytes]]
) -> dict:
    facts = {"r_ref_sha": None, "g_parent": None, "g_trailers": {}}
    if current_head is None:
        return facts
    current = []
    for path, data in candidates:
        parsed = _parse_evidence_path(path)
        if parsed is not None and parsed[0] == current_head:
            current.append((path, parsed[1], data))
    if not current:
        return facts
    _path, _run_id, raw = max(current, key=lambda item: (item[1], item[0]))
    lineage = _bundle_lineage(_load_bundle(raw))
    if lineage is None:
        return facts
    red_ref = lineage.get("red_ref")
    g_sha = lineage.get("g_sha")
    if isinstance(red_ref, str) and red_ref:
        facts["r_ref_sha"] = _git(repo, "rev-parse", red_ref)
    if isinstance(g_sha, str) and g_sha:
        facts["g_parent"] = _git(repo, "rev-parse", f"{g_sha}^")
        facts["g_trailers"] = _parse_trailers(_git(repo, "log", "-1", "--format=%B", g_sha))
    return facts


def check_release_evidence_file(repo: str) -> ReleaseEvidenceReport:
    """Read current Git/evidence facts and evaluate IF-RELEASE-001 without writes."""
    repo_path = Path(repo)
    candidates = _enumerate_evidence(repo_path)
    blobs = _enumerate_blobs(repo_path)
    current_head = _git(repo_path, "rev-parse", "HEAD")
    branch = _git(repo_path, "branch", "--show-current") or "(detached)"
    git_facts = _git_facts(repo_path, current_head, candidates)
    return check_release_evidence(
        current_head=current_head,
        branch=branch,
        evidence_candidates=candidates,
        blobs=blobs,
        git_facts=git_facts,
    )
