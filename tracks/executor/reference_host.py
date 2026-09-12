"""Python reference host provisioning (FR-0282, IF-REFERENCE-001).

Data-driven provisioning from wheel assets: the Python specifics live in
``tracks/assets/reference_host/**`` (declared template data), never in this
module's logic. The provisioned host binds a dedicated real GitHub remote
(Maestro ruling T-003 A) and walks the same release pipeline as the tracks
host itself; missing credentials or remote leave it needs_attention, never a
local-success downgrade.

Unit-level contract (this module is the NFR-0147-allowed Python isolation
zone): ``create_reference_host`` materializes the closed deployment set
(interfaces §1n) under an isolated target root: a fresh ``python -m venv``,
a real non-editable ``pip install --no-deps`` of the built wheel, ``trac
init`` through the installed interpreter (the install-usability witness) and
the reachability probe of the bound remote. A missing wheel, a failed
install or a failed init return a structured failure report -- never a
fabricated ``status="ok"``. ``verify_reference_equivalence`` performs
same-shape acceptance (interfaces §1n/§2b): a report is accepted only when
its journey event chain matches the tracks release shape (repeated event
kinds from re-runs/repair rounds are tolerated; the chain is matched as an
ordered subsequence of the report's event kinds).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Closed deployment set (interfaces §1n 部署落点封闭集): template asset
# name -> destination relative to the materialized host root.
_DEPLOY_RULES = {
    "pyproject.toml": "pyproject.toml",
    "flake8.ini": "flake8.ini",
    "host_calc.py": "host_calc.py",
    "tracks-project.toml": ".tracks/projects/project.toml",
    "architecture.md": ".tracks/projects/v0.1/architecture.md",
    "ci.yml": ".github/workflows/ci.yml",
}

# Same-shape journey contract (interfaces §2b): the ordered event-kind
# chain a tracks release run produces (kernel/release.py producer chain +
# the M-MILESTONE closing tail).
_JOURNEY_CHAIN = (
    "candidate.frozen",
    "local_gate.passed",
    "ci.run_observed",
    "prism.verdict",
    "security.assessed",
    "release.previewed",
    "release.decided",
    "publish.planned",
    "publish.executed",
    "milestone.trace_closed",
    "milestone.sealed",
    "refs.cleaned",
    "run.completed",
)
_CHAIN_SET = frozenset(_JOURNEY_CHAIN)
_SEAL_KIND = "milestone.sealed"
# The seal link and everything after it: a chain missing any of these
# cannot confirm the milestone seal closed out with a completed run.
_POST_SEAL_CHAIN = _JOURNEY_CHAIN[_JOURNEY_CHAIN.index(_SEAL_KIND):]
_REMOTE_PROBE_TIMEOUT_SECONDS = 30


def create_reference_host(
    template_dir: Path, target_dir: Path, wheel: Path, remote_url: str | None
) -> dict:
    """Fresh venv + non-editable wheel + asset deployment + remote binding.

    Missing remote binding (``remote_url`` falsy, no explicit simulation
    declaration) reports needs_attention without materializing anything —
    never a local-success downgrade (NFR-0149-01).
    """
    if not remote_url:
        return {
            "status": "needs_attention",
            "reason": (
                "missing remote credential binding: remote_url is missing; "
                "an explicit simulation declaration is required for "
                "local-only runs"
            ),
        }
    return _materialize(template_dir, target_dir, wheel, remote_url)


def verify_reference_equivalence(report: dict) -> tuple[bool, tuple[str, ...]]:
    """Same-shape acceptance (interfaces §1n/§2b).

    Accepts ``(True, ())`` only when the report's journey event chain
    carries the tracks release shape: the canonical chain must appear in
    order, while repeated (re-run/repair-round) kinds of a chain event are
    tolerated. Any divergence is rejected with identifiable reasons.
    """
    events = report.get("events") if isinstance(report, dict) else None
    if not isinstance(events, list):
        return False, ("report carries no journey event chain",)
    kinds = _event_kinds(events)
    if _chain_matches(kinds):
        return True, ()
    return False, _divergence_reasons(kinds)


def _materialize(
    template_dir: Path, target_dir: Path, wheel: Path, remote_url: str
) -> dict:
    """Real materialization: fresh venv, wheel install, closed set, init.

    Every step's outcome is a witness: a non-zero return code yields a
    structured failure report (no simulated success), and the remote must
    answer ``git ls-remote`` before anything is materialized.
    """
    if not remote_url:
        return _failure("remote_missing")
    template_dir = Path(template_dir)
    target_dir = Path(target_dir)
    wheel = Path(wheel)
    if not wheel.is_file():
        return _failure("wheel_missing", wheel=str(wheel))
    reachable, detail = _remote_reachable(remote_url)
    if not reachable:
        return {
            "status": "needs_attention",
            "reason": f"remote_unavailable: {detail}",
            "remote": remote_url,
        }
    target_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = target_dir / ".venv"
    created = _run([sys.executable, "-I", "-m", "venv", str(venv_dir)], target_dir)
    if created.returncode != 0:
        return _failure(
            "venv_create_failed", venv=str(venv_dir), detail=_tail(created)
        )
    python = _venv_python(venv_dir)
    installed = _run(
        [
            str(python), "-I", "-m", "pip", "install", "--no-deps", "--no-index",
            "--disable-pip-version-check", str(wheel.resolve()),
        ],
        target_dir,
    )
    if installed.returncode != 0:
        return _failure(
            "wheel_install_failed",
            venv=str(venv_dir),
            wheel=str(wheel),
            detail=_tail(installed),
        )
    deployed = _deploy_closed_set(template_dir, target_dir)
    initialized = _run(
        [str(python), "-I", "-m", "tracks.cli.main", "init"], target_dir
    )
    if initialized.returncode != 0:
        return _failure(
            "init_failed",
            venv=str(venv_dir),
            wheel=str(wheel),
            deployed=deployed,
            remote=remote_url,
            detail=_tail(initialized),
        )
    return {
        "status": "ok",
        "venv": str(venv_dir),
        "install": "non-editable",
        "wheel": str(wheel),
        "deployed": deployed,
        "remote": remote_url,
        "init": {
            "python": str(python),
            "returncode": 0,
            "stdout": (initialized.stdout or "").strip()[:500],
        },
    }


def _failure(reason: str, **detail) -> dict:
    """Structured failure report: the step did not happen, no fake success."""
    report = {"status": "failed", "reason": reason}
    report.update(detail)
    return report


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _tail(proc: subprocess.CompletedProcess, limit: int = 800) -> str:
    combined = (proc.stdout or "") + (proc.stderr or "")
    return combined.strip()[-limit:]


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _remote_reachable(remote_url: str) -> tuple[bool, str]:
    """Probe the bound remote with ``git ls-remote`` (local bare or real).

    No implicit downgrade: an unreachable remote reports needs_attention
    with the probe's own diagnostic instead of a pretended binding.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-remote", str(remote_url)],
            capture_output=True,
            text=True,
            timeout=_REMOTE_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"git ls-remote timed out after {_REMOTE_PROBE_TIMEOUT_SECONDS}s"
    if proc.returncode == 0:
        return True, ""
    return False, _tail(proc, limit=400)


def _deploy_closed_set(template_dir: Path, target_dir: Path) -> dict:
    """Copy each closed-set asset to its contracted destination."""
    deployed = {}
    for asset_name, destination in _DEPLOY_RULES.items():
        source = template_dir / asset_name
        if not source.is_file():
            raise FileNotFoundError(
                f"closed deployment set source missing: {asset_name} "
                f"(template dir: {template_dir})"
            )
        dest = target_dir / destination
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        deployed[asset_name] = str(dest)
    return deployed


def _event_kinds(events: list) -> list:
    """Extract ordered kinds; malformed entries become None sentinels."""
    kinds = []
    for event in events:
        if isinstance(event, dict) and isinstance(event.get("kind"), str):
            kinds.append(event["kind"])
        else:
            kinds.append(None)
    return kinds


def _chain_matches(kinds: list) -> bool:
    """The canonical chain as an ordered subsequence of the event kinds.

    Repeated kinds (``prism.verdict`` review rounds, re-run gates after an
    in-place repair) are consumed by the walk without breaking the shape;
    unknown kinds and malformed entries do.
    """
    if any(kind is None or kind not in _CHAIN_SET for kind in kinds):
        return False
    cursor = iter(kinds)
    return all(any(kind == want for kind in cursor) for want in _JOURNEY_CHAIN)


def _divergence_reasons(kinds: list) -> tuple[str, ...]:
    """Identify why an event chain diverges from the tracks release shape."""
    reasons = []
    if None in kinds:
        reasons.append("journey chain contains malformed event entries")
    reasons.extend(_unexpected_chain_reasons(kinds))
    reasons.extend(_missing_chain_reasons(kinds))
    if any(kind not in kinds for kind in _POST_SEAL_CHAIN):
        reasons.append(f"{_SEAL_KIND} seal unconfirmed: run completion missing")
    return tuple(reasons)


def _unexpected_chain_reasons(kinds: list) -> list:
    unexpected = []
    for kind in kinds:
        if kind is not None and kind not in _CHAIN_SET and kind not in unexpected:
            unexpected.append(kind)
    return [f"journey chain has unexpected event {kind}" for kind in unexpected]


def _missing_chain_reasons(kinds: list) -> list:
    """Greedy subsequence walk: missing links vs links found out of order."""
    reasons = []
    cursor = 0
    previous = None
    for want in _JOURNEY_CHAIN:
        found = False
        while cursor < len(kinds):
            kind = kinds[cursor]
            cursor += 1
            if kind == want:
                found = True
                break
        if found:
            previous = want
        elif want in kinds:
            reasons.append(
                f"journey chain order diverges at {want}: "
                f"found before {previous or 'its predecessor'}"
            )
        else:
            reasons.append(f"journey chain missing {want}")
    return reasons
