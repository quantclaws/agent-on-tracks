"""M-RELEASE executor domain (FR-0273/FR-0274, IF-RELEASE-002/003).

Preview aggregation binds candidate SHA + artifact digest + evidence digests
+ operation-plan digest + contract/policy digest into preview_digest; the
Human three-way gate consumes the preview via the independent release CLI
surface and every decision is append-only and digest-bound.
"""

from __future__ import annotations

from typing import Literal

StaleReason = Literal["candidate_drift", "evidence_staled", "operation_plan_changed"]


def build_operation_plan(contract, journey: str, version_facts: dict) -> dict:
    """Resolve the journey's declared operation steps with placeholders."""
    raise NotImplementedError("IF-RELEASE-002")


def compute_preview_digest(
    candidate_sha: str,
    artifact_digest: str,
    evidence_digests: dict,
    operation_plan_digest: str,
    contract_policy_digest: str,
) -> str:
    raise NotImplementedError("IF-RELEASE-002")


def generate_preview(candidate_sha: str, digests: dict, risks: list, plan: dict) -> dict:
    raise NotImplementedError("IF-RELEASE-002")


def judge_preview_stale(current_aggregate: dict, preview: dict) -> StaleReason | None:
    raise NotImplementedError("IF-RELEASE-002")


def validate_release_decision(
    decision: str, preview: dict, gate_status: dict
) -> tuple[bool, str | None]:
    """Fail-closed authorization check (stale preview / failed gate → reject)."""
    raise NotImplementedError("IF-RELEASE-003")
