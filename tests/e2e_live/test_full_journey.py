"""Opt-in full user journey driven by an installed wheel and real opencode.

Environment variables
---------------------
* ``TRAC_LIVE_PROVIDER/MODEL/BASE_URL/API_KEY`` - live provider config; the
  directory's ``live_enabled`` fixture skips when any is absent.
* ``TRACKS_E2E_GITHUB_REPO`` + ``GITHUB_TOKEN`` (or ``gh auth token``) - the
  disposable GitHub repo for the full/resume journeys' issue-creation and
  remote-ref assertions.
* ``TRAC_AGENT_TIMEOUT`` - per-dispatch agent (opencode subprocess) timeout.
  Default 120s; M-DESIGN DRAFT/RESPOND override to 1800s via ``agent_timeout``
  (design trio + scaffold + quality-guard is too big for 1200s).
* ``TRAC_LIVE_DESIGN_AGENT_TIMEOUT`` - env override for the M-DESIGN
  DRAFT/RESPOND ``agent_timeout`` (default 1800s; read at call time so
  observation runs can raise the cap without code changes). Non-integer or
  <=0 raises ``ValueError``; the kernel/opencode hard timeout still applies
  as a watchdog above ``max_dispatches × (agent_timeout + 300)``.
* ``TRAC_LIVE_COMMAND_TIMEOUT`` - outer ``trac run`` subprocess timeout.
  Default 1500s; auto-bumps to ``max_dispatches * (agent_timeout + 300)`` when
  no explicit ``timeout`` so a 3-attempt 1800s retry budget is not clipped.
* ``TRAC_LIVE_TOTAL_TIMEOUT`` - whole-journey deadline. Default 10800s (raised
  from 3600s so the 3-attempt retry budget at 1800s each is not clipped).
* ``TRAC_LIVE_SKIP_BASELINE=1`` - skip baseline snapshot capture after the
  M-REQ-APPROVAL checkpoint in the full journey.
* ``TRAC_LIVE_BASELINE_DIR`` - explicit baseline directory for the resume test.
* ``TRAC_LIVE_BUILD_BASELINE=1`` - if no baseline exists, run the prefix phases
  (story/spec/acceptance/approval) on a fresh host, snapshot, then continue.
* ``TRAC_LIVE_FORCE_BASELINE=1`` - use a baseline even when its tracks SHA does
  not match the current HEAD (otherwise the resume test skips on mismatch).
* ``TRAC_LIVE_LOCAL_REMOTE=1`` - for restored baseline-resume tests only, use
  an empty local bare ``origin`` under the current run's artifact directory.
"""

from __future__ import annotations

import json
import os
import sys
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
from tests.e2e_live.m_test_helpers import (
    _assert_issue_events,
    _capture_baseline,
    _capture_design_exit_baseline,
    _dispatches,
    _events,
    _find_baseline_for_sha,
    _find_design_exit_baseline,
    _git,
    _read_manifest,
    _restore_baseline,
    _sanity_check_design_exit_host,
    _sanity_check_resumed_host,
    _setup_baseline_remote,
    _tracks_short_sha,
    assert_criteria_pack_triple,
    assert_m_test_boundary,
    assert_shield_assignment_contract,
    assert_shield_no_commit_scope,
    assert_single_test_commit,
    assert_test_markers,
    snapshot_git_state,
)
from tracks.kernel.events import EventEnvelope
from tracks.kernel.machine import project as machine_project


def _design_agent_timeout() -> int:
    """Resolve the per-call agent timeout (seconds) for the M-DESIGN DRAFT and
    RESPOND steps. Reads ``TRAC_LIVE_DESIGN_AGENT_TIMEOUT`` at call time so
    observation runs can raise the cap without code changes; defaults to 1800s.
    Non-integer or non-positive values raise ``ValueError`` so a typo fails
    fast instead of silently falling back to the default."""
    raw = os.environ.get("TRAC_LIVE_DESIGN_AGENT_TIMEOUT", "").strip()
    if not raw:
        return 1800
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"TRAC_LIVE_DESIGN_AGENT_TIMEOUT must be an integer number of seconds, got {raw!r}"
        ) from exc
    if value <= 0:
        raise ValueError(f"TRAC_LIVE_DESIGN_AGENT_TIMEOUT must be > 0, got {value}")
    return value


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
    assert matches, f"no {initiator}-initiated finding thread with marker {marker}"
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
        assert human_replies, f"Human console answer was not persisted in {thread['thread_id']}"
        assert all(reply["body"].strip() for reply in human_replies)


def _assert_respond_diff(events: list[dict], role: str, doc: str, committed_type: str) -> None:
    dispatches = _dispatches(events, role, "RESPOND", doc)
    assert dispatches, f"missing {role} RESPOND dispatch for {doc}"
    dispatch = dispatches[-1]
    outcomes = [
        event
        for event in events
        if event["type"] == "outcome.received" and event["command_id"] == dispatch["command_id"]
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


def _assert_reviewer_pass(events: list[dict], event_type: str, *, expect_revise: bool = True):
    verdicts = [event for event in events if event["type"] == event_type]
    assert verdicts, f"missing {event_type} event"
    assert verdicts[-1]["payload"].get("verdict") == "pass"
    if expect_revise:
        assert any(event["payload"].get("verdict") == "revise" for event in verdicts)
    else:
        assert not any(event["payload"].get("verdict") == "revise" for event in verdicts), (
            f"unexpected revise verdict in single-pass {event_type}"
        )


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
        event["type"] == verdict_type and event["payload"].get("verdict") == "revise"
        for event in events
    )


def _run_design_review_loop(live_trac):
    """Prism review <-> Archer RESPOND loop, bounded to 2 review rounds
    (TRAC_LIVE_MAX_REVIEW_ROUNDS stays the wider outer net). Each round runs
    prism-design-review: a pass round transitions to M-TEST (the dispatch
    gate stops at Shield WRITE, substate change from PRISM_REVIEW); a revise
    round halts in RESPOND (live run042 died here while unscripted), Archer
    must answer every finding and return the state to PRISM_REVIEW, and the
    next round starts.

    v0.4: Prism pass no longer completes the run -- it transitions M-DESIGN
    EXIT -> M-TEST DISPATCH -> Shield WRITE. The dispatch gate stops at the
    substate change, so the output shows ``stage=M-TEST substate=WRITE``.

    Archer DRAFT/RESPOND carry the design trio + scaffold + quality-guard
    installation - too big for the 1200s budget, so agent_timeout=1800 is
    plumbed per call (live run049 attempt2 was ~90% done when killed at 1200s;
    run050 attempt1 hit the 1200s kernel timeout exactly). Prism review
    steps stay at the default timeout.
    """
    for review_round in range(1, 3):
        review = live_trac("run", scenario="prism-design-review")
        if "stage=M-TEST" in review.stdout:
            return review
        assert "stage=M-DESIGN" in review.stdout, review.stdout
        assert "substate=RESPOND" in review.stdout, (
            f"review round {review_round} neither transitioned to M-TEST nor "
            f"opened RESPOND: {review.stdout}"
        )
        respond = live_trac(
            "run",
            scenario="archer-design-respond",
            agent_timeout=_design_agent_timeout(),
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


def _run_m_test_review_loop(live_trac):
    """Run the M-TEST Prism review, repairing Shield revisions at most once."""
    for review_round in range(1, 3):
        prism = live_trac("run", scenario="prism-test-review")
        if "status=completed" in prism.stdout:
            return prism
        assert "stage=M-TEST" in prism.stdout, prism.stdout
        assert "substate=WRITE" in prism.stdout, (
            f"M-TEST Prism review round {review_round} neither completed nor "
            f"halted at WRITE: {prism.stdout}"
        )

        respond = live_trac("run", scenario="shield-test-respond")
        assert "stage=M-TEST" in respond.stdout, respond.stdout
        assert "substate=PRISM_REVIEW" in respond.stdout, (
            f"M-TEST Shield RESPOND round {review_round} did not return to "
            f"PRISM_REVIEW: {respond.stdout}"
        )
    raise AssertionError("M-TEST Prism review did not pass within 2 rounds")


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
    assert "substate=LEX_REVIEW" in live_trac("run", scenario="sage-spec-draft").stdout
    spec_review = _run_spec_review_loop(live_trac)
    assert "awaiting=review" in spec_review.stdout
    spec_events = _events(live_root, run_id)
    spec_respond_happened = bool(_dispatches(spec_events, "sage", "RESPOND", "spec.md"))
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
            f"unexpected spec threads when no RESPOND happened: {state.get('threads', [])}"
        )
    _assert_reviewer_pass(spec_events, "lex.verdict", expect_revise=spec_respond_happened)

    assert live_trac("review", "no-comment", "--actor", "LiveE2E-Human").returncode == 0
    assert "substate=LEX_REVIEW" in live_trac("run", scenario="sage-acceptance-draft").stdout
    acceptance_review = live_trac("run", scenario="lex-acceptance-review")
    assert "awaiting=review" in acceptance_review.stdout
    assert _discussion_state(live_trac, acceptance)["is_ready"]
    acceptance_events = _events(live_root, run_id)
    acceptance_dispatch = _dispatches(acceptance_events, "lex", "LEX_REVIEW", "acceptance.md")
    assert acceptance_dispatch, "missing normal Lex acceptance review dispatch"
    acceptance_command = acceptance_dispatch[-1]["command_id"]
    acceptance_outcomes = [
        event
        for event in acceptance_events
        if event["type"] == "outcome.received" and event["command_id"] == acceptance_command
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


def _phase_design_to_m_test(
    live_trac,
    live_root: Path,
    run_id: str,
    version: str,
):
    """Archer DRAFT + design review loop -> M-TEST WRITE (design-exit
    checkpoint). The run halts at ``stage=M-TEST substate=WRITE`` because
    the dispatch gate stops at the substate change (PRISM_REVIEW -> WRITE).

    v0.4: Prism pass transitions M-DESIGN EXIT -> M-TEST DISPATCH -> Shield
    WRITE; ``trac run`` stops before the Shield dispatch (different substate
    from the Prism review that drove the pass)."""
    design = live_trac(
        "run",
        scenario="archer-design-draft",
        agent_timeout=_design_agent_timeout(),
    )
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
        event["payload"]["doc"] for event in design_events if event["type"] == "design.committed"
    ) == ["architecture.md", "interfaces.md", "test-plan.md"]

    final = _run_design_review_loop(live_trac)
    # v0.4: Prism pass transitions to M-TEST, not run.completed. The dispatch
    # gate stops at the Shield WRITE dispatch (substate change from
    # PRISM_REVIEW to WRITE), so the run is still active at M-TEST/WRITE.
    assert "stage=M-TEST" in final.stdout, (
        f"Prism design pass should transition to M-TEST: {final.stdout}"
    )
    assert "substate=WRITE" in final.stdout, (
        f"design-exit should halt at M-TEST WRITE: {final.stdout}"
    )
    assert "status=active" in final.stdout, (
        f"run should still be active at M-TEST WRITE: {final.stdout}"
    )

    design_exit_events = _events(live_root, run_id)
    assert _dispatches(design_exit_events, "prism", "PRISM_REVIEW", None), (
        "missing Prism PRISM_REVIEW dispatch for the design trio"
    )
    prism_verdicts = [event for event in design_exit_events if event["type"] == "prism.verdict"]
    assert prism_verdicts and prism_verdicts[-1]["payload"]["verdict"] == "pass"
    # BS-05: no human gate after approval in M-DESIGN.
    approval_seq = next(
        event["seq"] for event in design_exit_events if event["type"] == "human.approval"
    )
    assert not [
        event
        for event in design_exit_events
        if event["type"].startswith("human.") and event["seq"] > approval_seq
    ], "M-DESIGN must carry no human gate (BS-05)"


def _phase_m_test_to_boundary(
    live_trac,
    live_root: Path,
    run_id: str,
    version: str,
    remote_url_before: str,
    remote_refs_before: str,
    expect_real_issues: bool,
):
    """M-TEST: Shield writes tests -> Runtime collection -> Prism reviews ->
    Runtime Red validation -> trace gate -> controlled test commit ->
    stage.exited(M-TEST) -> run.completed(boundary).

    P0 assertions:
      * Shield assignment contract (docs, skills, kind, stage, role)
      * Shield scope/no-commit (HEAD unchanged, only tests/ written)
      * Criteria-pack anti-self-report triple (assigned + echoed + no mismatch)
      * Test markers (R-1 TRACKS-TRACE in tests/integration|e2e/)
      * Strict boundary adjacency (stage.exited + stage.entered/run.completed)
      * Single test commit (exactly one new commit, matches test.committed)
    """
    git_before = snapshot_git_state(live_root)

    shield = live_trac("run", scenario="shield-test-draft")
    assert "stage=M-TEST" in shield.stdout, shield.stdout
    assert "substate=PRISM_REVIEW" in shield.stdout, (
        f"shield-test-draft did not return to PRISM_REVIEW: {shield.stdout}"
    )

    m_test_events = _events(live_root, run_id)
    assert_shield_assignment_contract(m_test_events, version)
    assert_shield_no_commit_scope(git_before, live_root, remote_refs_before)
    collected = [
        e
        for e in m_test_events
        if e["type"] == "test.collected" and e["payload"].get("status") == "passed"
    ]
    assert collected, "missing test.collected(passed) event after Shield WRITE"
    tests_dir = live_root / "tests"
    assert (tests_dir / "integration").is_dir(), "tests/integration/ missing"
    assert (tests_dir / "e2e").is_dir(), "tests/e2e/ missing"
    assert_test_markers(tracks_tests_dir=tests_dir, version=version)

    prism = _run_m_test_review_loop(live_trac)
    assert "status=completed" in prism.stdout, (
        f"M-TEST should complete at boundary: {prism.stdout}"
    )
    assert "awaiting=-" in prism.stdout, prism.stdout

    final_events = _events(live_root, run_id)
    assert_m_test_boundary(final_events)

    test_committed = [e for e in final_events if e["type"] == "test.committed"]
    assert len(test_committed) == 1, (
        f"expected exactly one test.committed, got {len(test_committed)}"
    )
    assert_single_test_commit(git_before["head"], live_root, test_committed[0])
    assert_criteria_pack_triple(final_events)

    report_dir = live_root / "report"
    assert (
        live_trac(
            "report",
            "--run-id",
            run_id,
            "--output",
            str(report_dir),
            "--format",
            "html",
        ).returncode
        == 0
    )
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
        "stage: `M-TEST`",
    ):
        assert required in report
    assert (report_dir / "index.html").is_file()
    assert "marked v15.0.7" in (report_dir / "index.html").read_text(encoding="utf-8")

    _assert_issue_events(final_events, expect_real_issues)
    assert _git(live_root, "remote", "get-url", "origin") == remote_url_before
    remote_refs_after = _git(live_root, "ls-remote", "origin")
    assert remote_refs_after == remote_refs_before
    assert _git(live_root, "symbolic-ref", "--short", "HEAD") == f"releases/{version}"
    print(f"LIVE_E2E_REMOTE={remote_url_before}", flush=True)
    print(f"LIVE_E2E_BRANCH=releases/{version}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_BEFORE={remote_refs_before!r}", flush=True)
    print(f"LIVE_E2E_REMOTE_REFS_AFTER={remote_refs_after!r}", flush=True)


# -- baseline snapshot (deliverable #2) ------------------------------------


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
    started = live_trac("start", version, stdin=live_scenarios["triage"].start_requirement)
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
    and the M-DESIGN + M-TEST stages. After the approval checkpoint a
    baseline snapshot is captured so the resume test can re-run only
    ``_phase_design``. After the M-DESIGN tail (design-exit) a second
    baseline is captured so the M-TEST resume test can re-run only
    ``_phase_m_test_to_boundary``."""
    version = "live-e2e-code-stats"
    run_id, remote_url_before, remote_refs_before, remote = _setup_host(
        live_trac, live_root, live_github_repo, live_scenarios, version
    )

    _phase_story(live_trac, live_root, run_id, version)
    _phase_spec_and_acceptance(live_trac, live_root, run_id, version)
    _phase_approval(live_trac)

    # Baseline snapshot at the M-REQ-APPROVED checkpoint (deliverable #2).
    _capture_baseline(live_root, version, run_id)

    _phase_design_to_m_test(live_trac, live_root, run_id, version)

    # Design-exit baseline (v0.4 M-TEST milestone): snapshot at
    # stage=M-TEST substate=WRITE so the M-TEST resume test skips the
    # expensive prefix + Archer DRAFT + Prism review loop.
    _capture_design_exit_baseline(live_root, version, run_id)

    _phase_m_test_to_boundary(
        live_trac,
        live_root,
        run_id,
        version,
        remote_url_before,
        remote_refs_before,
        expect_real_issues=True,
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
                    "TRAC_LIVE_BUILD_BASELINE=1 but capture was skipped (TRAC_LIVE_SKIP_BASELINE=1)"
                )
            run_id = _sanity_check_resumed_host(live_trac, live_root, baseline)
            _phase_design_to_m_test(live_trac, live_root, run_id, version)
            _capture_design_exit_baseline(live_root, version, run_id)
            _phase_m_test_to_boundary(
                live_trac,
                live_root,
                run_id,
                version,
                remote_url_before,
                remote_refs_before,
                expect_real_issues=False,
            )
            return
        pytest.skip(
            "no M-REQ-APPROVED baseline; run the full journey or set TRAC_LIVE_BUILD_BASELINE=1"
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

    # Capture refs_before AFTER restore so the baseline's local commits are in
    # place. The explicit local-remote switch rewires origin before ls-remote.
    remote_url_before, remote_refs_before = _setup_baseline_remote(live_root)

    run_id = _sanity_check_resumed_host(live_trac, live_root, baseline)
    _phase_design_to_m_test(live_trac, live_root, run_id, version)
    _capture_design_exit_baseline(live_root, version, run_id)
    _phase_m_test_to_boundary(
        live_trac,
        live_root,
        run_id,
        version,
        remote_url_before,
        remote_refs_before,
        expect_real_issues=False,
    )


def test_m_test_from_design_exit_baseline(
    live_root,
    host_with_opencode_config,
    live_scenarios,
    live_trac,
    monkeypatch,
):
    """Resume the journey from an M-TEST/WRITE (design-exit) baseline:
    restore the snapshot, sanity-check the M-TEST/WRITE state, then run
    ONLY ``_phase_m_test_to_boundary``. Never replays triage/scribe/sage/
    acceptance/approval/Archer-DRAFT/Prism-design-review.

    Gating: ``live_enabled`` (via live_root) skips when the provider env is
    absent. The baseline check runs BEFORE the GitHub requirement so a
    missing baseline skips cleanly.

    Build path (``TRAC_LIVE_BUILD_BASELINE=1``): run the prefix phases +
    Archer DRAFT + Prism design review loop, snapshot at design-exit, then
    continue to ``_phase_m_test_to_boundary``."""
    version = "live-e2e-code-stats"

    baseline = _find_design_exit_baseline(version)
    if baseline is None:
        if os.environ.get("TRAC_LIVE_BUILD_BASELINE", "").strip() == "1":
            slug = _require_github(monkeypatch, live_root)
            run_id, remote_url_before, remote_refs_before, _ = _setup_host(
                live_trac, live_root, slug, live_scenarios, version
            )
            _phase_story(live_trac, live_root, run_id, version)
            _phase_spec_and_acceptance(live_trac, live_root, run_id, version)
            _phase_approval(live_trac)
            _capture_baseline(live_root, version, run_id)
            _phase_design_to_m_test(live_trac, live_root, run_id, version)
            baseline = _capture_design_exit_baseline(live_root, version, run_id)
            if baseline is None:
                pytest.skip(
                    "TRAC_LIVE_BUILD_BASELINE=1 but capture was skipped (TRAC_LIVE_SKIP_BASELINE=1)"
                )
            run_id = _sanity_check_design_exit_host(live_trac, live_root, baseline)
            _phase_m_test_to_boundary(
                live_trac,
                live_root,
                run_id,
                version,
                remote_url_before,
                remote_refs_before,
                expect_real_issues=False,
            )
            return
        pytest.skip(
            "no DESIGN_EXIT_OBSERVED baseline; run the full journey"
            " or set TRAC_LIVE_BUILD_BASELINE=1"
        )

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

    remote_url_before, remote_refs_before = _setup_baseline_remote(live_root)

    run_id = _sanity_check_design_exit_host(live_trac, live_root, baseline)
    _phase_m_test_to_boundary(
        live_trac,
        live_root,
        run_id,
        version,
        remote_url_before,
        remote_refs_before,
        expect_real_issues=False,
    )


# -- deterministic unit test for outcome status=None hardening (deliverable #5) --


def _unit_envelopes(*items: tuple[str, dict]) -> list[EventEnvelope]:
    out = []
    for i, (etype, payload) in enumerate(items, start=1):
        out.append(
            EventEnvelope(
                seq=i,
                ts="2026-08-04T00:00:00+00:00",
                run_id="RUN",
                version="v0.1",
                type=etype,
                schema_version=1,
                command_id=f"C{i}",
                task_id=None,
                payload=payload,
            )
        )
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
    dispatched = (
        "command.issued",
        {
            "command": {
                "kind": "dispatch_agent",
                "params": {"role": "archer", "substate": "DRAFT"},
                "command_id": "C1",
            }
        },
    )
    # status=None - the bug: previously treated as success (doc_produced=True).
    none_outcome = (
        "outcome.received",
        {"role": "archer", "status": None, "self_report": "unparseable"},
    )

    one = machine_project(_unit_envelopes(*enter, dispatched, none_outcome))
    # The None status must consume an attempt (not set doc_produced).
    assert one.doc_produced is False, "status=None must not be treated as a produced document"
    assert one.current_attempt == 1, (
        f"status=None must consume an attempt; got {one.current_attempt}"
    )
    assert one.last_failure is not None
    assert one.last_failure["check"] == "agent_error", (
        f"missing/None status must classify as agent_error; got {one.last_failure['check']}"
    )
    assert one.status == "active", "first None-status outcome must not escalate"

    # Two more None-status outcomes escalate (3-attempt budget).
    two = machine_project(
        _unit_envelopes(
            *enter,
            dispatched,
            none_outcome,
            dispatched,
            none_outcome,
        )
    )
    assert two.current_attempt == 2
    assert two.status == "active", "second None-status outcome must not escalate yet"

    three = machine_project(
        _unit_envelopes(
            *enter,
            dispatched,
            none_outcome,
            dispatched,
            none_outcome,
            dispatched,
            none_outcome,
        )
    )
    assert three.status == "awaiting_human"
    assert three.awaiting == "escalation", (
        f"third None-status outcome must escalate; got awaiting={three.awaiting}"
    )
    assert (
        machine_project(
            _unit_envelopes(
                *enter,
                dispatched,
                none_outcome,
                dispatched,
                none_outcome,
                dispatched,
                none_outcome,
            )
        ).stage
        == "M-DESIGN"
    )

    # A done status still passes through as a produced doc (regression guard).
    done_outcome = ("outcome.received", {"role": "archer", "status": "done"})
    ok = machine_project(_unit_envelopes(*enter, dispatched, done_outcome))
    assert ok.doc_produced is True
    assert ok.current_attempt == 0
    assert ok.last_failure is None
