"""Python reference host provisioning (FR-0282, IF-REFERENCE-001).

Data-driven provisioning from wheel assets: the Python specifics live in
``tracks/assets/reference_host/**`` (declared template data), never in this
module's logic. The provisioned host binds a dedicated real GitHub remote
(Maestro ruling T-003 A) and walks the same release pipeline as the tracks
host itself; missing credentials or remote leave it needs_attention, never a
local-success downgrade.

Unit-level contract (this module is the NFR-0147-allowed Python isolation
zone): ``create_reference_host`` materializes the closed deployment set
(interfaces §1n) under an isolated target root and records the non-editable
wheel install plan in its report; the real venv/pip execution is the
deferred run surface (T-042 wiring). ``verify_reference_equivalence``
performs same-shape acceptance (interfaces §1n/§2b): a report is accepted
only when its journey event chain matches the tracks release shape.
"""

from __future__ import annotations

import shutil
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
# chain a tracks release run produces (kernel/release.py producer chain).
_JOURNEY_CHAIN = (
    "candidate.frozen",
    "local_gate.passed",
    "ci.run_observed",
    "prism.verdict",
    "security.assessed",
    "awaiting_release",
    "publish.executed",
    "milestone.sealed",
    "run.completed",
)
_SEAL_KIND = "milestone.sealed"
# The seal link and everything after it: a chain missing any of these
# cannot confirm the milestone seal closed out with a completed run.
_POST_SEAL_CHAIN = _JOURNEY_CHAIN[_JOURNEY_CHAIN.index(_SEAL_KIND):]


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
    matches the tracks release shape exactly; any divergence is rejected
    with identifiable reasons.
    """
    events = report.get("events") if isinstance(report, dict) else None
    if not isinstance(events, list):
        return False, ("report carries no journey event chain",)
    kinds = _event_kinds(events)
    if kinds == list(_JOURNEY_CHAIN):
        return True, ()
    return False, _divergence_reasons(kinds)


def _materialize(
    template_dir: Path, target_dir: Path, wheel: Path, remote_url: str
) -> dict:
    """Deploy the closed set into an isolated env root bound to the remote."""
    template_dir = Path(template_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = target_dir / ".venv"
    venv_dir.mkdir(parents=True, exist_ok=True)
    return {
        "status": "ok",
        "venv": str(venv_dir),
        "install": "non-editable",
        "wheel": str(wheel),
        "deployed": _deploy_closed_set(template_dir, target_dir),
        "remote": remote_url,
    }


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


def _divergence_reasons(kinds: list) -> tuple[str, ...]:
    """Identify why an event chain diverges from the tracks release shape."""
    reasons = []
    seen = set(kinds)
    chain_set = set(_JOURNEY_CHAIN)
    reasons.extend(_missing_chain_reasons(seen))
    reasons.extend(_unexpected_chain_reasons(kinds, chain_set))
    reasons.extend(_misordered_reasons(kinds, seen, bool(reasons)))
    if any(kind not in seen for kind in _POST_SEAL_CHAIN):
        reasons.append(f"{_SEAL_KIND} seal unconfirmed: run completion missing")
    return tuple(reasons)


def _missing_chain_reasons(seen: set) -> list:
    return [
        f"journey chain missing {kind}"
        for kind in _JOURNEY_CHAIN
        if kind not in seen
    ]


def _unexpected_chain_reasons(kinds: list, chain_set: set) -> list:
    unexpected = []
    for kind in kinds:
        if kind is not None and kind not in chain_set and kind not in unexpected:
            unexpected.append(kind)
    return [f"journey chain has unexpected event {kind}" for kind in unexpected]


def _misordered_reasons(kinds: list, seen: set, diverged_before: bool) -> list:
    if None in seen:
        return ["journey chain contains malformed event entries"]
    if diverged_before or len(kinds) != len(_JOURNEY_CHAIN):
        return []
    return [
        f"journey chain order diverges at position {index}: "
        f"expected {want}, got {got}"
        for index, (got, want) in enumerate(
            zip(kinds, _JOURNEY_CHAIN, strict=False)
        )
        if got != want
    ]
