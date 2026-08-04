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
    PRISM_REVIEW, and the next round starts."""
    for review_round in range(1, 3):
        review = live_trac("run", scenario="prism-design-review")
        if "status=completed" in review.stdout:
            return review
        assert "stage=M-DESIGN" in review.stdout, review.stdout
        assert "substate=RESPOND" in review.stdout, (
            f"review round {review_round} neither completed nor opened RESPOND: "
            f"{review.stdout}"
        )
        respond = live_trac("run", scenario="archer-design-respond")
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


def test_bounded_scripted_real_agent_journey(
    live_root,
    host_with_opencode_config,
    live_github_repo,
    live_scenarios,
    live_trac,
):
    """Exercise triage, both required finding loops, approval, GitHub Issues,
    and the M-DESIGN stage (Archer draft, bounded Prism review <-> Archer
    RESPOND revise loop, boundary completion)."""
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

    waiting = live_trac("run", scenario="approval-final")
    assert "stage=M-REQ-APPROVAL" in waiting.stdout
    assert "awaiting=approval" in waiting.stdout
    assert live_trac("approve", "--actor", "LiveE2E-Human").returncode == 0

    # M-DESIGN (flow.md §8, BS-05): no human gate after approval. One
    # Archer dispatch drafts the design trio, validates and commits it, then
    # waits in PRISM_REVIEW. Prism may revise (anchored findings) and Archer
    # RESPONDs until a pass round completes the run at the M-IMPL boundary
    # after the M-DESIGN EXIT gate.
    design = live_trac("run", scenario="archer-design-draft")
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
    print(f"LIVE_E2E_REMOTE={remote}", flush=True)
    print(f"LIVE_E2E_BRANCH=releases/{version}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_BEFORE={remote_refs_before!r}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_AFTER={remote_refs_after!r}", flush=True)
