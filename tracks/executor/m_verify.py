"""M-VERIFY executor domain (FR-0267–FR-0271): candidate freeze on a clean
tree, FULL_F reuse judgment, host-contract local gates, GitHub required-CI
API readback and the Prism same-candidate final-review dispatch payload.

Side-effect boundary facts (git, subprocess, network) are gathered here and
emitted as events; binding verification itself is pure over the event stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ReuseDecisionReason = Literal["reuse_full_f", "drift", "stale", "identity_mismatch"]


@dataclass(frozen=True)
class CandidateIdentity:
    candidate_sha: str
    clean_tree: bool
    branch: str


@dataclass(frozen=True)
class ReuseDecision:
    decision: Literal["reuse", "rerun"]
    reason: ReuseDecisionReason
    identity_basis: tuple[str, ...]


def freeze_candidate(repo: Path) -> CandidateIdentity:
    """Clean-tree check plus full HEAD SHA (Maestro ruling T-002 B)."""
    raise NotImplementedError("IF-VERIFY-001")


def judge_full_f_reuse(
    candidate_sha: str,
    full_f_evidence: dict,
    identity_quadruple: dict,
    stale_marks: tuple[str, ...],
) -> ReuseDecision:
    """Reuse only when candidate is undrifted, identity matches and no STALE."""
    raise NotImplementedError("IF-VERIFY-002")


def collect_binding_violations(events: list[dict], candidate_sha: str) -> list[str]:
    """Pure scan: any release-chain evidence not bound to candidate_sha."""
    raise NotImplementedError("IF-VERIFY-001")


def build_prism_final_review_assignment(
    candidate_sha: str,
    evidence_digests: dict,
) -> dict:
    """Assemble the same-candidate consistency-review dispatch envelope."""
    raise NotImplementedError("IF-VERIFY-005")
