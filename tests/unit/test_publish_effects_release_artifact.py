"""Direct coverage for release/artifact effects against a loopback stand-in.

The server is a minimal GitHub REST stand-in (releases + upload urls) selected
through ``TRAC_GITHUB_API_BASE``; no external network or real credentials are
used. Contract: coordinator rulings #2/#3 (IF-PUBLISH-001/002).
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from tracks.effects.publish import create_release, upload_artifact

_REPO = "acme/host"
_CANDIDATE = "a" * 40


class _GitHubStandIn:
    """Loopback subset of the releases and release-assets endpoints."""

    def __init__(self):
        self.releases: dict = {}
        self.assets: dict = {}
        self.requests: list = []
        self.created_payloads: list = []
        self.upload_headers: list = []
        self.fail: set = set()
        self.digest_mode = True
        self._next_id = 100
        self.repo_id = _REPO
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):  # pragma: no cover - server noise
                return

            def _read_body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _reply(self, status, body):
                raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                owner.requests.append(("GET", self.path))
                tags_prefix = f"/repos/{owner.repo_id}/releases/tags/"
                if parsed.path.startswith(tags_prefix):
                    if "read" in owner.fail:
                        self._reply(500, {"message": "read failure"})
                        return
                    tag = parsed.path[len(tags_prefix):]
                    release = owner.releases.get(tag)
                    if release is None:
                        self._reply(404, {"message": "not found"})
                        return
                    self._reply(200, owner.release_json(release))
                    return
                releases_prefix = f"/repos/{owner.repo_id}/releases/"
                if parsed.path.startswith(releases_prefix) and parsed.path.endswith("/assets"):
                    if "list" in owner.fail:
                        self._reply(500, {"message": "list failure"})
                        return
                    release_id = int(parsed.path.split("/")[-2])
                    assets = owner.assets.get(release_id, [])
                    self._reply(200, [owner.asset_json(asset) for asset in assets])
                    return
                self._reply(404, {"message": "not found"})

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler protocol
                parsed = urlparse(self.path)
                body = self._read_body()
                owner.requests.append(("POST", self.path))
                if parsed.path == f"/repos/{owner.repo_id}/releases":
                    if "create" in owner.fail:
                        self._reply(500, {"message": "create failure"})
                        return
                    payload = json.loads(body.decode("utf-8"))
                    owner.created_payloads.append(payload)
                    self._reply(201, owner.release_json(owner.new_release(payload)))
                    return
                if parsed.path.startswith("/uploads/"):
                    if "upload" in owner.fail:
                        self._reply(500, {"message": "upload failure"})
                        return
                    release_id = int(parsed.path.split("/")[-2])
                    name = parse_qs(parsed.query).get("name", [""])[0]
                    owner.upload_headers.append(dict(self.headers))
                    self._reply(201, owner.asset_json(owner.new_asset(release_id, name, body)))
                    return
                self._reply(404, {"message": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)

    def release_json(self, release: dict) -> dict:
        return {
            "id": release["id"],
            "tag_name": release["tag_name"],
            "name": release.get("name", release["tag_name"]),
            "body": release.get("body", ""),
            "prerelease": release["prerelease"],
            "target_commitish": release["target_commitish"],
            "upload_url": (
                f"{self.base_url}/uploads/repos/{self.repo_id}"
                f"/releases/{release['id']}/assets{{?name,label}}"
            ),
        }

    def asset_json(self, asset: dict) -> dict:
        payload = {"id": asset["id"], "name": asset["name"], "size": asset["size"]}
        if self.digest_mode:
            payload["digest"] = asset["digest"]
        return payload

    def new_release(self, payload: dict) -> dict:
        release = {
            "id": self._next_id,
            "tag_name": payload["tag_name"],
            "name": payload.get("name", payload["tag_name"]),
            "body": payload.get("body", ""),
            "prerelease": bool(payload.get("prerelease")),
            "target_commitish": payload.get("target_commitish", ""),
        }
        self._next_id += 1
        self.releases[release["tag_name"]] = release
        return release

    def new_asset(self, release_id: int, name: str, body: bytes) -> dict:
        asset = {
            "id": self._next_id,
            "name": name,
            "size": len(body),
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        }
        self._next_id += 1
        self.assets.setdefault(release_id, []).append(asset)
        return asset

    def seed_release(self, tag: str, *, prerelease: bool = False,
                     target_commitish: str = _CANDIDATE) -> dict:
        return self.new_release(
            {
                "tag_name": tag,
                "name": tag,
                "body": "",
                "prerelease": prerelease,
                "target_commitish": target_commitish,
            }
        )

    def seed_asset(self, tag: str, name: str, data: bytes) -> dict:
        return self.new_asset(self.releases[tag]["id"], name, data)


@pytest.fixture
def standin():
    server = _GitHubStandIn().start()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def api(monkeypatch, standin):
    monkeypatch.setenv("GITHUB_TOKEN", "loopback-token")
    monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)
    return standin


def _artifact(tmp_path, name: str = "demo-0.1.0-py3-none-any.whl",
              data: bytes = b"wheel-bytes"):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    path = dist / name
    path.write_bytes(data)
    return path


def _upload_posts(api) -> list:
    return [request for request in api.requests
            if request[0] == "POST" and request[1].startswith("/uploads/")]


# AC-FR0275-01@v0.8: create binds tag/prerelease/target_commitish; repeat skips.
def test_create_release_done_then_reconciled_skip(api):
    notes = f"candidate_sha={_CANDIDATE}\npreview_digest=sha256:abc\njourney=feature\n"

    first = create_release(_REPO, "v1.0.0", notes, False, target_commitish=_CANDIDATE)

    assert first["status"] == "done"
    assert first["release_id"] == 100
    assert first["remote_check"]["tag_name"] == "v1.0.0"
    assert len(api.created_payloads) == 1
    payload = api.created_payloads[0]
    assert payload["tag_name"] == "v1.0.0"
    assert payload["name"] == "v1.0.0"
    assert payload["body"] == notes
    assert payload["prerelease"] is False
    assert payload["target_commitish"] == _CANDIDATE

    second = create_release(_REPO, "v1.0.0", notes, False, target_commitish=_CANDIDATE)

    assert second["status"] == "reconciled_skip"
    assert second["release_id"] == 100
    assert len(api.created_payloads) == 1


# AC-FR0275-02@v0.8: an existing divergent release is a conflict, not overwritten.
def test_create_release_existing_divergence_is_conflict(api):
    api.seed_release("v1.0.0", prerelease=True, target_commitish=_CANDIDATE)

    result = create_release(_REPO, "v1.0.0", "notes", False, target_commitish=_CANDIDATE)

    assert result["status"] == "conflict"
    assert api.created_payloads == []


def test_create_release_target_commitish_mismatch_is_conflict(api):
    api.seed_release("v1.0.0", prerelease=False, target_commitish="b" * 40)

    result = create_release(_REPO, "v1.0.0", "notes", False, target_commitish=_CANDIDATE)

    assert result["status"] == "conflict"


def test_create_release_derives_target_from_notes(api):
    notes = f"candidate_sha: {_CANDIDATE}\njourney=feature\n"

    result = create_release(_REPO, "v1.0.0", notes, False)

    assert result["status"] == "done"
    assert api.created_payloads[0]["target_commitish"] == _CANDIDATE


def test_create_release_api_errors_are_structured(api):
    api.fail.add("read")
    read_result = create_release(_REPO, "v1.0.0", "notes", False)
    assert read_result["status"] == "failed"
    assert read_result["reason"].startswith("api_error:")

    api.fail.discard("read")
    api.fail.add("create")
    create_result = create_release(_REPO, "v1.0.0", "notes", False)
    assert create_result["status"] == "failed"
    assert create_result["reason"].startswith("api_error:")
    assert api.created_payloads == []


def test_release_unsupported_remote(api, tmp_path):
    release_result = create_release("https://gitlab.com/acme/host.git", "v1.0.0", "notes", False)
    assert release_result["status"] == "failed"
    assert release_result["reason"] == "unsupported_remote"

    artifact = _artifact(tmp_path)
    artifact_result = upload_artifact("git@github.com:acme/host.git", "v1.0.0", str(artifact))
    assert artifact_result["status"] == "failed"
    assert artifact_result["reason"] == "unsupported_remote"
    assert api.requests == []


def test_github_remote_url_resolves_to_repo_id(api):
    api.seed_release("v1.0.0", prerelease=False, target_commitish=_CANDIDATE)

    result = create_release(
        "https://github.com/acme/host.git", "v1.0.0", "notes", False,
        target_commitish=_CANDIDATE,
    )

    assert result["status"] == "reconciled_skip"


# AC-FR0275-01@v0.8: upload records name/size/local digest and readback confirms.
def test_upload_artifact_done_then_reconciled_skip(api, tmp_path):
    api.seed_release("v1.0.0")
    path = _artifact(tmp_path)
    pattern = str(tmp_path / "dist" / "*.whl")

    first = upload_artifact(_REPO, "v1.0.0", pattern)

    assert first["status"] == "done"
    assert first["name"] == path.name
    assert first["size"] == path.stat().st_size
    assert first["sha256_local"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first["remote_digest"] == "sha256:" + first["sha256_local"]
    assert len(_upload_posts(api)) == 1
    assert api.upload_headers[0]["Content-Type"] == "application/octet-stream"

    second = upload_artifact(_REPO, "v1.0.0", pattern)

    assert second["status"] == "reconciled_skip"
    assert len(_upload_posts(api)) == 1


def test_upload_artifact_conflict_when_same_name_differs(api, tmp_path):
    api.seed_release("v1.0.0")
    path = _artifact(tmp_path)
    api.seed_asset("v1.0.0", path.name, b"different-bytes")

    result = upload_artifact(_REPO, "v1.0.0", str(path))

    assert result["status"] == "conflict"
    assert _upload_posts(api) == []


def test_upload_artifact_digest_mismatch_is_conflict(api, tmp_path):
    api.seed_release("v1.0.0")
    path = _artifact(tmp_path)
    api.seed_asset("v1.0.0", path.name, path.read_bytes())["digest"] = "sha256:" + "0" * 64

    result = upload_artifact(_REPO, "v1.0.0", str(path))

    assert result["status"] == "conflict"


def test_upload_artifact_size_only_binding_without_remote_digest(api, tmp_path):
    api.digest_mode = False
    api.seed_release("v1.0.0")
    path = _artifact(tmp_path)

    first = upload_artifact(_REPO, "v1.0.0", str(path))
    second = upload_artifact(_REPO, "v1.0.0", str(path))

    assert first["status"] == "done"
    assert "remote_digest" not in first
    assert first["sha256_local"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert second["status"] == "reconciled_skip"
    assert "remote_digest" not in second


def test_upload_artifact_missing_release_and_malformed(api, tmp_path):
    path = _artifact(tmp_path)

    missing = upload_artifact(_REPO, "v1.0.0", str(path))
    assert missing["status"] == "failed"
    assert missing["reason"] == "missing_release"

    zero = upload_artifact(_REPO, "v1.0.0", str(tmp_path / "empty" / "*.whl"))
    assert zero["status"] == "failed"
    assert zero["reason"] == "malformed"

    _artifact(tmp_path, name="other-0.1.0-py3-none-any.whl", data=b"other")
    many = upload_artifact(_REPO, "v1.0.0", str(tmp_path / "dist" / "*.whl"))
    assert many["status"] == "failed"
    assert many["reason"] == "malformed"


def test_upload_artifact_api_errors_are_structured(api, tmp_path):
    api.seed_release("v1.0.0")
    path = _artifact(tmp_path)

    api.fail.add("list")
    list_result = upload_artifact(_REPO, "v1.0.0", str(path))
    assert list_result["status"] == "failed"
    assert list_result["reason"].startswith("api_error:")

    api.fail.discard("list")
    api.fail.add("upload")
    upload_result = upload_artifact(_REPO, "v1.0.0", str(path))
    assert upload_result["status"] == "failed"
    assert upload_result["reason"].startswith("api_error:")
