"""M-SECURITY executor domain (FR-0272, IF-SECURITY-001).

Security scans and the deep-audit review run strictly per the versioned host
contract / policy; tool versions and thresholds are pinned by contract. Any
unknown, missing or malformed result is fail-closed — the Runtime never
infers or skips a policy item.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Literal

from .host_contract import HostContract, NormalizedGateResult, SecurityScanDecl


def _run_command(command: str, repo, timeout_seconds: int) -> tuple[int | None, dict]:
    """Execute a declared scan command (shell=False, contract timeout)."""
    try:
        proc = subprocess.run(  # noqa: S603 (declared contract command)
            shlex.split(command),
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return proc.returncode, {
            "stdout_tail": (proc.stdout or "")[-500:],
            "stderr_tail": (proc.stderr or "")[-500:],
        }
    except FileNotFoundError as err:
        return None, {"stdout_tail": "", "stderr_tail": f"tool missing: {err}"}
    except subprocess.TimeoutExpired as err:
        return None, {"stdout_tail": "", "stderr_tail": f"timeout: {err}"}


def _scan_result(
    decl: SecurityScanDecl, exit_code: int | None, summary: dict
) -> NormalizedGateResult:
    """exit_code channel: the Runtime synthesizes the normalized result."""
    if exit_code is None:
        status: Literal["passed", "failed", "malformed"] = "malformed"
    elif exit_code == 0:
        status = "passed"
    else:
        status = "failed"
    return NormalizedGateResult(
        gate_id=decl.scan_id,
        result_version=1,
        status=status,
        exit_code=exit_code,
        summary=summary,
    )


def run_security_scans(
    contract: HostContract, repo, candidate_sha: str
) -> list[NormalizedGateResult]:
    """Install (if declared) and run every contract-declared scan.

    FR-0272-02: an undeclared/malformed contract (None or missing
    security_policy) has ZERO declared scans — it fails closed by returning
    an empty result set (aggregating to unknown → blocked) instead of
    crashing, and never infers or skips a policy item.
    """
    del candidate_sha  # binding happens on the security.assessed event
    if contract is None:
        return []
    try:
        scans = contract.security_scans or ()
    except AttributeError:
        # malformed contract object without the policy slot: fail closed
        return []
    results: list[NormalizedGateResult] = []
    for decl in scans:
        if decl.install:
            subprocess.run(  # noqa: S603 (declared contract install)
                shlex.split(decl.install),
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=decl.timeout_seconds,
                check=False,
            )
        exit_code, summary = _run_command(decl.command, repo, decl.timeout_seconds)
        results.append(_scan_result(decl, exit_code, summary))
    return results


def build_security_review_assignment(
    scan_results: list[NormalizedGateResult], policy_digest: str, candidate_sha: str
) -> dict:
    """Deep-audit review envelope for the Prism security-policy dispatch."""
    return {
        "scope": "security",
        "prism_scope": "security",
        "scan_results": list(scan_results),
        "policy_digest": policy_digest,
        "candidate_sha": candidate_sha,
    }


def aggregate_security_status(
    scan_results: list[NormalizedGateResult],
) -> Literal["passed", "failed", "unknown"]:
    """passed iff every declared scan passed; malformed/missing → unknown."""
    results = scan_results or []
    if not results:
        # nothing declared/verified can never authorize a release
        return "unknown"
    if any(r is None or getattr(r, "status", None) == "malformed" for r in results):
        return "unknown"
    if any(r.status == "failed" for r in results):
        return "failed"
    if all(r.status == "passed" for r in results):
        return "passed"
    return "unknown"
