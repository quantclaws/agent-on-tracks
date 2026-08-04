"""Opt-in full user journey driven by an installed wheel and real opencode.

Environment variables
---------------------
* ``TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY`` - live provider config; the
  directory's ``live_enabled`` fixture skips when any is absent.
* ``TRACKS_E2E_GITHUB_REPO`` + ``GITHUB_TOKEN`` (or ``gh auth token``) - the
  disposable GitHub repo for the full/resume journeys' issue-creation and
  remote-ref assertions.
* ``TRAC_AGENT_TIMEOUT`` - per-dispatch agent (opencode subprocess) timeout.
  Default 120s; M-DESIGN DRAFT/RESPOND dispatches override to 1800s via the
  ``agent_timeout`` per-call parameter (the design trio + scaffold + quality
  guard installation is too big for 1200s; live run049 attempt2 was ~90% done
  when killed at 1200s and run050 attempt1 hit the 1200s kernel timeout
  exactly).
* ``TRAC_LIVE_COMMAND_TIMEOUT`` - outer ``trac run`` subprocess timeout.
  Default 1500s (raised from 360s so a 1800s agent dispatch + overhead is not
  clipped; the per-call ``agent_timeout`` override automatically bumps the
  command timeout to ``max_dispatches * (agent_timeout + 300)`` when the
  caller does not pass an explicit ``timeout``, so a 3-attempt retry budget
  at 1800s each is not clipped; live run049 was killed mid-flight because the
  old single-attempt formula only budgeted ``agent_timeout + 300``).
* ``TRAC_LIVE_TOTAL_TIMEOUT`` - whole-journey deadline. Default 10800s (raised
  from 3600s so the 3-attempt retry budget at 1800s each is not clipped).
* ``TRAC_LIVE_SKIP_BASELINE=1`` - skip baseline snapshot capture after the
  M-REQ-APPROVAL checkpoint in the full journey.
* ``TRAC_LIVE_BASELINE_DIR`` - explicit baseline directory for the resume test.
* ``TRAC_LIVE_BUILD_BASELINE=1`` - if no baseline exists, run the prefix phases
  (story/spec/acceptance/approval) on a fresh host, snapshot, then continue.
* ``TRAC_LIVE_FORCE_BASELINE=1`` - use a baseline even when its tracks SHA does
  not match the current HEAD (otherwise the resume test skips on mismatch).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.e2e_live.harness import (
    CURRENT_PYTHON,
    CURRENT_VENV_BIN,
    REQUIRED_SCENARIOS,
    SCENARIO_DIR,
    clean_env,
    installed_trac_command,
    live_path,
    require_current_virtualenv,
    resolve_github_repo,
    select_wheel,
)
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import project as machine_project


def test_live_scenarios_are_external_test_only_inputs():
    paths = {path.stem: path for path in SCENARIO_DIR.glob("*.json")}
    assert paths.keys() >= REQUIRED_SCENARIOS
    for path in paths.values():
        assert path.resolve().parent == SCENARIO_DIR.resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["scenario_id"] == "code-stats"
        if data.get("console_file"):
            console = (SCENARIO_DIR / data["console_file"]).resolve()
            assert console.parent == SCENARIO_DIR.resolve()
            assert console.read_text(encoding="utf-8").startswith("tracks-live-console/v1\n")


def test_live_environment_removes_workspace_import_hooks(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/workspace/tracks")
    monkeypatch.setenv("VIRTUAL_ENV", "/workspace/tracks/.venv")
    clean = clean_env()
    assert "PYTHONPATH" not in clean
    assert "VIRTUAL_ENV" not in clean
    require_current_virtualenv()
    assert Path(sys.executable).resolve() == CURRENT_PYTHON


def test_live_wheel_selection_requires_one_wheel(tmp_path):
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    first = wheelhouse / "first.whl"
    first.write_bytes(b"wheel")
    assert select_wheel(wheelhouse) == first
    (wheelhouse / "second.whl").write_bytes(b"wheel")
    try:
        select_wheel(wheelhouse)
    except RuntimeError as exc:
        assert "exactly one wheel" in str(exc)
    else:
        raise AssertionError("multiple wheels must fail closed")


def test_live_cli_command_is_installed_trac(tmp_path):
    isolated_bin = tmp_path / "isolated-venv" / "bin"
    assert installed_trac_command(isolated_bin, "status") == [
        str(isolated_bin / "trac"),
        "status",
    ]


def test_live_path_uses_only_isolated_and_external_tool_bins(tmp_path):
    isolated_bin = tmp_path / "isolated-venv" / "bin"
    tools_bin = tmp_path / "tools" / "bin"
    source = os.pathsep.join([str(CURRENT_VENV_BIN), "/usr/bin", "/bin"])
    parts = live_path(isolated_bin, tools_bin, source).split(os.pathsep)
    assert parts[:2] == [str(isolated_bin), str(tools_bin)]
    assert str(CURRENT_VENV_BIN) not in parts[2:]
    assert parts[2:] == ["/usr/bin", "/bin"]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True
    )
    return process.stdout.strip()


def _events(repo: Path, run_id: str) -> list[dict]:
    database = repo / ".tracks" / "runtime" / "tracks.db"
    blob_dir = repo / ".tracks" / "runtime" / "blobs"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT seq, type, command_id, payload FROM events "
            "WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    events = []
    for seq, event_type, command_id, payload in rows:
        data = json.loads(payload)
        if isinstance(data, dict) and set(data.keys()) == {"$ref"}:
            ref_path = blob_dir / data["$ref"]
            if ref_path.exists():
                data = json.loads(ref_path.read_text(encoding="utf-8"))
        events.append({
            "seq": seq,
            "type": event_type,
            "command_id": command_id,
            "payload": data,
        })
    return events


def _discussion_state(live_trac, doc: str) -> dict:
    result = live_trac("discuss", "query", "--file", doc, "--check-ready")
    return json.loads(result.stdout)


def _replies(thread: dict) -> list[dict]:
    replies = []

    def visit(comment: dict):
        for child in comment.get("children", []):
            replies.append(child)
            visit(child)

    visit(thread["root"])
    return replies


def _finding_thread(state: dict, finding_id: str, initiator: str) -> dict:
    marker = f"[FINDING:{finding_id}]"
    matches = [
        thread
        for thread in state["threads"]
        if thread["initiator"].casefold() == initiator.casefold()
        and marker in thread["root"]["body"]
    ]
    assert matches, f"required finding absent: {marker} by {initiator}"
    assert matches[0]["initiator"] == initiator
    return matches[0]


def _organic_finding_thread(state: dict, initiator: str, marker_prefix: str) -> dict:
    marker = f"[FINDING:{marker_prefix}"
    matches = [
        thread
        for thread in state["threads"]
        if thread["initiator"].casefold() == initiator.casefold()
        and marker in thread["root"]["body"]
    ]
    assert matches, (
        f"no {initiator}-initiated finding thread with marker {marker}"
    )
    return matches[0]


def _assert_triage(state: dict):
    # TRIAGE dispatches Scribe to explore the seed and propose GO/NO-GO/PARK
    # in the outcome. No Human interview happens in TRIAGE, so story.md must
    # carry no discussion threads after the TRIAGE dispatch.
    threads = state.get("threads", [])
    assert threads == [], f"TRIAGE must not create discussion threads: {threads}"


def _assert_draft_interview(state: dict):
    # DRAFT is where Scribe interviews Human: one question recorded in the
    # story discussion, answered by LiveE2E-Human via the console_input
    # transcript, and the thread converged before the story enters review.
    threads = state.get("threads", [])
    assert threads, "DRAFT produced no Scribe interview discussion"
    for thread in threads:
        assert thread["initiator"].casefold() == "scribe", (
            f"unexpected DRAFT thread initiator: {thread['initiator']}"
        )
        assert thread["status"] == "resolved"
        human_replies = [reply for reply in _replies(thread) if reply["speaker"] == "LiveE2E-Human"]
        assert human_replies, (
            f"Human console answer was not persisted in {thread['thread_id']}"
        )
        assert all(reply["body"].strip() for reply in human_replies)


def _dispatches(events: list[dict], role: str, substate: str, doc: str) -> list[dict]:
    return [
        event
        for event in events
        if event["type"] == "command.issued"
        and event["payload"].get("command", {}).get("kind") == "dispatch_agent"
        and event["payload"].get("command", {}).get("params", {}).get("role") == role
        and event["payload"].get("command", {}).get("params", {}).get("substate") == substate
        and event["payload"].get("command", {}).get("params", {}).get("doc") == doc
    ]


def _assert_respond_diff(
    events: list[dict], role: str, doc: str, committed_type: str
) -> None:
    dispatches = _dispatches(events, role, "RESPOND", doc)
    assert dispatches, f"missing {role} RESPOND dispatch for {doc}"
    dispatch = dispatches[-1]
    outcomes = [
        event
        for event in events
        if event["type"] == "outcome.received"
        and event["command_id"] == dispatch["command_id"]
    ]
    assert outcomes and outcomes[-1]["payload"].get("status") == "done"
    diff_ref = outcomes[-1]["payload"].get("diff_ref")
    assert isinstance(diff_ref, str) and diff_ref.strip(), f"no RESPOND diff for {doc}"
    commits = [
        event
        for event in events
        if event["type"] == committed_type
        and not event["payload"].get("final")
        and event["seq"] > outcomes[-1]["seq"]
        and event["payload"].get("commit_sha")
    ]
    assert commits, f"no author commit after RESPOND for {doc}"


def _assert_reviewer_pass(
    events: list[dict], event_type: str, *, expect_revise: bool = True
):
    verdicts = [event for event in events if event["type"] == event_type]
    assert verdicts, f"missing {event_type} event"
    assert verdicts[-1]["payload"].get("verdict") == "pass"
    if expect_revise:
        assert any(event["payload"].get("verdict") == "revise" for event in verdicts)
    else:
        assert not any(
            event["payload"].get("verdict") == "revise" for event in verdicts
        ), f"unexpected revise verdict in single-pass {event_type}"


def _assert_finding_lifecycle(
    live_trac,
    live_root: Path,
    run_id: str,
    doc: str,
    finding_id: str,
    initiator: str,
    verdict_type: str,
):
    initial = _discussion_state(live_trac, doc)
    thread = _finding_thread(initial, finding_id, initiator)
    assert thread["status"] == "open"
    assert thread["reply_count"] == 0

    events = _events(live_root, run_id)
    assert any(
        event["type"] == verdict_type
        and event["payload"].get("verdict") == "revise"
        for event in events
    )


def _run_design_review_loop(live_trac):
    """Prism review <-> Archer RESPOND loop, bounded to 2 review rounds
    (TRAC_LIVE_MAX_REVIEW_ROUNDS stays the wider outer net). Each round runs
    prism-design-review: a pass round completes the run at the M-IMPL
    boundary; a revise round halts in RESPOND (live run042 died here while
    unscripted), Archer must answer every finding and return the state to
    PRISM_REVIEW, and the next round starts.

    Archer DRAFT/RESPOND carry the design trio + scaffold + quality-guard
    installation - too big for the 1200s budget, so agent_timeout=1800 is
    plumbed per call (live run049 attempt2 was ~90% done when killed at 1200s;
    run050 attempt1 hit the 1200s kernel timeout exactly). Prism review
    steps stay at the default timeout.
    """
    for review_round in range(1, 3):
        review = live_trac("run", scenario="prism-design-review")
        if "status=completed" in review.stdout:
            return review
        assert "stage=M-DESIGN" in review.stdout, review.stdout
        assert "substate=RESPOND" in review.stdout, (
            f"review round {review_round} neither completed nor opened RESPOND: "
            f"{review.stdout}"
        )
        respond = live_trac(
            "run", scenario="archer-design-respond", agent_timeout=1800
        )
        assert "stage=M-DESIGN" in respond.stdout, respond.stdout
        assert "substate=PRISM_REVIEW" in respond.stdout, (
            f"review round {review_round}: Archer RESPOND did not return to "
            f"PRISM_REVIEW: {respond.stdout}"
        )
    raise AssertionError("M-DESIGN review loop did not complete within 2 review rounds")


def _run_spec_review_loop(live_trac):
    """Lex review <-> Sage RESPOND loop, bounded to 2 review rounds
    (TRAC_LIVE_MAX_REVIEW_ROUNDS stays the wider outer net). Each round runs
    lex-spec-review: a pass round (awaiting=review) completes the spec review;
    a revise round halts in RESPOND, Sage must answer the finding and return
    the state to LEX_REVIEW, and the next round starts. Returns the final
    lex-spec-review result so the caller can assert awaiting=review."""
    spec_review = None
    for review_round in range(1, 3):
        spec_review = live_trac("run", scenario="lex-spec-review")
        if "awaiting=review" in spec_review.stdout:
            return spec_review
        assert "stage=M-SPEC" in spec_review.stdout, spec_review.stdout
        assert "substate=RESPOND" in spec_review.stdout, (
            f"spec review round {review_round} neither passed nor opened RESPOND: "
            f"{spec_review.stdout}"
        )
        respond = live_trac("run", scenario="sage-spec-respond")
        assert "stage=M-SPEC" in respond.stdout, respond.stdout
        assert "substate=LEX_REVIEW" in respond.stdout, (
            f"spec review round {review_round}: Sage RESPOND did not return to "
            f"LEX_REVIEW: {respond.stdout}"
        )
    raise AssertionError(
        "M-SPEC review loop did not pass within 2 review rounds: "
        f"{spec_review.stdout if spec_review else 'no review ran'}"
    )


# -- phase helpers (deliverable #1) ----------------------------------------


def _phase_story(live_trac, live_root: Path, run_id: str, version: str):
    """TRIAGE through sage-story-resolve + human review no-comment."""
    story = f".tracks/projects/{version}/story.md"

    triage = live_trac("run", scenario="triage")
    assert "awaiting=triage" in triage.stdout
    _assert_triage(_discussion_state(live_trac, story))
    assert live_trac("triage", "go", "--actor", "LiveE2E-Human").returncode == 0

    draft = live_trac("run", scenario="scribe-story-draft")
    assert "substate=SAGE_REVIEW" in draft.stdout
    _assert_draft_interview(_discussion_state(live_trac, story))
    finding_run = live_trac("run", scenario="sage-story-finding")
    assert "substate=RESPOND" in finding_run.stdout
    _assert_finding_lifecycle(
        live_trac,
        live_root,
        run_id,
        story,
        "STORY-OUTPUT-PLACEMENT",
        "Sage",
        "sage.verdict",
    )

    respond = live_trac("run", scenario="scribe-story-respond")
    assert "substate=SAGE_REVIEW" in respond.stdout
    state = _discussion_state(live_trac, story)
    updated_thread = _finding_thread(state, "STORY-OUTPUT-PLACEMENT", "Sage")
    assert any(reply["speaker"] == "Scribe" for reply in _replies(updated_thread))
    _assert_respond_diff(_events(live_root, run_id), "scribe", "story.md", "story.committed")

    rereview = live_trac("run", scenario="sage-story-resolve")
    assert "awaiting=review" in rereview.stdout
    state = _discussion_state(live_trac, story)
    resolved_story = _finding_thread(state, "STORY-OUTPUT-PLACEMENT", "Sage")
    assert resolved_story["status"] == "resolved"
    assert any(reply["speaker"] == "Scribe" for reply in _replies(resolved_story))
    _assert_reviewer_pass(_events(live_root, run_id), "sage.verdict")

    assert live_trac("review", "no-comment", "--actor", "LiveE2E-Human").returncode == 0


def _phase_spec_and_acceptance(live_trac, live_root: Path, run_id: str, version: str):
    """sage-spec-draft, spec review loop, spec thread assertions, human review,
    sage-acceptance-draft, lex-acceptance-review, human review."""
    spec = f".tracks/projects/{version}/spec.md"
    acceptance = f".tracks/projects/{version}/acceptance.md"
    assert "substate=LEX_REVIEW" in live_trac(
        "run", scenario="sage-spec-draft"
    ).stdout
    spec_review = _run_spec_review_loop(live_trac)
    assert "awaiting=review" in spec_review.stdout
    spec_events = _events(live_root, run_id)
    spec_respond_happened = bool(
        _dispatches(spec_events, "sage", "RESPOND", "spec.md")
    )
    state = _discussion_state(live_trac, spec)
    if spec_respond_happened:
        spec_thread = _organic_finding_thread(state, "Lex", "SPEC-")
        assert spec_thread["status"] == "resolved", (
            f"spec finding not resolved after RESPOND: {spec_thread}"
        )
        assert any(reply["speaker"] == "Sage" for reply in _replies(spec_thread)), (
            "no Sage reply in spec finding thread"
        )
        _assert_respond_diff(spec_events, "sage", "spec.md", "spec.committed")
    else:
        assert not state.get("threads", []), (
            f"unexpected spec threads when no RESPOND happened: "
            f"{state.get('threads', [])}"
        )
    _assert_reviewer_pass(
        spec_events, "lex.verdict", expect_revise=spec_respond_happened
    )

    assert live_trac("review", "no-comment", "--actor", "LiveE2E-Human").returncode == 0
    assert "substate=LEX_REVIEW" in live_trac(
        "run", scenario="sage-acceptance-draft"
    ).stdout
    acceptance_review = live_trac("run", scenario="lex-acceptance-review")
    assert "awaiting=review" in acceptance_review.stdout
    assert _discussion_state(live_trac, acceptance)["is_ready"]
    acceptance_events = _events(live_root, run_id)
    acceptance_dispatch = _dispatches(
        acceptance_events, "lex", "LEX_REVIEW", "acceptance.md"
    )
    assert acceptance_dispatch, "missing normal Lex acceptance review dispatch"
    acceptance_command = acceptance_dispatch[-1]["command_id"]
    acceptance_outcomes = [
        event
        for event in acceptance_events
        if event["type"] == "outcome.received"
        and event["command_id"] == acceptance_command
    ]
    assert acceptance_outcomes[-1]["payload"].get("status") == "done"
    assert any(
        event["type"] == "lex.verdict"
        and event["command_id"] == acceptance_command
        and event["payload"].get("verdict") == "pass"
        for event in acceptance_events
    )
    assert live_trac("review", "no-comment", "--actor", "LiveE2E-Human").returncode == 0


def _phase_approval(live_trac):
    """approval-final run + ``trac approve``. This is the M-REQ-APPROVED
    checkpoint; the baseline snapshot is captured immediately after."""
    waiting = live_trac("run", scenario="approval-final")
    assert "stage=M-REQ-APPROVAL" in waiting.stdout
    assert "awaiting=approval" in waiting.stdout
    assert live_trac("approve", "--actor", "LiveE2E-Human").returncode == 0


def _phase_design(
    live_trac,
    live_root: Path,
    run_id: str,
    version: str,
    remote_url_before: str,
    remote_refs_before: str,
):
    """Everything after approve: archer-design-draft assertions, design review
    loop, report assertions, final event assertions (run.completed, prism pass,
    no human gate after approval, issue events, remote refs, branch check)."""
    # M-DESIGN (flow.md §8, BS-05): no human gate after approval. One
    # Archer dispatch drafts the design trio, validates and commits it, then
    # waits in PRISM_REVIEW. Prism may revise (anchored findings) and Archer
    # RESPONDs until a pass round completes the run at the M-IMPL boundary
    # after the M-DESIGN EXIT gate.
    design = live_trac("run", scenario="archer-design-draft", agent_timeout=1800)
    assert "stage=M-DESIGN" in design.stdout
    assert "substate=PRISM_REVIEW" in design.stdout
    design_dir = live_root / ".tracks" / "projects" / version
    for design_doc in ("architecture.md", "interfaces.md", "test-plan.md"):
        assert (design_dir / design_doc).is_file()
    design_events = _events(live_root, run_id)
    assert _dispatches(design_events, "archer", "DRAFT", None), (
        "missing Archer DRAFT dispatch for the design trio"
    )
    assert sorted(
        event["payload"]["doc"]
        for event in design_events
        if event["type"] == "design.committed"
    ) == ["architecture.md", "interfaces.md", "test-plan.md"]

    final = _run_design_review_loop(live_trac)
    assert "status=completed" in final.stdout
    assert "awaiting=-" in final.stdout

    report_dir = live_root / "report"
    assert live_trac(
        "report",
        "--run-id",
        run_id,
        "--output",
        str(report_dir),
        "--format",
        "html",
    ).returncode == 0
    report = (report_dir / "report.md").read_text(encoding="utf-8")
    for required in (
        "scenario_id",
        "assignment expanded",
        "agent input ref=",
        "## Discussions",
        "attempt=",
        "commit:",
        "## Audit",
        "audit gaps:",
        "status: `completed`",
        "stage: `M-DESIGN`",
    ):
        assert required in report
    assert (report_dir / "index.html").is_file()
    assert "marked v15.0.7" in (report_dir / "index.html").read_text(encoding="utf-8")

    final_events = _events(live_root, run_id)
    assert final_events[-1]["type"] == "run.completed"
    assert final_events[-1]["payload"]["terminal_state"] == "boundary"
    assert _dispatches(final_events, "prism", "PRISM_REVIEW", None), (
        "missing Prism PRISM_REVIEW dispatch for the design trio"
    )
    prism_verdicts = [
        event for event in final_events if event["type"] == "prism.verdict"
    ]
    assert prism_verdicts and prism_verdicts[-1]["payload"]["verdict"] == "pass"
    approval_seq = next(
        event["seq"] for event in final_events if event["type"] == "human.approval"
    )
    assert not [
        event
        for event in final_events
        if event["type"].startswith("human.") and event["seq"] > approval_seq
    ], "M-DESIGN must carry no human gate (BS-05)"
    issue_events = [event for event in final_events if event["type"] == "issue.created"]
    assert issue_events and all(
        str(event["payload"]["issue_id"]).isdigit() for event in issue_events
    )
    assert _git(live_root, "remote", "get-url", "origin") == remote_url_before
    remote_refs_after = _git(live_root, "ls-remote", "origin")
    assert remote_refs_after == remote_refs_before
    assert _git(live_root, "symbolic-ref", "--short", "HEAD") == f"releases/{version}"
    print(f"LIVE_E2E_REMOTE={remote_url_before}", flush=True)
    print(f"LIVE_E2E_BRANCH=releases/{version}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_BEFORE={remote_refs_before!r}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_AFTER={remote_refs_after!r}", flush=True)


# -- baseline snapshot (deliverable #2) ------------------------------------


def _tracks_short_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _baselines_root() -> Path:
    return Path(os.environ.get("TMPDIR", "/tmp")) / "tracks" / "live-e2e" / "baselines"


def _baseline_dir(version: str, sha: str) -> Path:
    return _baselines_root() / f"baseline-{sha}-{version}"


# opencode-managed files recreated on demand; excluded from the snapshot so
# the baseline stays small and the resumed test rebuilds them from scratch.
_SNAPSHOT_EXCLUDE_DIRS = {".opencode" + os.sep + "node_modules"}
_SNAPSHOT_EXCLUDE_NAMES = {"node_modules"}


def _capture_baseline(live_root: Path, version: str, run_id: str) -> Path | None:
    """Capture a baseline snapshot of the live host immediately after the
    M-REQ-APPROVED checkpoint. The snapshot is a copy of the host directory
    (.tracks/ runtime DB + project docs, .opencode/ provider config, the git
    checkout) minus opencode's node_modules (recreated on demand). The
    isolated venv + wheel live outside the host in runNNN-artifacts/ and are
    NOT snapshotted: the resumed test rebuilds them from the current working
    tree so code iteration takes effect."""
    if os.environ.get("TRAC_LIVE_SKIP_BASELINE", "").strip() == "1":
        print("LIVE_E2E_BASELINE=skipped (TRAC_LIVE_SKIP_BASELINE=1)", flush=True)
        return None
    sha = _tracks_short_sha()
    target = _baseline_dir(version, sha)
    if target.exists():
        print(f"LIVE_E2E_BASELINE=exists {target}", flush=True)
        return target
    target.mkdir(parents=True, exist_ok=True)
    _copy_tree(live_root, target)
    manifest = {
        "tracks_sha": sha,
        "version": version,
        "run_id": run_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_host": str(live_root.resolve()),
    }
    (target / ".tracks-baseline-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"LIVE_E2E_BASELINE=captured {target}", flush=True)
    return target


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy src into dst, excluding opencode's node_modules (recreated on
    demand) and the baseline manifest itself."""
    for entry in src.iterdir():
        if entry.name in _SNAPSHOT_EXCLUDE_NAMES and entry.is_dir():
            continue
        if entry.is_dir() and entry.name == "node_modules":
            continue
        dest = dst / entry.name
        if entry.is_dir():
            shutil.copytree(entry, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, dest)


def _restore_baseline(baseline_dir: Path, live_root: Path) -> None:
    """Restore a baseline snapshot into the test's live host directory.

    Path-stability: the runtime resolves ``.tracks/`` from the runtime cwd
    (``paths.tracks_home``), and ``artifact_ref`` fields in the DB carry
    absolute paths from the original host but are used only as evidence
    strings (``last_failure`` re-dispatch context), never for file access.
    So restoring to a different path is safe - the runtime re-derives every
    path from the new cwd."""
    for entry in list(live_root.iterdir()):
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    _copy_tree(baseline_dir, live_root)


def _find_baseline_for_sha(version: str) -> Path | None:
    """Locate a baseline for the current HEAD.

    Lookup order: ``TRAC_LIVE_BASELINE_DIR`` (explicit path) else the newest
    ``baselines/baseline-*`` with a readable manifest. An exact SHA match is
    preferred; on SHA mismatch the caller decides (via
    ``TRAC_LIVE_FORCE_BASELINE=1``) whether to use it. Candidates whose
    manifest is missing or unreadable are skipped in both passes."""
    explicit = os.environ.get("TRAC_LIVE_BASELINE_DIR", "").strip()
    if explicit:
        path = Path(explicit)
        manifest = _read_manifest(path)
        if manifest is None:
            pytest.skip(f"TRAC_LIVE_BASELINE_DIR has no manifest: {path}")
        return path
    sha = _tracks_short_sha()
    root = _baselines_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        (p for p in root.glob(f"baseline-*-{version}") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # First pass: prefer an exact SHA match among readable manifests.
    for path in candidates:
        manifest = _read_manifest(path)
        if manifest and manifest.get("tracks_sha") == sha:
            return path
    # Fallback: newest candidate with a readable manifest; the caller
    # (test_journey_from_req_approved_baseline) handles SHA mismatch via
    # TRAC_LIVE_FORCE_BASELINE.
    for path in candidates:
        if _read_manifest(path) is not None:
            return path
    return None


def _read_manifest(path: Path) -> dict | None:
    manifest_path = path / ".tracks-baseline-manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# -- full journey (deliverable #1) -----------------------------------------


def _setup_host(
    live_trac, live_root: Path, live_github_repo: str, live_scenarios, version: str
) -> tuple[str, str, str, str]:
    """Remote add/commit/init/start; returns (run_id, remote_url_before,
    remote_refs_before, remote)."""
    slug = live_github_repo
    remote = f"git@github.com:{slug}.git"
    _git(live_root, "remote", "add", "origin", remote)
    remote_url_before = _git(live_root, "remote", "get-url", "origin")
    remote_refs_before = _git(live_root, "ls-remote", "origin")
    _git(live_root, "add", ".opencode/opencode.json")
    _git(live_root, "commit", "-m", "configure live e2e host")

    assert live_trac("init").returncode == 0
    started = live_trac(
        "start", version, stdin=live_scenarios["triage"].start_requirement
    )
    run_id = started.stdout.split("run ", maxsplit=1)[1].split(" started", maxsplit=1)[0]
    return run_id, remote_url_before, remote_refs_before, remote


def test_bounded_scripted_real_agent_journey(
    live_root,
    host_with_opencode_config,
    live_github_repo,
    live_scenarios,
    live_trac,
):
    """Exercise triage, both required finding loops, approval, GitHub Issues,
    and the M-DESIGN stage (Archer draft, bounded Prism review <-> Archer
    RESPOND revise loop, boundary completion). After the approval checkpoint
    a baseline snapshot is captured so the resume test can re-run only the
    M-DESIGN tail."""
    version = "live-e2e-code-stats"
    run_id, remote_url_before, remote_refs_before, remote = _setup_host(
        live_trac, live_root, live_github_repo, live_scenarios, version
    )

    _phase_story(live_trac, live_root, run_id, version)
    _phase_spec_and_acceptance(live_trac, live_root, run_id, version)
    _phase_approval(live_trac)

    # Baseline snapshot at the M-REQ-APPROVED checkpoint (deliverable #2).
    _capture_baseline(live_root, version, run_id)

    _phase_design(
        live_trac, live_root, run_id, version,
        remote_url_before, remote_refs_before,
    )


# -- resume test (deliverable #3) ------------------------------------------


def _require_github(monkeypatch, live_root: Path) -> str:
    """Deferred GitHub setup for the resume test's TRAC_LIVE_BUILD_BASELINE=1
    path: validate TRACKS_E2E_GITHUB_REPO + auth and set the env vars the issue
    backend needs. The baseline check runs BEFORE this so a missing baseline
    skips cleanly without needing GitHub coordinates."""
    return resolve_github_repo(monkeypatch, live_root)


def test_journey_from_req_approved_baseline(
    live_root,
    host_with_opencode_config,
    live_scenarios,
    live_trac,
    monkeypatch,
):
    """Resume the journey from an M-REQ-APPROVED baseline: restore the
    snapshot, sanity-check the post-approval state, then run ONLY
    ``_phase_design``. Never replays triage/scribe/sage-story/sage-spec-
    draft/acceptance/approval scenarios.

    Gating follows the existing live test: ``live_enabled`` (via live_root)
    skips when the provider env is absent. The baseline check runs BEFORE the
    GitHub requirement so a missing baseline skips cleanly without needing
    ``TRACKS_E2E_GITHUB_REPO``."""
    version = "live-e2e-code-stats"

    baseline = _find_baseline_for_sha(version)
    if baseline is None:
        if os.environ.get("TRAC_LIVE_BUILD_BASELINE", "").strip() == "1":
            # Build a baseline on the fly: run the prefix phases on a fresh
            # host, snapshot, then continue to _phase_design.
            slug = _require_github(monkeypatch, live_root)
            run_id, remote_url_before, remote_refs_before, _ = _setup_host(
                live_trac, live_root, slug, live_scenarios, version
            )
            _phase_story(live_trac, live_root, run_id, version)
            _phase_spec_and_acceptance(live_trac, live_root, run_id, version)
            _phase_approval(live_trac)
            baseline = _capture_baseline(live_root, version, run_id)
            if baseline is None:
                pytest.skip(
                    "TRAC_LIVE_BUILD_BASELINE=1 but capture was skipped "
                    "(TRAC_LIVE_SKIP_BASELINE=1)"
                )
            run_id = _sanity_check_resumed_host(live_trac, live_root, baseline)
            _phase_design(
                live_trac, live_root, run_id, version,
                remote_url_before, remote_refs_before,
            )
            return
        pytest.skip(
            "no M-REQ-APPROVED baseline; run the full journey or set "
            "TRAC_LIVE_BUILD_BASELINE=1"
        )

    # SHA mismatch: skip unless forced.
    manifest = _read_manifest(baseline)
    if (
        manifest
        and manifest.get("tracks_sha") != _tracks_short_sha()
        and os.environ.get("TRAC_LIVE_FORCE_BASELINE", "").strip() != "1"
    ):
        pytest.skip(
            f"baseline SHA {manifest.get('tracks_sha')} != HEAD "
            f"{_tracks_short_sha()}; set TRAC_LIVE_FORCE_BASELINE=1 to override"
        )

    _restore_baseline(baseline, live_root)

    # The baseline's .git already has the origin remote (set up before the
    # prefix phases). Capture refs_before AFTER restore so the baseline's
    # local commits are in place.
    remote_url_before = _git(live_root, "remote", "get-url", "origin")
    remote_refs_before = _git(live_root, "ls-remote", "origin")

    run_id = _sanity_check_resumed_host(live_trac, live_root, baseline)
    _phase_design(
        live_trac, live_root, run_id, version,
        remote_url_before, remote_refs_before,
    )


def _sanity_check_resumed_host(live_trac, live_root: Path, baseline: Path) -> str:
    """Assert the restored host is at the M-REQ-APPROVED checkpoint:
    stage=M-REQ-APPROVAL, substate=APPROVED, active (not escalation), and no
    failed outcomes in the DB. Returns the run_id from the manifest."""
    manifest = _read_manifest(baseline)
    assert manifest, f"baseline missing manifest: {baseline}"
    run_id = manifest["run_id"]

    status = live_trac("status")
    assert "stage=M-REQ-APPROVAL" in status.stdout, (
        f"baseline is not at M-REQ-APPROVAL: {status.stdout}"
    )
    assert "substate=APPROVED" in status.stdout, (
        f"baseline is not in APPROVED substate: {status.stdout}"
    )
    assert "status=active" in status.stdout, (
        f"baseline is not active (escalation?): {status.stdout}"
    )
    assert "awaiting=-" in status.stdout, (
        f"baseline is awaiting (escalation?): {status.stdout}"
    )

    # No escalation evidence: no failed outcomes in the baseline DB events.
    events = _events(live_root, run_id)
    failed = [
        e for e in events
        if e["type"] == "outcome.received" and e["payload"].get("status") != "done"
    ]
    assert not failed, (
        f"baseline has non-done outcomes (escalation evidence): "
        f"{[(e['seq'], e['payload'].get('status')) for e in failed]}"
    )
    return run_id


# -- deterministic unit test for outcome status=None hardening (deliverable #5) --


def _unit_envelopes(*items: tuple[str, dict]) -> list[EventEnvelope]:
    out = []
    for i, (etype, payload) in enumerate(items, start=1):
        out.append(EventEnvelope(
            seq=i, ts="2026-08-04T00:00:00+00:00", run_id="RUN",
            version="v0.1", type=etype, schema_version=1,
            command_id=f"C{i}", task_id=None, payload=payload,
        ))
    return out


def test_outcome_with_none_status_is_treated_as_failure():
    """run048 hardening: an outcome.received with status=None (missing,
    unparseable) must be recorded as a failure that consumes an attempt and
    escalates at the 3rd occurrence - never silently treated as a produced
    document.

    Before the fix, ``_on_outcome_received`` only checked
    ``p.get("status") == "failed"``, so a None/absent status fell through to
    the success branch (``s.doc_produced = True``), masking the backend
    failure and breaking the 3-attempt escalation evidence."""
    enter = [
        ("story.requested", {"raw_chars": 5}),
        ("stage.entered", {"stage": "M-DESIGN"}),
    ]
    dispatched = ("command.issued", {"command": {"kind": "dispatch_agent",
                                                  "params": {"role": "archer",
                                                             "substate": "DRAFT"},
                                                  "command_id": "C1"}})
    # status=None - the bug: previously treated as success (doc_produced=True).
    none_outcome = ("outcome.received", {"role": "archer", "status": None,
                                          "self_report": "unparseable"})

    one = machine_project(_unit_envelopes(*enter, dispatched, none_outcome))
    # The None status must consume an attempt (not set doc_produced).
    assert one.doc_produced is False, (
        "status=None must not be treated as a produced document"
    )
    assert one.current_attempt == 1, (
        f"status=None must consume an attempt; got {one.current_attempt}"
    )
    assert one.last_failure is not None
    assert one.last_failure["check"] == "agent_error", (
        f"missing/None status must classify as agent_error; "
        f"got {one.last_failure['check']}"
    )
    assert one.status == "active", "first None-status outcome must not escalate"

    # Two more None-status outcomes escalate (3-attempt budget).
    two = machine_project(_unit_envelopes(
        *enter, dispatched, none_outcome,
        dispatched, none_outcome,
    ))
    assert two.current_attempt == 2
    assert two.status == "active", "second None-status outcome must not escalate yet"

    three = machine_project(_unit_envelopes(
        *enter, dispatched, none_outcome,
        dispatched, none_outcome,
        dispatched, none_outcome,
    ))
    assert three.status == "awaiting_human"
    assert three.awaiting == "escalation", (
        f"third None-status outcome must escalate; got awaiting={three.awaiting}"
    )
    assert machine_project(_unit_envelopes(
        *enter, dispatched, none_outcome,
        dispatched, none_outcome,
        dispatched, none_outcome,
    )).stage == "M-DESIGN"

    # A done status still passes through as a produced doc (regression guard).
    done_outcome = ("outcome.received", {"role": "archer", "status": "done"})
    ok = machine_project(_unit_envelopes(*enter, dispatched, done_outcome))
    assert ok.doc_produced is True
    assert ok.current_attempt == 0
    assert ok.last_failure is None
