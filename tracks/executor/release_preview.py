"""Pure assembly and integrity helpers for release.previewed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tracks import paths
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    complete_version_facts,
    generate_preview,
    operation_plan_needs_n,
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
        "operations": {
            journey: {
                "steps": list(decl.steps),
                "requires": list(decl.requires),
            }
            for journey, decl in contract.operations.items()
        },
    }


def _version_facts(contract, facts: dict) -> dict:
    resolved = dict(facts or {})
    scheme = _contract_table(contract).get("version_scheme", {})
    for name in ("feature_tag", "patch_line", "prerelease_tag"):
        value = str(scheme.get(name, ""))
        for _ in range(3):
            for key, fact in resolved.items():
                value = value.replace("{" + key + "}", str(fact))
        resolved[name] = value
    return resolved


def _artifact_digest(repo: Path, contract, facts: dict) -> str | None:
    artifact = str(contract.build_artifact or "")
    for key, value in facts.items():
        artifact = artifact.replace("{" + key + "}", str(value))
    if not artifact:
        return ""
    path = (repo / artifact).resolve()
    try:
        path.relative_to(repo.resolve())
        raw = path.read_bytes()
    except (OSError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _evidence_key(event, payload: dict) -> str | None:
    if event.type in ("local_gate.passed", "local_gate.failed"):
        return str(payload.get("gate_identity") or payload.get("kind") or "local")
    if event.type == "full.executed":
        return "full_f"
    if event.type == "ci.run_observed":
        return "ci"
    if event.type == "prism.verdict":
        return "prism"
    if event.type == "security.assessed":
        return "security"
    return None


def _evidence_bound(event, payload: dict, candidate_sha: str) -> bool:
    if event.type == "full.executed":
        declared = payload.get("candidate_sha")
        if declared is not None:
            return declared == candidate_sha
        return payload.get("execution_commit") == candidate_sha
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
        identity = payload.get("identity")
        return (
            payload.get("passed") is True
            and payload.get("full_f_eligible") is True
            and (
                candidate_sha is None
                or payload.get("execution_commit") == candidate_sha
            )
            and payload.get("selection_id") == (identity or {}).get("selection_id")
            and all(
                isinstance(identity.get(key), (str, list))
                for key in ("tree", "command", "env")
            )
            and bool(payload.get("execution_commit"))
            and bool(payload.get("evidence_ids"))
        ) if isinstance(identity, dict) else False
    if event_type == "ci.run_observed":
        return payload.get("status") == "passed" and payload.get("api_verified") is True
    if event_type == "prism.verdict":
        return payload.get("verdict") == "pass"
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
    if barrier >= 0:
        required = set(before)
        if not required:
            return None
        current_success = {
            key
            for key, (_seq, event_type, payload) in latest.items()
            if _evidence_is_success(event_type, payload, candidate_sha)
        }
        if not required.issubset(current_success):
            return None
    return {
        key: canonical_digest(
            {field: value for field, value in payload.items() if field != "contract_digest"}
        )
        for key, (_seq, event_type, payload) in sorted(latest.items())
        if _evidence_is_success(event_type, payload, candidate_sha)
    }


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
    table = _contract_table(contract)
    patch_line = str(getattr(getattr(contract, "version", None), "patch_line", "") or "")
    resolved_facts, _error = complete_version_facts(
        repo,
        version_facts,
        patch_line,
        needs_n=operation_plan_needs_n(table, resolved_journey),
    )
    if resolved_facts is None:
        return None
    resolved_facts = _version_facts(contract, resolved_facts)
    plan = build_operation_plan(
        table,
        resolved_journey,
        resolved_facts,
        active_release_branch=active_release_branch(repo, resolved_facts),
    )
    if not isinstance(plan, dict):
        return None
    policy_digest = contract_policy_digest
    if not policy_digest.startswith("sha256:"):
        policy_digest = "sha256:" + policy_digest
    artifact_digest = _artifact_digest(repo, contract, resolved_facts)
    if artifact_digest is None:
        return None
    evidence = evidence_digests(events, candidate_sha)
    if evidence is None:
        return None
    preview = generate_preview(
        candidate_sha,
        {
            "artifact_digest": artifact_digest,
            "evidence_digests": evidence,
            "operation_plan_digest": canonical_digest(plan),
            "contract_policy_digest": policy_digest,
        },
        risks=[],
        plan=plan,
    )
    if known_issues:
        preview["known_issues"] = list(known_issues)
    return preview


def preview_blob_matches(home: Path, preview: dict) -> bool:
    ref = preview.get("blob_ref")
    if not isinstance(ref, str) or not ref:
        return False
    name = ref.rsplit("/", 1)[-1]
    if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
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


def preview_inputs_match(old: dict, current: dict) -> bool:
    old_inputs = {key: value for key, value in old.items() if key != "blob_ref"}
    current_inputs = {key: value for key, value in current.items() if key != "blob_ref"}
    return old_inputs == current_inputs
