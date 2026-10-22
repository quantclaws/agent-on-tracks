"""GitHub release and artifact API boundary (FR-0275, IF-PUBLISH-001/002).

Releases/assets ride the same stand-in channel; transport/HTTP failures land
in normalized ``error`` fields; missing credentials keep raising the
classified ``missing_token`` error (never a silent pass). The public surface
remains ``tracks.effects.github`` (re-exported).
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request

from tracks.effects.github_transport import (
    _api_base,
    _require_token,
)


def _facade():
    """The re-exporting facade, resolved lazily to keep imports cycle-free."""
    from tracks.effects import github

    return github


def _release_payload(
    data: object = None, *, exists: bool = False, error: str | None = None
) -> dict:
    fields = data if isinstance(data, dict) else {}
    return {
        "exists": exists,
        "tag_name": fields.get("tag_name"),
        "prerelease": fields.get("prerelease"),
        "target_commitish": fields.get("target_commitish"),
        "release_id": fields.get("id"),
        "upload_url": fields.get("upload_url"),
        "error": error,
    }


def readback_release(repo_id: str, tag: str) -> dict:
    """GET /repos/{repo}/releases/tags/{tag} normalized readback (FR-0275):
    404 is a normal absence (``exists=False``); other failures land in
    ``error``; missing credentials raise the classified ``missing_token``."""
    _require_token()
    req = _facade()._api_request(f"{_api_base()}/repos/{repo_id}/releases/tags/{tag}", "GET")
    data, error, status = _facade()._api_json(req)
    if error is not None:
        if status == 404:
            return _release_payload()
        return _release_payload(error=error)
    if not isinstance(data, dict):
        return _release_payload(error="malformed")
    return _release_payload(data, exists=True)


def create_release_api(
    repo_id: str,
    tag: str,
    notes: str,
    prerelease: bool,
    target_commitish: str | None = None,
) -> dict:
    """POST /repos/{repo}/releases: tag_name/name=tag, deterministic notes."""
    _require_token()
    payload = {
        "tag_name": tag,
        "name": tag,
        "body": notes,
        "prerelease": bool(prerelease),
    }
    if target_commitish:
        payload["target_commitish"] = target_commitish
    req = _facade()._api_request(f"{_api_base()}/repos/{repo_id}/releases", "POST", payload)
    data, error, _status = _facade()._api_json(req)
    if error is not None:
        return _release_payload(error=error)
    if not isinstance(data, dict):
        return _release_payload(error="malformed")
    return _release_payload(data, exists=True)


def list_release_assets(repo_id: str, release_id: object) -> dict:
    """GET /repos/{repo}/releases/{id}/assets -> {"assets", "error"}."""
    _require_token()
    url = f"{_api_base()}/repos/{repo_id}/releases/{release_id}/assets"
    data, error, _status = _facade()._api_json(_facade()._api_request(url, "GET"))
    if error is not None:
        return {"assets": [], "error": error}
    if not isinstance(data, list):
        return {"assets": [], "error": "malformed"}
    assets = []
    for asset in data:
        if not isinstance(asset, dict):
            return {"assets": [], "error": "malformed"}
        assets.append(
            {
                "id": asset.get("id"),
                "name": asset.get("name"),
                "size": asset.get("size"),
                "digest": asset.get("digest"),
            }
        )
    return {"assets": assets, "error": None}


def upload_release_asset(
    upload_url: str,
    name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> dict:
    """POST one asset to the release ``upload_url`` (``{?name,label}`` form)."""
    _require_token()
    base = str(upload_url or "").split("{", 1)[0]
    if not base:
        return {
            "id": None,
            "name": name,
            "size": None,
            "digest": None,
            "error": "malformed_upload_url",
        }
    query_name = urllib.parse.quote(str(name), safe="")
    url = f"{base}{'&' if '?' in base else '?'}name={query_name}"
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "Content-Type": content_type,
            "Content-Length": str(len(data)),
        },
    )
    payload, error, _status = _facade()._api_json(req)
    if error is not None:
        return {"id": None, "name": name, "size": None, "digest": None, "error": error}
    fields = payload if isinstance(payload, dict) else {}
    return {
        "id": fields.get("id"),
        "name": fields.get("name", name),
        "size": fields.get("size"),
        "digest": fields.get("digest"),
        "error": None,
    }
