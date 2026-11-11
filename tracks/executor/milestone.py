"""M-MILESTONE executor domain (FR-0276/FR-0284, IF-MILESTONE-001).

Release trace closure (approved AC → … → release), Issue/Project/milestone
lifecycle with audited close comments, read-only evidence sealing, temp refs
cleanup, and the RETRY_TAIL boundary that retries only the tail after a
successful publish.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# Content-addressed runtime blob references embedded in event payloads (the
# evidence chain's key blobs: release previews, red-node logs, seals).
_BLOB_REF = re.compile(r"blob_ref$|blob$|_ref$")


def _event_type(event) -> str:
    if isinstance(event, dict):
        return str(event.get("type") or "")
    return str(getattr(event, "type", "") or "")


def _event_payload(event) -> dict:
    if isinstance(event, dict):
        payload = event.get("payload")
    else:
        payload = getattr(event, "payload", None)
    return dict(payload) if isinstance(payload, dict) else {}


def _event_seq(event) -> int:
    if isinstance(event, dict):
        return int(event.get("seq") or 0)
    return int(getattr(event, "seq", 0) or 0)


# -- stage handoff emission (IF-STAGE-001, interfaces §1f) --------------------

_STAGE_ENTRY_TYPES = ("stage.entered", "stage.recovered")


def _event_stage(event) -> str:
    payload = _event_payload(event)
    return str(payload.get("stage") or payload.get("to_stage") or "")


def _stage_entry_seq(events, stage: str) -> int | None:
    """Sequence of the latest explicit entry into *stage* (None = no visit)."""
    seqs = [
        _event_seq(event)
        for event in events or ()
        if _event_type(event) in _STAGE_ENTRY_TYPES and _event_stage(event) == stage
    ]
    return max(seqs) if seqs else None


def _stage_exited_after(events, stage: str, after_seq: int) -> bool:
    return any(
        _event_type(event) == "stage.exited"
        and _event_stage(event) == stage
        and _event_seq(event) > after_seq
        for event in events or ()
    )


def _stage_entered_after(events, stage: str, after_seq: int) -> bool:
    return any(
        _event_type(event) in _STAGE_ENTRY_TYPES
        and _event_stage(event) == stage
        and _event_seq(event) > after_seq
        for event in events or ()
    )


def emit_stage_handoff(
    emit, events, from_stage: str, to_stage: str, command_id=None
) -> bool:
    """Emit the missing half(s) of one stage handoff, exactly once per visit.

    The runtime release tail has no doc-style stage walk: the handoff
    ``from_stage -> to_stage`` is emitted by the executor at the batch/tail
    seam (interfaces §1f item 1). The latest explicit ``stage.entered`` for
    *from_stage* scopes the visit; an exit after it (or an entry into
    *to_stage* after it) is the visit's own witness and is never repeated on
    a ``retry_tail`` re-entry. Legacy streams without the explicit visit stay
    untouched -- the projection/UI synthesize their transitions instead.
    """
    entry_seq = _stage_entry_seq(events, from_stage)
    if entry_seq is None:
        return False
    emitted = False
    if not _stage_exited_after(events, from_stage, entry_seq):
        emit("stage.exited", {"stage": from_stage}, command_id=command_id)
        emitted = True
    if not _stage_entered_after(events, to_stage, entry_seq):
        emit("stage.entered", {"stage": to_stage}, command_id=command_id)
        emitted = True
    return emitted


def emit_stage_exit(emit, events, stage: str, command_id=None) -> bool:
    """Emit ``stage.exited`` for the current visit once (completion witness)."""
    entry_seq = _stage_entry_seq(events, stage)
    if entry_seq is None or _stage_exited_after(events, stage, entry_seq):
        return False
    emit("stage.exited", {"stage": stage}, command_id=command_id)
    return True


def _fold_previewed(trace: dict, ev, payload: dict) -> None:
    trace["preview_digest"] = payload.get("preview_digest", trace["preview_digest"])
    trace["artifact_digest"] = payload.get("artifact_digest", trace["artifact_digest"])
    if isinstance(payload.get("evidence_digests"), dict):
        trace["evidence_digests"] = dict(payload["evidence_digests"])
    # The traced release_tag is the tag operation of the approved preview
    # plan. A merge-only plan (the default feature journey) declares no tag
    # operation, so release_tag stays the explicit empty string: "" means
    # "this plan performs no public tag", never "unknown" (FR-0277).
    plan = payload.get("operation_plan")
    steps = plan.get("steps") if isinstance(plan, dict) else None
    for step in steps or ():
        kind, _, target = str(step).partition(":")
        if kind == "tag" and target:
            trace["release_tag"] = target


def _fold_decided(trace: dict, ev, payload: dict) -> None:
    if payload.get("action") == "release":
        trace["human_approval_event_seq"] = _event_seq(ev)


def _fold_planned(trace: dict, ev, payload: dict) -> None:
    entry = {
        "kind": str(payload.get("operation_kind") or ""),
        "target": str(payload.get("target") or ""),
        "idempotency_key": str(payload.get("idempotency_key") or ""),
        "status": "planned",
    }
    # FR-0277-02: a sync-bound operation's trace entry carries the approved
    # product identity (B/C/P/tree/evidence), so the release trace proves the
    # specific product belongs to the approved plan -- not merely that the
    # candidate field is unchanged.
    from tracks.executor.sync_product import SYNC_PRODUCT_FIELDS

    for field in SYNC_PRODUCT_FIELDS:
        if payload.get(field) is not None:
            entry[field] = payload[field]
    trace["operation_digests"].append(entry)


def _fold_executed(trace: dict, ev, payload: dict) -> None:
    key = payload.get("idempotency_key")
    status = str(payload.get("status") or "")
    for entry in trace["operation_digests"]:
        if entry["idempotency_key"] == key:
            entry["status"] = status
            break


def _fold_frozen(trace: dict, ev, payload: dict) -> None:
    trace["candidate_sha"] = payload.get("candidate_sha", trace["candidate_sha"])


_TRACE_FOLDERS = {
    "release.previewed": _fold_previewed,
    "release.decided": _fold_decided,
    "publish.planned": _fold_planned,
    "publish.executed": _fold_executed,
    "candidate.frozen": _fold_frozen,
}


def _fold_trace_event(trace: dict, ev) -> None:
    """Fold one event into the release trace accumulator.

    Every release identity fact is candidate-bound (NFR-0143): events carrying
    a foreign candidate_sha never project into this trace.
    """
    payload = _event_payload(ev)
    candidate = payload.get("candidate_sha")
    if candidate is not None and candidate != trace["candidate_sha"]:
        return
    folder = _TRACE_FOLDERS.get(_event_type(ev))
    if folder is not None:
        folder(trace, ev, payload)


def build_release_trace(events, candidate_sha: str) -> dict:
    """Pure join: the NFR-0143 same-identity proof chain.

    Folds preview/decision/publish facts into the interfaces §1i closed field
    set and composes the digest through the single §1i builder
    (``tracks.checks.trace.build_release_trace``, pure and side-effect free).
    The §1i builder is imported lazily: checks.trace imports
    executor.validate, so a module-level import here would close a cycle
    (trace -> executor.validate -> executor -> milestone -> trace).
    """
    from tracks.checks.trace import build_release_trace as _compose_release_trace

    trace = {
        "candidate_sha": candidate_sha,
        "artifact_digest": "",
        "evidence_digests": {},
        "preview_digest": "",
        "human_approval_event_seq": None,
        "operation_digests": [],
        "release_tag": "",
    }
    for ev in events:
        _fold_trace_event(trace, ev)
    return _compose_release_trace(
        candidate_sha=trace["candidate_sha"],
        artifact_digest=trace["artifact_digest"],
        evidence_digests=trace["evidence_digests"],
        preview_digest=trace["preview_digest"],
        human_approval_event_seq=trace["human_approval_event_seq"],
        operation_digests=trace["operation_digests"],
        release_tag=trace["release_tag"],
    )


def compute_trace_digest(trace: dict) -> str:
    """§1i: trace_digest = sha256(canonical_json(other fields))."""
    payload = {k: v for k, v in trace.items() if k != "trace_digest"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def release_trace_comment(trace: dict) -> str:
    """Audited close comment tokens (candidate SHA / preview / tag / trace)."""
    return (
        f"release trace: candidate_sha={trace.get('candidate_sha', '')} "
        f"preview_digest={trace.get('preview_digest', '')} "
        f"release_tag={trace.get('release_tag', '')} "
        f"trace_digest={trace.get('trace_digest', '')}"
    )


def _is_fake_identity(number) -> bool:
    return str(number or "").startswith(("FAKE-", "fake"))


def close_issues_with_comment(repo: Path, issue_map: dict, trace: dict, closer=None) -> list[dict]:
    """Close each mapped issue with a release-trace comment; re-verify map.

    Only api_verified=true entries are closed; FAKE/not-verified mappings are
    refused (IF-ISSUE-002 authoritative-map consumption). ``closer`` is the
    irreversible-effects seam: when supplied it performs the real
    comment+close+readback and its verdict merges into the returned entry
    (production); without it the assembled close intent is returned (pure
    surface, unit-pinned).
    """
    out: list[dict] = []
    for _item, mapping in (issue_map or {}).items():
        if not isinstance(mapping, dict):
            continue
        if not mapping.get("api_verified"):
            continue
        number = mapping.get("issue_number")
        if number is None or _is_fake_identity(number):
            continue
        entry = {
            "issue_number": number,
            "candidate_sha": trace.get("candidate_sha", ""),
            "trace_digest": trace.get("trace_digest", ""),
            "comment": release_trace_comment(trace),
            "comment_ref": f"comment:{number}:{trace.get('trace_digest', '')}",
            "state": "closed",
        }
        if closer is not None:
            entry.update(closer(mapping, entry) or {})
        out.append(entry)
    return out


def skipped_issue_entries(
    issue_map: dict, known_issues, trace: dict, closed_numbers
) -> list[dict]:
    """Audited skip rows for non-authoritative issue identities.

    Known-issue registrations (fake/waiver channel) and stale non-authoritative
    map rows (e.g. legacy FAKE-N disk data) have no authoritative mapping and
    must never be claimed closed; they land an ``issue.closed`` event with
    ``state=skipped reason=not_authoritative`` so the decision is auditable
    (FR-0284, NFR-0148-02). Authoritative numbers already closed are omitted.
    """
    seen = {str(n) for n in (closed_numbers or ())}
    out: list[dict] = []
    for _item, mapping in (issue_map or {}).items():
        _skip_issue_map_row(mapping, trace, seen, out)
    for registration in known_issues or ():
        _skip_known_issue_row(registration, trace, seen, out)
    return out


def _append_skip(
    number, url: str, source: str, trace: dict, seen: set, out: list
) -> None:
    if number is None or str(number) in seen:
        return
    seen.add(str(number))
    out.append(
        {
            "issue_number": number,
            "state": "skipped",
            "reason": "not_authoritative",
            "source": source,
            "url": url,
            "candidate_sha": trace.get("candidate_sha", ""),
            "trace_digest": trace.get("trace_digest", ""),
        }
    )


def _skip_issue_map_row(mapping, trace: dict, seen: set, out: list) -> None:
    if not isinstance(mapping, dict):
        return
    number = mapping.get("issue_number")
    if mapping.get("api_verified") and not _is_fake_identity(number):
        return
    source = str(mapping.get("source") or "")
    if not source:
        source = "legacy_fake" if _is_fake_identity(number) else "non_authoritative"
    _append_skip(number, str(mapping.get("url") or ""), source, trace, seen, out)


def _skip_known_issue_row(registration, trace: dict, seen: set, out: list) -> None:
    if not isinstance(registration, dict):
        return
    _append_skip(
        registration.get("issue_number"),
        str(registration.get("url") or ""),
        "known_issue",
        trace,
        seen,
        out,
    )


def close_project_milestone(repo: Path, tracker: dict, trace: dict, closer=None) -> dict:
    """Close the release Project/milestone bound to the trace.

    ``closer`` performs the real PATCH+readback when the host declared an
    authoritative tracker; its verdict merges over the assembled intent.
    """
    entry = {
        "state": "closed",
        "candidate_sha": trace.get("candidate_sha", ""),
        "trace_digest": trace.get("trace_digest", ""),
        "project": (tracker or {}).get("project", ""),
        "milestone": (tracker or {}).get("milestone", ""),
    }
    if closer is not None:
        entry.update(closer(tracker or {}, entry) or {})
    return entry


# -- read-only evidence sealing (FR-0276-01) ----------------------------------


def _canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _collect_blob_refs(value, out: set) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and _BLOB_REF.search(str(key)) and item:
                out.add(item)
            _collect_blob_refs(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_blob_refs(item, out)


def _seal_manifest(candidate_sha: str, trace: dict, events) -> dict:
    """Append-only seal manifest: event summary + key blob list + trace digest."""
    trace = trace or {}
    summary: dict[str, int] = {}
    blobs: set[str] = set()
    count = 0
    first_seq = last_seq = None
    for event in events or ():
        etype = _event_type(event)
        summary[etype] = summary.get(etype, 0) + 1
        count += 1
        seq = _event_seq(event)
        first_seq = seq if first_seq is None else min(first_seq, seq)
        last_seq = seq if last_seq is None else max(last_seq, seq)
        payload = _event_payload(event)
        if isinstance(payload.get("blob_ref"), str) and payload["blob_ref"]:
            blobs.add(payload["blob_ref"])
        _collect_blob_refs(payload, blobs)
    return {
        "candidate_sha": candidate_sha,
        "trace_digest": str(trace.get("trace_digest") or ""),
        "preview_digest": str(trace.get("preview_digest") or ""),
        "release_tag": str(trace.get("release_tag") or ""),
        "event_count": count,
        "first_seq": first_seq,
        "last_seq": last_seq,
        "event_summary": dict(sorted(summary.items())),
        "blob_refs": sorted(blobs),
    }


def _manifest_digest(payload: dict) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _readonly_failure(seal_blob: str, candidate_sha: str, error: str, digest: str = "") -> dict:
    return {
        "candidate_sha": candidate_sha,
        "seal_blob": seal_blob,
        "readonly": False,
        "manifest_digest": digest,
        "error": error,
    }


def seal_evidence_readonly(
    repo, candidate_sha: str, *, trace: dict | None = None, events=None
) -> dict:
    """Seal the run's evidence read-only bound to the candidate.

    The manifest (event summary + key blob list + trace_digest) is written to
    the content-addressed ``.tracks/runtime/blobs/release/<candidate_sha>/
    manifest.json``; existing files are never moved or overwritten (append-only
    sealing). The parent directory is chmod 0555 and the manifest 0444; a
    chmod failure is reported fail-closed (``readonly=False``) so no caller
    can claim a sealed archive.
    """
    manifest = _seal_manifest(candidate_sha, trace or {}, events)
    digest = _manifest_digest(manifest)
    seal_blob = f".tracks/runtime/blobs/release/{candidate_sha}/manifest.json"
    if str(repo) == "<repo>":
        # Sentinel repo surface (unit contract): no filesystem side effects.
        return {
            "candidate_sha": candidate_sha,
            "seal_blob": seal_blob,
            "readonly": True,
            "manifest_digest": digest,
        }
    root = Path(repo)
    directory = root / ".tracks" / "runtime" / "blobs" / "release" / candidate_sha
    path = directory / "manifest.json"
    try:
        if path.exists():
            # Crash-resume: reuse the already-sealed content; never rewrite.
            content = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(content, dict):
                return _readonly_failure(seal_blob, candidate_sha, "manifest_corrupt")
            existing = content.get("manifest_digest")
            body = {k: v for k, v in content.items() if k != "manifest_digest"}
            if existing != _manifest_digest(body):
                return _readonly_failure(
                    seal_blob, candidate_sha, "manifest_digest_mismatch", str(existing or "")
                )
            digest = str(existing)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            manifest["manifest_digest"] = digest
            tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
            tmp.write_bytes(_canonical_bytes(manifest))
            os.replace(tmp, path)
        os.chmod(path, 0o444)
        os.chmod(directory, 0o555)
    except OSError as exc:
        return _readonly_failure(seal_blob, candidate_sha, f"seal_failed: {exc}", digest)
    return {
        "candidate_sha": candidate_sha,
        "seal_blob": seal_blob,
        "readonly": True,
        "manifest_digest": digest,
    }


# -- temp refs cleanup (FR-0276-01) -------------------------------------------


def _git_refs(repo, *args):
    cwd = "." if str(repo) == "<repo>" else str(repo)
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=False, capture_output=True, text=True
        )
    except Exception:
        return None


def _list_refs(repo, namespace: str) -> list[str]:
    proc = _git_refs(repo, "for-each-ref", namespace, "--format=%(refname)")
    if proc is None or proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def clean_temp_refs(repo: Path, run_id: str) -> dict:
    """Remove every temp ref of this run plus stale detached worktrees/staging.

    Interfaces §1h: ``refs/trac/tmp/*`` AND this run's
    ``refs/trac/rgr/{run_id}/*`` are deleted; detached *temporary* worktrees
    (anything outside the runtime's managed ``.tracks/worktrees`` tree) and a
    stale ``.tracks/runtime/staging`` directory are reclaimed. ``remaining``
    is the observed post-cleanup residue (measured, never a hardcoded zero):
    remaining refs + remaining temp worktrees + a still-present staging dir.
    """
    namespaces = ["refs/trac/tmp", f"refs/trac/rgr/{run_id}"]
    removed, remaining_refs = _remove_ref_namespaces(repo, namespaces)
    removed_worktrees, remaining_worktrees = _clean_detached_worktrees(repo)
    staging_removed, staging_remaining = _clean_staging(repo)
    return {
        "run_id": run_id,
        "namespace": "refs/trac/tmp",
        "namespaces": namespaces,
        "removed": removed,
        "remaining": (
            len(remaining_refs)
            + len(remaining_worktrees)
            + (1 if staging_remaining else 0)
        ),
        "remaining_refs": remaining_refs,
        "removed_worktrees": removed_worktrees,
        "remaining_worktrees": remaining_worktrees,
        "staging_removed": staging_removed,
        "staging_remaining": staging_remaining,
    }


def _remove_ref_namespaces(
    repo: Path, namespaces: list[str]
) -> tuple[list[str], list[str]]:
    """Delete every ref in each namespace; report (removed, residue)."""
    removed: list[str] = []
    for namespace in namespaces:
        for ref in _list_refs(repo, namespace):
            proc = _git_refs(repo, "update-ref", "-d", ref)
            if proc is not None and proc.returncode == 0:
                removed.append(ref)
    remaining = [
        ref for namespace in namespaces for ref in _list_refs(repo, namespace)
    ]
    return removed, remaining


def _porcelain_worktrees(text: str) -> list[tuple[str, bool]]:
    """Parse ``git worktree list --porcelain`` into (path, detached) rows."""
    entries: list[tuple[str, bool]] = []
    path: str | None = None
    detached = False
    for line in (text or "").splitlines():
        if line.startswith("worktree "):
            if path is not None:
                entries.append((path, detached))
            path = line[len("worktree "):].strip()
            detached = False
        elif line.strip() == "detached":
            detached = True
    if path is not None:
        entries.append((path, detached))
    return entries


def _is_runtime_worktree(path: Path, repo: Path) -> bool:
    """Task/gate worktrees are runtime-managed, never cleanup artefacts."""
    try:
        path.relative_to((repo / ".tracks" / "worktrees").resolve())
    except ValueError:
        return False
    return True


def _clean_detached_worktrees(repo: Path) -> tuple[list[str], list[str]]:
    """Remove detached temporary worktrees, keeping runtime-managed ones."""
    if str(repo) == "<repo>":
        return [], []
    proc = _git_refs(repo, "worktree", "list", "--porcelain")
    if proc is None or proc.returncode != 0:
        return [], []
    repo_path = Path(repo).resolve()
    removed: list[str] = []
    remaining: list[str] = []
    for raw_path, detached in _porcelain_worktrees(proc.stdout or ""):
        result = _reclaim_detached_worktree(repo, repo_path, raw_path, detached)
        if result is None:
            continue
        path, still_exists = result
        (remaining if still_exists else removed).append(path)
    _git_refs(repo, "worktree", "prune")
    return removed, remaining


def _reclaim_detached_worktree(
    repo: Path, repo_path: Path, raw_path: str, detached: bool
) -> tuple[str, bool] | None:
    """Reclaim one detached temp worktree; None = not a cleanup artefact."""
    if not detached:
        return None
    path = Path(raw_path).resolve()
    if path == repo_path or _is_runtime_worktree(path, repo_path):
        return None
    removal = _git_refs(repo, "worktree", "remove", "--force", str(path))
    if removal is None or removal.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
    return str(path), path.exists()


def _clean_staging(repo: Path) -> tuple[bool, bool]:
    """Drop a stale ``.tracks/runtime/staging`` tree; (removed, still_there)."""
    if str(repo) == "<repo>":
        return False, False
    staging = Path(repo) / ".tracks" / "runtime" / "staging"
    if not staging.exists():
        return False, False
    shutil.rmtree(staging, ignore_errors=True)
    return True, staging.exists()
