"""RGR git operations (FR-0070/FR-0080/FR-0120/FR-0200, IF-IMPL-004).

Red ref creation (compare-and-set), Green commit creation (parent=B +
trailers), Red classification (reuses v0.4 RedClass closed set), and lineage
proof (ref + trailer + event sequence triple, NOT Git ancestry per R-1).

This module is a contract stub: signatures are frozen, bodies raise
NotImplementedError with the IF- token. Devon fills the implementation during
M-IMPL; the declaration contract must not change.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RedRef:
    ref: str          # refs/trac/rgr/{run}/{task}/{attempt}/red
    sha: str          # R commit SHA
    created: bool     # True=new, False=already exists (compare-and-set failed)


@dataclass(frozen=True)
class GreenCommit:
    sha: str          # G commit SHA
    parent: str       # B commit SHA (base)
    trailers: dict    # {"Tracks-Task": task_id, "Tracks-Attempt": attempt,
                      #  "Tracks-R": r_sha, "Tracks-Issue": issue_number,
                      #  "Tracks-AC": ac_refs}


@dataclass(frozen=True)
class LineageProof:
    r_before_g: bool          # R ref exists + G trailer has Tracks-R + event seq
    r_ref_exists: bool        # git rev-parse refs/trac/rgr/.../red succeeds
    g_trailers_valid: bool    # git log --format='%B' -1 <G> has five trailers
    event_order_valid: bool   # red.checkpointed event seq < green.committed event seq


def create_red_ref(
    repo: str,          # git repository path
    run_id: str,
    task_id: str,
    attempt: int,
    test_diff: str,     # test-only diff (Devon RED outcome)
    base_sha: str,      # B commit SHA (C_design)
) -> RedRef:
    """FR-0070 create private commit R + git ref (compare-and-set).

    ref = refs/trac/rgr/{run}/{task}/{attempt}/red
    First git rev-parse to check if ref exists; if exists ->
    RedRef(created=False), open new attempt.
    If not exists -> git commit-tree (test_diff as tree patch on base_sha) +
    git update-ref.
    R is immutable (BS-06): same attempt retry overwriting R fails
    compare-and-set.
    """
    raise NotImplementedError("IF-IMPL-004")


def create_green_commit(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    impl_diff: str,     # Devon GREEN outcome product code diff
    base_sha: str,      # B commit SHA (parent=B)
    r_sha: str,         # R commit SHA (for trailer)
    issue_number: int,  # GitHub issue number (FR-0220, trailer Tracks-Issue)
    ac_refs: list[str], # AC/FR/NFR provenance (FR-0220, trailer Tracks-AC)
) -> GreenCommit:
    """FR-0120 create formal commit G (parent=B + trailers).

    git commit-tree (impl_diff as tree patch on base_sha) with trailers:
      Tracks-Task: {task_id}
      Tracks-Attempt: {attempt}
      Tracks-R: {r_sha}
      Tracks-Issue: {issue_number}
      Tracks-AC: {ac_refs}
    G parent=B (no Git ancestry topology assertion, R is not G's ancestor, R-1).
    Tracks-Issue/Tracks-AC added in R-3/R-4: Devon commit trailers carry
    issue# + FR/NFR/ACC provenance for downstream bug-fix traceability.
    """
    raise NotImplementedError("IF-IMPL-004")


def classify_red(
    test_id: str, returncode: int, stdout: str, stderr: str,
) -> str:
    """FR-0080 M-IMPL RED_GATE legitimate red classification (pure function,
    reuses v0.4 RedClass closed set).

    Returns RedClass (IF-004 §1g). M-IMPL legitimate red =
    assertion_failure / symbol_missing.
    stub_token_failure is NOT in M-IMPL legitimate red (belongs to M-TEST
    contract test scenario).
    Illegitimate red = collection_error / unexpected_pass / unclassified.
    """
    raise NotImplementedError("IF-IMPL-004")


def verify_lineage(
    repo: str,
    run_id: str,
    task_id: str,
    attempt: int,
    g_sha: str,
    events: list,       # red.checkpointed + green.committed events (sorted by seq)
) -> LineageProof:
    """FR-0120 R-before-G lineage proof (pure function + git read-only).

    No Git ancestry topology assertion (R-1): both R and G have B as parent.
    Lineage is jointly proven by ref + trailer + event sequence.
    """
    raise NotImplementedError("IF-IMPL-004")
