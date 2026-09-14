"""M-PUBLISH effects boundary (IF-PUBLISH-001): irreversible operations.

Git merge/tag pushes and GitHub release/artifact mutations happen ONLY here,
driven by the Runtime executor. No agent path can reach this module; remote
readback helpers serve the reconcile loop. Env (GITHUB_TOKEN and friends) is
read only at this boundary, mirroring effects/github.py discipline.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import os
import re
import subprocess
from pathlib import Path

from tracks.effects.github import (
    GithubIssuesError,
    create_release_api,
    list_release_assets,
    readback_release,
    upload_release_asset,
)

_PUBLISH_FETCH_REF = "refs/trac/publish-fetch"


def push_merge(remote_url: str, source_ref: str, target_branch: str) -> dict:
    """Synchronize TARGET with the approved candidate: no force.

    IF-PUBLISH-002: the remote decides. Same object -> reconciled_skip; a
    remote tip that is an ancestor of the candidate -> non-force fast-forward
    push plus readback confirmation; divergence -> conflict; an absent branch
    -> failed (branch_missing). The ancestry probe fetches into a private
    temporary ref so no working branch is touched.

    FR-0277-02: this effect keeps its fast-forward-only generic semantics.
    A genuinely diverged release branch is never resolved here -- the strict
    flow consumes the pre-approved product identity through
    :func:`push_sync_product` instead; this effect neither creates nor
    "verifies" a merge product.
    """
    expected = _resolve_commit(source_ref)
    if expected is None:
        return {
            "status": "failed",
            "reason": "invalid_ref",
            "remote": remote_url,
            "branch": target_branch,
            "ref": source_ref,
        }
    before = read_remote_state(
        remote_url, "merge", target_branch, expected_object=expected
    )
    if before.get("error"):
        return _merge_result(
            "failed",
            remote_url,
            target_branch,
            source_ref,
            before,
            before.get("error") or "remote_read_failed",
        )
    if not before.get("exists"):
        return _merge_result(
            "failed", remote_url, target_branch, source_ref, before, "branch_missing"
        )
    if before.get("matches"):
        return _merge_result(
            "reconciled_skip", remote_url, target_branch, source_ref, before
        )
    remote_tip, fetch_error = _fetch_remote_branch(remote_url, target_branch)
    if fetch_error:
        return _merge_result(
            "failed", remote_url, target_branch, source_ref, before, fetch_error
        )
    if remote_tip == expected:
        return _merge_result(
            "reconciled_skip", remote_url, target_branch, source_ref, before
        )
    ancestor, ancestry_error = _is_ancestor(remote_tip, expected)
    if ancestry_error:
        return _merge_result(
            "failed", remote_url, target_branch, source_ref, before, ancestry_error
        )
    if not ancestor:
        return _merge_result(
            "conflict", remote_url, target_branch, source_ref, before
        )
    pushed_returncode, push_error = _push_branch_once(
        remote_url, target_branch, expected
    )
    after = read_remote_state(
        remote_url, "merge", target_branch, expected_object=expected
    )
    if after.get("error"):
        return _merge_result(
            "failed",
            remote_url,
            target_branch,
            source_ref,
            after,
            "remote_read_failed_after_push",
        )
    if after.get("exists") and after.get("matches") and pushed_returncode == 0:
        return _merge_result("done", remote_url, target_branch, source_ref, after)
    if after.get("exists") and after.get("matches") and push_error is not None:
        return _merge_result(
            "reconciled_skip",
            remote_url,
            target_branch,
            source_ref,
            after,
            push_error,
        )
    reason = push_error or ("push_failed" if pushed_returncode else "push_unconfirmed")
    return _merge_result(
        "failed", remote_url, target_branch, source_ref, after, reason
    )


def push_sync_product(
    remote_url: str,
    target_branch: str,
    product_sha: str,
    *,
    baseline_sha: str,
    candidate_sha: str,
) -> dict:
    """Publish an already-approved sync product with an expected-old update.

    FR-0277-02: the effect only consumes the approved prepared identity. The
    atomic ref update is a ``--force-with-lease`` pinned to the approved
    baseline B, so the remote accepts the write only while its tip is still
    B. A tip that already equals P is ``reconciled_skip`` (exact-object
    readback); any other tip -- including an ancestor rollback or a tip that
    merely contains C -- is a ``conflict`` and the remote is never touched.
    A plain non-force push is NOT used: it would accept a rollback to an
    ancestor of B.
    """
    if not _valid_object_id(product_sha) or not _valid_object_id(baseline_sha):
        return _merge_result(
            "failed",
            remote_url,
            target_branch,
            product_sha,
            {},
            "invalid_ref",
        )
    before = read_remote_state(
        remote_url, "merge", target_branch, expected_object=product_sha
    )
    if before.get("error"):
        return _merge_result(
            "failed",
            remote_url,
            target_branch,
            product_sha,
            before,
            before.get("error") or "remote_read_failed",
        )
    if not before.get("exists"):
        return _merge_result(
            "failed",
            remote_url,
            target_branch,
            product_sha,
            before,
            "branch_missing",
        )
    if before.get("object_id") == product_sha:
        return _sync_result(
            "reconciled_skip",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            before,
        )
    if before.get("object_id") != baseline_sha:
        return _sync_result(
            "conflict",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            before,
            "remote_moved",
        )
    pushed_returncode, push_error = _push_sync_once(
        remote_url, target_branch, product_sha, baseline_sha
    )
    after = read_remote_state(
        remote_url, "merge", target_branch, expected_object=product_sha
    )
    if after.get("error"):
        return _sync_result(
            "failed",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            after,
            "remote_read_failed_after_push",
        )
    if after.get("exists") and after.get("matches") and pushed_returncode == 0:
        return _sync_result(
            "done",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            after,
        )
    if after.get("exists") and after.get("matches") and push_error is not None:
        return _sync_result(
            "reconciled_skip",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            after,
            push_error,
        )
    if after.get("exists") and after.get("object_id") not in (None, product_sha):
        return _sync_result(
            "conflict",
            remote_url,
            target_branch,
            product_sha,
            baseline_sha,
            candidate_sha,
            after,
            "remote_moved",
        )
    reason = push_error or ("push_failed" if pushed_returncode else "push_unconfirmed")
    return _sync_result(
        "failed",
        remote_url,
        target_branch,
        product_sha,
        baseline_sha,
        candidate_sha,
        after,
        reason,
    )


def _sync_result(
    status: str,
    remote_url: str,
    target_branch: str,
    product_sha: str,
    baseline_sha: str,
    candidate_sha: str,
    remote_check: dict,
    reason: str | None = None,
) -> dict:
    result = _merge_result(
        status,
        remote_url,
        target_branch,
        product_sha,
        remote_check,
        reason,
        merge_mode="sync_product",
        product_sha=product_sha,
        baseline_sha=baseline_sha,
        source_candidate_sha=candidate_sha,
    )
    return result


def push_tag(remote_url: str, tag: str, ref: str) -> dict:
    """Create a tag without force and reconcile its remote object identity."""
    expected = _resolve_commit(ref)
    if expected is None:
        return {
            "status": "failed",
            "reason": "invalid_ref",
            "remote": remote_url,
            "tag": tag,
            "ref": ref,
        }
    before = read_remote_state(remote_url, "tag", tag, expected_object=expected)
    if before.get("error"):
        return _tag_result(
            "failed",
            remote_url,
            tag,
            ref,
            before,
            before.get("error") or "remote_read_failed",
        )
    if before.get("exists"):
        status = "reconciled_skip" if before.get("matches") else "conflict"
        return _tag_result(status, remote_url, tag, ref, before)
    pushed_returncode, push_error = _push_tag_once(remote_url, tag, expected)
    after = read_remote_state(remote_url, "tag", tag, expected_object=expected)
    if after.get("error"):
        return _tag_result(
            "failed", remote_url, tag, ref, after, "remote_read_failed_after_push"
        )
    if after.get("exists") and not after.get("matches"):
        return _tag_result("conflict", remote_url, tag, ref, after)
    if after.get("exists") and after.get("matches") and pushed_returncode == 0:
        return _tag_result("done", remote_url, tag, ref, after)
    if after.get("exists") and after.get("matches") and push_error is not None:
        return _tag_result("reconciled_skip", remote_url, tag, ref, after, push_error)
    reason = push_error or ("push_failed" if pushed_returncode else "push_unconfirmed")
    return _tag_result("failed", remote_url, tag, ref, after, reason)


def _resolve_commit(ref: str) -> str | None:
    """Resolve HEAD/branch/commit-ish to one complete commit object ID."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value if _valid_object_id(value) and "\n" not in value else None


def _push_ref_once(
    remote_url: str, refspec: str, *options: str
) -> tuple[int | None, str | None]:
    try:
        proc = subprocess.run(
            ["git", "push", *options, remote_url, refspec],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "push_timeout"
    except OSError as exc:
        return None, f"push_error:{exc}"
    return proc.returncode, None


def _push_tag_once(remote_url: str, tag: str, object_id: str) -> tuple[int | None, str | None]:
    return _push_ref_once(remote_url, f"{object_id}:refs/tags/{tag}")


def _push_branch_once(
    remote_url: str, target_branch: str, object_id: str
) -> tuple[int | None, str | None]:
    return _push_ref_once(remote_url, f"{object_id}:refs/heads/{target_branch}")


def _push_sync_once(
    remote_url: str, target_branch: str, product_sha: str, baseline_sha: str
) -> tuple[int | None, str | None]:
    """Atomic expected-old ref update (``--force-with-lease=<ref>:<B>``)."""
    return _push_ref_once(
        remote_url,
        f"{product_sha}:refs/heads/{target_branch}",
        f"--force-with-lease=refs/heads/{target_branch}:{baseline_sha}",
    )


def _fetch_remote_branch(remote_url: str, target_branch: str) -> tuple[str | None, str | None]:
    """Fetch refs/heads/<T> into a private temp ref; delete it afterwards.

    The fetch only pulls the remote objects in (never a working branch) and
    the temp ref is dropped before the ancestry probe, which still sees the
    fetched objects.
    """
    temp_ref = f"{_PUBLISH_FETCH_REF}/{os.getpid()}-{target_branch}"
    try:
        proc = subprocess.run(
            [
                "git",
                "fetch",
                "--no-tags",
                remote_url,
                f"refs/heads/{target_branch}:{temp_ref}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if proc.returncode != 0:
            return None, proc.stderr.strip() or "remote_fetch_failed"
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", f"{temp_ref}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "remote_fetch_timeout"
    except OSError as exc:
        return None, f"remote_fetch_error:{exc}"
    finally:
        _drop_ref(temp_ref)
    value = resolved.stdout.strip()
    if resolved.returncode != 0 or not _valid_object_id(value):
        return None, "remote_fetch_unresolved"
    return value, None


def _drop_ref(ref: str) -> None:
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["git", "update-ref", "-d", ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


def _is_ancestor(ancestor: str, descendant: str) -> tuple[bool | None, str | None]:
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None, "ancestry_timeout"
    except OSError as exc:
        return None, f"ancestry_error:{exc}"
    if proc.returncode == 0:
        return True, None
    if proc.returncode == 1:
        return False, None
    return None, proc.stderr.strip() or "ancestry_check_failed"


def _valid_object_id(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(
        char in "0123456789abcdefABCDEF" for char in value
    )


def _tag_result(
    status: str,
    remote_url: str,
    tag: str,
    ref: str,
    remote_check: dict,
    reason: str | None = None,
) -> dict:
    result = {
        "status": status,
        "remote": remote_url,
        "tag": tag,
        "ref": ref,
        "remote_check": remote_check,
    }
    if remote_check.get("object_id"):
        result["object_id"] = remote_check["object_id"]
    if reason is not None:
        result["reason"] = reason
    return result


def _effect_result(
    status: str,
    remote_url: str,
    remote_check: dict | None,
    reason: str | None = None,
    **fields: object,
) -> dict:
    result = {
        "status": status,
        "remote": remote_url,
        "remote_check": remote_check or {},
        **fields,
    }
    if result["remote_check"].get("object_id"):
        result["object_id"] = result["remote_check"]["object_id"]
    if reason is not None:
        result["reason"] = reason
    return result


def _merge_result(
    status: str,
    remote_url: str,
    target_branch: str,
    source_ref: str,
    remote_check: dict,
    reason: str | None = None,
    **fields: object,
) -> dict:
    return _effect_result(
        status,
        remote_url,
        remote_check,
        reason,
        branch=target_branch,
        ref=source_ref,
        **fields,
    )


_GITHUB_REMOTE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
)
_REPO_ID = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CANDIDATE_IN_NOTES = re.compile(r"candidate[_-]?sha\s*[=:]\s*([0-9a-fA-F]+)")


def create_release(
    repo_id: str,
    tag: str,
    notes: str,
    prerelease: bool,
    target_commitish: str | None = None,
) -> dict:
    """Create or reconcile the release bound to the approved candidate.

    Readback first (the remote decides): an existing release with matching
    tag_name/prerelease/target_commitish is reconciled_skip, a divergent one
    is conflict; otherwise POST and confirm by a second readback. ``notes``
    are deterministic and may carry the candidate SHA when the caller does
    not pass ``target_commitish`` explicitly.
    """
    resolved = _resolve_repo_id(repo_id)
    if resolved is None:
        return _release_result("failed", repo_id, tag, None, "unsupported_remote")
    expected_target = target_commitish or _candidate_sha_from_notes(notes)
    before, error = _release_readback(resolved, tag)
    if error:
        return _release_result("failed", resolved, tag, before, error)
    if before.get("exists"):
        status = (
            "reconciled_skip"
            if _release_matches(before, tag, prerelease, expected_target)
            else "conflict"
        )
        return _release_result(status, resolved, tag, before)
    created, error = _release_create(resolved, tag, notes, prerelease, expected_target)
    if error:
        return _release_result("failed", resolved, tag, created, error)
    after, error = _release_readback(resolved, tag)
    if error:
        return _release_result("failed", resolved, tag, after, error)
    if after.get("exists") and _release_matches(after, tag, prerelease, expected_target):
        return _release_result("done", resolved, tag, after)
    return _release_result("failed", resolved, tag, after, "release_unconfirmed")


def upload_artifact(repo_id: str, tag: str, artifact_path: str) -> dict:
    """Upload the single artifact matched by TARGET, then read it back.

    TARGET must resolve to exactly one file (zero/many -> malformed before any
    effect). The release is located by tag; a same-name asset is reconciled by
    size and, when the remote exposes a digest, by sha256. The local digest is
    always recorded; a remote digest is only claimed when actually present.
    """
    target, error = _resolve_artifact_target(artifact_path)
    if error:
        return _artifact_result("failed", repo_id, tag, None, error)
    resolved = _resolve_repo_id(repo_id)
    if resolved is None:
        return _artifact_result("failed", repo_id, tag, None, "unsupported_remote")
    path = Path(target)
    data, read_error = _read_artifact(path)
    if read_error:
        return _artifact_result("failed", resolved, tag, None, read_error, name=path.name)
    return _upload_to_release(resolved, tag, path, data)


def _upload_to_release(repo_id: str, tag: str, path: Path, data: bytes) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    identity = {"name": path.name, "size": len(data), "sha256_local": digest}
    release, error = _release_for_upload(repo_id, tag)
    if error:
        return _artifact_result("failed", repo_id, tag, release, error, **identity)
    assets, error = _list_assets(repo_id, release["release_id"])
    if error:
        return _artifact_result("failed", repo_id, tag, release, error, **identity)
    existing = _find_asset(assets, path.name)
    if existing is not None:
        status = (
            "reconciled_skip"
            if _asset_matches(existing, len(data), digest)
            else "conflict"
        )
        return _artifact_result(
            status,
            repo_id,
            tag,
            release,
            None,
            remote_digest=existing.get("digest"),
            **identity,
        )
    _uploaded, error = _upload_asset(release, path.name, data)
    if error:
        return _artifact_result("failed", repo_id, tag, release, error, **identity)
    confirmed, error = _confirm_asset(
        repo_id, release["release_id"], path.name, len(data), digest
    )
    if error:
        return _artifact_result("failed", repo_id, tag, release, error, **identity)
    if confirmed is None:
        return _artifact_result(
            "failed", repo_id, tag, release, "artifact_unconfirmed", **identity
        )
    return _artifact_result(
        "done",
        repo_id,
        tag,
        release,
        None,
        remote_digest=confirmed.get("digest"),
        **identity,
    )


def _release_readback(repo_id: str, tag: str) -> tuple[dict, str | None]:
    try:
        payload = readback_release(repo_id, tag)
    except GithubIssuesError as exc:
        return {}, f"api_error:{exc.classification}"
    if payload.get("error"):
        return payload, f"api_error:{payload['error']}"
    return payload, None


def _release_create(
    repo_id: str,
    tag: str,
    notes: str,
    prerelease: bool,
    target_commitish: str | None,
) -> tuple[dict, str | None]:
    try:
        payload = create_release_api(repo_id, tag, notes, prerelease, target_commitish)
    except GithubIssuesError as exc:
        return {}, f"api_error:{exc.classification}"
    if payload.get("error"):
        return payload, f"api_error:{payload['error']}"
    return payload, None


def _release_result(
    status: str,
    repo_id: str,
    tag: str,
    remote_check: dict | None,
    reason: str | None = None,
) -> dict:
    result = _effect_result(status, repo_id, remote_check, reason, tag=tag)
    release_id = result["remote_check"].get("release_id")
    if release_id is not None:
        result["release_id"] = release_id
    return result


def _resolve_repo_id(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _GITHUB_REMOTE.match(text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    if _REPO_ID.fullmatch(text):
        return text
    return None


def _candidate_sha_from_notes(notes: str) -> str | None:
    match = _CANDIDATE_IN_NOTES.search(str(notes or ""))
    if not match:
        return None
    value = match.group(1)
    return value if _valid_object_id(value) else None


def _release_matches(
    remote: dict,
    tag: str,
    prerelease: bool,
    expected_target: str | None,
) -> bool:
    if not isinstance(remote, dict):
        return False
    if remote.get("tag_name") != tag:
        return False
    if remote.get("prerelease") != bool(prerelease):
        return False
    return expected_target is None or remote.get("target_commitish") == expected_target


def _resolve_artifact_target(pattern: str) -> tuple[str | None, str | None]:
    if not isinstance(pattern, str) or not pattern.strip():
        return None, "malformed"
    matches = [
        path for path in sorted(glob.glob(pattern, recursive=True)) if Path(path).is_file()
    ]
    if len(matches) != 1:
        return None, "malformed"
    return matches[0], None


def _read_artifact(path: Path) -> tuple[bytes | None, str | None]:
    try:
        return path.read_bytes(), None
    except OSError as exc:
        return None, f"read_error:{exc}"


def _release_for_upload(repo_id: str, tag: str) -> tuple[dict, str | None]:
    release, error = _release_readback(repo_id, tag)
    if error:
        return release, error
    if not release.get("exists"):
        return release, "missing_release"
    if release.get("release_id") is None or not release.get("upload_url"):
        return release, "malformed_release"
    return release, None


def _list_assets(repo_id: str, release_id: object) -> tuple[list[dict], str | None]:
    try:
        payload = list_release_assets(repo_id, release_id)
    except GithubIssuesError as exc:
        return [], f"api_error:{exc.classification}"
    if payload.get("error"):
        return [], f"api_error:{payload['error']}"
    return list(payload.get("assets") or []), None


def _find_asset(assets: list[dict], name: str) -> dict | None:
    for asset in assets:
        if isinstance(asset, dict) and str(asset.get("name")) == name:
            return asset
    return None


def _normalize_sha256(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[len("sha256:"):]
    return normalized if re.fullmatch(r"[0-9a-f]{64}", normalized) else None


def _asset_matches(asset: dict, size: int, digest: str) -> bool:
    if asset.get("size") != size:
        return False
    remote_digest = asset.get("digest")
    if remote_digest in (None, ""):
        return True
    return _normalize_sha256(remote_digest) == digest


def _upload_asset(release: dict, name: str, data: bytes) -> tuple[dict | None, str | None]:
    try:
        payload = upload_release_asset(
            release["upload_url"], name, data, "application/octet-stream"
        )
    except GithubIssuesError as exc:
        return None, f"api_error:{exc.classification}"
    if payload.get("error"):
        return payload, f"api_error:{payload['error']}"
    return payload, None


def _confirm_asset(
    repo_id: str,
    release_id: object,
    name: str,
    size: int,
    digest: str,
) -> tuple[dict | None, str | None]:
    assets, error = _list_assets(repo_id, release_id)
    if error:
        return None, error
    found = _find_asset(assets, name)
    if found is None or not _asset_matches(found, size, digest):
        return None, None
    return found, None


def _artifact_result(
    status: str,
    repo_id: str,
    tag: str,
    remote_check: dict | None,
    reason: str | None = None,
    *,
    name: str | None = None,
    size: int | None = None,
    sha256_local: str | None = None,
    remote_digest: str | None = None,
) -> dict:
    result = _effect_result(status, repo_id, remote_check, reason, tag=tag)
    if name is not None:
        result["name"] = name
    if size is not None:
        result["size"] = size
    if sha256_local is not None:
        result["sha256_local"] = sha256_local
    if remote_digest is not None:
        result["remote_digest"] = remote_digest
    return result


def _remote_state_payload(
    remote_url: str,
    ref: str,
    *,
    exists: bool,
    expected_object: str | None = None,
    object_id: str | None = None,
    error: str | None = None,
) -> dict:
    payload = {
        "exists": exists,
        "matches": (
            None
            if expected_object is None or object_id is None
            else object_id == expected_object
        ),
        "ref": ref,
        "remote": remote_url,
    }
    if object_id is not None:
        payload["object_id"] = object_id
    if error is not None:
        payload["error"] = error
    return payload


def read_remote_state(
    remote_url: str, kind: str, target: str, expected_object: str | None = None
) -> dict:
    """Read the real remote state: tag existence (""refs/tags/""), branch
    (""refs/heads/"") via ls-remote. exists/matches is decided from the
    remote, never assumed (IF-PUBLISH-002)."""
    if kind == "tag":
        ref = f"refs/tags/{target}"
    elif kind in ("merge", "branch"):
        ref = f"refs/heads/{target}"
    else:
        ref = f"refs/{target}"
    try:
        checked = subprocess.run(
            ["git", "check-ref-format", ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return _remote_state_payload(
            remote_url, ref, exists=False, error="ref_check_timeout"
        )
    except OSError as exc:
        return _remote_state_payload(
            remote_url, ref, exists=False, error=f"ref_check_error:{exc}"
        )
    if checked.returncode != 0:
        return _remote_state_payload(
            remote_url, ref, exists=False, error="invalid_ref"
        )
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--refs", remote_url, ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return _remote_state_payload(
            remote_url, ref, exists=False, error="read_timeout"
        )
    except OSError as exc:
        return _remote_state_payload(
            remote_url, ref, exists=False, error=f"read_error:{exc}"
        )
    if proc.returncode != 0:
        return _remote_state_payload(
            remote_url,
            ref,
            exists=False,
            error=proc.stderr.strip() or "git ls-remote failed",
        )
    output = proc.stdout.strip()
    if not output:
        return _remote_state_payload(remote_url, ref, exists=False)
    lines = output.splitlines()
    fields = lines[0].split() if len(lines) == 1 else []
    if len(fields) != 2 or fields[1] != ref or not _valid_object_id(fields[0]):
        return _remote_state_payload(
            remote_url,
            ref,
            exists=False,
            error="malformed git ls-remote response",
        )
    object_id = fields[0]
    return _remote_state_payload(
        remote_url,
        ref,
        exists=True,
        expected_object=expected_object,
        object_id=object_id,
    )
