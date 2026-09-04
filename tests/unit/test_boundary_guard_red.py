"""T-013 RED (boundary guard round): candidate freeze + M-IMPL boundary
routing for RELEASE-capable runs (FR-0268 AC face, IF-VERIFY-001 wiring,
FR-0267 boundary composition).

Pins the still-undelivered faces of tracks/executor/m_verify.py named by
the assignment (the NotImplementedError sites at m_verify.py:34/:72/:80):

1. Candidate identity binding -- ``freeze_candidate`` on a clean tree
   returns the bound ``CandidateIdentity``: full HEAD SHA (Maestro ruling
   T-002 B), clean flag, current branch; idempotent across calls
   (interfaces §1d, IF-VERIFY-001 "clean tree 冻结完整 HEAD SHA、幂等不重冻").
2. Dirty-tree rejection -- ``freeze_candidate`` on a dirty tree (tracked
   file modified or staged; interfaces §1d "已跟踪文件有变更") must NOT
   return an identity: the caller lands attention.required(reason=dirty_tree)
   (AC-FR0267-02 wiring face; §1d "不得返回身份").
3. RELEASE-capable routing -- the v0.8 extension carries the M-IMPL
   boundary-route capability so RELEASE-capable runs are routed into
   M-VERIFY (architecture §1.1 composition root: "M-IMPL boundary guard
   routes RELEASE-capable runs into stage.entered(M-VERIFY) ->
   Command(freeze_candidate)"; IF-VERIFY-001: "M-IMPL boundary 对 RELEASE
   能力版本改道 M-VERIFY（未达门槛保持 boundary）"). The capability follows
   the seam discipline of the V07Extension precedent
   (executor/v07_runtime.py ``before_mtest``): consumers resolve a named
   capability via ``resolve_capability``; below-threshold versions select
   no extension and therefore no route (architecture §1.0.9).

All three anchors fail on the current baseline with assertion_failure on
the contract tokens (NotImplementedError stub bodies are converted to
assertion failures; the missing v0.8 capability registration is probed
via ``resolve_capability``). Only unit tests are added (RED discipline,
manifest red_test_paths = tests/unit); the ×3 fullf_reuse anchors in
test_m_verify_fullf_reuse_red.py stay untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tracks.executor import m_verify
from tracks.executor.version_extensions import resolve_capability
from tracks.kernel.events import Command
from tracks.kernel.machine import State
from tracks.kernel.release import RELEASE_PIPELINE_VERSION


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def _freeze_or_fail(repo: Path):
    freeze = getattr(m_verify, "freeze_candidate", None)
    if freeze is None:
        _fail(
            "executor/m_verify.py missing freeze_candidate export "
            "(IF-VERIFY-001 interfaces §1d candidate freeze, architecture "
            "§3 modules row 4)"
        )
    try:
        return freeze(repo)
    except NotImplementedError as err:
        _fail(f"freeze_candidate not implemented ({err})")


def _reject_dirty(freeze, repo: Path, scenario: str) -> None:
    try:
        decision = freeze(repo)
    except NotImplementedError as err:
        _fail(
            f"freeze_candidate dirty-tree rejection not implemented for "
            f"{scenario} ({err})"
        )
    except Exception:
        return
    _fail(
        f"assertion failure: dirty tree ({scenario}) must not return an "
        f"identity, got {decision!r} (interfaces §1d: freeze_candidate 在 "
        f"dirty tree 时不得返回身份, caller lands attention.required)"
    )


# AC-FR0268-01@v0.8 TRACKS-TRACE IF-VERIFY-001 freeze binds candidate identity
def test_freeze_candidate_computes_bound_identity(host_repo):
    """AC-FR0268-01 chain head: clean tree -> bound CandidateIdentity with
    the full HEAD SHA, clean flag and current branch; idempotent."""
    identity = _freeze_or_fail(host_repo)
    if not isinstance(identity, m_verify.CandidateIdentity):
        _fail(
            f"freeze_candidate must return CandidateIdentity, got "
            f"{type(identity).__name__}"
        )
    head = _git(host_repo, "rev-parse", "HEAD")
    if identity.candidate_sha != head or len(head) != 40:
        _fail(
            f"candidate_sha {identity.candidate_sha!r} != full HEAD SHA "
            f"{head!r} (T-002 B ruling: clean tree freezes the complete SHA)"
        )
    if identity.clean_tree is not True:
        _fail(
            f"clean tree must freeze clean_tree=True, got "
            f"{identity.clean_tree!r}"
        )
    branch = _git(host_repo, "rev-parse", "--abbrev-ref", "HEAD")
    if identity.branch != branch:
        _fail(
            f"branch {identity.branch!r} != current branch {branch!r} "
            "(bound identity carries the frozen branch)"
        )
    again = _freeze_or_fail(host_repo)
    if again.candidate_sha != identity.candidate_sha:
        _fail(
            "freeze_candidate is not idempotent for the same HEAD "
            "(IF-VERIFY-001: 幂等不重冻)"
        )


# AC-FR0268-02@v0.8 TRACKS-TRACE IF-VERIFY-001 dirty tree rejects freeze
def test_freeze_candidate_rejects_dirty_or_drifted_tree(host_repo):
    """AC-FR0268-02 gate: dirty tree (tracked file modified, then staged)
    must not yield a frozen identity (interfaces §1d)."""
    freeze = getattr(m_verify, "freeze_candidate", None)
    if freeze is None:
        _fail(
            "executor/m_verify.py missing freeze_candidate export "
            "(IF-VERIFY-001 interfaces §1d candidate freeze)"
        )
    (host_repo / "README.md").write_text("dirty worktree\n", encoding="utf-8")
    _reject_dirty(freeze, host_repo, "modified tracked file")
    _git(host_repo, "add", "README.md")
    _reject_dirty(freeze, host_repo, "staged tracked change")


def _route_or_fail(route, state):
    try:
        return route(state)
    except NotImplementedError as err:
        _fail(f"boundary-route capability not implemented ({err})")


# AC-FR0268-03@v0.8 TRACKS-TRACE IF-VERIFY-001 RELEASE-capable boundary route
def test_boundary_guard_routes_release_capable_run_to_m_verify():
    """The v0.8 extension carries the M-IMPL boundary route into M-VERIFY;
    below-threshold versions select no route (IF-VERIFY-001, arch §1.1)."""
    route = resolve_capability(RELEASE_PIPELINE_VERSION, "after_m_impl")
    if route is None:
        _fail(
            "v0.8 extension missing the after_m_impl boundary-route "
            "capability (IF-VERIFY-001: M-IMPL boundary 对 RELEASE 能力版本 "
            "改道 M-VERIFY -> Command(freeze_candidate), architecture §1.1 "
            "composition root); register the RELEASE-capable extension on "
            "the version capability seam (§1.0.9, V07Extension precedent)"
        )
    if resolve_capability("v0.6", "after_m_impl") is not None:
        _fail(
            "unregistered below-threshold version must select no boundary "
            "route (未达门槛保持 boundary; §1.0.9 early versions select no "
            "extension)"
        )
    boundary = State(
        version=RELEASE_PIPELINE_VERSION,
        stage="M-IMPL",
        substate="BASELINE",
        stage_exited=True,
    )
    command = _route_or_fail(route, boundary)
    if not isinstance(command, Command):
        _fail(
            f"boundary route must return a Command, got "
            f"{type(command).__name__}"
        )
    if command.kind != "freeze_candidate":
        _fail(
            f"boundary route command kind {command.kind!r} != "
            f"'freeze_candidate' (M-VERIFY chain head, architecture §1.1)"
        )
    if command.params.get("stage") != "M-VERIFY":
        _fail(
            f"boundary route must target M-VERIFY, got params "
            f"{command.params!r}"
        )
    parked = _route_or_fail(
        route,
        State(
            version=RELEASE_PIPELINE_VERSION,
            stage="M-IMPL",
            substate="BASELINE",
            stage_exited=False,
        ),
    )
    if parked is not None:
        _fail(
            f"M-IMPL not exited yet must not route, got {parked!r} (the "
            f"boundary guard routes only at the boundary)"
        )
