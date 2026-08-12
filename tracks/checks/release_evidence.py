"""Interface stub for the current-candidate release-evidence check.

Contract: IF-RELEASE-001.  Devon replaces only the function bodies during M-IMPL.
"""

from dataclasses import dataclass
from typing import Literal

ReleaseEvidenceReason = Literal[
    "ok",
    "missing",
    "stale",
    "malformed",
    "not_real",
    "audit_incomplete",
    "journey_incomplete",
]


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


def check_release_evidence(
    *,
    current_head: str,
    branch: str,
    evidence_candidates: list[tuple[str, bytes]],
    blobs: dict[str, bytes],
    git_facts: dict,
) -> ReleaseEvidenceReport:
    """Deterministically evaluate IF-RELEASE-001 from supplied observable facts."""
    raise NotImplementedError("IF-RELEASE-001")


def check_release_evidence_file(repo: str) -> ReleaseEvidenceReport:
    """Read current Git/evidence facts and evaluate IF-RELEASE-001 without writes."""
    raise NotImplementedError("IF-RELEASE-001")
