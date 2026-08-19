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

import hashlib
import re
import subprocess
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

# interfaces.md §1e — cross-version AC reference grammar.
# ``AC-FRXXXX-YY@version`` or ``AC-NFRXXXX-YY@version``.
_AC_REF_RE = re.compile(r"^(AC-(?:N?FR)\d{4}-\d{2})@(v\d+\.\d+)$")

# ARCH-006 §3.2 P-3: ``releases/*`` branch prefix.
_RELEASE_BRANCH_PREFIX = "releases/"

# ARCH-006 §3.1: optional bug-template fields (parse_issue_hints).
_VERSION_HINT_RE = re.compile(r"^###\s*版本\s*$", re.MULTILINE)
_FR_NFR_HINT_RE = re.compile(r"^###\s*对应\s+FR/NFR\s*$", re.MULTILINE)
_VERSION_TUPLE_RE = re.compile(r"^v(\d+)\.(\d+)$")

# Baseline document filenames inherited by the hotfix project directory
# (ARCH-006 §3.4, IF-HOTFIX-005): source-approval record stores read-only
# paths to these six docs, never a copy.
_BASELINE_DOC_NAMES = (
    "story.md",
    "spec.md",
    "acceptance.md",
    "architecture.md",
    "interfaces.md",
    "test-plan.md",
)


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
        return "bug" in self.labels


@dataclass(frozen=True)
class PrecheckReport:
    """Outcome of the deterministic PRECHECK rules P-1..P-5 (IF-HOTFIX-003)."""

    status: Literal["pass", "rejected"]
    reason: HotfixRejectionReason | None
    next: str | None
    target_version: str | None
    active_branch: str | None


def _strip_branch_name(raw: str) -> str:
    """Strip ``* `` prefix and whitespace from a ``git branch --list`` line."""
    return raw.strip().lstrip("* ").strip()


def _version_tuple(version: str) -> tuple[int, int] | None:
    """Parse ``v<major>.<minor>`` -> numeric tuple (shared helper)."""
    match = _VERSION_TUPLE_RE.match(version.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _reject(
    reason: HotfixRejectionReason, next_step: str, active_branch: str | None = None
) -> PrecheckReport:
    """Build a deterministic rejected PRECHECK report (P-1..P-5)."""
    return PrecheckReport(
        status="rejected",
        reason=reason,
        next=next_step,
        target_version=None,
        active_branch=active_branch,
    )


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
    # P-1: issue fetch and type=bug check
    if issue is None:
        return _reject(
            "issue_not_found",
            "confirm the issue exists; complete the issue seed, then retry",
        )
    if not issue.is_bug:
        return _reject("not_bug", "file a bug issue or use `trac start` for new features")

    # P-2: scenario validity
    if scenario not in ("post-release", "dev"):
        return _reject(
            "scenario_invalid",
            "rerun with --scenario post-release (released) or --scenario dev (in-development)",
        )

    # P-3: locate the active release branch (dev scenario only)
    active_branch = _active_release_branch(repo_branches, active_run_branch)
    if scenario == "dev" and active_branch is None:
        return _reject(
            "no_active_release_branch",
            "checkout or create an active release branch (releases/*), then retry",
        )

    # P-4: target version location
    target = locate_target_version(scenario, active_branch, projects_dir, approved_versions)
    if target is None:
        return _reject(
            "baseline_not_locatable",
            "approve/complete the target version baseline, then retry",
            active_branch if scenario == "dev" else None,
        )

    # P-5: fix/{issue} branch not already taken
    if _fix_branch_taken(repo_branches, issue.number):
        return _reject(
            "fix_branch_exists",
            f"the fix/{issue.number} branch already exists; resolve it, then retry",
            active_branch if scenario == "dev" else None,
        )

    return PrecheckReport(
        status="pass",
        reason=None,
        next=None,
        target_version=target,
        active_branch=active_branch if scenario == "dev" else None,
    )


def _fix_branch_taken(repo_branches: list[str], issue_number: int) -> bool:
    """P-5: True when ``fix/{issue}`` already exists in the branch set."""
    wanted = f"fix/{issue_number}"
    return any(_strip_branch_name(raw) == wanted for raw in repo_branches)


def _active_release_branch(
    repo_branches: list[str], active_run_branch: str | None
) -> str | None:
    """P-3: resolve the active ``releases/*`` branch (ARCH-006 §3.2).

    Prefer the active run's branch if it is a ``releases/*`` branch;
    otherwise scan the repo branch list for one whose HEAD is ``releases/*``
    (the first found is the canonical active release branch).
    """
    if active_run_branch is not None and active_run_branch.startswith(_RELEASE_BRANCH_PREFIX):
        return active_run_branch
    for raw in repo_branches:
        name = _strip_branch_name(raw)
        if name.startswith(_RELEASE_BRANCH_PREFIX):
            return name
    return None


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
    if scenario == "dev":
        return _locate_dev_version(active_branch, approved_versions)
    return _locate_post_release_version(projects_dir, approved_versions)


def _locate_dev_version(
    active_branch: str | None, approved_versions: set[str]
) -> str | None:
    """dev: ``releases/vX.Y`` -> ``vX.Y``, must already be approved."""
    if active_branch is None:
        return None
    version = active_branch.rsplit("/", 1)[-1]
    if not version.startswith("v"):
        version = f"v{version}"
    if version in approved_versions:
        return version
    return None


def _locate_post_release_version(
    projects_dir: Path, approved_versions: set[str]
) -> str | None:
    """post-release: highest approved version whose spec/acceptance exist."""
    locatable = [
        (parsed, version)
        for version in approved_versions
        if (parsed := _version_tuple(version)) is not None
        and _baseline_docs_present(projects_dir, version)
    ]
    if not locatable:
        return None
    _, best_version = max(locatable)
    return best_version


def _baseline_docs_present(projects_dir: Path, version: str) -> bool:
    """P-4 post-release: spec.md + acceptance.md are on disk for ``version``."""
    vdir = projects_dir / version
    return (vdir / "spec.md").exists() and (vdir / "acceptance.md").exists()


def parse_issue_hints(body: str) -> dict:
    """IF-HOTFIX-003 optional bug-template field parsing (pure function).

    Deterministic regex over the issue body's optional 「版本 / 对应 FR/NFR」
    fields. Returns ``{"version": str | None, "fr_nfr": list[str]}``;
    missing fields yield empty values — PRECHECK never fails on them
    (hints only feed the Sage anchoring corpus).
    """
    return {"version": _parse_version_hint(body), "fr_nfr": _parse_fr_nfr_hints(body)}


def _parse_version_hint(body: str) -> str | None:
    """First non-heading line after a ``### 版本`` field, if any."""
    match = _VERSION_HINT_RE.search(body)
    if match is None:
        return None
    for line in body[match.end():].splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def _parse_fr_nfr_hints(body: str) -> list[str]:
    """Comma/newline-separated FR/NFR ids after a ``### 对应 FR/NFR`` field."""
    fr_nfr: list[str] = []
    match = _FR_NFR_HINT_RE.search(body)
    if match is None:
        return fr_nfr
    for line in body[match.end():].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            break
        for part in line.replace(",", " ").split():
            if part:
                fr_nfr.append(part)
    return fr_nfr


def parse_anchor_refs(acs: list[str]) -> list[tuple[str, str]]:
    """IF-HOTFIX-004 anchor-reference parsing (pure function).

    ``"AC-FR0030-01@v0.5"`` -> ``("AC-FR0030-01", "v0.5")`` (grammar:
    interfaces.md §1e). Malformed references (missing @version, bad id
    shape) raise ``ValueError("IF-HOTFIX-004:<detail>")``.
    """
    refs: list[tuple[str, str]] = []
    for ref in acs:
        match = _AC_REF_RE.match(ref)
        if match is None:
            raise ValueError(f"IF-HOTFIX-004:malformed anchor reference: {ref!r}")
        refs.append((match.group(1), match.group(2)))
    return refs


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
    missing: list[str] = []
    for ac_id, version in refs:
        acc_path = projects_dir / version / "acceptance.md"
        if not acc_path.exists():
            missing.append(f"{ac_id}@{version} not in {acc_path}")
            continue
        body = acc_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^###\s+" + re.escape(ac_id) + r"\b", body, re.MULTILINE)
        if match is None:
            missing.append(f"{ac_id}@{version} not in {acc_path}")
            continue
    return (len(missing) == 0, missing)


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
    # Determine the base branch per scenario (ARCH-006 §3.4).
    base = _resolve_base_branch(repo, scenario, run_id)
    branch_name = f"fix/{issue}"

    # Create the branch (fails closed: RuntimeError propagates to the
    # executor handler which emits run.completed(rejected)).
    commit_sha = _create_fix_branch(repo, branch_name, base)

    # Compute baseline digest (simple sha256 of the target version's trio).
    baseline_digest = _compute_baseline_digest(repo, target_version)

    # Baseline doc paths: target version's approved trio + design docs.
    vdir = repo / ".tracks" / "projects" / target_version
    baseline_doc_paths = [str(vdir / name) for name in _BASELINE_DOC_NAMES]

    return {
        "branch_name": branch_name,
        "base": base,
        "commit_sha": commit_sha,
        "target_version": target_version,
        "anchor_acs": list(anchor_acs),
        "baseline_digest": baseline_digest,
        "baseline_doc_paths": baseline_doc_paths,
    }


def _resolve_base_branch(repo: Path, scenario: str, run_id: str) -> str:
    """Resolve the base branch for a hotfix entry (ARCH-006 §3.4).

    dev: active ``releases/*`` branch from the repo's current branches;
    otherwise ``main``. Raises RuntimeError(IF-HOTFIX-005) when a dev
    scenario has no active release branch.
    """
    if scenario != "dev":
        return "main"
    proc = subprocess.run(
        ["git", "branch", "--list"], cwd=repo, capture_output=True, text=True, check=False
    )
    branches = proc.stdout.splitlines() if proc.returncode == 0 else []
    base = _active_release_branch(branches, None)
    if base is None:
        raise RuntimeError(
            f"IF-HOTFIX-005: no active release branch for dev scenario "
            f"(run_id={run_id})"
        )
    return base


def _create_fix_branch(repo: Path, branch_name: str, base: str) -> str:
    """Create ``branch_name`` from ``base`` and return the new HEAD sha.

    Fails closed: a nonzero ``git checkout -b`` exit raises RuntimeError
    (IF-HOTFIX-005), leaving no half-built run; ``git rev-parse`` failure
    yields an empty sha rather than a partial event.
    """
    proc = subprocess.run(
        ["git", "checkout", "-b", branch_name, base],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"IF-HOTFIX-005: branch creation failed: {proc.stderr.strip()}")

    sha_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return sha_proc.stdout.strip() if sha_proc.returncode == 0 else ""


def _compute_baseline_digest(repo: Path, target_version: str) -> str:
    """Compute a deterministic content digest for the target version baseline.

    Hashes the concatenation of the target version's story/spec/acceptance
    content (if they exist). This is a simple fingerprint, not a full
    git-style tree hash — sufficient for source-approval tracking.
    """
    hasher = hashlib.sha256()
    vdir = repo / ".tracks" / "projects" / target_version
    for doc in ("story.md", "spec.md", "acceptance.md"):
        path = vdir / doc
        if path.exists():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()
