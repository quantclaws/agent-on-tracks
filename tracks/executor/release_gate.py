"""M-RELEASE executor domain (FR-0273/FR-0274/FR-0277, IF-RELEASE-002/003).

Preview aggregation binds candidate SHA + artifact digest + evidence digests
+ operation-plan digest + contract/policy digest into preview_digest; the
Human three-way gate consumes the preview via the independent release CLI
surface and every decision is append-only and digest-bound. Three journey
plans (feature/post-release/dev, IF-JOURNEY-001) resolve declared operation
steps with version facts; dev precheck fails closed without an active release
branch.
"""

# ruff: noqa
from __future__ import annotations

import hashlib
import json
from typing import Literal

StaleReason = Literal["candidate_drift", "evidence_staled", "operation_plan_changed"]

_JOURNEY_MAP = {
    "feature": "feature",
    "post-release": "post_release",
    "post_release": "post_release",
    "dev": "dev",
}


def _render_placeholders(template: str, facts: dict) -> str:
    """Render the base placeholder set from version facts."""
    out = template
    for key, value in (facts or {}).items():
        out = out.replace("{" + key + "}", str(value))
    return out


def build_operation_plan(contract: dict, journey: str, version_facts: dict) -> dict:
    """Resolve the journey's declared operation steps with placeholders."""
    ops = (contract or {}).get("operations", {})
    section = ops.get(journey) or ops.get(_JOURNEY_MAP.get(journey, journey)) or {}
    steps = list(section.get("steps") or [])
    resolved = [_render_placeholders(s, version_facts) for s in steps]
    return {
        "journey": journey,
        "steps": resolved,
        "operations": {journey: {"steps": resolved}},
        "requires": list(section.get("requires") or ()),
    }


def dev_precheck(contract: dict, version_facts: dict, *, active_release_branch) -> tuple[bool, str]:
    """IF-JOURNEY-001 dev precheck: no active release branch -> fail closed
    (no plan steps, no fake public release)."""
    if not active_release_branch:
        return False, "no active release branch"
    ops = (contract or {}).get("operations", {})
    section = ops.get("dev") or {}
    steps = section.get("steps") or []
    if not steps:
        return False, "dev journey has no declared steps"
    return True, ""


def compute_preview_digest(
    candidate_sha: str,
    artifact_digest: str,
    evidence_digests: dict,
    operation_plan_digest: str,
    contract_policy_digest: str,
) -> str:
    """§1g: preview_digest = sha256(canonical_json(all five components))."""
    raw = {
        "candidate_sha": candidate_sha,
        "artifact_digest": artifact_digest,
        "evidence_digests": evidence_digests,
        "operation_plan_digest": operation_plan_digest,
        "contract_policy_digest": contract_policy_digest,
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_preview(candidate_sha: str, digests: dict, risks: list, plan: dict) -> dict:
    """Assemble the content-address preview blob (IF-RELEASE-002)."""
    preview = {
        "candidate_sha": candidate_sha,
        "artifact_digest": digests.get("artifact_digest", ""),
        "evidence_digests": digests.get("evidence_digests", {}),
        "operation_plan_digest": digests.get("operation_plan_digest", ""),
        "contract_policy_digest": digests.get("contract_policy_digest", ""),
        "risks": list(risks or []),
        "operation_plan": dict(plan or {}),
    }
    preview["preview_digest"] = compute_preview_digest(
        candidate_sha,
        preview["artifact_digest"],
        preview["evidence_digests"],
        preview["operation_plan_digest"],
        preview["contract_policy_digest"],
    )
    return preview


def judge_preview_stale(current_aggregate: dict, preview: dict) -> StaleReason | None:
    """IF-RELEASE-002: recompute aggregate at read time; mismatch -> stale."""
    current = current_aggregate or {}
    if not current:
        return None
    if current.get("candidate_sha") != preview.get("candidate_sha"):
        return "candidate_drift"
    if current.get("preview_digest") is not None and current.get("preview_digest") != preview.get(
        "preview_digest"
    ):
        return "operation_plan_changed"
    if current.get("evidence_staled"):
        return "evidence_staled"
    return None


def validate_release_decision(
    decision: str, preview: dict, gate_status: dict
) -> tuple[bool, str | None]:
    """Fail-closed authorization check (stale preview / failed gate → reject).

    IF-RELEASE-003: the three-way gate rejects when the preview is stale or a
    prerequisite gate failed; no Human decision may bypass a failing gate.
    """
    if decision not in ("release", "delay", "return"):
        return False, "invalid decision"
    if gate_status.get("preview_stale"):
        return False, "preview stale"
    if decision == "release":
        if gate_status.get("gate_failed"):
            return False, "gate failed"
        if gate_status.get("security_status") in ("failed", "unknown"):
            return False, "security not passed"
        if gate_status.get("prism_status") in ("failed", "revise"):
            return False, "prism not passed"
    return True, None