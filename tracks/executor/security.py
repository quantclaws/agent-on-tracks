"""M-SECURITY executor domain (FR-0272, IF-SECURITY-001).

Security scans and the deep-audit review run strictly per the versioned host
contract / policy; tool versions and thresholds are pinned by contract. Any
unknown, missing or malformed result is fail-closed — the Runtime never
infers or skips a policy item.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

from .host_contract import (
    HostContract,
    LocalGateDecl,
    NormalizedGateResult,
    SecurityScanDecl,
    execute_gate,
)


def security_policy_digest(contract: HostContract) -> str:
    """Return the canonical digest of the contract's declared security scans.

    The digest deliberately covers only the ordered scan declarations.  This
    keeps the policy identity stable when unrelated host-contract fields
    change, while making every security-policy input field identity-bearing.
    """
    scans = [
        {
            "id": scan.scan_id,
            "tool": scan.tool,
            "tool_version": scan.tool_version,
            "install": scan.install,
            "command": scan.command,
            "result_channel": scan.result_channel,
            "threshold": scan.threshold,
            "timeout_seconds": scan.timeout_seconds,
        }
        for scan in contract.security_scans
    ]
    canonical = json.dumps(scans, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def security_scan_evidence_complete(contract: HostContract, entries) -> bool:
    """Validate a complete, passed evidence set for every declared scan."""
    try:
        declarations = tuple(contract.security_scans)
    except (AttributeError, TypeError):
        return False
    if not declarations:
        return False

    declared_ids: list[str] = []
    for declaration in declarations:
        scan_id = getattr(declaration, "scan_id", None)
        if not isinstance(scan_id, str) or not scan_id.strip() or scan_id in declared_ids:
            return False
        declared_ids.append(scan_id)
    if not isinstance(entries, list) or len(entries) != len(declared_ids):
        return False

    ids = [_passed_scan_entry_id(entry) for entry in entries]
    return None not in ids and len(set(ids)) == len(ids) and set(ids) == set(declared_ids)


def _passed_scan_entry_id(entry) -> str | None:
    """Normalize the two event spellings without accepting malformed results."""
    if not isinstance(entry, dict):
        return None
    scan_id = entry.get("id", entry.get("scan_id"))
    if "scan_id" in entry and entry["scan_id"] != scan_id:
        return None
    exit_code = entry.get("exit_code")
    valid = (
        isinstance(scan_id, str) and bool(scan_id.strip())
        and entry.get("status") == "passed"
        and type(entry.get("result_version")) is int and entry["result_version"] == 1
        and isinstance(entry.get("summary"), dict)
        and (exit_code is None or (type(exit_code) is int and exit_code == 0))
    )
    return scan_id if valid else None


def _run_command(command: str, repo, timeout_seconds: int) -> tuple[int | None, dict]:
    """Execute a declared scan command (shell=False, contract timeout)."""
    if not command or not command.strip():
        # Fail closed: an empty/blank declared command is malformed contract
        # input, never an implicit pass (subprocess would raise on empty argv).
        return None, {"stdout_tail": "", "stderr_tail": "malformed: empty command"}
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
    except (OSError, ValueError) as err:
        return None, {"stdout_tail": "", "stderr_tail": f"command unavailable: {err}"}
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
        if decl.install and decl.install.strip():
            exit_code, summary = _run_command(decl.install, repo, decl.timeout_seconds)
            if exit_code != 0:
                results.append(_scan_result(decl, exit_code, {**summary, "phase": "install"}))
                continue
        results.append(_execute_scan(decl, repo))
    return results


def _execute_scan(decl: SecurityScanDecl, repo) -> NormalizedGateResult:
    """Consume the declared result channel using the shared normalized protocol."""
    if not decl.command.strip() or decl.result_channel not in ("exit_code", "file"):
        return _scan_result(decl, None, {"error": "malformed scan declaration"})
    gate = LocalGateDecl(
        kind="quality", source="command", command=decl.command, categories=(),
        result_channel=decl.result_channel, timeout_seconds=decl.timeout_seconds,
    )
    try:
        result = execute_gate(gate, Path(repo), {})
    except (OSError, ValueError) as err:
        return _scan_result(decl, None, {"error": str(err)})
    return replace(result, gate_id=decl.scan_id)


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


def security_assessed_payload(
    candidate_sha: str,
    policy_digest: str,
    results,
    *,
    entry_key: str = "scans",
    id_key: str = "id",
) -> dict:
    """Emit-ready ``security.assessed`` payload (fail-closed aggregate).

    Aggregates the scan results and attaches the cve repair route whenever
    the aggregate is not a pass. ``entry_key``/``id_key`` keep the two
    historical payload spellings (``scans``/``id`` vs ``findings``/
    ``scan_id``) without duplicating the construction."""
    status = aggregate_security_status(results)
    payload = {
        "status": status,
        "candidate_sha": candidate_sha,
        "policy_digest": policy_digest,
        entry_key: [
            {
                id_key: r.gate_id, "status": r.status, "exit_code": r.exit_code,
                "result_version": r.result_version, "summary": dict(r.summary),
                "command_echo": list(r.command_echo),
            }
            for r in results
        ],
        "repair_route": "none",
    }
    if status != "passed":
        payload["repair_route"] = cve_repair_route()
    return payload


def cve_repair_route() -> dict:
    """Unified §1.0.14 B repair route for a non-passing security assessment:
    a cve-class finding is an Archer advisory (in-place, never a rollback)."""
    return {
        "exit_class": "defect_repair",
        "defect_class": "cve",
        "owner": "Archer",
        "discipline": "cve_advisory",
        "budget_remaining": None,
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
