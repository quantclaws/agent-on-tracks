"""Runtime-owned authority and plan validation for tag publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import tomllib

from tracks import paths
from tracks.executor.helpers import git
from tracks.executor.host_contract import (
    CANONICAL_CONTRACT_RELPATH,
    load_host_contract,
)
from tracks.executor.publish import plan_operations
from tracks.executor.release_gate import build_operation_plan, compute_preview_digest


def _digest(value: object) -> str:
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _preview_blob_matches(home: Path, preview: dict) -> bool:
    ref = preview.get("blob_ref")
    if not isinstance(ref, str) or not ref:
        return False
    name = ref.rsplit("/", 1)[-1]
    if len(name) != 64 or any(char not in "0123456789abcdef" for char in name):
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
    resolved = dict(facts or {})
    scheme = contract.get("version_scheme", {})
    if not isinstance(scheme, dict):
        return resolved
    for name in ("feature_tag", "patch_line", "prerelease_tag"):
        value = str(scheme.get(name, ""))
        for _ in range(3):
            for key, fact in resolved.items():
                value = value.replace("{" + key + "}", str(fact))
        resolved[name] = value
    return resolved


def _load_contract(repo: Path) -> tuple[bytes, dict] | None:
    path = repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
    try:
        raw = path.read_bytes()
        table = tomllib.loads(raw.decode("utf-8")).get("host-contract")
        load_host_contract(path)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, ValueError):
        return None
    return raw, table if isinstance(table, dict) else {}


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
    journey = str(operation_plan.get("journey") or "")
    current_plan = build_operation_plan(
        contract_table, journey, _facts_with_scheme(version_facts, contract_table)
    )
    if current_plan != operation_plan:
        return None, "operation_plan_changed"
    return current_plan, None


def _valid_tag_ref(repo: Path, target: str) -> bool:
    """Git ref-name semantics for a tag target via local check-ref-format.

    ``check-ref-format`` is a purely local name validator (no network);
    non-zero exit covers ``bad..tag``, trailing dots, ``@{``, forbidden
    characters, and similar invalid tag names. The effects boundary applies
    the same rule at push time; this preflight enforces it for the whole
    batch before any WAL/effect.
    """
    if not isinstance(target, str) or not target:
        return False
    check = git(repo, "check-ref-format", "refs/tags/" + target, check=False)
    return check.returncode == 0


def _operation_records(
    repo: Path, operation_plan: dict, preview_digest: str
) -> tuple[list[dict] | None, str | None]:
    """Validate every declared operation step before any effect.

    Complete ordered multi-tag handling: each step must be a well-formed
    tag (canonical idempotency key per tag) whose target is a valid Git
    ref name. Sanity-checked unsupported kinds (merge/artifact/release,
    ...) fail with the IF reason ``unknown_operation``; undecomposable or
    ref-invalid steps fail ``malformed`` before any WAL/effect.
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
    for record in records:
        if record["operation_kind"] != "tag":
            return None, "unknown_operation"
        if not _valid_tag_ref(repo, record["target"]):
            return None, "malformed"
    return records, None


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
    records, error = _operation_records(repo, operation_plan, preview_digest)
    if error:
        return None, error
    remote = git(repo, "config", "--get", "remote.origin.url", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        return None, "missing_remote"
    return {
        "candidate_sha": candidate_sha,
        "preview_digest": preview_digest,
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
    return result.get("status") if exact else None, confirmed


def _handle_remote(
    authority: dict,
    command_id: str,
    remote: dict,
    push_tag,
    emit,
    fail,
) -> bool:
    """Reconcile one tag against the remote (the remote decides).

    Returns True only for a proven terminal success (``reconciled_skip`` on
    exact remote match, or ``done`` after a push confirmed by remote
    readback); False after emitting the failure so the ordered batch can
    stop on the first failed/conflicting effect.
    """
    candidate_sha = authority["candidate_sha"]
    record = authority["record"]
    key = record["idempotency_key"]
    if not isinstance(remote, dict) or remote.get("error"):
        fail("remote_read_failed", remote)
        return False
    verdict = _remote_verdict(remote, candidate_sha)
    if verdict == "skip":
        emit(
            "publish.executed",
            {
                "idempotency_key": key,
                "candidate_sha": candidate_sha,
                "target": record["target"],
                "status": "reconciled_skip",
                "remote_check": remote,
            },
            command_id=command_id,
        )
        return True
    if verdict == "conflict":
        emit(
            "reconcile_conflict",
            {
                "idempotency_key": key,
                "candidate_sha": candidate_sha,
                "expected": {
                    "object_id": candidate_sha,
                    "candidate_sha": candidate_sha,
                    "target": record["target"],
                },
                "remote": remote,
            },
            command_id=command_id,
        )
        fail("reconcile_conflict", remote)
        return False
    if verdict != "pending":
        fail("remote_read_failed", remote)
        return False
    status, confirmed = _push_confirmation(
        push_tag(authority["remote_url"], record["target"], candidate_sha),
        candidate_sha,
    )
    if status is not None:
        emit(
            "publish.executed",
            {
                "idempotency_key": key,
                "candidate_sha": candidate_sha,
                "target": record["target"],
                "status": status,
                "remote_check": confirmed,
            },
            command_id=command_id,
        )
        return True
    fail("remote_mismatch", confirmed)
    return False


def _keyed_fail(fail, key):
    def keyed(reason, remote_check):
        fail(reason, remote_check, key)

    return keyed


def execute_publish_operations(
    authority: dict,
    command_id: str,
    read_events,
    read_remote,
    push_tag,
    emit,
    fail,
    when: str | None,
) -> None:
    """Run the ordered multi-tag batch after full pre-effect validation.

    Per tag: the same-command WAL record is reused without duplication,
    the effect runs only on remote proof, and the batch stops on the first
    failed/conflicting effect (no aggregate success is ever emitted).
    ``fail`` is invoked as ``fail(reason, remote_check, idempotency_key)``.
    """
    for record in authority["records"]:
        single = {**authority, "record": record}
        succeeded = execute_tag_operation(
            single,
            command_id,
            read_events(),
            read_remote,
            push_tag,
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

    The write-ahead record is planned before the effect and reused (never
    duplicated) by a same-command recovery; the executed event is emitted
    only after actual remote proof. Returns True on terminal success,
    False after the failure emission (stop-on-first-failure signal).
    """
    record = authority["record"]
    planned, plan_error = _planned_payload(events, authority, command_id, when)
    if plan_error:
        fail(plan_error, None)
        return False
    if planned is not None:
        emit("publish.planned", planned, command_id=command_id)
    remote = read_remote(
        authority["remote_url"],
        "tag",
        record["target"],
        expected_object=authority["candidate_sha"],
    )
    return _handle_remote(authority, command_id, remote, push_tag, emit, fail)
