"""In-place release-pipeline repair and Known Issue policy (FR-0286).

Defects found in M-VERIFY/M-SECURITY/M-PUBLISH/M-MILESTONE are repaired
inside the current run: classification only decides who repairs (Devon
RED-first for behaviour defects, verification-only for gate defects, Archer
advisory consultation for CVEs, controlled contract revision for contract
defects). No automatic rollback to M-DESIGN/M-PLANNING is ever produced. A
fix that lands a new commit creates a new candidate and the full M-VERIFY
chain re-walks (SM-01.20). When the repair budget (default 3) is exhausted
with Prism confirming the attribution unchanged, product-quality defects may
be registered as Known Issues (waiver + next-version backlog); mechanism
failures and security findings are excluded and must be fixed or abandoned.
"""

from __future__ import annotations

from typing import Literal

RepairDiscipline = Literal["red_first", "verification_only", "cve_advisory", "contract_delta"]


def classify_defect(finding: dict, context: dict) -> dict:
    """Map a gate/scan/review finding to {defect_class, owner, discipline}.

    Closed defect set: behaviour | gate | cve | contract (§1.0.14 table B).
    """
    raise NotImplementedError("IF-REPAIR-001")


def open_repair_round(run_id: str, classification: dict, budget: int) -> dict:
    """Emit repair.round_started {round, budget, classification}; never a
    stage.rolled_back — in-place repair only (AC-FR0286-01)."""
    raise NotImplementedError("IF-REPAIR-001")


def assert_frozen_tests_untouched(repo, before_digests: dict) -> None:
    """Frozen int/e2e must not change across a repair round (FR-0286 §10)."""
    raise NotImplementedError("IF-REPAIR-001")


def mark_fix_new_candidate(run_id: str, old_candidate: str) -> dict:
    """Emit evidence.staled(reason=fix_new_candidate) and trigger the full
    M-VERIFY re-walk on the new candidate (SM-01.20, AC-FR0286-03)."""
    raise NotImplementedError("IF-REPAIR-002")


def judge_irreparable(rounds_used: int, budget: int, prism_attribution: dict) -> bool:
    """Budget exhausted AND Prism confirms attribution unchanged, or the fix
    exceeds controlled contract revision, or the dependency has no fix."""
    raise NotImplementedError("IF-REPAIR-002")


def register_known_issue(repo, attribution: dict, candidate_sha: str) -> dict:
    """Prism-confirmed product-quality defect -> GitHub issue with the
    known-issue label linked to candidate + evidence; rejects mechanism
    failures and security findings (not_product_defect)."""
    raise NotImplementedError("IF-KNOWNISSUE-001")


def list_known_issues_for_preview(run_id: str) -> list[dict]:
    """Every unfixed known issue MUST appear in the preview; an unlisted one
    blocks release (informed consent, FR-0286 §5)."""
    raise NotImplementedError("IF-KNOWNISSUE-001")
