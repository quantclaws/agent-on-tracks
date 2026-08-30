"""M-MILESTONE executor domain (FR-0276/FR-0284, IF-MILESTONE-001).

Release trace closure (approved AC → … → release), Issue/Project/milestone
lifecycle with audited close comments, read-only evidence sealing, temp refs
cleanup, and the RETRY_TAIL boundary that retries only the tail after a
successful publish.
"""

from __future__ import annotations

from pathlib import Path


def build_release_trace(events: list[dict], candidate_sha: str) -> dict:
    """Pure join: the NFR-0143 same-identity proof chain."""
    raise NotImplementedError("IF-MILESTONE-001")


def compute_trace_digest(trace: dict) -> str:
    raise NotImplementedError("IF-MILESTONE-001")


def close_issues_with_comment(repo: Path, issue_map: dict, trace: dict) -> list[dict]:
    """Close each mapped issue with a release-trace comment; re-verify map."""
    raise NotImplementedError("IF-ISSUE-002")


def close_project_milestone(repo: Path, tracker: dict, trace: dict) -> dict:
    raise NotImplementedError("IF-ISSUE-002")


def seal_evidence_readonly(repo: Path, candidate_sha: str) -> dict:
    raise NotImplementedError("IF-MILESTONE-001")


def clean_temp_refs(repo: Path, run_id: str) -> dict:
    """Remove refs/trac/tmp/{run}/*; verified empty via for-each-ref."""
    raise NotImplementedError("IF-MILESTONE-001")
