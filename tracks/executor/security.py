"""M-SECURITY executor domain (FR-0272, IF-SECURITY-001).

Security scans and the deep-audit review run strictly per the versioned host
contract / policy; tool versions and thresholds are pinned by contract. Any
unknown, missing or malformed result is fail-closed — the Runtime never
infers or skips a policy item.
"""

from __future__ import annotations

from typing import Literal

from .host_contract import HostContract, NormalizedGateResult


def run_security_scans(
    contract: HostContract, repo, candidate_sha: str
) -> list[NormalizedGateResult]:
    """Install (if declared) and run every contract-declared scan."""
    raise NotImplementedError("IF-SECURITY-001")


def build_security_review_assignment(
    scan_results: list[NormalizedGateResult], policy_digest: str, candidate_sha: str
) -> dict:
    """Deep-audit review envelope for the Prism security-policy dispatch."""
    raise NotImplementedError("IF-SECURITY-001")


def aggregate_security_status(
    scan_results: list[NormalizedGateResult],
) -> Literal["passed", "failed", "unknown"]:
    """passed iff every declared scan passed; malformed/missing → unknown."""
    raise NotImplementedError("IF-SECURITY-001")
