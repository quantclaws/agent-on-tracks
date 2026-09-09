"""M-PUBLISH effects boundary (IF-PUBLISH-001): irreversible operations.

Git merge/tag pushes and GitHub release/artifact mutations happen ONLY here,
driven by the Runtime executor. No agent path can reach this module; remote
readback helpers serve the reconcile loop. Env (GITHUB_TOKEN and friends) is
read only at this boundary, mirroring effects/github.py discipline.
"""

from __future__ import annotations

import subprocess


def push_merge(remote_url: str, source_ref: str, target_branch: str) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


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


def _push_tag_once(remote_url: str, tag: str, object_id: str) -> tuple[int | None, str | None]:
    try:
        proc = subprocess.run(
            ["git", "push", remote_url, f"{object_id}:refs/tags/{tag}"],
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


def create_release(repo_id: str, tag: str, notes: str, prerelease: bool) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


def upload_artifact(repo_id: str, tag: str, artifact_path: str) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


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
