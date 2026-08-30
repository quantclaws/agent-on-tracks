"""M-PUBLISH effects boundary (IF-PUBLISH-001): irreversible operations.

Git merge/tag pushes and GitHub release/artifact mutations happen ONLY here,
driven by the Runtime executor. No agent path can reach this module; remote
readback helpers serve the reconcile loop. Env (GITHUB_TOKEN and friends) is
read only at this boundary, mirroring effects/github.py discipline.
"""

from __future__ import annotations


def push_merge(remote_url: str, source_ref: str, target_branch: str) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


def push_tag(remote_url: str, tag: str, ref: str) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


def create_release(repo_id: str, tag: str, notes: str, prerelease: bool) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


def upload_artifact(repo_id: str, tag: str, artifact_path: str) -> dict:
    raise NotImplementedError("IF-PUBLISH-001")


def read_remote_state(remote_url: str, kind: str, target: str) -> dict:
    """ls-remote / tag existence / release-artifact API readback."""
    raise NotImplementedError("IF-PUBLISH-002")
