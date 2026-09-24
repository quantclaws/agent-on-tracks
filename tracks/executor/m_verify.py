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

from tracks.executor import version_extensions
from tracks.executor.helpers import _git_stdout as _git
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
    if expected is None:
        # IF-VERIFY-002: reuse requires the quadruple to be PROVEN
        # consistent. Evidence recorded without an identity_basis carries no
        # proof -- treating absence as a match let a drifted/stale FULL_F
        # reuse with an empty basis (AC-FR0268-02/03 fail-closed). Rerun.
        return ReuseDecision(decision="rerun", reason="identity_mismatch", identity_basis=())
    def _canonical(value):
        if isinstance(value, (list, tuple)):
            return tuple(_canonical(item) for item in value)
        return value

    quad = (
        identity_quadruple.get("tree"),
        _canonical(identity_quadruple.get("command")),
        identity_quadruple.get("env"),
        identity_quadruple.get("selection_id"),
    )
    exp_tuple = tuple(_canonical(item) for item in expected)
    if exp_tuple != quad:
        return ReuseDecision(
            decision="rerun", reason="identity_mismatch", identity_basis=exp_tuple
        )
    # Drift folds into identity_mismatch in this slice: a moved candidate
    # surfaces as a basis mismatch above. With no STALE and a matching
    # basis, the FULL_F evidence is reused verbatim.
    return ReuseDecision(
        decision="reuse", reason="reuse_full_f", identity_basis=exp_tuple
    )


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
        "verify_final_review": {
            "discussion_refs": {
                "required_on": "revise",
                "item_fields": {
                    "file": "repo-relative document path",
                    "thread_id": "current T-NNN id from parse_threads",
                    "token": "exact token_for(thread) object for locate()",
                    "finding_id": "matching blocker finding id from findings[]",
                },
                "anchor_rule": (
                    "Each ref must locate uniquely to an open/reopen Prism or Lex "
                    "thread and its finding_id must match a finding with severity=blocker."
                ),
            }
        },
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


def _before_mtest(state):
    """M-TEST entry pre-gate for RELEASE-capable runs (IF-VERIFY-004).

    v0.8 inherits the v0.7 Phase 0 sealing unchanged (architecture: 事件溯源
    与 Phase 0 封存全部保持不变), so the kernel entry guard resolves the
    same pure ``decide_phase0`` projection the v0.7 composition root
    assembles: any non-SEALED Phase 0 projection routes to
    ``Command(phase0_validate)``; SEALED/BLOCKED park (§1c). The lazy
    import mirrors ``machine._resolve_before_mtest`` -- importing
    ``kernel.phase0`` at module scope here would cycle through the kernel
    package's own lazy seam call points.
    """
    from tracks.kernel.phase0 import decide_phase0

    return decide_phase0(state)


class V08Extension:
    """Capability namespace registered for the v0.8 project version.

    Importing this module registers the extension exactly once on the
    version capability seam (architecture §1.0.9, ``V07Extension``
    precedent in ``executor/v07_runtime.py``): RELEASE-capable versions
    re-route at the M-IMPL boundary via ``after_m_impl`` while the M-TEST
    entry resolves the inherited Phase 0 pre-gate via ``before_mtest``
    (missing pieces block fail-closed, §1.0.9). Below-threshold versions
    select no extension and keep their boundary (未达门槛保持 boundary;
    the seam's ``None``).

    The ISLAND_GATE_2 exit gate and closure rendering are inherited
    unchanged from the v0.7 composition (architecture: candidate-bound
    closure semantics keep v0.7 semantics); forwarding verbatim to the
    same shared callbacks keeps a single implementation truth.
    """

    after_m_impl = staticmethod(_after_m_impl)
    before_mtest = staticmethod(_before_mtest)

    @staticmethod
    def trace(*args):
        """The same candidate-bound closure join the v0.7 CLI/gate share."""
        from tracks.checks.trace import check_closure_candidate

        return check_closure_candidate(*args)

    @staticmethod
    def release_trace(events):
        """IF-TRACE-003 release segment appended after the closure output."""
        from tracks.checks.trace import build_release_trace_segment

        return build_release_trace_segment(events)

    @staticmethod
    def island_gate_2(arguments=(), **kwargs):
        from tracks.executor.failclosed import demonstrate_failclosed

        return demonstrate_failclosed(*arguments, **kwargs)

    @staticmethod
    def render_closure(payload):
        from tracks.executor.v07_runtime import _render_closure

        return _render_closure(payload)


version_extensions.register_extension(EXTENSION_VERSION, V08Extension())
# 2026-09-24 (island-2 boundary, run 01M2QTJB v0.9): the release pipeline
# (M-VERIFY -> M-SECURITY -> M-RELEASE) is a version-INHERITED capability --
# every version from RELEASE_PIPELINE_VERSION onward gets the same
# chain-head (freeze_candidate) unless a later version-specific extension
# replaces it. v0.9 completed M-IMPL and parked at the boundary with no
# after_m_impl callback: the exact-key lookup had no v0.9 binding. Register
# for the same extension every version >= the pipeline floor.
_RELEASE_FLOOR = tuple(int(x) for x in EXTENSION_VERSION[1:].split("."))
for _minor in range(_RELEASE_FLOOR[1] + 1, 32):
    version_extensions.register_extension(
        f"v{_RELEASE_FLOOR[0]}.{_minor}", V08Extension()
    )
