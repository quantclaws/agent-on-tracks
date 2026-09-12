import pytest

from tests.unit.test_m_impl_runtime_support import (
    _TASK_REVIEW_FLAWS,
    RGR_RED_DIFF,
    Command,
    Path,
    _add_lint_section,
    _contract,
    _executor,
    _fake_linter,
    _gate_manifest,
    _git,
    _green_outcome,
    _immutable_r_checkpoint,
    _impl_only_started_store,
    _m_impl_red_classification_error,
    _m_impl_red_classifications,
    _red_gate_lint_store,
    _repo,
    _run_red_gate,
    _selection_failure_evidence,
    _started_task_store,
    _structured_outcome,
    _task,
    _task_review_scenario,
    _write_failing_unit_test,
    _write_if_mapped_integration_test,
    cleanup_worktree,
    create_gate_worktree,
    json,
)


@pytest.mark.parametrize('flaw', _TASK_REVIEW_FLAWS)
def test_task_review_is_not_unconditional(tmp_path, flaw):
    """Contract 4: TASK_REVIEW is not unconditional. Observed scope overflow,
    exact-lineage failure, secret-shaped added line, missing AC/provenance, or
    budget overflow must fail closed (verdict.failed, never verdict.passed)."""
    repo = _repo(tmp_path)
    executor, store = _task_review_scenario(repo, flaw)
    assert store.state("RUN").substate == "TASK_REVIEW"
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        store.state("RUN"),
        None,
        False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, f"{flaw}: TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.failed", (
        f"{flaw}: TASK_REVIEW must fail closed, got {new_events[-1].type}"
    )
    assert new_events[-1].payload.get("reason"), f"{flaw}: TASK_REVIEW failure must carry a reason"


def test_gate_commands_run_in_runtime_selected_worktree_cwd(tmp_path):
    """Contract 5: gate commands run in the Runtime-selected gate/candidate
    worktree cwd — never the cwd an Agent reports. Agent-reported commands and
    results stay audit-only. Under D-41 the proof is behavioral: the selected
    unit node exists ONLY in the prepared Runtime worktree, so an impl_defect
    verdict whose failed_nodes name that node proves the SELECT_TASK execution
    ran there (a main-repo fallback would fail closed as contract_error with
    an empty inventory)."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, red["r_sha"], agent_cwd="/agent/claimed/cwd"),
    )
    base = _git(repo, "rev-parse", "HEAD")
    gate = create_gate_worktree(str(repo), base, "", "", "RUN", task["task_id"])
    try:
        _write_failing_unit_test(Path(gate.path))
        _write_if_mapped_integration_test(Path(gate.path))
        executor = _executor(repo, store)
        executor._do_run_task_gates(
            Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
            store.state("RUN"),
            None,
            False,
        )
        fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
        assert fails, "the Runtime gate must execute and fail closed"
        last = fails[-1].payload
        assert last["check"] == "impl_defect"
        evidence = _selection_failure_evidence(last)
        assert [node["node"] for node in evidence["failed_nodes"]] == [
            "tests/unit/test_app.py::test_app"
        ], (
            "the selected node failed where only the prepared Runtime worktree "
            "has it - the gate ran in that worktree"
        )
        audit_outcomes = [
            ev.payload for ev in store.events("RUN") if ev.type == "outcome.received"
        ]
        assert "/agent/claimed/cwd" in json.dumps(audit_outcomes), (
            "agent-reported cwd should remain preserved as audit-only evidence"
        )
        runtime_evidence = [
            ev.payload
            for ev in store.events("RUN")
            if ev.type in ("test.selected", "verdict.failed", "verdict.passed")
        ]
        assert "/agent/claimed/cwd" not in json.dumps(runtime_evidence), (
            "agent-reported cwd must never become Runtime execution evidence"
        )
    finally:
        cleanup_worktree(gate)


def test_task_review_budget_respects_retry_cutoff(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): verdict.failed events
    recorded BEFORE a human.retry are superseded (FR-11 budget reset) and
    must not fail TASK_REVIEW's budget check. The budget_overflow flaw still
    fails when no retry intervenes."""
    repo = _repo(tmp_path)
    executor, store = _task_review_scenario(repo, "budget_overflow")
    store.append("RUN", "v0.5", "human.retry", {})
    state = store.state("RUN")
    assert state.substate == "TASK_REVIEW", "retry must not leave the review substate"
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state,
        None,
        False,
    )
    budget_fails = [
        ev
        for ev in store.events("RUN")
        if ev.type == "verdict.failed"
        and ev.payload.get("check") == "budget"
        and ev.seq > max(e.seq for e in store.events("RUN") if e.type == "human.retry")
    ]
    assert not budget_fails, (
        "pre-retry verdict.failed must not fail the post-retry budget check"
    )


def test_m_impl_event_recorded_respects_retry_cutoff(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): `trac retry` (FR-11)
    resets the attempt budget, so a post-retry attempt number N is a fresh
    attempt, not the pre-retry attempt N. Idempotency scans must only
    consider events strictly after the last human.retry."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": "a" * 40,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": "b" * 40,
            "base_sha": "c" * 40,
            "trailers": [],
        },
    )
    executor = _executor(repo, store)
    assert executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "without a retry the recorded event must be visible"
    )
    store.append("RUN", "v0.5", "human.retry", {"task_id": task["task_id"]})
    assert not executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "after human.retry the pre-retry event is superseded"
    )
    store.append(
        "RUN",
        "v0.5",
        "green.committed",
        {
            "g_sha": "d" * 40,
            "task_id": task["task_id"],
            "attempt": 1,
            "r_sha": "e" * 40,
            "base_sha": "f" * 40,
            "trailers": [],
        },
    )
    assert executor._m_impl_event_recorded("green.committed", task["task_id"], 1), (
        "a fresh post-retry event must be visible again"
    )


def test_commit_green_stale_prefailure_does_not_block_after_retry(tmp_path):
    """Regression (run 01KZTHE7 T-013, 2026-08-16): a verdict.failed(
    impl_defect, attempt=1) recorded BEFORE human.retry must not false-
    positive the post-retry fresh attempt=1 with "Runtime gate already
    failed for this attempt"."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {
            "check": "impl_defect",
            "reason": "stale pre-retry failure",
            "evidence": "legacy",
            "task_id": task["task_id"],
            "attempt": 1,
        },
    )
    store.append("RUN", "v0.5", "human.retry", {"task_id": task["task_id"]})
    state = store.state("RUN")
    assert state.current_attempt == 0, "retry must reset the attempt budget"

    executor = _executor(repo, store)
    before = len(list(store.events("RUN")))
    executor._do_commit_green(
        Command("commit_green", {"task_id": task["task_id"]}, command_id="C-CG"),
        state,
        task["task_id"],
        None,
    )
    new_events = list(store.events("RUN"))[before:]
    stale_blocks = [
        ev
        for ev in new_events
        if ev.type == "verdict.failed"
        and ev.payload.get("reason") == "Runtime gate already failed for this attempt"
    ]
    assert not stale_blocks, (
        "pre-retry verdict.failed must not block the post-retry fresh attempt"
    )


def test_red_classification_inferred_from_commands_when_results_missing(tmp_path):
    """Devon RED outcomes that omit `results` should still pass RED_GATE when
    `commands[*].output_summary` carries a legal `classify_red -> <token>`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {
            "cmd": "pytest",
            "output_summary": (
                "37 failed / 0 passed, exit 1; runtime classify_red -> "
                "assertion_failure (legal M-IMPL red)"
            ),
        }
    ]
    outcome["verdict"] = "assertion_failure"
    event = _run_red_gate(executor, store, outcome)
    assert event.type == "verdict.passed"
    assert event.payload["check"] == "red_valid"


def test_red_classification_inference_fails_when_no_pattern_in_commands(tmp_path):
    """An empty/legal `commands` list with no `classify_red` token must NOT
    be inferred - the gate stays fail-closed with `classifications missing`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [{"cmd": "pytest", "output_summary": "all good"}]
    outcome["verdict"] = "assertion_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "missing" in failed.payload["reason"]


def test_red_classification_inference_rejects_stub_token(tmp_path):
    """`classify_red -> stub_token_failure` must NOT be inferred; only
    assertion_failure/symbol_missing match. Ensures the counterexample patch
    (`red_classification.patch`) keeps failing."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {"cmd": "pytest", "output_summary": "classify_red -> stub_token_failure"}
    ]
    outcome["verdict"] = "stub_token_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "missing" in failed.payload["reason"]


def test_red_classification_inference_mixed_fails(tmp_path):
    """When commands disagree (assertion_failure vs symbol_missing) the
    existing `mixed classifications` check must still fire."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        diff_ref=RGR_RED_DIFF,
    )
    outcome.pop("results", None)
    outcome["commands"] = [
        {"cmd": "pytest", "output_summary": "classify_red -> assertion_failure"},
        {"cmd": "pytest", "output_summary": "classify_red -> symbol_missing"},
    ]
    outcome["verdict"] = "assertion_failure"
    failed = _run_red_gate(executor, store, outcome)
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "mixed" in failed.payload["reason"]


def test_validated_diff_generates_from_changed_paths_when_diff_ref_missing(tmp_path):
    """When `diff_ref` is absent but `changed_paths` references a file that
    exists on disk, the gate must reconstruct the diff via `git add -N` +
    `git diff` instead of failing `no captured diff_ref`."""
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    test_file = repo / "tests" / "unit" / "test_app.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_app():\n    assert False\n", encoding="utf-8")
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
    )
    outcome.pop("diff_ref", None)
    event = _run_red_gate(executor, store, outcome)
    assert event.type == "verdict.passed"
    assert event.payload["check"] == "red_valid"


def test_devon_red_test_dirs_helper(tmp_path):
    """The RED write-grant helper derives Devon test dirs from
    project.toml [layout.devon] (rstripped, same source as _unit_commands)."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    assert executor._devon_red_test_dirs() == ["tests/unit"]


def test_manifest_includes_red_test_paths_for_impl_only_task(tmp_path):
    """Regression (T-016 manifest deadlock): an impl-only task (test_refs all
    integration/e2e) must still publish red_test_paths and a phase_rules.red
    that names it, so Devon knows RED unit tests live in tests/unit/ even
    though allowed_paths (green impl scope) grants no test path."""
    repo = _repo(tmp_path)
    task = _task("tests/integration/test_impl.py::test_impl")
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    state = store.state("RUN")
    task_node = executor._task_node(task)
    manifest = executor._task_manifest(task_node, state)
    assert manifest["allowed_paths"] == ["tracks/app.py"]
    assert manifest["red_test_paths"] == ["tests/unit"]
    assert "red_test_paths" in manifest["phase_rules"]["red"]


def test_assignment_red_injects_red_test_paths_for_stale_manifest(tmp_path):
    """Regression (T-016 retry): the manifest persists at task.started, so a
    retry reuses a stale manifest without red_test_paths. Devon RED dispatch
    must inject red_test_paths at the assignment level and refresh the stale
    manifest's phase_rules.red so the retry sees the new contract."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    assignment = {
        "phase": "red",
        "manifest": {
            "task_id": task["task_id"],
            "allowed_paths": ["tracks/app.py"],
            "forbidden_paths": [],
            "phase_rules": {
                "red": "write failing unit tests only",
                "green": "write implementation only; keep R tests immutable",
                "refactor": "quality-only changes; preserve green behavior",
            },
        },
    }
    executor._add_assignment_role_fields(
        assignment, store.state("RUN"), {"role": "devon", "substate": "RED"}
    )
    assert assignment["red_test_paths"] == ["tests/unit"]
    assert assignment["manifest"]["red_test_paths"] == ["tests/unit"]
    assert "red_test_paths" in assignment["manifest"]["phase_rules"]["red"]


def test_red_classification_infers_assertion_failure_from_natural_language():
    """Fix L regression (T-016 attempt 2, 2026-08-16): Devon RED outcome
    omits `results` and uses natural-language failure descriptions instead
    of the `classify_red ->` token. Keyword inference must still recognize
    the legal `assertion_failure` classification so RED_GATE does not waste
    an attempt budget on a bogus `red_invalid` verdict.

    Logic-chain check (spec item (c)):
    - cmd1 (fail, full real summary): no assembly-error substring; no symbol
      pattern; `assert` keyword present -> assertion_failure.
    - cmd2 (fail, "no collection/import errors"): the `collection error`
      pattern needs a literal "collection error" substring; the summary
      writes "collection/import errors" (slash, not space) so it does NOT
      match; `ImportError` is case-sensitive and the summary has lowercase
      "import" only, so it does NOT match -> not assembly, not symbol, and
      no `assert` keyword -> infers nothing.
    - cmd3 (pass guard): result != "fail" -> skipped even though its
      summary contains `assert`.
    """
    outcome = {
        "role": "devon",
        "status": "done",
        "phase": "red",
        "changed_paths": ["tests/unit/test_executor_doc_gap.py"],
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest -n 4 tests/unit/test_executor_doc_gap.py --tb=short -q",
                "result": "fail",
                "output_summary": (
                    "6 collected: 2 passed (AC-FR0234-04 no-delta control, "
                    "AC-NFR0090-02 open-thread guard), 4 failed on §1m contract "
                    "tokens: test_legal_discussion_pauses_outcome_before_ordinary_validation "
                    "(assert len(doc_comment.detected)==0==1), "
                    "test_illegal_body_edit_is_rejected_atomically_as_over_reach "
                    "(assert len(outcome.rejected)==0==1), "
                    "test_illegal_body_edit_takes_precedence_over_legal_discussion "
                    "(assert len(outcome.rejected)==0==1), "
                    "test_closed_thread_resumes_with_new_dispatch_and_attempt "
                    "(assert (0+0)==1 on outcome.restored|discarded). Failures are "
                    "missing-event contract gaps in executor doc-gap wiring, not "
                    "assembly errors (control test passes, fixtures sound)."
                ),
            },
            {
                "cmd": ".venv/bin/python -m pytest -n 4 tests/unit --tb=short -q",
                "result": "fail",
                "output_summary": (
                    "Full unit suite: only the 4 doc-gap tests above fail; all "
                    "other unit tests pass. New RED test file introduces no "
                    "collection/import errors or regressions elsewhere."
                ),
            },
            {
                "cmd": ".venv/bin/python -m ruff check .",
                "result": "pass",
                "output_summary": "All checks passed.",
            },
        ],
        "manifest_compliance": True,
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["assertion_failure"]
    assert has_results is True
    assert _m_impl_red_classification_error(outcome) is None


def test_red_classification_keyword_inference_skips_assembly_errors():
    """Assembly errors (collection/import/fixture) are never inferred as a
    legal RED classification - the failure is illegit and should route to
    collection_error, not assertion_failure/symbol_missing."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest tests/unit/test_x.py -q",
                "result": "fail",
                "output_summary": (
                    "ERROR collecting tests/unit/test_x.py collection error: "
                    'cannot import name "_fixture"'
                ),
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["missing"]
    assert has_results is False


def test_red_classification_keyword_inference_symbol_missing():
    """AttributeError on a missing product-code symbol is the canonical
    `symbol_missing` RED failure: the test runs, but the product object
    lacks the expected attribute/method. Must infer `symbol_missing`, not
    `assertion_failure` and not assembly error."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m pytest tests/unit/test_doc_gap.py -q",
                "result": "fail",
                "output_summary": (
                    "AttributeError: 'Executor' object has no attribute "
                    "'_route_doc_comment_first' - the doc-gap branch is "
                    "not yet wired in the executor dispatch."
                ),
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["symbol_missing"]
    assert has_results is True
    assert _m_impl_red_classification_error(outcome) is None


def test_red_classification_keyword_inference_ignores_passing_guards():
    """Passing guard commands (ruff, git status) are not RED evidence even
    if their output_summary mentions `assert`. Only `result: "fail"`
    commands are inspected by the keyword-inference fallback."""
    outcome = {
        "phase": "red",
        "commands": [
            {
                "cmd": ".venv/bin/python -m ruff check .",
                "result": "pass",
                "output_summary": "All checks passed. assert count is fine.",
            }
        ],
    }
    classifications, has_results = _m_impl_red_classifications(outcome)
    assert classifications == ["missing"]
    assert has_results is False


def test_red_gate_lint_findings_fail_with_check_lint(tmp_path):
    """B4 (issue #5): RED deliverables with declared-lint findings fail the
    gate as check=lint — mechanical errors surface in the phase that made
    them instead of detonating at commit time (T-018 burned 3 attempts)."""
    repo = _repo(tmp_path)
    _contract(repo)
    linter = _fake_linter(
        repo,
        exit_code=1,
        output="tests/unit/test_app.py:28:1: F401 'textwrap' imported but unused",
    )
    _add_lint_section(repo, linter)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    executor = _executor(repo, store)

    last = _run_red_gate(executor, store, outcome)

    assert last.type == "verdict.failed"
    assert last.payload["check"] == "lint"
    assert "F401" in last.payload["evidence"]


def test_red_gate_clean_lint_and_missing_tool_fail_open(tmp_path):
    """B4 (issue #5): a clean declared linter leaves the gate untouched; a
    missing/unrunnable linter is skipped fail-open with an audit note on the
    passed verdict (hygiene tooling must not block the pipeline)."""
    repo = _repo(tmp_path)
    _contract(repo)
    clean = _fake_linter(repo, exit_code=0, output="")
    _add_lint_section(repo, clean)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    executor = _executor(repo, store)

    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert "lint" not in last.payload

    _add_lint_section(repo, "./no_such_linter")
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert last.payload["lint"].startswith("lint skipped")


def test_red_gate_without_lint_contract_skips_lint(tmp_path):
    """No [lint] section -> no lint at all (language-neutral default; existing
    projects keep their gate behavior byte-compatibly)."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, _task = _red_gate_lint_store(repo)
    _write_failing_unit_test(repo)
    executor = _executor(repo, store)
    outcome = _structured_outcome(
        "red",
        ["tests/unit/test_app.py"],
        classification="assertion_failure",
        verdict="assertion_failure",
        diff_ref=RGR_RED_DIFF,
    )
    last = _run_red_gate(executor, store, outcome)
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"
    assert "lint" not in last.payload


def test_green_gate_lint_findings_fail_with_check_lint(tmp_path):
    """B4 (issue #5): GREEN lints the delivered non-test sources with the
    declared command; findings fail the gate as check=lint."""
    repo = _repo(tmp_path)
    _contract(repo)
    linter = _fake_linter(repo, exit_code=1, output="tracks/app.py:1:1: E501 line too long")
    _add_lint_section(repo, linter)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": _gate_manifest(task)},
    )
    red = _immutable_r_checkpoint(repo, task)
    store.append("RUN", "v0.5", "red.checkpointed", red)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _green_outcome(task, red["r_sha"]),
    )
    _write_failing_unit_test(repo, passes=True)
    _write_if_mapped_integration_test(repo)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text(
        'IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8"
    )
    executor = _executor(repo, store)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "lint findings must fail the GREEN gate closed"
    assert fails[-1].payload["check"] == "lint"
    assert "E501" in fails[-1].payload["evidence"]

