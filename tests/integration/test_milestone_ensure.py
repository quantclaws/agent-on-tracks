"""Tracker milestone lifecycle completion (IF-TRACKER-001).

The ensure face (list -> create -> API readback, idempotent reuse) is driven
against the loopback milestone stand-in; the first-contact wiring in the
closing chain is driven through the milestone mixin, observing the
``attention.required(area=project_close, reason=milestone_not_found)``
audited-skip outlet with its actionable next, and the success after the
operator's manual repair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._support.github_api_standin import GithubApiStandIn

pytestmark = pytest.mark.integration

_TRACE = {
    "trace_digest": "sha256:" + "0" * 64,
    "candidate_sha": "a" * 40,
    "release_tag": "v0.8.0",
}


def _tracker_repo(tmp_path: Path) -> Path:
    """A host repo declaring the tracker channel + milestone template."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("tracker host\n", encoding="utf-8")
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "tracker@example.com"),
        ("config", "user.name", "Tracker Human"),
        ("add", "README.md"),
        ("commit", "-m", "initial"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    contract = repo / ".tracks" / "projects" / "project.toml"
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        "[host-contract]\n"
        "version = 1\n"
        'language = "python"\n'
        'toolchain = "cpython"\n'
        'install = "true"\n'
        "\n[host-contract.tracker]\n"
        'repo_env = "TRAC_GITHUB_REPO"\n'
        'project_env = "TRAC_GITHUB_PROJECT"\n'
        'milestone_template = "release {version}"\n',
        encoding="utf-8",
    )
    return repo


def _make_executor(repo: Path):
    from tracks.executor.executor import Executor
    from tracks.kernel.events import Command
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(repo))
    run_id = "run-tracker-1"
    store.append(run_id, "v0.8", "story.requested", {"raw_chars": 1})
    executor = Executor(store, repo, run_id)
    command = Command(
        kind="close_milestone",
        params={
            "tracker": {"repo": "acme/host", "project": "", "milestone": "release v0.8"}
        },
        command_id="cmd-tracker-close-1",
    )
    return store, executor, command, run_id


def _service_events(repo: Path) -> list[dict]:
    from tracks.paths import tracks_home
    from tracks.store import Store

    store = Store(tracks_home(repo))
    try:
        return [
            {"type": e.type, "payload": dict(e.payload or {})}
            for e in store.events("run-tracker-1")
        ]
    finally:
        store.close()


# AC-FR0331-01@v0.10 TRACKS-TRACE ensure milestone create reuse readback
def test_ensure_milestone_create_reuse_readback(tmp_path, monkeypatch):
    """AC-FR0331-01: with the repo/project/milestone declared and credentials
    present, the first tracker contact creates the remote milestone and reads
    it back through the API; a repeated contact reuses the same title without
    a second create (idempotent, never a fake verified claim)."""
    from tracks.effects import github

    monkeypatch.setenv("GITHUB_TOKEN", "tracker-ensure-token")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    standin = GithubApiStandIn().start()
    try:
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)

        ensure = getattr(github, "ensure_project_milestone", None)
        assert ensure is not None, (
            "IF-TRACKER-001 §1r.5 declares ensure_project_milestone on "
            "tracks/effects/github.py"
        )

        first = ensure("acme/host", "", "release v0.8")
        assert first.get("created") is True, (
            f"the first contact must create the milestone: {first!r}"
        )
        assert first.get("api_verified") is True, (
            "the create must be verified by the API readback"
        )
        assert first.get("number")
        creates_after_first = len(standin.created)
        assert creates_after_first == 1, "exactly one create on the first contact"

        second = ensure("acme/host", "", "release v0.8")
        assert second.get("created") is False, (
            f"the repeated contact must reuse the milestone: {second!r}"
        )
        assert second.get("api_verified") is True
        assert len(standin.created) == creates_after_first, (
            "the idempotent reuse must not create a second milestone"
        )
    finally:
        standin.close()


# AC-FR0331-02@v0.10 TRACKS-TRACE milestone not found actionable next, retry works
def test_milestone_not_found_actionable_next(tmp_path, monkeypatch):
    """AC-FR0331-02: when the remote milestone is absent and cannot be created
    automatically, the closing chain lands the audited
    ``milestone_not_found`` skip with an actionable next (create the
    template-rendered title manually, then resume); after the manual repair
    the same contact closes the milestone for real."""
    repo = _tracker_repo(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "tracker-repair-token")
    monkeypatch.setenv("TRAC_GITHUB_REPO", "acme/host")
    monkeypatch.setenv("TRAC_GITHUB_PROJECT", "")
    standin = GithubApiStandIn(create_status=422).start()
    try:
        monkeypatch.setenv("TRAC_GITHUB_API_BASE", standin.base_url)
        store, executor, command, _run_id = _make_executor(repo)
        try:
            # first contact: creation is refused by the remote
            executor._close_milestone_project(command, "task-1", _TRACE, dict(command.params), [])
            attention = [
                e for e in _service_events(repo)
                if e["type"] == "attention.required"
                and (e["payload"] or {}).get("area") == "project_close"
            ]
            assert attention, "the failed ensure must land the audited attention"
            payload = attention[-1]["payload"]
            assert payload["reason"] == "milestone_not_found"
            next_step = str(payload.get("next") or "")
            assert "release v0.8" in next_step, (
                f"the next must name the template-rendered milestone title: {next_step!r}"
            )
            assert "resume" in next_step, (
                f"the next must direct the resume retry: {next_step!r}"
            )
            closed = [
                e for e in _service_events(repo) if e["type"] == "project.closed"
            ]
            assert closed and closed[-1]["payload"]["state"] == "skipped", (
                "the failed ensure keeps the audited skip form"
            )
            assert closed[-1]["payload"]["api_verified"] is False

            # the operator manually creates the milestone; the retry closes
            standin.create_status = 201
            standin.milestones.append(
                {"number": 7, "title": "release v0.8", "state": "open"}
            )
            store.append(
                "run-tracker-1", "v0.8", "stage.entered", {"stage": "M-MILESTONE"}
            )
            executor._close_milestone_project(
                command, "task-1", _TRACE, dict(command.params), []
            )
            retry_closed = [
                e for e in _service_events(repo) if e["type"] == "project.closed"
            ]
            assert retry_closed[-1]["payload"]["state"] == "closed", (
                "after the manual repair the contact must close the milestone"
            )
            assert retry_closed[-1]["payload"]["api_verified"] is True
            assert len(standin.created) == 0, (
                "the reused milestone must not be created again"
            )
        finally:
            store.close()
    finally:
        standin.close()
