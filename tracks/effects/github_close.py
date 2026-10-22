"""GitHub issue/project close chain and milestone ensure (FR-0284, FR-0331).

The M-MILESTONE closer's irreversible effects: comment + close + readback
per mapping; transport/HTTP failures land in ``error``, never api_verified.
First-contact ensure: the closing chain asks the remote to already have the
declared milestone; api_verified is never claimed on an unobserved milestone.
The public surface remains ``tracks.effects.github`` (re-exported).
"""

from __future__ import annotations

import os
import urllib.parse

from tracks.effects.github_transport import (
    GithubIssuesError,
    _api_base,
    _require_token,
)


def _facade():
    """The re-exporting facade, resolved lazily to keep imports cycle-free."""
    from tracks.effects import github

    return github


# -- IF-ISSUE-002 issue/project close chain (FR-0284) --------------------------
# The M-MILESTONE closer's irreversible effects: comment + close + readback
# per mapping; transport/HTTP failures land in ``error``, never api_verified.


def _issue_api_repo(repo_id: str) -> str:
    resolved = str(repo_id or "").strip() or os.environ.get("TRAC_GITHUB_REPO", "")
    if not resolved:
        raise GithubIssuesError("not_found", "TRAC_GITHUB_REPO not set (owner/name)")
    return resolved


def close_issue(repo_id: str, issue_number: int, comment: str) -> dict:
    """Comment + close + API readback for one issue (AC-FR0284-01): the
    durable audit comment lands first, then ``state=closed`` is PATCHed and
    re-read — only an observed closed state for the same number verifies."""
    _require_token()
    repo = _issue_api_repo(repo_id)
    base = _api_base()
    number = int(issue_number)
    comment_payload: dict = {"comment_id": None}
    if comment:
        body, error, _status = _facade()._api_json(
            _facade()._api_request(
                f"{base}/repos/{repo}/issues/{number}/comments", "POST", {"body": comment}
            )
        )
        if error is not None:
            return {"issue_number": number, "state": "", "comment_id": None,
                    "api_verified": False, "error": error}
        if isinstance(body, dict):
            comment_payload["comment_id"] = body.get("id")
    data, error, _status = _facade()._api_json(
        _facade()._api_request(f"{base}/repos/{repo}/issues/{number}", "PATCH", {"state": "closed"})
    )
    if error is not None:
        return {"issue_number": number, "state": "", "comment_id": comment_payload["comment_id"],
                "api_verified": False, "error": error}
    state = str((data or {}).get("state") or "") if isinstance(data, dict) else ""
    try:
        readback = _facade().readback_issue(repo, number)
    except GithubIssuesError as exc:
        return {
            "issue_number": number,
            "state": state,
            "comment_id": comment_payload["comment_id"],
            "api_verified": False,
            "error": f"{exc.classification}: {exc}",
        }
    verified = readback.get("state") == "closed" and int(readback.get("issue_number", -1)) == number
    return {
        "issue_number": number,
        "state": readback.get("state", state),
        "comment_id": comment_payload["comment_id"],
        "api_verified": bool(verified),
        "error": None if verified else "readback_unconfirmed",
    }


def _list_milestones(base: str, repo: str) -> tuple[list | None, str | None]:
    """GET /repos/{repo}/milestones?state=all; (listing, classified error)."""
    listing, list_error, _status = _facade()._api_json(
        _facade()._api_request(f"{base}/repos/{repo}/milestones?state=all", "GET")
    )
    if list_error is not None:
        return None, list_error
    if not isinstance(listing, list):
        return None, "milestone_listing_malformed"
    return listing, None


def _resolve_milestone_number(base: str, repo: str, title: str) -> tuple[str | None, str | None]:
    """Resolve a title-declared milestone to its number (title, error):
    REST addresses milestones by NUMBER (a title path 404s); ``state=all``
    keeps closed ones findable; numeric declarations pass through."""
    if title.isdigit():
        return title, None
    listing, list_error = _list_milestones(base, repo)
    if list_error is not None:
        return None, list_error
    for item in listing:
        if isinstance(item, dict) and str(item.get("title") or "") == title:
            number = item.get("number")
            return (str(number) if number is not None else None), None
    return None, "milestone_not_found"


def _patch_milestone_closed(url: str) -> tuple[str, str | None]:
    """PATCH the milestone closed and read the observed state back."""
    data, error, _status = _facade()._api_json(
        _facade()._api_request(url, "PATCH", {"state": "closed"})
    )
    if error is not None:
        return "", error
    state = str((data or {}).get("state") or "") if isinstance(data, dict) else ""
    readback, readback_error, _status = _facade()._api_json(
        _facade()._api_request(url, "GET")
    )
    if readback_error is None and isinstance(readback, dict):
        state = str(readback.get("state") or state)
    return state, None


def close_project_milestone(repo_id: str, project: str, milestone) -> dict:
    """PATCH + readback for the release Project/milestone (AC-FR0284-01):
    only an observed ``state=closed`` verifies; an empty milestone is a
    malformed request (callers emit the audited skip instead)."""
    _require_token()
    repo = _issue_api_repo(repo_id)
    if milestone in (None, ""):
        return {"project": project, "milestone": milestone, "state": "",
                "api_verified": False, "error": "milestone_not_declared"}
    base = _api_base()
    milestone_ref, resolve_error = _resolve_milestone_number(base, repo, str(milestone))
    if resolve_error is not None or milestone_ref is None:
        return {"project": project, "milestone": milestone, "state": "",
                "api_verified": False, "error": resolve_error or "milestone_not_found"}
    milestone_segment = urllib.parse.quote(milestone_ref, safe="")
    state, error = _patch_milestone_closed(f"{base}/repos/{repo}/milestones/{milestone_segment}")
    if error is not None:
        return {"project": project, "milestone": milestone, "state": "",
                "api_verified": False, "error": error}
    verified = state == "closed"
    return {
        "project": project,
        "milestone": milestone,
        "state": state,
        "api_verified": verified,
        "error": None if verified else "readback_unconfirmed",
    }


# -- IF-TRACKER-001 milestone ensure primitive (FR-0331, interfaces 1r.5.1) ----
# First-contact ensure: the closing chain asks the remote to already have the
# declared milestone; api_verified is never claimed on an unobserved milestone.


def ensure_project_milestone(repo_id: str, project: str, title: str) -> dict:
    """Ensure the release milestone exists (interfaces 1r.5.1):
    ``GET /repos/{repo}/milestones?state=all`` by exact title match — a hit
    is reused (``created=false``, ``api_verified=true``; the listing is the
    API observation); a miss with credentials POSTs exactly one create and
    verifies it by a GET readback (``created=true``), idempotently reusing
    the same title. Any failure returns the classified ``error`` result —
    never a false ``api_verified``."""
    result = {
        "project": project,
        "milestone": str(title or ""),
        "number": None,
        "state": "",
        "created": False,
        "api_verified": False,
        "error": None,
    }
    try:
        return _ensure_milestone(result, repo_id, title)
    except GithubIssuesError as exc:
        return {**result, "error": f"{exc.classification}: {exc}"}


def _ensure_milestone(result: dict, repo_id: str, title: str) -> dict:
    """Authorized ensure: list -> exact title match -> reuse, else create."""
    _require_token()
    repo = _issue_api_repo(repo_id)
    wanted = str(title or "")
    if not wanted:
        return {**result, "error": "milestone_not_declared"}
    listing, list_error = _list_milestones(_api_base(), repo)
    if list_error is not None:
        return {**result, "error": list_error}
    for item in listing:
        if isinstance(item, dict) and str(item.get("title") or "") == wanted:
            return {
                **result,
                "number": item.get("number"),
                "state": str(item.get("state") or ""),
                "api_verified": True,
            }
    return _create_milestone_verified(result, _api_base(), repo, wanted)


def _create_milestone_verified(
    result: dict, base: str, repo: str, title: str
) -> dict:
    """POST one create, then GET the milestone back before claiming verified."""
    data, error, _status = _facade()._api_json(
        _facade()._api_request(
            f"{base}/repos/{repo}/milestones", "POST", {"title": title}
        )
    )
    if error is not None:
        return {**result, "error": error}
    if not isinstance(data, dict) or data.get("number") is None:
        return {**result, "error": "malformed"}
    number = data.get("number")
    readback, readback_error, _status = _facade()._api_json(
        _facade()._api_request(f"{base}/repos/{repo}/milestones/{number}", "GET")
    )
    if readback_error is not None:
        # The create was issued but never observed back — honest created,
        # unverified, classified error (never a false api_verified).
        return {**result, "created": True, "error": readback_error}
    fields = readback if isinstance(readback, dict) else {}
    verified = (
        str(fields.get("title") or "") == title
        and str(fields.get("number")) == str(number)
    )
    return {
        **result,
        "number": number,
        "state": str(fields.get("state") or ""),
        "created": True,
        "api_verified": verified,
        "error": None if verified else "readback_unconfirmed",
    }
