"""Runtime-owned authority and plan validation for tag publication."""

from __future__ import annotations

import glob
import hashlib
import json
import re
from pathlib import Path

import tomllib

from tracks import paths
from tracks.executor.helpers import git
from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    load_host_contract,
)
from tracks.executor.publish import plan_operations
from tracks.executor.release_gate import (
    active_release_branch,
    build_operation_plan,
    complete_version_facts,
    compute_preview_digest,
    expand_version_scheme,
    operation_plan_needs_n,
)
from tracks.executor.sync_product import (
    record_verified,
    remote_branch_tip,
    validate_record,
)


def _digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _preview_blob_matches(home: Path, preview: dict) -> bool:
    name = paths.blob_name_from_ref(preview.get("blob_ref"))
    if name is None:
        return False
    blob_path = paths.blobs_dir(home) / name
    try:
        raw = blob_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != name:
            return False
        blob = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    expected = {key: value for key, value in preview.items() if key != "blob_ref"}
    return blob == expected


def _facts_with_scheme(facts: dict, contract: dict) -> dict:
    return expand_version_scheme(facts, contract.get("version_scheme", {}))


def _load_contract(repo: Path) -> tuple[bytes, dict] | None:
    """Host-declared contract, or the runtime-materialized default when the
    host declares NO ``[host-contract]`` table (IF-HOSTCONTRACT-001).

    Mirrors release_authorization._load_contract: the M-VERIFY producer
    binds the preview's ``contract_policy_digest`` to the materialized
    default for an undeclared host, so the publish-time plan re-validation
    must resolve the SAME bytes or every undeclared host fails with
    ``missing_contract``/``contract_changed`` after a valid preview. A
    DECLARED-but-invalid contract stays authoritative and fails closed."""
    path = repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
    declared = None
    raw = None
    try:
        raw = path.read_bytes()
        declared = tomllib.loads(raw.decode("utf-8")).get("host-contract")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        declared = None
    if declared is None:
        materialized = (
            paths.runtime_dir(paths.tracks_home(repo))
            / "materialized-host-contract.toml"
        )
        try:
            raw = materialized.read_bytes()
            table = tomllib.loads(raw.decode("utf-8")).get("host-contract")
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            return None
        return raw, table if isinstance(table, dict) else {}
    try:
        load_host_contract(path)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, ValueError):
        return None
    return raw, declared if isinstance(declared, dict) else {}


def _latest_preview(events: list, preview_digest: str | None):
    previews = [event for event in events if event.type == "release.previewed"]
    if not previews:
        return None, "missing_preview"
    if preview_digest:
        matching = [
            event for event in previews
            if (event.payload or {}).get("preview_digest") == preview_digest
        ]
        if not matching:
            return None, "missing_preview"
        event = matching[-1]
    else:
        event = previews[-1]
        preview_digest = (event.payload or {}).get("preview_digest")
        if not preview_digest:
            return None, "missing_preview"
    if event is not previews[-1]:
        return None, "preview_stale"
    return event, None


def _candidate_sha(repo: Path, events: list, preview: dict):
    frozen = [event for event in events if event.type == "candidate.frozen"]
    payload = (frozen[-1].payload if frozen else {}) or {}
    candidate_sha = str(payload.get("candidate_sha") or "")
    if not candidate_sha or preview.get("candidate_sha") != candidate_sha:
        return None, "missing_candidate"
    head = git(repo, "rev-parse", "HEAD", check=False)
    if head.returncode != 0 or head.stdout.strip() != candidate_sha:
        return None, "candidate_drift"
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no", check=False)
    if dirty.returncode != 0 or dirty.stdout.strip():
        return None, "candidate_dirty"
    return candidate_sha, None


def _approval_error(events: list, preview_event, preview_digest: str, candidate_sha: str):
    decisions = [
        event for event in events
        if event.type == "release.decided"
        and (event.payload or {}).get("preview_digest") == preview_digest
        and event.seq > preview_event.seq
    ]
    decision = (decisions[-1].payload if decisions else {}) or {}
    if decisions and decision.get("candidate_sha") != candidate_sha:
        return "candidate_mismatch"
    if not decisions or decision.get("action") != "release":
        return "not_approved"
    if decision.get("actor") != "human":
        return "not_approved"
    if any(
        event.seq > preview_event.seq
        and event.type in ("candidate.stale", "evidence.staled")
        and (event.payload or {}).get("candidate_sha") == candidate_sha
        for event in events
    ):
        return "preview_stale"
    return None


def _preview_error(
    repo: Path,
    preview: dict,
    preview_digest: str,
    candidate_sha: str,
    version_facts: dict,
):
    expected = compute_preview_digest(
        candidate_sha,
        preview.get("artifact_digest", ""),
        preview.get("evidence_digests", {}),
        preview.get("operation_plan_digest", ""),
        preview.get("contract_policy_digest", ""),
    )
    if expected != preview_digest:
        return None, "preview_digest_mismatch"
    operation_plan = preview.get("operation_plan")
    if not isinstance(operation_plan, dict):
        return None, "malformed"
    if _digest(operation_plan) != preview.get("operation_plan_digest"):
        return None, "operation_plan_changed"
    contract = _load_contract(repo)
    if contract is None:
        return None, "missing_contract"
    contract_raw, contract_table = contract
    if _digest_bytes(contract_raw) != preview.get("contract_policy_digest"):
        return None, "contract_changed"
    current_plan, error = _revalidated_plan(repo, operation_plan, contract_table, version_facts)
    if error is not None:
        return None, error
    if current_plan != operation_plan:
        return None, "operation_plan_changed"
    return current_plan, None


def _revalidated_plan(
    repo: Path, operation_plan: dict, contract_table: dict, version_facts: dict
):
    """Rebuild the operation plan from the contract; (plan, None) or error."""
    journey = str(operation_plan.get("journey") or "")
    patch_line = str(
        (contract_table.get("version_scheme") or {}).get("patch_line") or ""
    )
    facts, facts_error = complete_version_facts(
        repo,
        version_facts,
        patch_line,
        needs_n=operation_plan_needs_n(contract_table, journey),
    )
    if facts is None:
        return None, facts_error or "release_facts_unresolved"
    resolved_facts = _facts_with_scheme(facts, contract_table)
    sync_products = (
        operation_plan.get("sync_products")
        if isinstance(operation_plan, dict)
        else None
    )
    plan = build_operation_plan(
        contract_table,
        journey,
        resolved_facts,
        active_release_branch=active_release_branch(repo, resolved_facts),
        sync_products=sync_products,
    )
    return plan, None


def _valid_ref(repo: Path, ref: str) -> bool:
    """Git ref-name semantics via local check-ref-format (no network)."""
    if not isinstance(ref, str) or not ref:
        return False
    check = git(repo, "check-ref-format", ref, check=False)
    return check.returncode == 0


def _valid_tag_ref(repo: Path, target: object) -> bool:
    if not isinstance(target, str) or not target:
        return False
    return _valid_ref(repo, "refs/tags/" + target)


def _valid_branch_ref(repo: Path, target: object) -> bool:
    if not isinstance(target, str) or not target:
        return False
    return _valid_ref(repo, "refs/heads/" + target)


_GITHUB_REMOTE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def _repo_id_from_remote(remote_url: object) -> str | None:
    """Resolve a GitHub https remote to ``owner/repo`` (release/artifact)."""
    if not isinstance(remote_url, str):
        return None
    match = _GITHUB_REMOTE.match(remote_url.strip())
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def _resolve_artifact(repo: Path, target: object) -> dict | None:
    """Resolve TARGET (glob/path, repo-root relative) to exactly one file.

    Zero or multiple matches are malformed before any WAL/effect; a single
    match carries its absolute path plus the local sha256/size identity.
    """
    if not isinstance(target, str) or not target.strip():
        return None
    pattern = Path(target)
    if not pattern.is_absolute():
        pattern = Path(repo) / pattern
    matches = [
        path
        for path in sorted(glob.glob(str(pattern), recursive=True))
        if Path(path).is_file()
    ]
    if len(matches) != 1:
        return None
    path = Path(matches[0])
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return {
        "artifact_path": str(path.resolve()),
        "name": path.name,
        "size": len(data),
        "sha256_local": hashlib.sha256(data).hexdigest(),
    }


_OPERATION_KINDS = frozenset({"tag", "merge", "release", "artifact"})


def _operation_records(
    repo: Path,
    operation_plan: dict,
    preview_digest: str,
    candidate_sha: str,
    events: list,
) -> tuple[list[dict] | None, str | None]:
    """Validate every declared operation step before any effect.

    Each step must be a well-formed tag/merge/release/artifact whose target
    passes the kind's rules: tag and release targets are refs/tags names,
    merge targets are refs/heads names, a release target must equal a tag
    step's target (release is bound to a planned tag), and an artifact
    target must resolve to exactly one file under the repo root (bound to
    the plan's single release target so the upload knows its release).
    Unknown kinds fail ``unknown_operation``; malformed steps fail
    ``malformed``. FR-0277-02: a sync-bound merge step additionally proves
    the approved product identity (parents/tree/re-derivation + the verified
    event) -- a preview that claims a product without that proof fails
    closed.
    Records are returned only when every step passes (zero side effects on
    partial validity).
    """
    records = plan_operations(operation_plan, preview_digest)
    if not records:
        steps = (
            (operation_plan or {}).get("steps")
            if isinstance(operation_plan, dict)
            else None
        )
        if isinstance(steps, list) and steps:
            return None, "malformed"
        return None, "unknown_operation"
    tag_targets = {
        record["target"]
        for record in records
        if record["operation_kind"] == "tag"
    }
    artifact_release = _artifact_release_target(records)
    resolved: list[dict] = []
    for record in records:
        validated, error = _validated_record(
            repo, record, tag_targets, artifact_release, candidate_sha, events
        )
        if error is not None:
            return None, error
        resolved.append(validated)
    return resolved, None


def _validated_record(
    repo: Path,
    record: dict,
    tag_targets: set[str],
    artifact_release: str | None,
    candidate_sha: str,
    events: list,
):
    """Kind validation plus, for a sync-bound merge, the product proof."""
    if record["operation_kind"] == "merge" and record.get("product_sha"):
        error = _validate_sync_merge(repo, record, candidate_sha, events)
        if error is not None:
            return None, error
    return _validate_operation_record(repo, record, tag_targets, artifact_release)


def _validate_sync_merge(
    repo: Path, record: dict, candidate_sha: str, events: list
) -> str | None:
    """FR-0277-02: a sync-bound merge needs the exact verified identity.

    The product must re-derive (parents/tree/deterministic rebuild), its
    source candidate must be C, and the matching ``sync_product.verified``
    event must be present after the latest stale barrier -- a preview that
    claims a product without that proof fails closed.
    """
    error = validate_record(repo, record, candidate_sha)
    if error is not None:
        return error
    if not record_verified(events, record, candidate_sha):
        return "sync_product_unverified"
    return None


def _artifact_release_target(records: list[dict]) -> str | None:
    """The plan's single release/tag target bound to artifact uploads."""
    unique_tags = list(dict.fromkeys(
        record["target"] for record in records
        if record["operation_kind"] == "tag"
    ))
    unique_releases = list(dict.fromkeys(
        record["target"] for record in records
        if record["operation_kind"] == "release"
    ))
    if len(unique_releases) == 1:
        return unique_releases[0]
    if not unique_releases and len(unique_tags) == 1:
        return unique_tags[0]
    return None


def _validate_operation_record(
    repo: Path, record: dict, tag_targets: set[str], artifact_release: str | None
):
    """Validate one step against its kind's rules; (record, None) or error."""
    kind = record["operation_kind"]
    target = record["target"]
    if kind not in _OPERATION_KINDS:
        return None, "unknown_operation"
    if kind == "tag":
        if not _valid_tag_ref(repo, target):
            return None, "malformed"
    elif kind == "merge":
        if not _valid_branch_ref(repo, target):
            return None, "malformed"
    elif kind == "release":
        if not _valid_tag_ref(repo, target) or target not in tag_targets:
            return None, "malformed"
    else:
        identity = _resolve_artifact(repo, target)
        if identity is None or artifact_release is None:
            return None, "malformed"
        return {
            **record,
            **identity,
            "release_target": artifact_release,
        }, None
    return record, None


def _sync_product_remote_check(
    repo: Path, records: list[dict]
) -> str | None:
    """FR-0277-02: at publish resolution the remote tip of every approved
    sync target must still be B (not yet executed) or P (already executed).
    Any other tip is stale/conflicting and blocks before any effect; a crash
    resume therefore reuses the same P instead of minting another merge.
    """
    for record in records:
        if record["operation_kind"] != "merge" or not record.get("product_sha"):
            continue
        tip, error = remote_branch_tip(repo, record["target"])
        if error is not None:
            return error
        if tip is not None and tip not in (
            record["baseline_sha"],
            record["product_sha"],
        ):
            return "sync_product_stale"
    return None


def resolve_publish_authority(
    repo: Path,
    events: list,
    preview_digest: str | None,
    version_facts: dict,
    store_home: Path,
) -> tuple[dict | None, str | None]:
    """Resolve current preview, candidate, contract and human approval.

    Returns an ordered list of tag records (all operations validated before
    any effect); ``record`` remains for single-tag call-site compatibility.
    """
    preview_event, error = _latest_preview(events, preview_digest)
    if error:
        return None, error
    preview = dict(preview_event.payload or {})
    preview_digest = str(preview.get("preview_digest") or "")
    if not _preview_blob_matches(store_home, preview):
        return None, "preview_blob_mismatch"
    candidate_sha, error = _candidate_sha(repo, events, preview)
    if error:
        return None, error
    error = _approval_error(events, preview_event, preview_digest, candidate_sha)
    if error:
        return None, error
    operation_plan, error = _preview_error(
        repo, preview, preview_digest, candidate_sha, version_facts
    )
    if error:
        return None, error
    records, error = _operation_records(
        repo, operation_plan, preview_digest, candidate_sha, events
    )
    if error:
        return None, error
    error = _sync_product_remote_check(repo, records)
    if error:
        return None, error
    remote = git(repo, "config", "--get", "remote.origin.url", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        return None, "missing_remote"
    return {
        "candidate_sha": candidate_sha,
        "preview_digest": preview_digest,
        "journey": str(operation_plan.get("journey") or ""),
        "record": records[0],
        "records": records,
        "remote_url": remote.stdout.strip(),
    }, None


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _planned_payload(events, authority: dict, command_id: str, when: str | None):
    record = authority["record"]
    key = record["idempotency_key"]
    planned = [
        event
        for event in events
        if event.type == "publish.planned"
        and (event.payload or {}).get("idempotency_key") == key
    ]
    for event in planned:
        payload = event.payload or {}
        same_operation = (
            event.command_id == command_id
            and payload.get("candidate_sha") == authority["candidate_sha"]
            and payload.get("target") == record["target"]
            and payload.get("preview_digest") == authority["preview_digest"]
            and payload.get("operation_kind") == record["operation_kind"]
            and payload.get("product_sha") == record.get("product_sha")
            and payload.get("baseline_sha") == record.get("baseline_sha")
        )
        if not same_operation:
            return None, "planned_conflict"
    if planned:
        return None, None
    return {
        **record,
        "candidate_sha": authority["candidate_sha"],
        "when": when,
    }, None


def _remote_verdict(remote: dict, candidate_sha: str) -> str:
    if remote.get("exists") is True:
        exact = (
            remote.get("matches") is True
            and remote.get("object_id") == candidate_sha
        )
        return "skip" if exact else "conflict"
    if remote.get("exists") is False:
        return "pending"
    return "invalid"


def _push_confirmation(result: dict, candidate_sha: str):
    if not isinstance(result, dict):
        return None, {}
    confirmed = result.get("remote_check") or {}
    object_id = result.get("object_id") or confirmed.get("object_id")
    exact = (
        result.get("status") in ("done", "reconciled_skip")
        and object_id == candidate_sha
        and confirmed.get("exists") is True
        and confirmed.get("matches") is True
    )
    if exact:
        return result.get("status"), confirmed
    return None, confirmed


def _operation_extras(record: dict, result: dict | None = None) -> dict:
    """Kind-specific audit fields carried by planned/executed payloads."""
    result = result or {}
    extras: dict = {}
    if record["operation_kind"] == "artifact":
        extras["name"] = record.get("name")
        extras["size"] = record.get("size")
        extras["sha256_local"] = record.get("sha256_local")
        extras["artifact_path"] = record.get("artifact_path")
        if result.get("remote_digest") is not None:
            extras["remote_digest"] = result["remote_digest"]
    if record["operation_kind"] == "merge":
        # FR-0277-02: a sync-bound merge carries the complete approved product
        # identity into planned/executed (WAL + audit + recovery reuse).
        from tracks.executor.sync_product import SYNC_PRODUCT_FIELDS

        for field in SYNC_PRODUCT_FIELDS:
            if record.get(field) is not None:
                extras[field] = record[field]
        if result.get("merge_mode"):
            extras["merge_mode"] = result["merge_mode"]
        if result.get("merge_commit") is not None:
            extras["merge_commit"] = result["merge_commit"]
    if result.get("release_id") is not None:
        extras["release_id"] = result["release_id"]
    return extras


def _executed_payload(
    authority: dict,
    record: dict,
    status: str,
    remote_check: dict,
    result: dict | None = None,
) -> dict:
    payload = {
        "idempotency_key": record["idempotency_key"],
        "candidate_sha": authority["candidate_sha"],
        "target": record["target"],
        "status": status,
        "remote_check": remote_check,
    }
    payload.update(_operation_extras(record, result))
    return payload


def _emit_conflict(authority: dict, command_id: str, remote: dict, emit) -> None:
    record = authority["record"]
    candidate_sha = authority["candidate_sha"]
    expected: dict = {"candidate_sha": candidate_sha, "target": record["target"]}
    if record["operation_kind"] in ("tag", "merge"):
        expected["object_id"] = record.get("product_sha") or candidate_sha
    if record.get("product_sha"):
        expected["baseline_sha"] = record["baseline_sha"]
        expected["product_sha"] = record["product_sha"]
        expected["source_candidate_sha"] = record["source_candidate_sha"]
    emit(
        "reconcile_conflict",
        {
            "idempotency_key": record["idempotency_key"],
            "candidate_sha": candidate_sha,
            "expected": expected,
            "remote": remote,
        },
        command_id=command_id,
    )


def _sync_confirmation(result: dict, product_sha: str):
    """Exact-object readback: only the approved P confirms the sync."""
    if not isinstance(result, dict):
        return None, {}
    confirmed = result.get("remote_check") or {}
    object_id = result.get("object_id") or confirmed.get("object_id")
    exact = (
        result.get("status") in ("done", "reconciled_skip")
        and object_id == product_sha
        and confirmed.get("exists") is True
        and confirmed.get("matches") is True
    )
    return (result.get("status"), confirmed) if exact else (None, confirmed)


def _handle_sync_merge(
    authority: dict,
    command_id: str,
    remote: dict,
    push_sync,
    emit,
    fail,
) -> bool:
    """FR-0277-02: publish exactly one approved sync product.

    The remote decides with exact-object semantics: tip == P is a
    reconciled_skip (a crash after the push resumes without a new merge),
    tip == B executes the atomic expected-old update, anything else is a
    conflict/``sync_product_stale`` -- never a re-created product.
    """
    record = authority["record"]
    product_sha = record["product_sha"]
    baseline_sha = record["baseline_sha"]
    if not isinstance(remote, dict) or remote.get("error"):
        fail("remote_read_failed", remote)
        return False
    if remote.get("exists") is False:
        fail("branch_missing", remote)
        return False
    tip = remote.get("object_id")
    if tip == product_sha:
        emit(
            "publish.executed",
            _executed_payload(authority, record, "reconciled_skip", remote),
            command_id=command_id,
        )
        return True
    if tip != baseline_sha:
        _emit_conflict(authority, command_id, remote, emit)
        fail("sync_product_stale", remote)
        return False
    if push_sync is None:
        fail("operation_failed", remote)
        return False
    result = push_sync(
        authority["remote_url"],
        record["target"],
        product_sha,
        baseline_sha=baseline_sha,
        candidate_sha=record["source_candidate_sha"],
    )
    if isinstance(result, dict) and result.get("status") == "conflict":
        confirmed = result.get("remote_check") or {}
        _emit_conflict(authority, command_id, confirmed, emit)
        fail("sync_product_stale", confirmed)
        return False
    status, confirmed = _sync_confirmation(result, product_sha)
    if status is not None:
        emit(
            "publish.executed",
            _executed_payload(authority, record, status, confirmed, result),
            command_id=command_id,
        )
        return True
    reason = "remote_mismatch"
    if isinstance(result, dict) and result.get("reason"):
        reason = str(result["reason"])
    fail(reason, confirmed)
    return False


def _handle_push_result(
    authority: dict, command_id: str, result: dict, emit, fail
) -> bool:
    candidate_sha = authority["candidate_sha"]
    record = authority["record"]
    if isinstance(result, dict) and result.get("status") == "conflict":
        remote = result.get("remote_check") or {}
        _emit_conflict(authority, command_id, remote, emit)
        fail("reconcile_conflict", remote)
        return False
    status, confirmed = _push_confirmation(result, candidate_sha)
    if status is not None:
        emit(
            "publish.executed",
            _executed_payload(authority, record, status, confirmed, result),
            command_id=command_id,
        )
        return True
    reason = "remote_mismatch"
    if isinstance(result, dict) and result.get("reason"):
        reason = str(result["reason"])
    fail(reason, confirmed)
    return False


def _handle_remote(
    authority: dict,
    command_id: str,
    remote: dict,
    push,
    emit,
    fail,
    *,
    allow_ff: bool,
) -> bool:
    """Reconcile one ref effect against the remote (the remote decides).

    A proven exact match is ``reconciled_skip``; a divergent tag is a
    conflict without touching the remote. A merge may still fast-forward a
    divergent (but ancestor) remote tip, so its mismatch delegates to the
    effect, which decides done/conflict/failed. Returns True only on a
    proven terminal success.
    """
    candidate_sha = authority["candidate_sha"]
    record = authority["record"]
    if not isinstance(remote, dict) or remote.get("error"):
        fail("remote_read_failed", remote)
        return False
    verdict = _remote_verdict(remote, candidate_sha)
    if verdict == "skip":
        emit(
            "publish.executed",
            _executed_payload(authority, record, "reconciled_skip", remote),
            command_id=command_id,
        )
        return True
    if verdict == "conflict" and not allow_ff:
        _emit_conflict(authority, command_id, remote, emit)
        fail("reconcile_conflict", remote)
        return False
    if verdict == "invalid":
        fail("remote_read_failed", remote)
        return False
    result = push(authority["remote_url"], record["target"], candidate_sha)
    return _handle_push_result(authority, command_id, result, emit, fail)


def _handle_api_effect(
    authority: dict, command_id: str, result: dict, emit, fail
) -> bool:
    """Map a release/artifact effect outcome onto the publish event family.

    The effect already read the remote back; a matching ``done`` /
    ``reconciled_skip`` is terminal, ``conflict`` is a reconcile_conflict
    audit plus failure, anything else fails with the effect's reason.
    """
    if not isinstance(result, dict):
        fail("operation_failed", None)
        return False
    status = result.get("status")
    remote = result.get("remote_check") or {}
    record = authority["record"]
    if status in ("done", "reconciled_skip"):
        emit(
            "publish.executed",
            _executed_payload(authority, record, status, remote, result),
            command_id=command_id,
        )
        return True
    if status == "conflict":
        _emit_conflict(authority, command_id, remote, emit)
        fail("reconcile_conflict", remote)
        return False
    fail(str(result.get("reason") or "operation_failed"), remote)
    return False


def _release_notes(authority: dict, record: dict) -> str:
    """Deterministic, auditable release body (candidate + preview + key)."""
    return (
        "Tracks release\n"
        f"candidate_sha: {authority['candidate_sha']}\n"
        f"preview_digest: {authority['preview_digest']}\n"
        f"journey: {authority.get('journey', '')}\n"
        f"idempotency_key: {record['idempotency_key']}\n"
    )


def _keyed_fail(fail, key):
    def keyed(reason, remote_check):
        fail(reason, remote_check, key)

    return keyed


def _dispatch_merge(
    authority: dict, command_id: str, effects: dict, emit, fail
) -> bool:
    """Merge dispatch: approved sync product vs the generic FF path."""
    record = authority["record"]
    product_sha = record.get("product_sha")
    remote = effects["read_remote"](
        authority["remote_url"],
        "merge",
        record["target"],
        expected_object=product_sha or authority["candidate_sha"],
    )
    if product_sha:
        # FR-0277-02 strict path: publish the approved prepared product with
        # the expected-old ref update; the runtime never builds or verifies a
        # product here.
        return _handle_sync_merge(
            authority, command_id, remote, effects.get("push_sync"), emit, fail
        )

    def push_merge_adapter(remote_url, target, candidate_sha):
        return effects["push_merge"](remote_url, candidate_sha, target)

    return _handle_remote(
        authority,
        command_id,
        remote,
        push_merge_adapter,
        emit,
        fail,
        allow_ff=True,
    )


def _execute_record(
    authority: dict,
    command_id: str,
    events: list,
    effects: dict,
    emit,
    fail,
    when: str | None,
) -> bool:
    """Run one already-authorized record with WAL and kind dispatch."""
    record = authority["record"]
    planned, plan_error = _planned_payload(events, authority, command_id, when)
    if plan_error:
        fail(plan_error, None)
        return False
    if planned is not None:
        emit("publish.planned", planned, command_id=command_id)
    kind = record["operation_kind"]
    if kind == "tag":
        remote = effects["read_remote"](
            authority["remote_url"],
            "tag",
            record["target"],
            expected_object=authority["candidate_sha"],
        )
        return _handle_remote(
            authority,
            command_id,
            remote,
            effects["push_tag"],
            emit,
            fail,
            allow_ff=False,
        )
    if kind == "merge":
        return _dispatch_merge(authority, command_id, effects, emit, fail)
    repo_id = _repo_id_from_remote(authority["remote_url"])
    if repo_id is None:
        fail("unsupported_remote", None)
        return False
    if kind == "release":
        result = effects["create_release"](
            repo_id,
            record["target"],
            _release_notes(authority, record),
            False,
            authority["candidate_sha"],
        )
    elif kind == "artifact":
        result = effects["upload_artifact"](
            repo_id, record["release_target"], record["artifact_path"]
        )
    else:
        fail("unknown_operation", None)
        return False
    return _handle_api_effect(authority, command_id, result, emit, fail)


def execute_publish_operations(
    authority: dict,
    command_id: str,
    read_events,
    effects: dict,
    emit,
    fail,
    when: str | None,
) -> None:
    """Run the ordered batch after full pre-effect validation.

    ``effects`` maps ``read_remote``/``push_tag``/``push_merge``/
    ``create_release``/``upload_artifact`` to the Runtime-only effects
    boundary. Per operation: the same-command WAL record is reused without
    duplication, the effect runs only behind remote proof where the remote
    can prove it, and the batch stops on the first failed/conflicting
    effect (no aggregate success is ever emitted). ``fail`` is invoked as
    ``fail(reason, remote_check, idempotency_key)``.
    """
    for record in authority["records"]:
        single = {**authority, "record": record}
        succeeded = _execute_record(
            single,
            command_id,
            read_events(),
            effects,
            emit,
            _keyed_fail(fail, record["idempotency_key"]),
            when,
        )
        if not succeeded:
            break


def execute_tag_operation(
    authority: dict,
    command_id: str,
    events: list,
    read_remote,
    push_tag,
    emit,
    fail,
    when: str | None,
) -> bool:
    """Run one already-authorized tag with WAL and remote reconciliation.

    Kept for single-tag call-site compatibility; the ordered batch handler
    dispatches by operation kind through ``execute_publish_operations``.
    """
    effects = {
        "read_remote": read_remote,
        "push_tag": push_tag,
        "push_merge": None,
        "push_sync": None,
        "create_release": None,
        "upload_artifact": None,
    }
    return _execute_record(
        authority, command_id, events, effects, emit, fail, when
    )
