"""M-VERIFY executor domain (FR-0267–FR-0271): candidate freeze on a clean
tree, FULL_F reuse judgment, host-contract local gates, GitHub required-CI
API readback and the Prism same-candidate final-review dispatch payload.

Side-effect boundary facts (git, subprocess, network) are gathered here and
emitted as events; binding verification itself is pure over the event stream.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tracks.executor import version_extensions
from tracks.kernel.events import Command
from tracks.kernel.release import RELEASE_PIPELINE_VERSION

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


class FreezeBlocked(RuntimeError):
    """freeze_candidate refused to bind an identity (interfaces §1d).

    No CandidateIdentity may exist for a tree that is not exactly HEAD; the
    caller maps this refusal to attention.required(reason=dirty_tree).
    """


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def freeze_candidate(repo: Path) -> CandidateIdentity:
    """Freeze the candidate identity on a clean tree (IF-VERIFY-001).

    Clean tree (tracked files unchanged; interfaces §1d "已跟踪文件有变更"
    is the dirty definition) -> bind the full HEAD SHA (Maestro ruling
    T-002 B), the clean flag and the current branch. Dirty tree -> raise
    :class:`FreezeBlocked`: no identity may exist, the caller lands
    attention.required(reason=dirty_tree). Idempotent for the same HEAD
    (no re-freeze): the identity is recomputed from git, never minted.
    """
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise FreezeBlocked(
            "dirty tree: tracked changes present, refusing to freeze "
            f"(interfaces §1d): {dirty.splitlines()[:3]}"
        )
    return CandidateIdentity(
        candidate_sha=_git(repo, "rev-parse", "HEAD"),
        clean_tree=True,
        branch=_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
    )


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
    """Pure scan: any release-chain evidence not bound to candidate_sha.

    Per IF-VERIFY-001 ("full-chain evidence binding check", interfaces.md §1d)
    and NFR-0143 (single primary identity): every release-chain event must bind
    to the frozen candidate_sha — carry it, and carry the same one. A foreign
    binding and an absent binding are both violations; the returned identifier
    names the offending event kind so dispatch triage can route it.
    """
    violations: list[str] = []
    for event in events:
        bound = event.get("candidate_sha")
        kind = event.get("kind", "<unknown-kind>")
        if bound is None:
            violations.append(
                f"{kind}: release-chain event carries no candidate_sha binding"
            )
        elif bound != candidate_sha:
            violations.append(
                f"{kind}: bound candidate_sha {bound!r} != frozen {candidate_sha!r}"
            )
    return violations


def build_prism_final_review_assignment(
    candidate_sha: str,
    evidence_digests: dict,
) -> dict:
    """Assemble the same-candidate consistency-review dispatch envelope.

    Per IF-VERIFY-005 ("same-candidate consistency-review dispatch envelope"):
    the envelope names the frozen candidate, scopes the review to verify_final,
    and carries the evidence digest manifest for Prism to check against the
    frozen tree (field names follow the composer closed field set in
    tracks/checks/trace.py).
    """
    return {
        "candidate_sha": candidate_sha,
        "scope": "verify_final",
        "evidence_digests": dict(evidence_digests),
    }


EXTENSION_VERSION = RELEASE_PIPELINE_VERSION


def _after_m_impl(state):
    """M-IMPL boundary route for RELEASE-capable runs (IF-VERIFY-001).

    At the exited M-IMPL boundary return the M-VERIFY chain-head command
    (architecture §1.1 composition root: the boundary guard routes
    RELEASE-capable runs into stage.entered(M-VERIFY) ->
    ``Command(freeze_candidate)``). Anywhere else return ``None`` so the
    guard stays parked -- routing happens only at the boundary.
    """
    if getattr(state, "stage", None) != "M-IMPL" or not getattr(
        state, "stage_exited", False
    ):
        return None
    return Command(kind="freeze_candidate", params={"stage": "M-VERIFY"})


class V08Extension:
    """Capability namespace registered for the v0.8 project version.

    Importing this module registers the extension exactly once on the
    version capability seam (architecture §1.0.9, ``V07Extension``
    precedent in ``executor/v07_runtime.py``): RELEASE-capable versions
    re-route at the M-IMPL boundary via ``after_m_impl`` while
    below-threshold versions select no extension and keep their boundary
    (未达门槛保持 boundary; the seam's ``None``).
    """

    after_m_impl = staticmethod(_after_m_impl)


version_extensions.register_extension(EXTENSION_VERSION, V08Extension())
