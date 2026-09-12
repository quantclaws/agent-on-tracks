"""Behavior coverage for the M-PUBLISH effects boundary (``publish``).

Error classification and reconciliation branches of push_merge/push_tag,
release create/upload readback joins, artifact target resolution, GitHub API
error mapping and git subprocess failure windows (IF-PUBLISH-001/002).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.effects import publish
from tracks.effects.github import GithubIssuesError
from tracks.effects.publish import (
    _asset_matches,
    _candidate_sha_from_notes,
    _confirm_asset,
    _list_assets,
    _normalize_sha256,
    _read_artifact,
    _release_matches,
    _release_readback,
    _resolve_artifact_target,
    _resolve_commit,
    _resolve_repo_id,
    create_release,
    push_merge,
    push_tag,
    read_remote_state,
    upload_artifact,
)

OID = "a" * 40


def _check(exists=True, matches=None, object_id=OID, error=None):
    payload = {"exists": exists, "matches": matches, "object_id": object_id, "ref": "r"}
    if error:
        payload["error"] = error
    return payload


# ---------------------------------------------------------------------------
# push_merge
# ---------------------------------------------------------------------------


@pytest.fixture
def merge_stubs(monkeypatch):
    state = {"before": _check(matches=False), "after": _check(matches=True), "results": []}

    def _read(*a, **k):
        return state["results"].pop(0) if state["results"] else state["before"]

    calls = {"fetch": (OID, None), "ancestor": (True, None), "push": (0, None)}
    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: OID)
    monkeypatch.setattr(publish, "read_remote_state", _read)
    monkeypatch.setattr(publish, "_fetch_remote_branch", lambda *a: calls["fetch"])
    monkeypatch.setattr(publish, "_is_ancestor", lambda *a: calls["ancestor"])
    monkeypatch.setattr(publish, "_push_branch_once", lambda *a: calls["push"])
    return state, calls


def test_push_merge_invalid_and_remote_read_failure(monkeypatch):
    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: None)
    result = push_merge("git@x", "HEAD", "main")
    assert (result["status"], result["reason"]) == ("failed", "invalid_ref")

    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: OID)
    monkeypatch.setattr(publish, "read_remote_state", lambda *a, **k: _check(exists=False, error="read_timeout"))
    result = push_merge("git@x", "HEAD", "main")
    assert (result["status"], result["reason"]) == ("failed", "read_timeout")


def test_push_merge_branch_missing_and_matches(merge_stubs):
    state, _ = merge_stubs
    state["before"] = _check(exists=False)
    assert push_merge("git@x", "HEAD", "main")["reason"] == "branch_missing"

    state["after"] = _check(matches=True)
    state["before"] = _check(matches=True)
    result = push_merge("git@x", "HEAD", "main")
    assert result["status"] == "reconciled_skip"


def test_push_merge_fetch_error_and_tip_equality(merge_stubs):
    state, calls = merge_stubs
    calls["fetch"] = (None, "remote_fetch_failed")
    assert push_merge("git@x", "HEAD", "main")["reason"] == "remote_fetch_failed"

    calls["fetch"] = (OID, None)
    assert push_merge("git@x", "HEAD", "main")["status"] == "reconciled_skip"


def test_push_merge_ancestry_error_and_conflict(merge_stubs):
    state, calls = merge_stubs
    calls["fetch"] = ("b" * 40, None)
    calls["ancestor"] = (None, "ancestry_timeout")
    assert push_merge("git@x", "HEAD", "main")["reason"] == "ancestry_timeout"

    calls["ancestor"] = (False, None)
    assert push_merge("git@x", "HEAD", "main")["status"] == "conflict"


def _queue(state, *results):
    state["results"].extend(results)


def test_push_merge_done_and_post_push_failures(merge_stubs):
    state, calls = merge_stubs
    calls["fetch"] = ("b" * 40, None)
    _queue(state, state["before"], state["after"])
    assert push_merge("git@x", "HEAD", "main")["status"] == "done"

    state["after"] = _check(matches=True)
    calls["push"] = (1, "push_timeout")
    _queue(state, state["before"], state["after"])
    result = push_merge("git@x", "HEAD", "main")
    assert (result["status"], result["reason"]) == ("reconciled_skip", "push_timeout")

    calls["push"] = (1, None)
    _queue(state, state["before"], state["after"])
    assert push_merge("git@x", "HEAD", "main")["reason"] == "push_failed"

    calls["push"] = (None, None)
    _queue(state, state["before"], state["after"])
    assert push_merge("git@x", "HEAD", "main")["reason"] == "push_unconfirmed"

    state["after"] = _check(matches=False)
    calls["push"] = (1, None)
    _queue(state, state["before"], state["after"])
    assert push_merge("git@x", "HEAD", "main")["reason"] == "push_failed"

    state["after"] = _check(matches=True, error="read_error:x")
    _queue(state, state["before"], state["after"])
    assert push_merge("git@x", "HEAD", "main")["reason"] == "remote_read_failed_after_push"


# ---------------------------------------------------------------------------
# push_tag
# ---------------------------------------------------------------------------


def test_push_tag_error_and_reconcile_branches(monkeypatch):
    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: None)
    assert push_tag("git@x", "v1", "HEAD")["reason"] == "invalid_ref"

    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: OID)
    monkeypatch.setattr(publish, "read_remote_state", lambda *a, **k: _check(exists=False, error="read_timeout"))
    assert push_tag("git@x", "v1", "HEAD")["reason"] == "read_timeout"

    monkeypatch.setattr(publish, "read_remote_state", lambda *a, **k: _check(exists=True, matches=True))
    assert push_tag("git@x", "v1", "HEAD")["status"] == "reconciled_skip"

    monkeypatch.setattr(publish, "read_remote_state", lambda *a, **k: _check(exists=True, matches=False))
    assert push_tag("git@x", "v1", "HEAD")["status"] == "conflict"


def test_push_tag_post_push_states(monkeypatch):
    monkeypatch.setattr(publish, "_resolve_commit", lambda ref: OID)
    seq = {"after": _check(exists=False), "calls": 0}

    def _read(*a, **k):
        seq["calls"] += 1
        return _check(exists=False) if seq["calls"] % 2 == 1 else seq["after"]

    monkeypatch.setattr(publish, "read_remote_state", _read)
    monkeypatch.setattr(publish, "_push_tag_once", lambda *a: (0, None))
    result = push_tag("git@x", "v1", "HEAD")
    assert result["status"] == "failed" and result["reason"] == "push_unconfirmed"

    seq["after"] = _check(exists=True, matches=False)
    assert push_tag("git@x", "v1", "HEAD")["status"] == "conflict"

    seq["after"] = _check(exists=True, matches=True)
    assert push_tag("git@x", "v1", "HEAD")["status"] == "done"

    monkeypatch.setattr(publish, "_push_tag_once", lambda *a: (1, "push_timeout"))
    result = push_tag("git@x", "v1", "HEAD")
    assert (result["status"], result["reason"]) == ("reconciled_skip", "push_timeout")

    monkeypatch.setattr(publish, "_push_tag_once", lambda *a: (1, None))
    assert push_tag("git@x", "v1", "HEAD")["reason"] == "push_failed"

    seq["after"] = _check(exists=True, matches=True, error="read_error:x")
    assert push_tag("git@x", "v1", "HEAD")["reason"] == "remote_read_failed_after_push"

    seq["after"] = _check(exists=True, matches=False, object_id=OID)
    result = push_tag("git@x", "v1", "HEAD")
    assert result["object_id"] == OID


# ---------------------------------------------------------------------------
# git subprocess failure windows
# ---------------------------------------------------------------------------


def _run_raiser(exc):
    def _run(*a, **k):
        raise exc

    return _run


def test_resolve_commit_subprocess_failures(monkeypatch):
    monkeypatch.setattr(publish.subprocess, "run", _run_raiser(OSError("no git")))
    assert _resolve_commit("HEAD") is None
    monkeypatch.setattr(
        publish.subprocess, "run", _run_raiser(subprocess.TimeoutExpired("git", 60))
    )
    assert _resolve_commit("HEAD") is None


def test_resolve_commit_and_push_ref_success(monkeypatch):
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=OID + "\n", stderr=""),
    )
    assert _resolve_commit("HEAD") == OID

    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="bad"),
    )
    assert _resolve_commit("HEAD") is None

    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert publish._push_ref_once("r", "spec") == (0, None)
    assert publish._push_tag_once("r", "v1", OID) == (0, None)
    assert publish._push_branch_once("r", "main", OID) == (0, None)


def test_fetch_remote_branch_success(monkeypatch):
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=OID + "\n", stderr=""),
    )
    assert publish._fetch_remote_branch("r", "main") == (OID, None)


def test_push_ref_once_subprocess_failures(monkeypatch):
    import tracks.effects.publish as pub

    monkeypatch.setattr(
        pub.subprocess, "run", _run_raiser(subprocess.TimeoutExpired("git", 60))
    )
    assert pub._push_ref_once("r", "spec") == (None, "push_timeout")
    monkeypatch.setattr(pub.subprocess, "run", _run_raiser(OSError("boom")))
    rc, err = pub._push_ref_once("r", "spec")
    assert rc is None and err.startswith("push_error:")


def test_fetch_remote_branch_failures(monkeypatch):
    calls = []

    def _run(args, **kwargs):
        calls.append(args)
        if args[0] == "git" and args[1] == "fetch":
            return SimpleNamespace(returncode=1, stderr="fetch failed", stdout="")
        return SimpleNamespace(returncode=0, stdout=OID + "\n", stderr="")

    monkeypatch.setattr(publish.subprocess, "run", _run)
    assert publish._fetch_remote_branch("r", "main") == (None, "fetch failed")

    monkeypatch.setattr(
        publish.subprocess, "run", _run_raiser(subprocess.TimeoutExpired("git", 60))
    )
    assert publish._fetch_remote_branch("r", "main") == (None, "remote_fetch_timeout")

    monkeypatch.setattr(publish.subprocess, "run", _run_raiser(OSError("x")))
    rc, err = publish._fetch_remote_branch("r", "main")
    assert rc is None and err.startswith("remote_fetch_error:")

    def _bad_resolve(args, **kwargs):
        if args[1] == "fetch":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="short\n", stderr="")

    monkeypatch.setattr(publish.subprocess, "run", _bad_resolve)
    assert publish._fetch_remote_branch("r", "main") == (None, "remote_fetch_unresolved")


def test_is_ancestor_error_windows(monkeypatch):
    monkeypatch.setattr(
        publish.subprocess, "run", _run_raiser(subprocess.TimeoutExpired("git", 60))
    )
    assert publish._is_ancestor("a", "b") == (None, "ancestry_timeout")
    monkeypatch.setattr(publish.subprocess, "run", _run_raiser(OSError("x")))
    rc, err = publish._is_ancestor("a", "b")
    assert rc is None and err.startswith("ancestry_error:")

    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="broken"),
    )
    assert publish._is_ancestor("a", "b") == (None, "broken")

    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    assert publish._is_ancestor("a", "b") == (True, None)

    monkeypatch.setattr(
        publish.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert publish._is_ancestor("a", "b") == (False, None)


# ---------------------------------------------------------------------------
# read_remote_state
# ---------------------------------------------------------------------------


def _remote_runner(check_rc=0, ls_rc=0, ls_out="", stderr=""):
    def _run(args, **kwargs):
        if args[1] == "check-ref-format":
            return SimpleNamespace(returncode=check_rc, stdout="", stderr=stderr)
        return SimpleNamespace(returncode=ls_rc, stdout=ls_out, stderr=stderr)

    return _run


def test_read_remote_state_kind_and_check_ref_failures(monkeypatch):
    monkeypatch.setattr(publish.subprocess, "run", _remote_runner(ls_out=""))
    payload = read_remote_state("r", "branch", "main")
    assert payload["ref"] == "refs/heads/main"
    payload = read_remote_state("r", "weird", "x")
    assert payload["ref"] == "refs/x"

    monkeypatch.setattr(
        publish.subprocess, "run", _run_raiser(subprocess.TimeoutExpired("git", 60))
    )
    assert read_remote_state("r", "tag", "v1")["error"] == "ref_check_timeout"

    monkeypatch.setattr(publish.subprocess, "run", _run_raiser(OSError("x")))
    assert read_remote_state("r", "tag", "v1")["error"].startswith("ref_check_error:")

    monkeypatch.setattr(publish.subprocess, "run", _remote_runner(check_rc=1))
    assert read_remote_state("r", "tag", "v1")["error"] == "invalid_ref"


def test_read_remote_state_ls_remote_failures(monkeypatch):
    def _raise_on_ls(exc):
        def _run(args, **kwargs):
            if args[1] == "check-ref-format":
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise exc

        return _run

    monkeypatch.setattr(
        publish.subprocess, "run", _raise_on_ls(subprocess.TimeoutExpired("git", 60))
    )
    assert read_remote_state("r", "tag", "v1")["error"] == "read_timeout"
    monkeypatch.setattr(publish.subprocess, "run", _raise_on_ls(OSError("x")))
    assert read_remote_state("r", "tag", "v1")["error"].startswith("read_error:")
    monkeypatch.setattr(publish.subprocess, "run", _remote_runner(ls_rc=2, stderr="ls failed"))
    assert read_remote_state("r", "tag", "v1")["error"] == "ls failed"
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        _remote_runner(ls_out=f"{OID}\trefs/tags/other\n"),
    )
    assert read_remote_state("r", "tag", "v1")["error"] == "malformed git ls-remote response"
    monkeypatch.setattr(
        publish.subprocess,
        "run",
        _remote_runner(ls_out=f"short\n{OID}\trefs/tags/v1\n"),
    )
    assert read_remote_state("r", "tag", "v1")["error"] == "malformed git ls-remote response"
    monkeypatch.setattr(publish.subprocess, "run", _remote_runner(ls_out=f"{OID}\trefs/tags/v1\n"))
    payload = read_remote_state("r", "tag", "v1", expected_object=OID)
    assert payload["exists"] is True and payload["matches"] is True


# ---------------------------------------------------------------------------
# create_release
# ---------------------------------------------------------------------------


def test_create_release_branches(monkeypatch):
    monkeypatch.setattr(publish, "_resolve_repo_id", lambda value: None)
    assert create_release("bogus", "v1", "n", False)["reason"] == "unsupported_remote"

    monkeypatch.setattr(publish, "_resolve_repo_id", lambda value: "o/r")
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: ({}, "api_error:network"))
    assert create_release("o/r", "v1", "n", False)["reason"] == "api_error:network"

    existing = {"exists": True, "tag_name": "v1", "prerelease": False, "target_commitish": "t"}
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: (existing, None))
    assert create_release("o/r", "v1", "n", False)["status"] == "reconciled_skip"
    existing["tag_name"] = "v2"
    assert create_release("o/r", "v1", "n", False)["status"] == "conflict"
    existing["tag_name"] = "v1"
    existing["prerelease"] = True
    assert create_release("o/r", "v1", "n", False)["status"] == "conflict"


def test_create_release_post_and_readback_sequence(monkeypatch):
    monkeypatch.setattr(publish, "_resolve_repo_id", lambda value: "o/r")
    readbacks = iter(
        [
            ({"exists": False}, None),
            (
                {
                    "exists": True,
                    "tag_name": "v1",
                    "prerelease": False,
                    "target_commitish": "t",
                    "release_id": 7,
                },
                None,
            ),
        ]
    )
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: next(readbacks))
    monkeypatch.setattr(publish, "_release_create", lambda *a: ({"release_id": 7}, None))
    result = create_release("o/r", "v1", "candidate_sha=" + OID, False, "t")
    assert result["status"] == "done"
    assert result["release_id"] == 7

    readbacks = iter(
        [
            ({"exists": False}, None),
            ({"exists": True, "tag_name": "v1", "prerelease": False, "target_commitish": "other"}, None),
        ]
    )
    result = create_release("o/r", "v1", "n", False, "t")
    assert result["status"] == "failed" and result["reason"] == "release_unconfirmed"

    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: ({"exists": False}, None))
    monkeypatch.setattr(publish, "_release_create", lambda *a: ({}, "api_error:auth"))
    assert create_release("o/r", "v1", "n", False)["reason"] == "api_error:auth"

    readbacks = iter([({"exists": False}, None), ({}, "api_error:timeout")])
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: next(readbacks))
    monkeypatch.setattr(publish, "_release_create", lambda *a: ({"release_id": 8}, None))
    assert create_release("o/r", "v1", "n", False)["reason"] == "api_error:timeout"


# ---------------------------------------------------------------------------
# upload_artifact
# ---------------------------------------------------------------------------


def test_upload_artifact_delegates_to_release_upload(tmp_path, monkeypatch):
    target = tmp_path / "a.bin"
    target.write_bytes(b"data")
    seen = {}

    def _upload(repo_id, tag, path, data):
        seen.update(repo_id=repo_id, tag=tag, path=path, data=data)
        return {"status": "done"}

    monkeypatch.setattr(publish, "_upload_to_release", _upload)
    result = upload_artifact("o/r", "v1", str(target))
    assert result == {"status": "done"}
    assert seen["data"] == b"data" and seen["path"].name == "a.bin"


def test_upload_to_release_missing_release(monkeypatch):
    monkeypatch.setattr(
        publish, "_release_for_upload", lambda repo, tag: ({}, "missing_release")
    )
    result = publish._upload_to_release("o/r", "v1", Path("x.zip"), b"d")
    assert result["reason"] == "missing_release"
    assert result["name"] == "x.zip"


def test_upload_artifact_target_and_read_errors(tmp_path, monkeypatch):
    assert upload_artifact("o/r", "v1", "")["reason"] == "malformed"
    assert upload_artifact("o/r", "v1", str(tmp_path / "*.zip"))["reason"] == "malformed"

    monkeypatch.setattr(publish, "_resolve_repo_id", lambda value: None)
    monkeypatch.setattr(publish, "_resolve_artifact_target", lambda pattern: ("/tmp/x.zip", None))
    assert upload_artifact("o/r", "v1", "/tmp/x.zip")["reason"] == "unsupported_remote"

    monkeypatch.setattr(publish, "_resolve_repo_id", lambda value: "o/r")
    monkeypatch.setattr(publish, "_read_artifact", lambda path: (None, "read_error:denied"))
    result = upload_artifact("o/r", "v1", "/tmp/x.zip")
    assert result["reason"] == "read_error:denied" and result["name"] == "x.zip"


def test_upload_to_release_states(monkeypatch):
    release = {"exists": True, "release_id": 5, "upload_url": "https://u"}
    monkeypatch.setattr(publish, "_release_for_upload", lambda repo, tag: (release, None))

    monkeypatch.setattr(publish, "_list_assets", lambda repo, rid: ([], "api_error:auth"))
    result = publish._upload_to_release("o/r", "v1", Path("x.zip"), b"data")
    assert result["reason"] == "api_error:auth"

    monkeypatch.setattr(publish, "_list_assets", lambda repo, rid: ([], None))
    monkeypatch.setattr(
        publish,
        "_upload_asset",
        lambda rel, name, data: (None, "api_error:network"),
    )
    assert publish._upload_to_release("o/r", "v1", Path("x.zip"), b"data")["reason"] == "api_error:network"

    monkeypatch.setattr(publish, "_upload_asset", lambda rel, name, data: ({"id": 1}, None))
    monkeypatch.setattr(
        publish, "_confirm_asset", lambda *a: (None, "api_error:timeout")
    )
    assert publish._upload_to_release("o/r", "v1", Path("x.zip"), b"data")["reason"] == "api_error:timeout"

    monkeypatch.setattr(publish, "_confirm_asset", lambda *a: (None, None))
    assert publish._upload_to_release("o/r", "v1", Path("x.zip"), b"data")["reason"] == "artifact_unconfirmed"

    monkeypatch.setattr(
        publish, "_confirm_asset", lambda *a: ({"digest": "sha256:" + "b" * 64}, None)
    )
    result = publish._upload_to_release("o/r", "v1", Path("x.zip"), b"data")
    assert result["status"] == "done"
    assert result["remote_digest"] == "sha256:" + "b" * 64


def test_upload_to_release_existing_asset_match_and_conflict(monkeypatch):
    release = {"exists": True, "release_id": 5, "upload_url": "https://u"}
    monkeypatch.setattr(publish, "_release_for_upload", lambda repo, tag: (release, None))
    data = b"data"
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        publish,
        "_list_assets",
        lambda repo, rid: ([{"name": "x.zip", "size": len(data), "digest": digest}], None),
    )
    result = publish._upload_to_release("o/r", "v1", Path("x.zip"), data)
    assert result["status"] == "reconciled_skip"
    assert result["sha256_local"] == digest

    monkeypatch.setattr(
        publish,
        "_list_assets",
        lambda repo, rid: ([{"name": "x.zip", "size": len(data), "digest": "f" * 64}], None),
    )
    assert publish._upload_to_release("o/r", "v1", Path("x.zip"), data)["status"] == "conflict"


# ---------------------------------------------------------------------------
# API error mapping and small helpers
# ---------------------------------------------------------------------------


def test_github_api_error_mapping(monkeypatch):
    def _raise(*a, **k):
        raise GithubIssuesError("auth", "nope")

    monkeypatch.setattr(publish, "readback_release", _raise)
    assert _release_readback("o/r", "v1") == ({}, "api_error:auth")

    monkeypatch.setattr(publish, "create_release_api", _raise)
    assert publish._release_create("o/r", "v1", "n", False, None) == ({}, "api_error:auth")

    monkeypatch.setattr(publish, "list_release_assets", _raise)
    assert _list_assets("o/r", 1) == ([], "api_error:auth")

    monkeypatch.setattr(publish, "upload_release_asset", _raise)
    assert publish._upload_asset({"upload_url": "u"}, "n", b"d") == (None, "api_error:auth")

    monkeypatch.setattr(publish, "list_release_assets", _raise)
    assert _confirm_asset("o/r", 1, "n", 1, "d") == (None, "api_error:auth")


def test_release_for_upload_states(monkeypatch):
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: ({}, "api_error:x"))
    assert publish._release_for_upload("o/r", "v1") == ({}, "api_error:x")
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: ({"exists": False}, None))
    assert publish._release_for_upload("o/r", "v1")[1] == "missing_release"
    monkeypatch.setattr(
        publish, "_release_readback", lambda repo, tag: ({"exists": True}, None)
    )
    assert publish._release_for_upload("o/r", "v1")[1] == "malformed_release"
    release = {"exists": True, "release_id": 1, "upload_url": "u"}
    monkeypatch.setattr(publish, "_release_readback", lambda repo, tag: (release, None))
    assert publish._release_for_upload("o/r", "v1") == (release, None)


def test_api_payload_success_windows(monkeypatch):
    monkeypatch.setattr(publish, "readback_release", lambda repo, tag: {"exists": True})
    assert _release_readback("o/r", "v1") == ({"exists": True}, None)

    monkeypatch.setattr(
        publish, "create_release_api", lambda *a: {"release_id": 1}
    )
    assert publish._release_create("o/r", "v1", "n", False, None) == (
        {"release_id": 1},
        None,
    )

    monkeypatch.setattr(
        publish, "list_release_assets", lambda repo, rid: {"assets": [{"name": "n"}]}
    )
    assert _list_assets("o/r", 1) == ([{"name": "n"}], None)

    monkeypatch.setattr(
        publish, "upload_release_asset", lambda *a: {"id": 3}
    )
    assert publish._upload_asset({"upload_url": "u"}, "n", b"d") == ({"id": 3}, None)


def test_api_payload_error_windows(monkeypatch):
    monkeypatch.setattr(
        publish, "readback_release", lambda repo, tag: {"error": "server_error"}
    )
    assert _release_readback("o/r", "v1") == ({"error": "server_error"}, "api_error:server_error")

    monkeypatch.setattr(
        publish, "create_release_api", lambda *a: {"error": "validation"}
    )
    assert publish._release_create("o/r", "v1", "n", False, None) == (
        {"error": "validation"},
        "api_error:validation",
    )

    monkeypatch.setattr(
        publish, "list_release_assets", lambda repo, rid: {"error": "rate_limit"}
    )
    assert _list_assets("o/r", 1) == ([], "api_error:rate_limit")

    monkeypatch.setattr(
        publish, "upload_release_asset", lambda *a: {"error": "payload"}
    )
    assert publish._upload_asset({"upload_url": "u"}, "n", b"d") == (
        {"error": "payload"},
        "api_error:payload",
    )


def test_confirm_asset_direct(monkeypatch):
    monkeypatch.setattr(publish, "_list_assets", lambda repo, rid: ([], None))
    assert _confirm_asset("o/r", 1, "n", 1, "d") == (None, None)

    monkeypatch.setattr(
        publish,
        "_list_assets",
        lambda repo, rid: ([{"name": "n", "size": 2}], None),
    )
    assert _confirm_asset("o/r", 1, "n", 1, "d") == (None, None)

    asset = {"name": "n", "size": 1, "digest": ""}
    monkeypatch.setattr(publish, "_list_assets", lambda repo, rid: ([asset], None))
    assert _confirm_asset("o/r", 1, "n", 1, "d") == (asset, None)


def test_helpers_edge_inputs(tmp_path):
    assert _resolve_repo_id(5) is None
    assert _resolve_repo_id(" https://github.com/owner/repo.git ") == "owner/repo"
    assert _resolve_repo_id("owner/repo") == "owner/repo"
    assert _resolve_repo_id("not a repo") is None

    assert _candidate_sha_from_notes("candidate_sha=" + OID) == OID
    assert _candidate_sha_from_notes("candidate-sha: deadbeef") is None
    assert _candidate_sha_from_notes(None) is None

    assert _release_matches("nope", "v1", False, None) is False
    assert _release_matches({"tag_name": "v2"}, "v1", False, None) is False
    assert _release_matches({"tag_name": "v1", "prerelease": True}, "v1", False, None) is False
    assert _release_matches({"tag_name": "v1", "prerelease": False}, "v1", False, None) is True

    assert _normalize_sha256(5) is None
    assert _normalize_sha256("SHA256:" + "A" * 64) == "a" * 64
    assert _normalize_sha256("zz") is None
    assert _asset_matches({"size": 1}, 2, "d") is False
    assert _asset_matches({"size": 2, "digest": ""}, 2, "d") is True
    assert _asset_matches({"size": 2, "digest": "sha256:" + "c" * 64}, 2, "c" * 64) is True

    assert _resolve_artifact_target(" ") == (None, "malformed")
    one = tmp_path / "one.bin"
    one.write_bytes(b"x")
    assert _resolve_artifact_target(str(tmp_path / "*.bin")) == (str(one), None)

    missing = tmp_path / "missing.bin"
    data, err = _read_artifact(missing)
    assert data is None and err.startswith("read_error:")

    context = publish._effect_result("done", "r", None)
    assert context == {"status": "done", "remote": "r", "remote_check": {}}
    tagged = publish._tag_result("done", "r", "v1", "ref", {"object_id": OID}, reason="why")
    assert tagged["object_id"] == OID and tagged["reason"] == "why"
    payload = publish._remote_state_payload("r", "refs/x", exists=False, error="oops")
    assert payload["error"] == "oops"
