"""T-021 RED: publish reconcile contract (FR-0275/NFR-0144, IF-PUBLISH-002).

Pins the still-undelivered slices of the publish contract (registered IF
vocabulary IF-PUBLISH-002; scope = executor/publish.py + effects/publish.py):

- AC-FR0275-02 / AC-NFR0144-01: remote-authoritative reconcile — an
  already-executed op whose remote state matches is a skip (reconciled_skip,
  no duplicate effect); an absent remote target is pending (execute).
- AC-NFR0144-02: same idempotency key with divergent remote content is a
  conflict (reconcile_conflict audit, blocked).
- IF-PUBLISH-002 effects half: read_remote_state reads the real remote
  (ls-remote / tag existence) — exists/not-exists is decided from the remote,
  never assumed.

All target tests fail on the pre-fix baseline with assertion_failure on the
contract behaviour (no stub_token, no assembly errors). Only unit tests are
added (RED discipline, manifest red_test_paths = tests/unit).
"""

from __future__ import annotations

import subprocess

from tracks.executor.publish import reconcile_operation


# AC-FR0275-02@v0.8 TRACKS-TRACE IF-PUBLISH-002 skip on matching remote
def test_reconcile_skip_when_remote_matches():
    """Already-executed op with matching remote state -> skip (reconciled_skip)."""
    planned = {"idempotency_key": "k1", "target": "t", "kind": "tag"}
    try:
        verdict = reconcile_operation(planned, {"exists": True, "matches": True})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: reconcile_operation not implemented (skip path)"
        ) from err
    assert verdict == "skip", (
        f"assertion failure: matching remote must yield skip, got {verdict!r}"
    )


# AC-NFR0144-01@v0.8 TRACKS-TRACE IF-PUBLISH-002 pending when remote absent
def test_reconcile_pending_when_remote_absent():
    """Unfinished op (remote absent) -> pending: reconcile continues, not skips."""
    planned = {"idempotency_key": "k2", "target": "t", "kind": "tag"}
    try:
        verdict = reconcile_operation(planned, {"exists": False})
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: reconcile_operation not implemented (pending path)"
        ) from err
    assert verdict == "pending", (
        f"assertion failure: absent remote must yield pending, got {verdict!r}"
    )


# AC-NFR0144-02@v0.8 TRACKS-TRACE IF-PUBLISH-002 conflict on remote divergence
def test_reconcile_conflict_when_remote_differs():
    """Same key, divergent remote content -> conflict (reconcile_conflict)."""
    planned = {"idempotency_key": "sha256:abc", "target": "t", "digest": "d1"}
    try:
        verdict = reconcile_operation(
            planned,
            {"idempotency_key": "sha256:abc", "target": "t", "digest": "d2"},
        )
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: reconcile_operation not implemented (conflict path)"
        ) from err
    assert verdict == "conflict", (
        f"assertion failure: divergent remote must yield conflict, got {verdict!r}"
    )


# IF-PUBLISH-002@v0.8 TRACKS-TRACE read_remote_state reads the real remote
def test_read_remote_state_tag_existence(tmp_path):
    """effects half: read_remote_state decides existence from the actual
    remote (ls-remote / tag list), never by assumption."""
    try:
        from tracks.effects.publish import read_remote_state
    except ImportError as err:
        raise AssertionError(
            "assertion failure: read_remote_state not implemented in effects"
        ) from err

    seed = tmp_path / "seed"
    seed.mkdir()
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(seed)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.name", "T"],
        check=True,
        capture_output=True,
    )
    (seed / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "f.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-qm", "init"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(seed), "tag", "v1.0.0"], check=True, capture_output=True
    )
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(seed), "push", "-q", str(bare), "refs/heads/main:refs/heads/main",
         "refs/tags/v1.0.0:refs/tags/v1.0.0"],
        check=True,
        capture_output=True,
    )

    try:
        tag_hit = read_remote_state(str(bare), "tag", "v1.0.0")
        tag_miss = read_remote_state(str(bare), "tag", "v9.9.9")
        branch_hit = read_remote_state(str(bare), "merge", "main")
    except NotImplementedError as err:
        raise AssertionError(
            "assertion failure: read_remote_state not implemented"
        ) from err
    assert isinstance(tag_hit, dict) and tag_hit.get("exists") is True, (
        f"assertion failure: existing tag must be found, got {tag_hit!r}"
    )
    assert isinstance(tag_miss, dict) and not tag_miss.get("exists"), (
        f"assertion failure: missing tag must not exist, got {tag_miss!r}"
    )
    assert isinstance(branch_hit, dict) and branch_hit.get("exists") is True, (
        f"assertion failure: existing branch must be found, got {branch_hit!r}"
    )
