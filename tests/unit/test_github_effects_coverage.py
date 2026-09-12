"""Behavior coverage for the GitHub effects boundary (``github``).

Item/AC parsing, fake and live issue backends, CI run readback pagination
and the pure binding judge, issue verify/reject/persist, close chain and
release/asset API error classification (FR-0200, FR-0270, FR-0284,
IF-PUBLISH-001).
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from tracks.effects import github
from tracks.effects.github import (
    FakeIssueBackend,
    GithubBackend,
    GithubIssuesError,
    _ac_index,
    _api_json,
    _issue_api_repo,
    _item_blocks,
    _select_workflow_run,
    _workflow_job_page,
    _workflow_jobs,
    _workflow_jobs_complete,
    _workflow_matches,
    _workflow_run_error,
    close_issue,
    close_project_milestone,
    create_issue_verified,
    create_release_api,
    issue_items,
    judge_ci_binding,
    list_release_assets,
    persist_issue_mapping,
    readback_ci_run,
    readback_issue,
    readback_release,
    reject_fake_artifact,
    select_issue_backend,
    upload_release_asset,
)

SPEC = """\
---
version: v0.1
---

### FR-0001 Story capture
The runtime captures raw requirements.

### NFR-0002 Latency
Fast enough.

## 5. IF Registry
"""

ACC = """\
---
version: v0.1
---

## FR-0001

### AC-FR0001-01 captured
Raw text is stored.

### AC-FR0001-02 queued
Backlog path.

## NFR-0002

### AC-NFR0002-01 fast
under budget.
"""


def _vdir(tmp_path):
    vdir = tmp_path / "v0.1"
    vdir.mkdir()
    (vdir / "spec.md").write_text(SPEC, encoding="utf-8")
    (vdir / "acceptance.md").write_text(ACC, encoding="utf-8")
    return vdir


def _http_error(code, reason="reason"):
    return urllib.error.HTTPError("http://x", code, reason, {}, None)


# ---------------------------------------------------------------------------
# parsing helpers
# ---------------------------------------------------------------------------


def test_item_blocks_splits_items_and_drops_quote_lines():
    blocks = _item_blocks(SPEC)
    assert blocks == [
        ("FR-0001", "Story capture", "The runtime captures raw requirements."),
        ("NFR-0002", "Latency", "Fast enough."),
    ]
    assert _item_blocks("> quoted\n### FR-0001 T\nbody") == [
        ("FR-0001", "T", "body")
    ]


def test_ac_index_groups_sections():
    index = _ac_index(ACC)
    assert index["FR-0001"] == ["AC-FR0001-01 captured", "AC-FR0001-02 queued"]
    assert index["NFR-0002"] == ["AC-NFR0002-01 fast"]
    assert _ac_index("## FR-0001\n## Other\n### AC-FR0001-01 orphan") == {"FR-0001": []}


def test_issue_items_body_shape(tmp_path):
    items = issue_items(_vdir(tmp_path), "DIGEST")
    assert [i[0] for i in items] == ["FR-0001", "NFR-0002"]
    item_id, title, body = items[0]
    assert title == "[FR-0001] Story capture"
    assert "## Acceptance criteria" in body
    assert "- AC-FR0001-01 captured" in body
    assert "baseline digest: DIGEST" in body


# ---------------------------------------------------------------------------
# FakeIssueBackend
# ---------------------------------------------------------------------------


def test_fake_backend_token_sequence_and_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_FAKE_SIMULATE", "github:create=fail|ok")
    backend = FakeIssueBackend(tmp_path, "v0.1")
    with pytest.raises(GithubIssuesError) as exc:
        backend.create_issue("t", "b", [])
    assert exc.value.classification == "network"
    assert backend.create_issue("t", "b", ["x"]) == "FAKE-1"
    assert backend.create_issue("t2", "b2", []) == "FAKE-2"


def test_fake_backend_persists_issues_and_project_noop(tmp_path):
    backend = FakeIssueBackend(tmp_path, "v0.1")
    assert backend.create_issue("t", "b", []) == "FAKE-1"
    assert backend.add_to_project("FAKE-1", "proj") is None
    stored = json.loads((tmp_path / ".tracks" / "runtime" / "issues.json").read_text())
    assert stored["FAKE-1"]["title"] == "t"


def test_fake_backend_fetch_issue_seed(tmp_path):
    backend = FakeIssueBackend(tmp_path, "v0.1")
    assert backend.fetch_issue(1) is None

    host_path = tmp_path / ".tracks" / "runtime" / "host-issues.json"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    host_path.write_text("{bad json", encoding="utf-8")
    assert backend.fetch_issue(1) is None

    host_path.write_text(json.dumps({"1": "not-a-dict"}), encoding="utf-8")
    assert backend.fetch_issue(1) is None

    host_path.write_text(
        json.dumps({"1": {"title": "T", "body": "B", "labels": "oops"}}), encoding="utf-8"
    )
    issue = backend.fetch_issue(1)
    assert issue.title == "T" and issue.labels == ()

    host_path.write_text(
        json.dumps({"1": {"title": "T", "body": "B", "labels": ["bug", 7]}}),
        encoding="utf-8",
    )
    issue = backend.fetch_issue(1)
    assert issue.labels == ("bug", "7")


# ---------------------------------------------------------------------------
# GithubBackend / selection
# ---------------------------------------------------------------------------


def test_github_backend_requires_repo_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    with pytest.raises(GithubIssuesError) as exc:
        GithubBackend(Path("/tmp"), "v0.1")
    assert exc.value.classification == "not_found"


def test_github_backend_get_and_fetch_issue(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "o/r")
    backend = GithubBackend(tmp_path, "v0.1")

    seen = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=30):
        seen.append(req.method)
        return _Resp(
            json.dumps(
                {"title": "T", "body": "B", "labels": [{"name": "bug"}, "skip", {}]}
            ).encode()
        )

    monkeypatch.setattr(github.urllib.request, "urlopen", _fake_urlopen)
    issue = backend.fetch_issue(5)
    assert seen == ["GET"]
    assert issue.title == "T" and issue.labels == ("bug",)

    monkeypatch.setattr(
        github.urllib.request,
        "urlopen",
        lambda req, timeout=30: (_ for _ in ()).throw(_http_error(404, "gone")),
    )
    assert backend.fetch_issue(5) is None

    monkeypatch.setattr(
        github.urllib.request,
        "urlopen",
        lambda req, timeout=30: (_ for _ in ()).throw(_http_error(401, "denied")),
    )
    with pytest.raises(GithubIssuesError) as exc:
        backend.fetch_issue(5)
    assert exc.value.classification == "auth"


def test_select_issue_backend_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.delenv("TRAC_AGENT_BACKEND", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as exc:
        select_issue_backend(tmp_path, "v0.1")
    assert exc.value.classification == "missing_token"

    monkeypatch.setenv("TRAC_AGENT_BACKEND", "fake")
    assert isinstance(select_issue_backend(tmp_path, "v0.1"), FakeIssueBackend)

    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "o/r")
    assert isinstance(select_issue_backend(tmp_path, "v0.1"), GithubBackend)


# ---------------------------------------------------------------------------
# workflow matching / pagination
# ---------------------------------------------------------------------------


def test_workflow_matches_branches():
    assert _workflow_matches({}, "") is False
    assert _workflow_matches({"workflow_id": 42}, "42") is True
    assert _workflow_matches({"path": ".github/workflows/ci.yml"}, "ci.yml") is True
    assert _workflow_matches({"path": ".github/workflows/ci.yml"}, "other.yml") is False
    assert _workflow_matches({}, "ci.yml") is False


def test_workflow_job_page_validation(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    def _get(data):
        monkeypatch.setattr(github, "_get_any", lambda req: data)
        return _workflow_job_page("https://api.example", "o/r", 1, 1, {}, None)

    assert _get("not-a-dict") == (0, None, False)
    assert _get({"jobs": []}) == (0, None, True)
    assert _get({"jobs": [], "total_count": True}) == (0, None, False)
    assert _get({"jobs": [], "total_count": 5}) == (0, 5, True)
    assert _get({"jobs": ["nope"], "total_count": 1}) == (0, 1, False)
    assert _get({"jobs": [{"name": "a"}], "total_count": 1}) == (1, 1, True)

    checks = {"a": None}
    monkeypatch.setattr(github, "_get_any", lambda req: {"jobs": [{"name": "a"}]})
    assert _workflow_job_page("https://api.example", "o/r", 1, 1, checks, None) == (0, None, False)

    monkeypatch.setattr(github, "_get_any", lambda req: {"jobs": [], "total_count": 5})
    assert _workflow_job_page("https://api.example", "o/r", 1, 1, {}, 9) == (0, 9, False)


def test_workflow_jobs_complete_branches():
    assert _workflow_jobs_complete({}, 100, None) is None
    assert _workflow_jobs_complete({}, 3, None) is True
    assert _workflow_jobs_complete({"a": 1, "b": 2}, 0, 1) is False
    assert _workflow_jobs_complete({"a": 1}, 0, 1) is True
    assert _workflow_jobs_complete({}, 0, 2) is False
    assert _workflow_jobs_complete({}, 100, 5) is None


def test_workflow_jobs_paths(monkeypatch):
    monkeypatch.setattr(
        github, "_workflow_job_page", lambda *a: (0, None, False)
    )
    assert _workflow_jobs("b", "o/r", 1) == ({}, False)

    monkeypatch.setattr(
        github, "_workflow_job_page", lambda *a: (0, 5, True)
    )
    assert _workflow_jobs("b", "o/r", 1) == ({}, False)

    monkeypatch.setattr(
        github, "_workflow_job_page", lambda *a: (5, None, True)
    )
    assert _workflow_jobs("b", "o/r", 1) == ({}, True)

    monkeypatch.setattr(
        github, "_workflow_job_page", lambda *a: (100, None, True)
    )
    assert _workflow_jobs("b", "o/r", 1) == ({}, False)


# ---------------------------------------------------------------------------
# CI readback / judge
# ---------------------------------------------------------------------------


def test_select_workflow_run_and_run_error():
    assert _select_workflow_run(None, "ci.yml") == (None, "malformed")
    assert _select_workflow_run({"workflow_runs": "x"}, "ci.yml") == (None, "malformed")
    assert _select_workflow_run({"workflow_runs": []}, "ci.yml") == (None, "missing")
    assert _select_workflow_run({"workflow_runs": [{"path": "other"}]}, "ci.yml") == (
        None,
        "workflow_mismatch",
    )
    run = {"path": "ci.yml"}
    assert _select_workflow_run({"workflow_runs": [run]}, "ci.yml") == (run, None)

    assert _workflow_run_error({"id": True, "head_sha": "s"}) == ("malformed", "s", True)
    assert _workflow_run_error({"id": 1, "head_sha": ""}) == ("missing", "", 1)
    assert _workflow_run_error({"id": 1, "head_sha": "s", "status": "queued"}) == (
        "run_incomplete",
        "s",
        1,
    )
    assert _workflow_run_error({"id": 1, "head_sha": "s", "status": "completed"}) == (
        None,
        "s",
        1,
    )


def test_readback_ci_run_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError) as exc:
        readback_ci_run("o/r", "ci.yml", "abc")
    assert exc.value.classification == "missing_token"


def test_judge_ci_binding_fail_closed_paths():
    assert judge_ci_binding(None, "sha", [])["reason"] == "missing"
    assert judge_ci_binding({"stale": True}, "sha", [])["reason"] == "stale"
    assert judge_ci_binding({"head_sha": ""}, "sha", [])["reason"] == "missing"
    assert judge_ci_binding({"head_sha": "sha", "reason": "jobs_incomplete"}, "sha", [])[
        "reason"
    ] == "check_failed"
    malformed = judge_ci_binding({"head_sha": "sha", "reason": "malformed"}, "sha", [])
    assert malformed["status"] == "failed" and malformed["reason"] == "malformed"
    assert judge_ci_binding({"head_sha": "other"}, "sha", [])["reason"] == "mismatch"
    assert judge_ci_binding(
        {"head_sha": "sha", "conclusion": "failure"}, "sha", []
    )["reason"] == "failed_conclusion"
    assert judge_ci_binding(
        {"head_sha": "sha", "conclusion": "success", "checks": {}}, "sha", ["unit"]
    )["reason"] == "check_failed"
    passed = judge_ci_binding(
        {"head_sha": "sha", "conclusion": "success", "checks": {"unit": "success"}},
        "sha",
        ["unit"],
    )
    assert passed["status"] == "passed" and passed["reason"] == "bound"


def test_readback_ci_run_outcome_payload(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    runs = {
        "workflow_runs": [
            {"id": 1, "head_sha": "sha", "status": "completed", "conclusion": "success",
             "path": "ci.yml", "name": "CI"}
        ]
    }
    monkeypatch.setattr(github, "_get_any", lambda req: runs)
    monkeypatch.setattr(github, "_workflow_jobs", lambda *a: ({"unit": "success"}, True))
    payload = readback_ci_run("o/r", "ci.yml", "sha")
    assert payload["status"] == "passed"
    assert payload["api_verified"] is True


# ---------------------------------------------------------------------------
# issue verify / persist / close
# ---------------------------------------------------------------------------


def test_readback_issue_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError):
        readback_issue("o/r", 1)

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(_http_error(403))
    )
    with pytest.raises(GithubIssuesError) as exc:
        readback_issue("o/r", 1)
    assert exc.value.classification == "rate_limit"

    monkeypatch.setattr(
        github,
        "_get_any",
        lambda req: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    with pytest.raises(GithubIssuesError) as exc:
        readback_issue("o/r", 1)
    assert exc.value.classification == "network"

    monkeypatch.setattr(
        github, "_get_any", lambda req: {"number": 2, "title": "T", "state": "open"}
    )
    assert readback_issue("o/r", 1)["api_verified"] is True


def test_create_issue_verified_paths(tmp_path, monkeypatch):
    fake = FakeIssueBackend(tmp_path, "v0.1")
    mapping = create_issue_verified(fake, "t", "b", [])
    assert mapping["api_verified"] is False

    class _FakeArtifact:
        gh_repo = "o/r"

        def create_issue(self, title, body, labels):
            return "FAKE-99"

    assert create_issue_verified(_FakeArtifact(), "t", "b", []) == {
        "issue_number": "FAKE-99",
        "api_verified": False,
    }

    class _Live:
        gh_repo = "o/r"

        def create_issue(self, title, body, labels):
            return "7"

    monkeypatch.setattr(
        github, "readback_issue", lambda repo, number: {"title": "T", "state": "open", "api_verified": True}
    )
    mapping = create_issue_verified(_Live(), "t", "b", [])
    assert mapping == {"issue_number": "7", "title": "T", "state": "open", "api_verified": True}


def test_persist_issue_mapping_merge_and_corrupt(tmp_path):
    assert persist_issue_mapping(tmp_path, "FR-1", {"issue_number": 1}) == {
        "FR-1": {"issue_number": 1}
    }
    path = tmp_path / ".tracks" / "runtime" / "issue-map.json"
    path.write_text("{corrupt", encoding="utf-8")
    assert persist_issue_mapping(tmp_path, "FR-2", {"issue_number": 2}) == {
        "FR-2": {"issue_number": 2}
    }
    assert persist_issue_mapping(tmp_path, "FR-3", {"issue_number": 3})["FR-3"] == {
        "issue_number": 3
    }


def test_reject_fake_artifact():
    assert reject_fake_artifact("FAKE-1")["reason"] == "fake_rejected"
    assert reject_fake_artifact("fake-1")["reason"] == "fake_rejected"
    assert reject_fake_artifact("42") == {
        "issue_id": "42",
        "status": "ok",
        "reason": "not_fake",
        "api_verified": False,
    }


def test_issue_api_repo_fallback(monkeypatch):
    monkeypatch.setenv("TRAC_GITHUB_REPO", "env/repo")
    assert _issue_api_repo("") == "env/repo"
    assert _issue_api_repo("explicit/repo") == "explicit/repo"
    monkeypatch.delenv("TRAC_GITHUB_REPO", raising=False)
    with pytest.raises(GithubIssuesError) as exc:
        _issue_api_repo("")
    assert exc.value.classification == "not_found"


def test_close_issue_chain(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github, "_api_request", lambda url, method, payload=None: (url, method))
    responses = iter(
        [
            ({"id": 11}, None, None),  # comment
            ({"state": "closed"}, None, None),  # patch
        ]
    )
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    monkeypatch.setattr(
        github, "readback_issue", lambda repo, number: {"state": "closed", "issue_number": number}
    )
    result = close_issue("o/r", 7, "done")
    assert result["api_verified"] is True and result["comment_id"] == 11

    responses = iter([({"id": 12}, None, None), ({"state": "open"}, None, None)])
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    monkeypatch.setattr(github, "readback_issue", lambda repo, number: {"state": "open", "issue_number": number})
    result = close_issue("o/r", 7, "done")
    assert result["api_verified"] is False and result["error"] == "readback_unconfirmed"

    responses = iter([(None, "network: down", None)])
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    result = close_issue("o/r", 7, "")
    assert result["error"] == "network: down"
    assert result["comment_id"] is None

    responses = iter([(None, "auth: nope", 401)])
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    assert close_issue("o/r", 7, "comment")["api_verified"] is False

    monkeypatch.setattr(github, "_api_json", lambda req: ({"state": "closed"}, None, None))
    monkeypatch.setattr(
        github,
        "readback_issue",
        lambda repo, number: (_ for _ in ()).throw(GithubIssuesError("network", "down")),
    )
    result = close_issue("o/r", 7, "")
    assert result["error"] == "network: down"


def test_close_project_milestone_paths(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    result = close_project_milestone("o/r", "p", None)
    assert result["error"] == "milestone_not_declared"

    monkeypatch.setattr(github, "_api_request", lambda url, method, payload=None: (url, method))
    responses = iter(
        [
            ({"state": "closed"}, None, None),
            ({"state": "closed"}, None, None),
        ]
    )
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    result = close_project_milestone("o/r", "p", 3)
    assert result["api_verified"] is True

    responses = iter([(None, "auth: nope", 401)])
    monkeypatch.setattr(github, "_api_json", lambda req: next(responses))
    result = close_project_milestone("o/r", "p", 3)
    assert result["api_verified"] is False and result["error"] == "auth: nope"


# ---------------------------------------------------------------------------
# release / asset API
# ---------------------------------------------------------------------------


def test_api_json_classification(monkeypatch):
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(_http_error(404))
    )
    assert _api_json(object())[1] == "not_found: HTTP 404: reason"
    monkeypatch.setattr(
        github,
        "_get_any",
        lambda req: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    assert _api_json(object())[1] == "network: down"
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(ValueError("bad"))
    )
    assert _api_json(object())[1] == "malformed: bad"


def test_readback_release_paths(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GithubIssuesError):
        readback_release("o/r", "v1")

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(_http_error(404))
    )
    assert readback_release("o/r", "v1")["exists"] is False

    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(urllib.error.URLError("x"))
    )
    result = readback_release("o/r", "v1")
    assert result["error"] == "network: x"

    monkeypatch.setattr(github, "_get_any", lambda req: ["not-a-dict"])
    assert readback_release("o/r", "v1")["error"] == "malformed"

    monkeypatch.setattr(
        github,
        "_get_any",
        lambda req: {"tag_name": "v1", "prerelease": False, "id": 9, "upload_url": "u"},
    )
    result = readback_release("o/r", "v1")
    assert result["exists"] is True and result["release_id"] == 9


def test_create_release_api_paths(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(_http_error(422, "bad"))
    )
    assert create_release_api("o/r", "v1", "n", True, "sha")["error"].startswith("network")
    monkeypatch.setattr(github, "_get_any", lambda req: "not-a-dict")
    assert create_release_api("o/r", "v1", "n", True, "sha")["error"] == "malformed"
    monkeypatch.setattr(github, "_get_any", lambda req: {"id": 3, "tag_name": "v1"})
    assert create_release_api("o/r", "v1", "n", False)["release_id"] == 3


def test_list_release_assets_paths(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setattr(github, "_get_any", lambda req: {"not": "a list"})
    assert list_release_assets("o/r", 1)["error"] == "malformed"
    monkeypatch.setattr(github, "_get_any", lambda req: [1, {"name": "a"}])
    assert list_release_assets("o/r", 1)["error"] == "malformed"
    monkeypatch.setattr(
        github,
        "_get_any",
        lambda req: [{"id": 2, "name": "a", "size": 10, "digest": "sha256:x"}],
    )
    assets = list_release_assets("o/r", 1)
    assert assets["assets"][0]["name"] == "a" and assets["error"] is None
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(_http_error(403))
    )
    assert list_release_assets("o/r", 1)["error"].startswith("rate_limit")


def test_upload_release_asset_paths(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert upload_release_asset("", "a.bin", b"x")["error"] == "malformed_upload_url"
    monkeypatch.setattr(
        github, "_get_any", lambda req: {"id": 4, "name": "a.bin", "size": 1, "digest": "d"}
    )
    result = upload_release_asset("https://uploads/{?name}", "a.bin", b"x")
    assert result == {"id": 4, "name": "a.bin", "size": 1, "digest": "d", "error": None}
    monkeypatch.setattr(
        github, "_get_any", lambda req: (_ for _ in ()).throw(urllib.error.URLError("down"))
    )
    assert upload_release_asset("https://uploads/", "a.bin", b"x")["error"] == "network: down"
