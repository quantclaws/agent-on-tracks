"""Environment readiness probes (IF-PROJ-001).

Four closed probes per FR-0290: contract (project.toml parse + required
sections), harness_model (configured agent backend executable present),
credentials_ref (required credential env NAMES configured — values are never
read into the UI), tools (required executables on PATH). Each probe reports
{ok, reason}; a failed probe blocks run creation with the concrete reason
(no fake runs). The existing configured environment is used as-is — no
package-manager GUI this version.

Contract token: IF-PROJ-001.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import tomllib

PROBE_KINDS = ("contract", "harness_model", "credentials_ref", "tools")

CONTRACT_RELPATH = Path(".tracks") / "projects" / "project.toml"
REQUIRED_CONTRACT_SECTIONS = ("unit", "integration")
BACKEND_ENV_VAR = "TRAC_AGENT_BACKEND"
DEFAULT_BACKEND = "opencode"
FAKE_BACKEND = "fake"
REQUIRED_CREDENTIAL_REFS = ("GITHUB_TOKEN",)
REQUIRED_TOOLS = ("git", "python3")


@dataclass(frozen=True)
class ProbeResult:
    check: str  # one of PROBE_KINDS
    ok: bool
    reason: str | None


def _contract_probe(repo: Path) -> ProbeResult:
    path = repo / CONTRACT_RELPATH
    if not path.is_file():
        return ProbeResult("contract", False, f"project contract not found: {CONTRACT_RELPATH}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, ValueError):
        return ProbeResult("contract", False, f"project contract unparsable: {CONTRACT_RELPATH}")
    missing = [s for s in REQUIRED_CONTRACT_SECTIONS if not isinstance(data.get(s), dict)]
    if missing:
        return ProbeResult("contract", False, f"project contract missing: {', '.join(missing)}")
    return ProbeResult("contract", True, None)


def _harness_probe() -> ProbeResult:
    backend = os.environ.get(BACKEND_ENV_VAR, DEFAULT_BACKEND).strip().lower()
    if not backend:
        backend = DEFAULT_BACKEND
    if backend == FAKE_BACKEND:
        return ProbeResult("harness_model", True, None)
    if shutil.which(backend) is None:
        return ProbeResult("harness_model", False, f"agent backend not found: {backend}")
    return ProbeResult("harness_model", True, None)


def _credentials_probe() -> ProbeResult:
    missing = [name for name in REQUIRED_CREDENTIAL_REFS if not os.environ.get(name)]
    if missing:
        return ProbeResult("credentials_ref", False, f"missing credential: {', '.join(missing)}")
    return ProbeResult("credentials_ref", True, None)


def _tools_probe() -> ProbeResult:
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        return ProbeResult("tools", False, f"missing tools: {', '.join(missing)}")
    return ProbeResult("tools", True, None)


def run_readiness(repo: Path) -> list:
    """Run all four probes against the registered repo; emit the
    project.readiness_checked event through the command service caller."""
    root = Path(repo)
    if not root.is_dir():
        return [
            ProbeResult("contract", False, f"repo not found: {root}"),
            _harness_probe(),
            _credentials_probe(),
            _tools_probe(),
        ]
    return [_contract_probe(root), _harness_probe(), _credentials_probe(), _tools_probe()]


def summarize_readiness(results: list) -> dict:
    """Aggregate probe results into the readiness summary.

    Returns {"ok": bool, "checks": {kind: {"ok": bool, "reason"}}} matching
    the project.readiness_checked payload body (interfaces §1a#5). A check
    is ok only when every report for it is ok; the first failing reason is
    kept.
    """
    grouped: dict = {kind: [] for kind in PROBE_KINDS}
    for probe in results or []:
        if probe.check in grouped:
            grouped[probe.check].append(probe)
    checks = {}
    for kind in PROBE_KINDS:
        failing = [entry for entry in grouped[kind] if not entry.ok]
        if failing:
            checks[kind] = {"ok": False, "reason": failing[0].reason}
        else:
            checks[kind] = {"ok": True, "reason": None}
    return {"ok": all(pair["ok"] for pair in checks.values()), "checks": checks}


def blocking_reason(summary: dict) -> str | None:
    """Return None when the summary is ready, else the blocking message.

    The message names every failing closed probe kind with its concrete
    reason so create_run can refuse with a reason (AC-FR0290-02) instead
    of creating a fake run.
    """
    checks = (summary or {}).get("checks") or {}
    failing = []
    for kind in PROBE_KINDS:
        pair = checks.get(kind) or {}
        if not pair.get("ok", True):
            detail = pair.get("reason") or "not ready"
            failing.append(f"{kind}: {detail}")
    if not failing:
        return None
    return "readiness not ready — " + "; ".join(failing)
