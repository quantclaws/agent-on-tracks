"""Pure assembly and integrity helpers for release.previewed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tracks import paths
from tracks.executor.full_f_authority import is_authoritative_full_f
from tracks.executor.host_contract import resolve_artifact_identity
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    complete_version_facts,
    expand_version_scheme,
    generate_preview,
    operation_plan_needs_n,
)
from tracks.executor.review_findings import has_blocking_findings
from tracks.executor.sync_product import validate_record, verified_records

# interfaces §1b item 1: a new preview/authorization is invalid unless every
# one of these keys carries successful evidence.
REQUIRED_EVIDENCE_KEYS = frozenset(
    {"full_f", "ci", "prism.verify_final", "prism.security", "security"}
)


def canonical_digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _contract_table(contract) -> dict:
    scheme = contract.version
    return {
        "version": contract.contract_version,
        "language": contract.language,
        "toolchain": contract.toolchain,
        "install": contract.install,
        "version_scheme": {
            "feature_tag": scheme.feature_tag,
            "patch_line": scheme.patch_line,
            "prerelease_tag": scheme.prerelease_tag,
        },
        # D6: the declared artifact is the `{artifact}` placeholder's source
        # for the operation plan, exactly as the publish-time re-validation
        # reads it from the raw [host-contract.build] table (single truth).
        "build": {"artifact": str(getattr(contract, "build_artifact", "") or "")},
        "operations": {
            journey: {
                "steps": list(decl.steps),
                "requires": list(decl.requires),
            }
            for journey, decl in contract.operations.items()
        },
    }


def _version_facts(contract, facts: dict) -> dict:
    scheme = _contract_table(contract).get("version_scheme", {})
    return expand_version_scheme(facts, scheme)


def artifact_resolution_error(repo: Path, contract, facts: dict) -> str | None:
    """The declared artifact's resolution error, or None when it is absent.

    An undeclared artifact (``build_artifact`` empty) is not an error: the
    preview keeps its legacy empty ``artifact_digest``. Zero/multiple glob
    matches and unreadable files fail closed with their reason (D1).
    """
    _identity, error = resolve_artifact_identity(
        repo, str(getattr(contract, "build_artifact", "") or ""), facts
    )
    return None if error in (None, "artifact_undeclared") else error


def _evidence_key(event, payload: dict) -> str | None:
    if event.type in ("local_gate.passed", "local_gate.failed"):
        return str(payload.get("gate_identity") or payload.get("kind") or "local")
    if event.type == "full.executed":
        return "full_f"
    if event.type == "evidence.reused":
        return "full_f" if payload.get("kind") == "full_f" else None
    if event.type == "ci.run_observed":
        return "ci"
    if event.type == "prism.verdict":
        # interfaces §1b item 2: only the two release scopes are evidence;
        # M-DESIGN/M-TEST/M-IMPL verdicts never enter the release set.
        scope = payload.get("scope")
        if scope == "verify_final":
            return "prism.verify_final"
        if scope == "security":
            return "prism.security"
        return None
    if event.type == "security.assessed":
        return "security"
    return None


def _evidence_bound(event, payload: dict, candidate_sha: str) -> bool:
    if event.type == "full.executed":
        declared = payload.get("candidate_sha")
        if declared is not None:
            return declared == candidate_sha
        return payload.get("execution_commit") == candidate_sha
    if event.type == "evidence.reused":
        return (
            payload.get("kind") == "full_f"
            and payload.get("candidate_sha") == candidate_sha
        )
    return payload.get("candidate_sha") == candidate_sha


def _evidence_is_success(
    event_type: str, payload: dict, candidate_sha: str | None = None
) -> bool:
    if event_type in ("local_gate.passed", "local_gate.failed"):
        result = payload.get("normalized_result")
        return (
            event_type == "local_gate.passed"
            and isinstance(result, dict)
            and result.get("status") == "passed"
            and result.get("exit_code") == 0
        )
    if event_type == "full.executed":
        # interfaces §1a/§1b: FULL_F credit comes from the single authority
        # predicate, never from ``serves_as_full_f`` alone.
        return is_authoritative_full_f(payload, candidate_sha)
    if event_type == "evidence.reused":
        # A bound reuse is a valid full_f proof; _evidence_bound owns the
        # candidate binding, this predicate owns the kind.
        return payload.get("kind") == "full_f" and bool(payload.get("candidate_sha"))
    if event_type == "ci.run_observed":
        return payload.get("status") == "passed" and payload.get("api_verified") is True
    if event_type == "prism.verdict":
        # interfaces §1c: pass with blocking findings is a contradictory
        # verdict and never counts as success.
        return payload.get("verdict") == "pass" and not has_blocking_findings(
            payload.get("findings")
        )
    if event_type == "security.assessed":
        return payload.get("status") == "passed"
    return False


def _stale_barrier(events, candidate_sha: str) -> int:
    stale_types = ("candidate.stale", "evidence.staled")
    return max(
        (
            event.seq
            for event in events
            if event.type in stale_types
            and (event.payload or {}).get("candidate_sha") in (None, candidate_sha)
        ),
        default=-1,
    )


def _collect_evidence(events, candidate_sha: str, barrier: int):
    latest: dict[str, tuple[int, str, dict]] = {}
    before: dict[str, tuple[int, str, dict]] = {}
    for event in events:
        payload = event.payload or {}
        if not _evidence_bound(event, payload, candidate_sha):
            continue
        key = _evidence_key(event, payload)
        if key is None:
            continue
        target = before if barrier >= 0 and event.seq <= barrier else latest
        target[key] = (event.seq, event.type, dict(payload))
    return before, latest


def evidence_digests(events, candidate_sha: str) -> dict | None:
    barrier = _stale_barrier(events, candidate_sha)
    before, latest = _collect_evidence(events, candidate_sha, barrier)
    successful = {
        key: canonical_digest(
            {
                field: value
                for field, value in payload.items()
                if field != "contract_digest"
            }
        )
        for key, (_seq, event_type, payload) in sorted(latest.items())
        if _evidence_is_success(event_type, payload, candidate_sha)
    }
    # A stale barrier requires the PRE-barrier evidence set to be
    # re-established after it. When the pre-barrier set is EMPTY the
    # candidate's entire evidence landed AFTER the barrier (a full re-walk --
    # exactly the escape/return disposition): there is nothing stale to
    # re-verify and the current set stands as-is.
    if barrier >= 0 and before and not set(before).issubset(successful):
        return None
    # interfaces §1b item 1: completeness is unconditional -- a stale barrier
    # never skips the five-key requirement.
    if not REQUIRED_EVIDENCE_KEYS.issubset(successful):
        return None
    return successful


def _journey(contract, requested: str | None) -> str | None:
    operations = contract.operations
    if requested:
        return requested if requested in operations else None
    if "feature" in operations:
        return "feature"
    return next(iter(operations), None)


def assemble_preview(
    repo: Path,
    contract,
    candidate_sha: str,
    contract_policy_digest: str,
    version_facts: dict,
    events,
    *,
    journey: str | None = None,
    known_issues: list | None = None,
) -> dict | None:
    resolved_journey = _journey(contract, journey)
    if resolved_journey is None:
        return None
    plan, resolved_facts = _preview_plan(
        repo,
        contract,
        resolved_journey,
        version_facts,
        candidate_sha=candidate_sha,
        events=events,
    )
    if plan is None:
        return None
    policy_digest = contract_policy_digest
    if not policy_digest.startswith("sha256:"):
        policy_digest = "sha256:" + policy_digest
    digests = _preview_digests(repo, contract, candidate_sha, resolved_facts, plan, events)
    if digests is None:
        return None
    digests["contract_policy_digest"] = policy_digest
    preview = generate_preview(candidate_sha, digests, risks=[], plan=plan)
    if known_issues:
        preview["known_issues"] = list(known_issues)
    return preview


def _preview_plan(
    repo: Path,
    contract,
    resolved_journey: str,
    version_facts: dict,
    *,
    candidate_sha: str | None = None,
    events=None,
):
    """Resolve the version facts + operation plan, or (None, None) on refusal.

    FR-0277-02: a verified sync product record is re-validated here (exact
    parents/tree, byte-identical re-derivation, evidence digests) before it
    can enter the plan; a preview can never bind an unverified or mismatched
    product.
    """
    table = _contract_table(contract)
    patch_line = str(getattr(getattr(contract, "version", None), "patch_line", "") or "")
    resolved_facts, _error = complete_version_facts(
        repo,
        version_facts,
        patch_line,
        needs_n=operation_plan_needs_n(table, resolved_journey),
    )
    if resolved_facts is None:
        return None, None
    resolved_facts = _version_facts(contract, resolved_facts)
    sync_products: list = []
    if candidate_sha and events is not None:
        sync_products = verified_records(events, candidate_sha)
        for record in sync_products:
            if validate_record(repo, record, candidate_sha) is not None:
                return None, None
    plan = build_operation_plan(
        table,
        resolved_journey,
        resolved_facts,
        active_release_branch=active_release_branch(repo, resolved_facts),
        sync_products=sync_products,
    )
    if not isinstance(plan, dict):
        return None, None
    return plan, resolved_facts


def _preview_digests(
    repo: Path,
    contract,
    candidate_sha: str,
    resolved_facts: dict,
    plan: dict,
    events,
) -> dict | None:
    """Artifact/evidence/plan digests, or None when evidence is missing."""
    identity, error = resolve_artifact_identity(
        repo, str(getattr(contract, "build_artifact", "") or ""), resolved_facts
    )
    if identity is None and error != "artifact_undeclared":
        return None
    digests = {
        "artifact_digest": str(identity["digest"]) if identity else "",
        "evidence_digests": None,
        "operation_plan_digest": canonical_digest(plan),
    }
    if identity:
        # D1: the resolved artifact's byte identity is part of the preview
        # payload (name/size/digest), not only a bare digest.
        digests["artifact_identity"] = {
            key: identity[key] for key in ("name", "size", "digest")
        }
    evidence = evidence_digests(events, candidate_sha)
    if evidence is None:
        return None
    digests["evidence_digests"] = evidence
    return digests


def preview_blob_matches(home: Path, preview: dict) -> bool:
    name = paths.blob_name_from_ref(preview.get("blob_ref"))
    if name is None:
        return False
    path = paths.blobs_dir(home) / name
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != name:
            return False
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    expected = {key: value for key, value in preview.items() if key != "blob_ref"}
    return payload == expected


def _legacy_prism_evidence_matches(old_evidence: dict, current_evidence: dict) -> bool:
    """A v0.12.0 blob recorded one ``"prism"`` digest for the latest verdict.

    It stays readable by matching that key against either split prism key
    (INV-5); every other recorded key still compares exactly.
    """
    split_prism = (
        current_evidence.get("prism.security"),
        current_evidence.get("prism.verify_final"),
    )
    for key, value in old_evidence.items():
        if key == "prism":
            if value not in split_prism:
                return False
        elif current_evidence.get(key) != value:
            return False
    return True


def preview_inputs_match(old: dict, current: dict) -> bool:
    """Whether *current* reproduces every input recorded by *old*.

    Split-schema previews compare exactly. A legacy blob's recorded key set
    is compared against the current preview with the single ``"prism"``
    digest mapped onto the split prism keys -- no DB migration (INV-5).
    """
    old_inputs = {key: value for key, value in old.items() if key != "blob_ref"}
    current_inputs = {key: value for key, value in current.items() if key != "blob_ref"}
    old_evidence = old_inputs.get("evidence_digests")
    current_evidence = current_inputs.get("evidence_digests")
    if (
        isinstance(old_evidence, dict)
        and "prism" in old_evidence
        and "prism.security" not in old_evidence
        and isinstance(current_evidence, dict)
    ):
        old_rest = {
            key: value
            for key, value in old_inputs.items()
            if key not in ("evidence_digests", "preview_digest")
        }
        current_rest = {
            key: value
            for key, value in current_inputs.items()
            if key not in ("evidence_digests", "preview_digest")
        }
        if old_rest != current_rest:
            return False
        return _legacy_prism_evidence_matches(old_evidence, current_evidence)
    return old_inputs == current_inputs
