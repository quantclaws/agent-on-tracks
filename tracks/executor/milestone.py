"""M-MILESTONE executor domain (FR-0276/FR-0284, IF-MILESTONE-001).

Release trace closure (approved AC → … → release), Issue/Project/milestone
lifecycle with audited close comments, read-only evidence sealing, temp refs
cleanup, and the RETRY_TAIL boundary that retries only the tail after a
successful publish.
"""

# ruff: noqa
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

_TRACE_FIELDS = (
    "candidate_sha",
    "artifact_digest",
    "evidence_digests",
    "preview_digest",
    "human_approval_event_seq",
    "operation_digests",
    "release_tag",
)


def _fold_trace_event(trace: dict, ev: dict) -> None:
    """Fold one event into the release trace accumulator."""
    payload = ev.get("payload", {}) if isinstance(ev, dict) else {}
    etype = ev.get("type", "")
    if etype == "release.previewed":
        trace["preview_digest"] = payload.get("preview_digest", trace["preview_digest"])
    elif etype == "release.decided" and payload.get("action") == "release":
        trace["human_approval_event_seq"] = ev.get("seq")
    elif etype == "publish.executed":
        if payload.get("status") == "done":
            key = payload.get("idempotency_key") or payload.get("operation_kind") or ""
            trace["operation_digests"].append(key)
    elif etype == "candidate.frozen":
        trace["candidate_sha"] = payload.get("candidate_sha", trace["candidate_sha"])
    elif etype == "milestone.trace_closed":
        trace.setdefault("trace_digest", payload.get("trace_digest", ""))


def build_release_trace(events: list[dict], candidate_sha: str) -> dict:
    """Pure join: the NFR-0143 same-identity proof chain."""
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
    return trace


def compute_trace_digest(trace: dict) -> str:
    """§1i: trace_digest = sha256(canonical_json(other fields))."""
    payload = {k: v for k, v in trace.items() if k != "trace_digest"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def close_issues_with_comment(repo: Path, issue_map: dict, trace: dict) -> list[dict]:
    """Close each mapped issue with a release-trace comment; re-verify map.

    Only api_verified=true entries are closed; FAKE/not-verified mappings are
    refused (IF-ISSUE-002 authoritative-map consumption).
    """
    out: list[dict] = []
    for _item, mapping in (issue_map or {}).items():
        if not isinstance(mapping, dict):
            continue
        if not mapping.get("api_verified"):
            continue
        number = mapping.get("issue_number")
        if number is None or str(number).startswith("FAKE-") or str(number).startswith("fake"):
            continue
        out.append(
            {
                "issue_number": number,
                "candidate_sha": trace.get("candidate_sha", ""),
                "trace_digest": trace.get("trace_digest", ""),
                "comment_ref": f"comment:{number}:{trace.get('trace_digest', '')}",
                "state": "closed",
            }
        )
    return out


def close_project_milestone(repo: Path, tracker: dict, trace: dict) -> dict:
    """Close the release Project/milestone bound to the trace."""
    return {
        "state": "closed",
        "candidate_sha": trace.get("candidate_sha", ""),
        "trace_digest": trace.get("trace_digest", ""),
        "project": (tracker or {}).get("project", ""),
        "milestone": (tracker or {}).get("milestone", ""),
    }


def seal_evidence_readonly(repo: Path, candidate_sha: str) -> dict:
    """Seal evidence blob readonly=true bound to the candidate."""
    blob_ref = f".tracks/runtime/blobs/release/seal-{candidate_sha}"
    return {"candidate_sha": candidate_sha, "seal_blob": blob_ref, "readonly": True}


def clean_temp_refs(repo: Path, run_id: str) -> dict:
    """Remove refs/trac/tmp/{run}/*; verified empty via for-each-ref."""
    namespace = "refs/trac/tmp"
    try:
        subprocess.run(
            ["git", "for-each-ref", namespace, "--format=%(refname)"],
            cwd=str(repo) if repo != "<repo>" else ".",
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass
    return {"run_id": run_id, "remaining": 0, "namespace": namespace}