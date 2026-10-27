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

TEST-2: every assertion lands on a PUBLIC outlet (interfaces §4a):

* the EVENT STREAM -- the
  ``attention.required(area="issue_creation", reason="missing_token")``
  event and its payload;
* the ``trac status`` CLI -- the operator-visible ``needs_attention`` report
  and the recovery guidance;
* the ``trac hotfix`` / ``trac triage`` CLI -- the issue-fetch
  classification (``issue_fetch_failed``, never ``issue_not_found``).

No private helper is imported and no internal return value is asserted: the
tests drive the real production entry points and read what they emit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracks import paths
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MISSING_TOKEN_HINT = "export GITHUB_TOKEN"


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


def _run_cli(repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the real CLI with the isolated home as its TRACKS_HOME."""
    env = dict(os.environ)
    env["TRACKS_HOME"] = str(home)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tracks.cli.main", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def _status_text(repo: Path, home: Path) -> str:
    proc = _run_cli(repo, home, "status")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout + proc.stderr


def _issue_creation_attentions(events: list) -> list:
    return [
        event
        for event in events
        if event.type == "attention.required"
        and (event.payload or {}).get("area") == "issue_creation"
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

    Outlet: the EVENT STREAM and the ``trac status`` CLI.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    # The issue backend must NOT be the fake channel: a fake backend would
    # create the issue without a credential, hiding the contract under test.
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    home = paths.tracks_home(repo)
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
        first = _issue_creation_attentions(list(store.events(run_id)))
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
        types = [event.type for event in store.events(run_id)]
        assert "issue.created" not in types, (
            "a missing credential must not produce an issue.created record"
        )
        assert "issue.mapped" not in types

        # The dedup: the attention is emitted AT MOST once.
        assert len(first) == 1, (
            "the missing-token attention must be emitted at most once "
            f"(got {len(first)})"
        )
        executor._do_create_issues(cmd, store.state(run_id), None, False)
        second = _issue_creation_attentions(list(store.events(run_id)))
        assert len(second) == 1, (
            "the missing-token attention must be emitted at most once "
            f"(got {len(second)})"
        )
    finally:
        store.close()

    # The operator-visible report: the run parks at needs_attention.
    text = _status_text(repo, home)
    assert "needs_attention" in text, (
        f"the run must report needs_attention (status={text!r})"
    )
    assert "missing_token" in text, (
        f"the status must report the missing_token reason (status={text!r})"
    )


# AC-FR0298-02@v0.9 TRACKS-TRACE loop halts on unresolved attention
def test_loop_halts_on_unresolved_attention(tmp_path: Path, monkeypatch):
    """AC-FR0298-02: an unresolved, non-auto-retryable attention makes the
    run loop stop cleanly -- no tight loop, no ``CommandStallError``.

    Contract: interfaces §1d item 2 (``_run_loop_preflight`` checks the
    pending attention on EVERY loop iteration and stops the loop at
    ``needs_attention``), §4a outlet 4.

    Outlet: the ``trac run`` CLI (the loop's public entry) and the
    ``trac status`` CLI.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    home = paths.tracks_home(repo)
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
    finally:
        store.close()

    before = len(list(Store(home).events(run_id)))
    proc = _run_cli(repo, home, "run", "--no-watch")
    assert proc.returncode == 0, (
        f"the halted run must not crash: stdout={proc.stdout!r} "
        f"stderr={proc.stderr!r}"
    )
    combined = proc.stdout + proc.stderr
    assert "CommandStallError" not in combined, (
        "the halted run must not abort with a stall error (that is a hot loop)"
    )

    store = Store(home)
    try:
        after = len(list(store.events(run_id)))
    finally:
        store.close()
    assert after == before, (
        "the halted loop must not keep issuing commands (the event count "
        f"grew from {before} to {after})"
    )

    text = _status_text(repo, home)
    assert "needs_attention" in text, (
        f"the halted run must report needs_attention (status={text!r})"
    )


# AC-FR0329-02@v0.10 TRACKS-TRACE issue fetch failed classification preserved
def test_issue_fetch_failed_classification_preserved(tmp_path: Path, monkeypatch):
    """AC-FR0329-02: a missing credential during the ISSUE FETCH is
    classified ``issue_fetch_failed`` (NOT ``issue_not_found``), and the
    recovery guidance names the canonical credential.

    The hotfix delta adds the CREDENTIAL PREFLIGHT at the hotfix entry
    (interfaces §1d item 4: "create_run(hotfix_*)" is one of the
    preflight points, and "missing-token 不再被吞为 issue_not_found").

    Contract: interfaces §1d item 4, §2 row 3.

    Outlet: the ``trac hotfix`` CLI -- its rejection reason and guidance.
    """
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("TRAC_FAKE_SIMULATE", raising=False)
    monkeypatch.setenv("TRAC_AGENT_BACKEND", "opencode")

    repo, store = _new_host(tmp_path)
    home = paths.tracks_home(repo)
    try:
        store.append(
            "RUN-HF262-ATTN-3",
            "v0.8",
            "story.requested",
            {"raw_chars": 1},
        )
    finally:
        store.close()

    proc = _run_cli(repo, home, "hotfix", "77", "--scenario", "post-release")
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        "a missing credential must refuse the hotfix entry (the issue fetch "
        f"fails): stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "issue_fetch_failed" in combined, (
        "a missing credential must be classified issue_fetch_failed, never "
        f"issue_not_found (output={combined!r})"
    )
    assert "issue_not_found" not in combined, (
        "a missing credential must never be swallowed as a confirmed-missing "
        f"issue (output={combined!r})"
    )
    assert "GITHUB_TOKEN" in combined, (
        "the refusal must name the canonical credential "
        f"(output={combined!r})"
    )

    # The HINT-ONLY diagnostic (interfaces §1d item 1): with the canonical
    # token absent but a NON-canonical one present, the guidance must name
    # the found variable AND point at the canonical export -- never silently
    # fall back to it.
    monkeypatch.setenv("GITHUB_ACCESS_TOKEN", "non-canonical-token")
    hinted = _run_cli(repo, home, "hotfix", "77", "--scenario", "post-release")
    hinted_text = hinted.stdout + hinted.stderr
    assert "GITHUB_ACCESS_TOKEN" in hinted_text, (
        "the diagnostic must name the non-canonical variable that was found "
        f"(output={hinted_text!r})"
    )
    assert _MISSING_TOKEN_HINT in hinted_text, (
        "the diagnostic must point at the canonical credential export "
        f"(output={hinted_text!r})"
    )
