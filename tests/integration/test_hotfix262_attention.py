"""Integration: credential attention, dedup and loop halt (IF-ATTN-001).

The contract under test is INV-6 / INV-7 (interfaces §1d):

* ``GITHUB_TOKEN`` is the ONLY canonical credential -- there is NO
  ``GITHUB_ACCESS_TOKEN`` / ``GH_TOKEN`` fallback;
* a missing canonical token while a non-canonical one is present yields a
  DIAGNOSTIC HINT ONLY (never a silent fallback);
* an unresolved, non-auto-retryable ``attention.required`` makes the run
  loop stop cleanly at ``needs_attention`` (no tight loop, no
  ``CommandStallError``);
* the missing-token attention is emitted AT MOST ONCE (the dedup).

Assertions enter through the public outlets:

* the issue-backend selection (``tracks.effects.github_transport.
  select_issue_backend``) and the issue-creation handler
  (``release_tail._ensure_issue_backend``) -- the public entry the M-REQ-
  APPROVAL path drives;
* the run loop's preflight (``run_loop._run_loop_preflight`` /
  ``_pending_operator_attention``) -- the public loop entry;
* the EVENT STREAM (interfaces §4a outlet 4): the
  ``attention.required(area="issue_creation", reason="missing_token")``
  payload, its count and its hint.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_MISSING_TOKEN_HINT = "export GITHUB_TOKEN"


class _Denied(Exception):
    """The production ``deny`` callback's terminal disposition."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(reason, detail)
        self.reason = reason
        self.detail = detail


def _new_host(tmp_path: Path) -> tuple[Path, Store]:
    repo = tmp_path / "host"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "hf262@example.com"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "HF262 Human"], cwd=repo, check=True)
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True
    )
    # The version docs the issue-creation path reads (the story/spec/acceptance
    # trio) -- fixture data, not a product artifact.
    vdir = paths.version_dir(paths.tracks_home(repo), "v0.8")
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "story.md").write_text(
        "---\ntitle: hotfix-262 host story\n---\n\n# Story\n\nFR-0267 the fixture story.\n",
        encoding="utf-8",
    )
    (vdir / "spec.md").write_text(
        "---\n---\n\n# Spec\n\n## FR-0267 M-VERIFY\n\nthe fixture requirement body.\n",
        encoding="utf-8",
    )
    (vdir / "acceptance.md").write_text(
        "---\n---\n\n# Acceptance\n\n## FR-0267 M-VERIFY\n\n### AC-FR0267-01\n\n  - the fixture acceptance clause.\n",
        encoding="utf-8",
    )
    return repo, Store(paths.tracks_home(repo))


def _approval_digest(repo: Path) -> str:
    """The approved baseline digest the issue-creation path recomputes."""
    from tracks.baseline import revision_digest

    return revision_digest(paths.version_dir(paths.tracks_home(repo), "v0.8"))


def _attention_events(store: Store, run_id: str) -> list:
    return [
        event
        for event in store.events(run_id)
        if event.type == "attention.required"
    ]


def _issue_creation_attentions(store: Store, run_id: str) -> list:
    return [
        event
        for event in _attention_events(store, run_id)
        if (event.payload or {}).get("area") == "issue_creation"
        and (event.payload or {}).get("reason") == "missing_token"
    ]


# AC-FR0283-02@v0.8 TRACKS-TRACE missing token yields one attention and a hint
def test_missing_token_single_attention_and_hint(tmp_path: Path, monkeypatch):
    """AC-FR0283-02: with the canonical ``GITHUB_TOKEN`` missing and a
    non-canonical ``GITHUB_ACCESS_TOKEN`` present, the issue-creation path
    emits AT MOST ONE ``attention.required(area="issue_creation",
    reason="missing_token")`` carrying an actionable hint, and NO issue is
    created.

    Contract: interfaces §1d items 1 (canonical credential + hint-only) and
    3 (the dedup), §4a outlet 4.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # The issue backend must NOT be the fake channel: a fake backend would
    # create the issue without a credential, hiding the contract under test.
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-ATTN-1"
    digest = _approval_digest(repo)
    try:
        store.append(
            run_id,
            "v0.8",
            "stage.entered",
            {"stage": "M-REQ-APPROVAL", "digest": digest},
        )
        executor = Executor(store, repo, run_id)
        cmd = Command(
            kind="create_issues",
            params={"digest": digest},
            command_id="cmd-issues-1",
        )
        executor._do_create_issues(cmd, store.state(run_id), None, False)
        first = _issue_creation_attentions(store, run_id)
        assert first, (
            "the missing-token path must emit an issue_creation attention "
            f"(got {len(first)})"
        )
        payload = first[0].payload or {}
        assert payload.get("area") == "issue_creation"
        assert payload.get("reason") == "missing_token", (
            "the attention must carry the contract's closed reason "
            f"missing_token (got {payload.get('reason')!r})"
        )
        hint = str(payload.get("next") or "") + str(payload.get("detail") or "")
        assert _MISSING_TOKEN_HINT in hint, (
            "the missing-token attention must carry an actionable hint naming "
            f"the canonical credential export (hint={hint!r})"
        )
        assert "issue.created" not in [
            event.type for event in store.events(run_id)
        ], "a missing credential must not produce an issue.created record"
        assert "issue.mapped" not in [event.type for event in store.events(run_id)]

        # The dedup: the attention is emitted AT MOST once.
        assert len(first) == 1, (
            "the missing-token attention must be emitted at most once "
            f"(got {len(first)})"
        )
        executor._do_create_issues(cmd, store.state(run_id), None, False)
        second = _issue_creation_attentions(store, run_id)
        assert len(second) == 1, (
            "the missing-token attention must be emitted at most once "
            f"(got {len(second)})"
        )
    finally:
        store.close()


# AC-FR0298-02@v0.9 TRACKS-TRACE loop halts on unresolved attention
def test_loop_halts_on_unresolved_attention(tmp_path: Path, monkeypatch):
    """AC-FR0298-02: an unresolved, non-auto-retryable attention makes the
    run loop stop cleanly -- no tight loop, no ``CommandStallError``.

    Contract: interfaces §1d item 2 (``_run_loop_preflight`` checks the
    pending attention on EVERY loop iteration and stops the loop at
    ``needs_attention``), §4a outlet 4.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    run_id = "RUN-HF262-ATTN-2"
    try:
        store.append(run_id, "v0.8", "stage.entered", {"stage": "M-REQ-APPROVAL"})
        store.append(
            run_id,
            "v0.8",
            "attention.required",
            {
                "area": "issue_creation",
                "reason": "missing_token",
                "stage": "M-REQ-APPROVAL",
                "next": "export GITHUB_TOKEN then re-run",
            },
        )
        executor = Executor(store, repo, run_id)
        pending = executor._pending_operator_attention(store.state(run_id))
        assert pending is not None, (
            "an unresolved non-auto-retryable attention must be visible to the "
            "loop preflight"
        )
        payload = pending.payload or {}
        assert payload.get("reason") == "missing_token"

        # The loop stops: the preflight returns a terminal disposition and
        # the run does not spin. The stall breaker is NOT tripped (a
        # CommandStallError would be a hot loop, not a clean stop).
        before = len(list(store.events(run_id)))
        disposition, state = executor._run_loop_preflight()
        assert disposition == "return", (
            "the loop preflight must return a terminal disposition for an "
            f"unresolved attention (got {disposition!r})"
        )
        assert state is not None
        after = len(list(store.events(run_id)))
        assert after == before, (
            "the halted loop must not keep issuing commands (the attention "
            "count grew from "
            f"{before} to {after})"
        )
    finally:
        store.close()


# AC-FR0329-02@v0.10 TRACKS-TRACE issue fetch failed classification preserved
def test_issue_fetch_failed_classification_preserved(tmp_path: Path, monkeypatch):
    """AC-FR0329-02: a missing credential during the ISSUE FETCH is
    classified ``issue_fetch_failed`` (NOT ``issue_not_found``), and the
    recovery guidance names the canonical credential.

    The hotfix delta adds the CREDENTIAL PREFLIGHT at the hotfix entry
    (interfaces §1d item 4: "create_run(hotfix_*)" is one of the
    preflight points, and "missing-token 不再被吞为 issue_not_found").

    Contract: interfaces §1d item 4, §2 row 3.
    """
    from tracks.executor.hotfix import fetch_failure_report

    report = fetch_failure_report("missing_token")
    assert report.status == "rejected"
    assert report.reason == "issue_fetch_failed", (
        "a missing credential must be classified issue_fetch_failed, never "
        f"issue_not_found (got {report.reason!r})"
    )
    assert "GITHUB_TOKEN" in report.next, (
        "the issue_fetch_failed guidance must name the canonical credential "
        f"(next={report.next!r})"
    )
    assert report.target_version is None

    # The confirmed-missing classification stays narrow and distinct.
    from tracks.executor.hotfix import precheck_hotfix

    missing = precheck_hotfix(
        None,
        "post-release",
        [],
        None,
        Path("/nonexistent-projects-dir"),
        {"v0.8"},
    )
    assert missing.reason == "issue_not_found", (
        "a CONFIRMED missing issue must keep the narrow issue_not_found "
        f"classification (got {missing.reason!r})"
    )

    # The hotfix entry's credential preflight: with the canonical token
    # absent, the hotfix entry must report issue_fetch_failed (with the
    # classified guidance) -- never issue_not_found.
    from tracks.executor.hotfix_face import precheck_hotfix_report

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    try:
        entry_report, issue = precheck_hotfix_report(repo, store, 77, "post-release")
    finally:
        store.close()

    assert issue is None, (
        "a failed fetch must not yield an issue object (the entry is rejected)"
    )
    assert entry_report.reason == "issue_fetch_failed", (
        "the hotfix entry's credential preflight must report issue_fetch_failed "
        f"(got {entry_report.reason!r})"
    )
    assert "GITHUB_TOKEN" in entry_report.next, (
        "the hotfix entry's guidance must name the canonical credential "
        f"(next={entry_report.next!r})"
    )

    # The INTAKE entry's credential preflight (interfaces §1d item 4: the
    # preflight points include ``submit_intake(source="issue")``): a missing
    # canonical token must be denied as ``issue_fetch_failed`` -- never
    # swallowed as ``issue_not_found`` and never silently accepted.
    from tracks.supervisor.service_intake import IntakeCommands

    denials: list[tuple[str, str]] = []

    def _deny(reason: str, detail: str) -> None:
        """The production ``deny`` terminates the acceptance; the test
        records the reason and raises the same way."""
        denials.append((reason, detail))
        raise _Denied(reason, detail)

    commands = IntakeCommands()
    with pytest.raises(_Denied) as denied:
        commands._fetch_issue_or_deny(repo, 77, _deny)
    reason, detail = denied.value.args
    assert reason == "issue_fetch_failed", (
        "the intake entry's credential preflight must deny as "
        f"issue_fetch_failed (got {reason!r})"
    )
    assert "missing_token" in detail, (
        "the denial must carry the classified reason (got "
        f"{detail!r})"
    )

    # The hotfix's HINT-ONLY diagnostic (interfaces §1d item 1): with the
    # canonical token absent but a NON-canonical one present, the denial
    # must name the found variable and point at the canonical export --
    # never silently fall back to it.
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    with pytest.raises(_Denied) as hinted:
        commands._fetch_issue_or_deny(repo, 77, _deny)
    hint = " ".join(str(part) for part in hinted.value.args)
    assert "GITHUB_ACCESS_TOKEN" in hint, (
        "the diagnostic must name the non-canonical variable that was found "
        f"(got {hint!r})"
    )
    assert "export GITHUB_TOKEN" in hint, (
        "the diagnostic must point at the canonical credential export "
        f"(got {hint!r})"
    )
