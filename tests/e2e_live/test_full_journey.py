"""Opt-in full user journey driven by an installed wheel and real opencode."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.e2e_live.harness import (
    CURRENT_PYTHON,
    CURRENT_VENV_BIN,
    REQUIRED_SCENARIOS,
    SCENARIO_DIR,
    clean_env,
    installed_trac_command,
    live_path,
    require_current_virtualenv,
    select_wheel,
)


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
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT seq, type, command_id, payload FROM events "
            "WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
    return [
        {
            "seq": seq,
            "type": event_type,
            "command_id": command_id,
            "payload": json.loads(payload),
        }
        for seq, event_type, command_id, payload in rows
    ]


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


def _assert_triage(state: dict):
    threads = [
        thread
        for thread in state["threads"]
        if thread["initiator"].casefold() == "scribe"
    ]
    assert threads, "TRIAGE produced no Scribe discussion"
    for thread in threads:
        assert thread["status"] == "resolved"
        human_replies = [reply for reply in _replies(thread) if reply["speaker"] == "LiveE2E-Human"]
        assert human_replies, f"Human transcript was not persisted in {thread['thread_id']}"
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


def _assert_reviewer_pass(events: list[dict], event_type: str):
    verdicts = [event for event in events if event["type"] == event_type]
    assert verdicts, f"missing {event_type} event"
    assert verdicts[-1]["payload"].get("verdict") == "pass"
    assert any(event["payload"].get("verdict") == "revise" for event in verdicts)


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


def test_bounded_scripted_real_agent_journey(
    live_root,
    host_with_opencode_config,
    live_github_repo,
    live_scenarios,
    live_trac,
):
    """Exercise triage, both required finding loops, approval, and GitHub Issues."""
    version = "live-e2e-code-stats"
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
    story = f".tracks/projects/{version}/story.md"

    triage = live_trac("run", scenario="triage")
    assert "awaiting=triage" in triage.stdout
    _assert_triage(_discussion_state(live_trac, story))
    assert live_trac("triage", "go", "--actor", "LiveE2E-Human").returncode == 0

    draft = live_trac("run", scenario="scribe-story-draft")
    assert "substate=SAGE_REVIEW" in draft.stdout
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

    spec = f".tracks/projects/{version}/spec.md"
    acceptance = f".tracks/projects/{version}/acceptance.md"
    assert "substate=LEX_REVIEW" in live_trac(
        "run", scenario="sage-spec-draft"
    ).stdout
    assert "substate=RESPOND" in live_trac(
        "run", scenario="lex-spec-finding"
    ).stdout
    _assert_finding_lifecycle(
        live_trac,
        live_root,
        run_id,
        spec,
        "SPEC-BLANK-LINE-SEMANTICS",
        "Lex",
        "lex.verdict",
    )
    assert "substate=LEX_REVIEW" in live_trac(
        "run", scenario="sage-spec-respond"
    ).stdout
    state = _discussion_state(live_trac, spec)
    spec_thread = _finding_thread(state, "SPEC-BLANK-LINE-SEMANTICS", "Lex")
    assert any(reply["speaker"] == "Sage" for reply in _replies(spec_thread))
    _assert_respond_diff(_events(live_root, run_id), "sage", "spec.md", "spec.committed")

    assert "awaiting=review" in live_trac(
        "run", scenario="lex-spec-resolve"
    ).stdout
    state = _discussion_state(live_trac, spec)
    resolved_spec = _finding_thread(state, "SPEC-BLANK-LINE-SEMANTICS", "Lex")
    assert resolved_spec["status"] == "resolved"
    assert any(reply["speaker"] == "Sage" for reply in _replies(resolved_spec))
    _assert_reviewer_pass(_events(live_root, run_id), "lex.verdict")

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

    waiting = live_trac("run", scenario="approval-final")
    assert "stage=M-REQ-APPROVAL" in waiting.stdout
    assert "awaiting=approval" in waiting.stdout
    assert live_trac("approve", "--actor", "LiveE2E-Human").returncode == 0
    final = live_trac("run", scenario="approval-final")
    assert "status=completed" in final.stdout

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
        "stage: `M-REQ-APPROVAL`",
    ):
        assert required in report
    assert (report_dir / "index.html").is_file()
    assert "marked v15.0.7" in (report_dir / "index.html").read_text(encoding="utf-8")

    final_events = _events(live_root, run_id)
    assert final_events[-1]["type"] == "run.completed"
    assert final_events[-1]["payload"]["terminal_state"] == "boundary"
    issue_events = [event for event in final_events if event["type"] == "issue.created"]
    assert issue_events and all(
        str(event["payload"]["issue_id"]).isdigit() for event in issue_events
    )
    assert _git(live_root, "remote", "get-url", "origin") == remote_url_before
    remote_refs_after = _git(live_root, "ls-remote", "origin")
    assert remote_refs_after == remote_refs_before
    assert _git(live_root, "symbolic-ref", "--short", "HEAD") == f"releases/{version}"
    print(f"LIVE_E2E_REMOTE={remote}", flush=True)
    print(f"LIVE_E2E_BRANCH=releases/{version}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_BEFORE={remote_refs_before!r}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_AFTER={remote_refs_after!r}", flush=True)
