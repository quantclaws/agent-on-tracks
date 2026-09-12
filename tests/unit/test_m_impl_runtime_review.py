from tests.unit.test_m_impl_runtime_support import (
    RGR_GREEN_DIFF,
    RGR_RED_DIFF,
    Command,
    _contract,
    _executor,
    _gate_manifest,
    _git,
    _green_gate_with_r_checkpoint,
    _green_outcome,
    _immutable_r_checkpoint,
    _repo,
    _selection_failure_evidence,
    _started_task_store,
    _store,
    _structured_outcome,
    _task,
    _write_failing_unit_test,
    _write_if_mapped_integration_test,
    json,
)


def test_green_gate_fails_closed_on_runtime_unit_command_failure(tmp_path):
    """Contract 1: a well-formed Devon GREEN outcome self-reporting passing
    commands/results/manifest cannot cause a GREEN pass when the Runtime's
    SELECT_TASK re-execution of the task-selected unit node exits 1 (D-41).
    The verdict must be impl_defect carrying the actionable JSON evidence
    (selection_id/outcomes_ref/failed_nodes) and no G is created."""
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
    _write_failing_unit_test(repo)
    _write_if_mapped_integration_test(repo)
    head_before = _git(repo, "rev-parse", "HEAD")

    executor = _executor(repo, store)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "Runtime selected unit node exits 1 -> GREEN must fail closed"
    last = fails[-1].payload
    assert last["check"] == "impl_defect"
    evidence = _selection_failure_evidence(last)
    assert [node["node"] for node in evidence["failed_nodes"]] == [
        "tests/unit/test_app.py::test_app"
    ]
    assert _git(repo, "rev-parse", "HEAD") == head_before, "no G may be created"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_no_change_with_reason_does_not_short_circuit(tmp_path):
    """Regression (run 01KZTHE7 T-013 attempt 2, 2026-08-15): a GREEN resubmit
    may legitimately carry no new changed_paths when the implementation is
    already on disk. The evidence check must not short-circuit with the
    stale "Devon GREEN evidence has no changed paths" false-kill; the gate
    must proceed to re-execute the Runtime unit commands, where a genuine
    defect still fails closed with an observed unit-command reason."""
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
    no_change_outcome = _structured_outcome(
        "green", [], r_identity=red["r_sha"], diff_ref=RGR_GREEN_DIFF
    )
    no_change_outcome["no_change_reason"] = (
        "attempt 1 implementation already on disk; resubmit unchanged"
    )
    no_change_outcome["commands"] = [
        {"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}
    ]
    no_change_outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", no_change_outcome)
    _write_failing_unit_test(repo)
    _write_if_mapped_integration_test(repo)

    executor = _executor(repo, store)
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "failing unit test on disk must still fail GREEN closed"
    last = fails[-1].payload
    assert last["check"] == "impl_defect"
    assert "no changed paths" not in last.get("reason", ""), (
        "no-change GREEN with reason must not hit the stale false-kill"
    )
    evidence = _selection_failure_evidence(last)
    assert [node["node"] for node in evidence["failed_nodes"]] == [
        "tests/unit/test_app.py::test_app"
    ], "the gate must proceed to the SELECT_TASK re-execution and fail there"


def test_green_gate_not_regression_when_r_unit_test_mutation_not_claimed(tmp_path):
    """B39 (run 01M0AMKV T-006 seq 728): regression is judged ONLY on this
    task's GREEN evidence (Devon changed_paths) touching an R-frozen tests/
    file — never on the whole worktree-vs-R tests/ diff. A hidden mutation of
    the frozen R unit test that Devon did NOT report (changed_paths only
    tracks/app.py) is no longer misjudged as task cheating: the gate proceeds
    to the Runtime unit re-execution instead of emitting `regression`."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"),
        None,
        False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    # Candidate state the Runtime observes: a tests/ file differing from R,
    # but Devon's changed_paths reports only tracks/app.py.
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)
    _write_if_mapped_integration_test(repo)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert not fails, "unclaimed tree-vs-R tests diff must NOT fail GREEN as regression"
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def test_green_gate_regression_when_devon_reports_changed_r_frozen_test(tmp_path):
    """B39: the narrowed regression must still catch task cheating — a GREEN
    outcome whose changed_paths claims an R-frozen tests/ file that exists in
    the R tree fails GREEN closed with `regression` and names the file."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tests/unit/test_app.py", "tracks/app.py"],
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "GREEN evidence touching an R-frozen tests/ file must fail GREEN closed"
    assert fails[-1].payload["check"] == "regression"
    evidence = fails[-1].payload.get("evidence") or ""
    assert "tests/unit/test_app.py" in evidence, "evidence must name the R-frozen test"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_regression_when_captured_diff_touches_r_test_unclaimed(tmp_path):
    """B39 supplement: the Runtime's authoritative captured diff (outcome
    diff_ref, parsed from `diff --git` headers — not Devon's changed_paths)
    must catch a hidden mutation of an R-frozen tests/ file that Devon did
    NOT claim. The union (captured_paths ∪ claimed paths) closes the
    self-report gap instead of re-opening the seq 728 false-positive."""
    _HIDDEN_R_TEST_MUTATION_DIFF = (
        "diff --git a/tracks/app.py b/tracks/app.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/tracks/app.py\n"
        "@@ -0,0 +1 @@\n"
        "+IMPLEMENTED = True\n"
        "diff --git a/tests/unit/test_app.py b/tests/unit/test_app.py\n"
        "--- a/tests/unit/test_app.py\n"
        "+++ b/tests/unit/test_app.py\n"
        "@@ -1 +1 @@\n"
        "-def test_app(): assert False\n"
        "+def test_app(): assert True\n"
    )
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tracks/app.py"],
        diff_ref=_HIDDEN_R_TEST_MUTATION_DIFF,
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, (
        "captured-diff mutation of an R-frozen test (unclaimed by Devon) "
        "must fail GREEN closed"
    )
    assert fails[-1].payload["check"] == "regression"
    evidence = fails[-1].payload.get("evidence") or ""
    assert "tests/unit/test_app.py" in evidence, "evidence must name the hidden-mutated test"
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_green_gate_not_regression_on_tests_added_after_r(tmp_path):
    """B39 (run 01M0AMKV T-006 seq 728 repro): tests/ files added to the
    baseline AFTER R (ops fixes, earlier batch siblings) must not fail GREEN —
    the tree differs from r_sha in tests/, but Devon's changed_paths contains
    no R-frozen tests/ path."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(repo, store, executor, changed_paths=["tracks/app.py"])
    _write_if_mapped_integration_test(repo)
    (repo / "tests" / "unit" / "test_ops_fix.py").write_text(
        "def test_ops_fix():\n    assert True\n", encoding="utf-8"
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    assert not any(ev.type == "verdict.failed" for ev in store.events("RUN"))
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def test_green_gate_not_regression_when_reported_test_not_in_r(tmp_path):
    """B39: a new tests/ file that does not exist in the R tree (RED-added test
    later adjusted) is outside the frozen R set; GREEN_GATE only protects the
    R-frozen tests, later adjustments are governed by test_defect/SHIELD_FIX."""
    repo = _repo(tmp_path)
    _contract(repo)
    task = _task()
    manifest = {
        "task_id": task["task_id"],
        "allowed_paths": [
            "tracks/app.py",
            "tests/unit/test_app.py",
            "tests/unit/test_extra.py",
        ],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store = _store(repo)
    from tests.unit.helpers import m_impl_graph
    raw = json.dumps({"tasks": [task]}, sort_keys=True)
    m_impl_graph(store, task, raw=raw)
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": task["task_id"], "task": task, "manifest": manifest},
    )
    executor = _executor(repo, store)
    _green_gate_with_r_checkpoint(
        repo,
        store,
        executor,
        changed_paths=["tracks/app.py", "tests/unit/test_extra.py"],
    )
    (repo / "tests" / "unit" / "test_extra.py").write_text(
        "def test_extra():\n    assert True\n", encoding="utf-8"
    )

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert not fails, "reported test file not in R tree must NOT trigger regression"
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert passed and passed[-1].payload["check"] == "green"


def test_green_gate_ambiguous_r_identity_fails_closed_as_contract_error(tmp_path):
    """D-41: an absent/all-zero R identity is ambiguity, not a skip — the
    SELECT_TASK baseline cannot be trusted, so the gate fails closed with
    contract_error (routed to DIAGNOSE) instead of skipping the regression
    check and passing."""
    repo = _repo(tmp_path)
    _contract(repo)
    store, task = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"r_sha": "0" * 40, "task_id": task["task_id"], "attempt": 1},
    )
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome(
        "green", ["tests/unit/test_app.py", "tracks/app.py"], r_identity="0" * 40
    )
    outcome["commands"] = [{"cmd": ".venv/bin/python -m pytest -n 4 tests/unit", "result": "pass"}]
    outcome["results"] = [{"classification": "pass"}]
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    (repo / "tracks").mkdir(parents=True, exist_ok=True)
    (repo / "tracks" / "app.py").write_text('IMPLEMENTED_IF = "IF-IMPL-001"\n', encoding="utf-8")
    _write_failing_unit_test(repo, passes=True)

    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "GREEN_GATE"}, command_id="C-GATE"),
        store.state("RUN"),
        None,
        False,
    )

    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert fails, "ambiguous all-zero R identity must fail closed"
    last = fails[-1].payload
    assert last["check"] == "contract_error"
    assert "SELECT_TASK failed closed" in last["reason"]
    passed = [ev for ev in store.events("RUN") if ev.type == "verdict.passed"]
    assert not passed, "an ambiguous R identity must never pass GREEN"
    # B49 (#64): contract_error parks for the operator instead of routing
    # into DIAGNOSE (the stub_gap auto-rollback loop, run 01M0S0FQ).
    state = store.state("RUN")
    assert state.substate == "GREEN_GATE"
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"


def test_refactor_no_change_fails_closed_without_persisted_green_evidence(tmp_path):
    """D-41 supersession of Contract 3: REFACTOR `no_change` no longer blind-
    reruns the Green gate — it reuses the persisted green.committed
    evidence/identity. Without that persisted evidence the reuse state is
    unresolvable, so the gate fails closed with contract_error and parks for
    the operator (B49: awaiting_human/escalation) instead of emitting
    refactor.no_change or re-executing anything."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    outcome = _structured_outcome("refactor", [], r_identity="1" * 40)
    outcome["no_change_reason"] = "no improvements found"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    executor._do_run_refactor_gate(
        Command("run_refactor_gate", {"stage": "M-IMPL"}, command_id="C-RF"),
        store.state("RUN"),
        None,
        False,
    )
    events = list(store.events("RUN"))
    assert not any(ev.type == "refactor.no_change" for ev in events), (
        "without persisted green.committed evidence/identity no refactor.no_change "
        "may be emitted"
    )
    fails = [ev for ev in events if ev.type == "verdict.failed"]
    assert fails, "unresolvable green reuse state must fail closed"
    assert fails[-1].payload["check"] == "contract_error"
    assert "REFACTOR SELECT_TASK failed closed" in fails[-1].payload["reason"]
    assert not any(ev.type == "test.selected" for ev in events), (
        "the gate must not re-execute any selection without persisted evidence"
    )
    # B49 (#64): contract_error parks instead of DIAGNOSE routing -- the
    # substate stays wherever the gate ran from.
    state = store.state("RUN")
    assert state.substate != "DIAGNOSE"
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"

