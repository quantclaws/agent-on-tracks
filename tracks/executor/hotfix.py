"""Hotfix entry side effects: precheck, anchor validation, entry completion.

v0.6 executor-side module (IF-HOTFIX-003/004/005, ARCH-006 §1.0.1/§3.2-§3.4).
Executed by the Executor handlers ``_do_precheck_hotfix`` /
``_do_validate_anchor`` / ``_do_complete_hotfix_entry`` for the commands
produced by ``tracks/kernel/hotfix.py``. All I/O lives here (issue fetch via
``tracks/effects/github.py``, branch probing, acceptance.md scanning, git
branch creation); the kernel stays pure (NFR-0010).

M-DESIGN scaffold stub (ARCH-006 §2): signatures are frozen by
interfaces.md §1c/§1d; Devon fills the bodies without changing the declared
contracts. Each raise carries the owning IF- token so Shield's contract
tests can bind the RED to a specific interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# interfaces.md §1c — closed rejection-reason set for triage.prechecked.
HotfixRejectionReason = Literal[
    "not_bug",
    "issue_not_found",
    "issue_fetch_failed",
    "scenario_invalid",
    "no_active_release_branch",
    "baseline_not_locatable",
    "fix_branch_exists",
]


@dataclass(frozen=True)
class HostIssue:
    """Deterministic issue snapshot from the read channel (IF-HOTFIX-003)."""

    number: int
    title: str
    body: str
    labels: tuple[str, ...]

    @property
    def is_bug(self) -> bool:
        """True iff labels contain ``bug`` (host repo convention, §3.1)."""
        raise NotImplementedError("IF-HOTFIX-003: HostIssue.is_bug")


@dataclass(frozen=True)
class PrecheckReport:
    """Outcome of the deterministic PRECHECK rules P-1..P-5 (IF-HOTFIX-003)."""

    status: Literal["pass", "rejected"]
    reason: HotfixRejectionReason | None
    next: str | None
    target_version: str | None
    active_branch: str | None


def precheck_hotfix(
    issue: HostIssue | None,
    scenario: str,
    repo_branches: list[str],
    active_run_branch: str | None,
    projects_dir: Path,
    approved_versions: set[str],
) -> PrecheckReport:
    """IF-HOTFIX-003 deterministic precheck (pure function, no LLM).

    Rules (ARCH-006 §3.2): issue type=bug (labels); scenario validity; dev
    scenario requires an active release branch (active run branch or HEAD,
    ``releases/*``); target version locatable (dev = branch version,
    post-release = highest approved version, numeric tuple compare);
    ``fix/{issue}`` not already present. ``issue=None`` (fetch failed)
    maps to issue_not_found / issue_fetch_failed. Rejection reasons come
    from the closed set :data:`HotfixRejectionReason`; each rejected report
    carries a human-readable ``next`` (retry after completing the issue, or
    use ``trac start``).
    """
    raise NotImplementedError("IF-HOTFIX-003: precheck_hotfix")


def locate_target_version(
    scenario: str,
    active_branch: str | None,
    projects_dir: Path,
    approved_versions: set[str],
) -> str | None:
    """IF-HOTFIX-005 target-version location (pure function).

    dev: ``releases/vX.Y`` -> ``vX.Y`` (must be approved); post-release:
    numeric-tuple maximum among approved versions with the triple present.
    Unlocatable -> None (PRECHECK maps to baseline_not_locatable, keeping
    the retry-after-completion path of FR-0240).
    """
    raise NotImplementedError("IF-HOTFIX-005: locate_target_version")


def parse_issue_hints(body: str) -> dict:
    """IF-HOTFIX-003 optional bug-template field parsing (pure function).

    Deterministic regex over the issue body's optional 「版本 / 对应 FR/NFR」
    fields. Returns ``{"version": str | None, "fr_nfr": list[str]}``;
    missing fields yield empty values — PRECHECK never fails on them
    (hints only feed the Sage anchoring corpus).
    """
    raise NotImplementedError("IF-HOTFIX-003: parse_issue_hints")


def parse_anchor_refs(acs: list[str]) -> list[tuple[str, str]]:
    """IF-HOTFIX-004 anchor-reference parsing (pure function).

    ``"AC-FR0030-01@v0.5"`` -> ``("AC-FR0030-01", "v0.5")`` (grammar:
    interfaces.md §1e). Malformed references (missing @version, bad id
    shape) raise ``ValueError("IF-HOTFIX-004:<detail>")``.
    """
    raise NotImplementedError("IF-HOTFIX-004: parse_anchor_refs")


def validate_anchor_refs(
    refs: list[tuple[str, str]],
    projects_dir: Path,
) -> tuple[bool, list[str]]:
    """IF-HOTFIX-004 anchor program validation (pure + file read-only).

    Every ``(ac_id, version)`` must resolve to an existing
    ``projects_dir/<version>/acceptance.md`` containing a
    ``### <ac_id>`` heading. Returns ``(all_exist, missing)``; ``missing``
    entries are human-locatable strings. Any miss -> verdict.failed
    (check=anchor_invalid) -> Sage redispatch (<=3, SM-01.6).
    """
    raise NotImplementedError("IF-HOTFIX-004: validate_anchor_refs")


def complete_hotfix_entry(
    repo: Path,
    run_id: str,
    issue: int,
    scenario: str,
    target_version: str,
    anchor_acs: list[str],
) -> dict:
    """IF-HOTFIX-005 ANCHORED atomic entry completion (side effects).

    create_branch(``fix/{issue}``, base = main HEAD for post-release /
    active release branch HEAD for dev) + checkout + ``baseline.inherited``
    (source approval: digest + read-only baseline doc paths, never a copy,
    never re-approval) + ``stage.entered(M-DESIGN)`` (SM-01.10). Fails
    closed: branch-creation failure completes the run as rejected without
    a half-built run. Idempotency markers = branch.created(fix/N) +
    baseline.inherited events (per-kind reconcile, D-13).
    """
    raise NotImplementedError("IF-HOTFIX-005: complete_hotfix_entry")
