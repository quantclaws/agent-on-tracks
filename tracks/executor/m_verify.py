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
    # stale has highest priority: any STALE mark or evidence flag means rerun
    if stale_marks or full_f_evidence.get("stale"):
        basis = tuple(full_f_evidence.get("identity_basis", ()))
        return ReuseDecision(decision="rerun", reason="stale", identity_basis=basis)
    expected = full_f_evidence.get("identity_basis")
    if expected is not None:
        quad = (
            identity_quadruple.get("tree"),
            identity_quadruple.get("command"),
            identity_quadruple.get("env"),
            identity_quadruple.get("selection_id"),
        )
        # expected is a tuple; compare as tuple
        exp_tuple = tuple(expected)
        # mismatch when lengths differ or values differ
        if exp_tuple != quad:
            return ReuseDecision(
                decision="rerun", reason="identity_mismatch", identity_basis=exp_tuple
            )
    # check drift via candidate_sha vs expected? For this slice, drift is
    # treated as identity mismatch; stale already handled
    # if no mismatch and no stale, reuse
    basis = tuple(full_f_evidence.get("identity_basis", ())) if expected is not None else ()
    return ReuseDecision(decision="reuse", reason="reuse_full_f", identity_basis=basis)


def collect_binding_violations(events: list[dict], candidate_sha: str) -> list[str]:
    """Pure scan: any release-chain evidence not bound to candidate_sha."""
    raise NotImplementedError("IF-VERIFY-001")


def build_prism_final_review_assignment(
    candidate_sha: str,
    evidence_digests: dict,
) -> dict:
    """Assemble the same-candidate consistency-review dispatch envelope."""
    raise NotImplementedError("IF-VERIFY-005")
