"""T-009 RED: hotfix precheck classification mapping (FR-0329, IF-HOTFIX-011, #180).

Devon-owned unit RED for the T-009 delivery slice (scope:
``tracks/executor/hotfix_face.py`` fetch-face classification intake +
``tracks/executor/hotfix.py`` classification -> next guidance mapping):

- the fetch face (``precheck_hotfix_report`` / ``select_issue_backend``
  chain) must stop swallowing every ``GithubIssuesError`` into
  ``issue=None``: a classified fetch failure (``auth`` / ``rate_limit`` /
  ``network`` / ``missing_token``) rejects as ``issue_fetch_failed`` and
  carries a classification-specific actionable ``next`` (interfaces 1r.3):
  missing_token -> configure ``GITHUB_TOKEN`` per the ops prerequisites,
  auth -> check token permissions, rate_limit -> wait for the quota reset,
  network -> check connectivity/TLS or ``TRAC_GITHUB_CA_BUNDLE``;
- ``issue_not_found`` stays narrow (AC-FR0329-01 semantics): only a
  confirmed missing issue (the 404->None confirmation from ``fetch_issue``
  or a raised ``not_found`` classification) may report it;
- the ``issue_fetch_failed`` REJECTED path keeps the retry path open: once
  the operator fixes the environment, a fresh precheck over the same entry
  passes through the unchanged pure rules (IF-HOTFIX-003 signature/rules
  unchanged — the pass report below is produced by the real rules engine,
  not a stub).

RED discipline: every node below fails on the current baseline with a real
``AssertionError`` — the fetch face maps every classified failure to
``issue_not_found`` today (the #180 defect: the comment promises
``issue_fetch_failed`` but it is unreachable), so the ``reason``
assertions fail first; no stub tokens, no assembly errors. Only the
external GitHub boundary (``select_issue_backend``) is replaced by a
scripted stand-in; the classification intake under test is the real
``precheck_hotfix_report`` face (test-plan 6.2: hotfix precheck rules are
the system under test and are never mocked).

AC: FR-0329 — TRACKS-TRACE IF-HOTFIX-011.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git_repo
from tracks.effects.github import GithubIssuesError
from tracks.executor import hotfix_face
from tracks.executor.hotfix import HostIssue
from tracks.store import Store


def _backend_raising(classification: str, message: str = "fetch blew up"):
    """A stand-in issue backend whose fetch fails with a classified error."""

    def _fetch(_number: int):
        raise GithubIssuesError(classification, message)

    return SimpleNamespace(fetch_issue=_fetch)


def _backend_returning(issue: HostIssue | None):
    """A stand-in issue backend yielding a confirmed snapshot (or None)."""
    return SimpleNamespace(fetch_issue=lambda _number: issue)


def _entry_fixture(tmp_path: Path) -> tuple[Path, Store]:
    """A real git repo + event store pair for the precheck entry face."""
    repo = git_repo(tmp_path)
    store = Store(tmp_path / ".tracks")
    return repo, store


# AC-FR0329-02@v0.10 TRACKS-TRACE IF-HOTFIX-011 classified failure + next
@pytest.mark.parametrize(
    "classification,expected_token",
    [
        ("missing_token", "github_token"),
        ("auth", "permission"),
        ("rate_limit", "rate limit"),
        ("network", "trac_github_ca_bundle"),
    ],
)
def test_fetch_failures_reject_as_issue_fetch_failed_with_classified_next(
    tmp_path, monkeypatch, classification, expected_token
):
    """AC-FR0329-02: a classified fetch failure must reject as
    ``issue_fetch_failed`` (not the stale ``issue_not_found`` swallow) and
    carry its classification-specific recovery pointer in ``next``."""
    repo, store = _entry_fixture(tmp_path)
    monkeypatch.setattr(
        hotfix_face,
        "select_issue_backend",
        lambda _repo, _version: _backend_raising(classification),
    )
    report, issue = hotfix_face.precheck_hotfix_report(repo, store, 101, "post-release")
    assert issue is None, (
        f"assertion failure: a failed fetch yields no issue snapshot, got {issue!r}"
    )
    assert report.status == "rejected", (
        f"assertion failure: classified fetch failure ({classification}) must reject, "
        f"got status={report.status!r}"
    )
    assert report.reason == "issue_fetch_failed", (
        f"assertion failure: classified fetch failure ({classification}) must map to "
        f"issue_fetch_failed (IF-HOTFIX-011), got {report.reason!r}"
    )
    assert report.next and expected_token in report.next.lower(), (
        f"assertion failure: {classification} rejection must carry its classified "
        f"recovery pointer in next (expected token {expected_token!r}), "
        f"got {report.next!r}"
    )


# AC-FR0329-01@v0.10 TRACKS-TRACE IF-HOTFIX-011 issue_not_found stays narrow
def test_confirmed_missing_stays_issue_not_found_fetch_failure_does_not(tmp_path, monkeypatch):
    """AC-FR0329-01 narrowness: only confirmed missing (the 404->None
    confirmation or a raised ``not_found`` classification) reports
    ``issue_not_found``; a network fetch failure must not reuse it."""
    repo, store = _entry_fixture(tmp_path)
    outcomes: dict[str, str | None] = {}
    for key, backend in (
        ("raised_not_found", _backend_raising("not_found", "TRAC_GITHUB_REPO not set")),
        ("returned_none", _backend_returning(None)),
        ("raised_network", _backend_raising("network", "down")),
    ):
        monkeypatch.setattr(
            hotfix_face, "select_issue_backend", lambda _r, _v, _b=backend: _b
        )
        report, _issue = hotfix_face.precheck_hotfix_report(repo, store, 101, "post-release")
        outcomes[key] = report.reason
    assert outcomes == {
        "raised_not_found": "issue_not_found",
        "returned_none": "issue_not_found",
        "raised_network": "issue_fetch_failed",
    }, (
        f"assertion failure: issue_not_found must stay narrow to confirmed missing "
        f"(IF-HOTFIX-011); fetch failures belong to issue_fetch_failed, got {outcomes!r}"
    )


# AC-FR0329-02@v0.10 TRACKS-TRACE IF-HOTFIX-011 REJECTED retry path reopens
def test_fetch_failed_rejection_reopens_retry_after_environment_fix(tmp_path, monkeypatch):
    """AC-FR0329-02 retry-open: the ``issue_fetch_failed`` rejection leaves
    the entry retryable — once the environment is fixed, a fresh precheck
    over the same entry passes through the unchanged pure rules."""
    repo, store = _entry_fixture(tmp_path)
    store.append("OTHER", "v0.5", "approval.recorded", {"digest": "d", "actor": "a"})
    vdir = repo / ".tracks" / "projects" / "v0.5"
    vdir.mkdir(parents=True)
    (vdir / "spec.md").write_text("s", encoding="utf-8")
    (vdir / "acceptance.md").write_text("a", encoding="utf-8")

    monkeypatch.setattr(
        hotfix_face,
        "select_issue_backend",
        lambda _repo, _version: _backend_raising("missing_token", "GITHUB_TOKEN not set"),
    )
    rejected, missing = hotfix_face.precheck_hotfix_report(repo, store, 101, "post-release")
    assert missing is None, (
        f"assertion failure: a failed fetch yields no issue snapshot, got {missing!r}"
    )
    assert rejected.status == "rejected", (
        f"assertion failure: classified fetch failure must reject, "
        f"got status={rejected.status!r}"
    )
    assert rejected.reason == "issue_fetch_failed", (
        f"assertion failure: missing_token fetch failure must map to issue_fetch_failed "
        f"(IF-HOTFIX-011), got {rejected.reason!r}"
    )

    bug = HostIssue(101, "[#180] hotfix precheck swallows fetch failures", "body", ("bug",))
    monkeypatch.setattr(
        hotfix_face,
        "select_issue_backend",
        lambda _repo, _version: _backend_returning(bug),
    )
    passed, fetched = hotfix_face.precheck_hotfix_report(repo, store, 101, "post-release")
    assert fetched == bug, (
        f"assertion failure: the retry precheck must fetch the fixed-channel issue, "
        f"got {fetched!r}"
    )
    assert passed.status == "pass", (
        f"assertion failure: after the environment fix the retry precheck must pass "
        f"through the unchanged rules (IF-HOTFIX-003), got "
        f"{passed.status}/{passed.reason}"
    )
    assert passed.target_version == "v0.5", (
        f"assertion failure: the retry pass must locate the approved baseline, "
        f"got {passed.target_version!r}"
    )
