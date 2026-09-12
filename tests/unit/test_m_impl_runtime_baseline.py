from tests.unit.test_m_impl_runtime_support import (
    Command,
    _contract,
    _docs,
    _executor,
    _git,
    _graph,
    _RecordingBackend,
    _repo,
    _store,
    _structured_outcome,
    _task,
    hashlib,
    json,
    m_impl_baseline_digest,
    m_impl_baseline_missing,
)


def test_baseline_payload_is_deterministic_and_stale_inputs_are_visible(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    store = _store(repo)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    executor = _executor(repo, store)
    command = Command("freeze_baseline", command_id="C-BASE")
    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    payload = list(store.events("RUN"))[-1].payload
    assert {"status", "digest", "summary", "frozen_test_paths"} <= payload.keys()
    assert payload["status"] == "current"
    digest = m_impl_baseline_digest(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=payload["frozen_test_paths"],
        branch=payload["branch"],
        tip=payload["tip"],
        design_checkpoint=payload["design_checkpoint"],
    )
    assert payload["digest"] == digest

    (repo / "tests" / "e2e").rmdir()
    executor._do_freeze_baseline(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].payload["status"] == "stale"
    assert "tests/e2e/" in list(store.events("RUN"))[-1].payload["missing"]


def test_m_impl_baseline_digest_is_bound_to_branch_tip_and_design_checkpoint(tmp_path):
    """The M-IMPL baseline digest must change when the branch name, branch tip,
    or the M-DESIGN checkpoint commit identity changes (identity binding)."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    common = {
        "vdir": vdir,
        "repo": repo,
        "approval_digest": "approval-1",
        "issue_evidence": '{"FR-0001":1}',
        "frozen_test_paths": ["tests/integration/", "tests/e2e/"],
    }
    branch_a = m_impl_baseline_digest(
        **common, branch="main", tip="0" * 40, design_checkpoint="1" * 40
    )
    branch_b = m_impl_baseline_digest(
        **common, branch="release/v0.5", tip="0" * 40, design_checkpoint="1" * 40
    )
    tip_b = m_impl_baseline_digest(
        **common, branch="main", tip="2" * 40, design_checkpoint="1" * 40
    )
    checkpoint_b = m_impl_baseline_digest(
        **common, branch="main", tip="0" * 40, design_checkpoint="3" * 40
    )
    assert branch_a != branch_b
    assert branch_a != tip_b
    assert branch_a != checkpoint_b


def test_m_impl_baseline_missing_reports_absent_identity(tmp_path):
    """A baseline missing its branch name, branch tip, or M-DESIGN checkpoint
    commit identity reports the identity as missing (fail closed)."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    missing = m_impl_baseline_missing(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=["tests/integration/"],
        contract_valid=True,
        branch="",
        tip="",
        design_checkpoint="",
    )
    assert "branch.name" in missing
    assert "branch.tip" in missing
    assert "design.checkpointed" in missing


def test_freeze_baseline_payload_binds_branch_tip_and_design_checkpoint(tmp_path):
    """baseline.frozen payload carries the branch name, branch tip, and the
    M-DESIGN checkpoint commit identity the digest is bound to."""
    repo = _repo(tmp_path)
    _contract(repo)
    vdir = _docs(repo)
    store = _store(repo)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    tip = _git(repo, "rev-parse", "HEAD")
    executor = _executor(repo, store)
    executor._do_freeze_baseline(
        Command("freeze_baseline", command_id="C-BASE-ID"),
        store.state("RUN"),
        None,
        False,
    )
    payload = list(store.events("RUN"))[-1].payload
    assert payload["status"] == "current"
    assert payload.get("branch") == _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    assert payload.get("tip") == tip
    assert payload.get("design_checkpoint") == tip
    digest = m_impl_baseline_digest(
        vdir,
        repo,
        approval_digest="approval-1",
        issue_evidence='{"FR-0001":1}',
        frozen_test_paths=payload["frozen_test_paths"],
        branch=payload["branch"],
        tip=payload["tip"],
        design_checkpoint=payload["design_checkpoint"],
    )
    assert payload["digest"] == digest


def test_freeze_baseline_missing_design_checkpoint_is_stale(tmp_path):
    """A baseline with no M-DESIGN checkpoint identity fails closed as stale."""
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo, design_checkpoint=False)
    store.append("RUN", "v0.5", "approval.recorded", {"digest": "approval-1", "actor": "human"})
    store.append("RUN", "v0.5", "issues.created", {"mapping": {"FR-0001": 1}})
    executor = _executor(repo, store)
    executor._do_freeze_baseline(
        Command("freeze_baseline", command_id="C-BASE-STALE"),
        store.state("RUN"),
        None,
        False,
    )
    payload = list(store.events("RUN"))[-1].payload
    assert payload["status"] == "stale"
    assert "design.checkpointed" in payload["missing"]


def test_taskgraph_missing_invalid_valid_and_no_tasks_json_overwrite(tmp_path):
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TASK")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert not (vdir / "tasks.json").exists()
    assert list(store.events("RUN"))[-1].payload["check"] == "taskgraph"

    invalid = vdir / "tasks.json"
    invalid.write_text('{"tasks": [{"task_id": "T-001"}]}', encoding="utf-8")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].type == "verdict.failed"

    task = _task()
    source = json.dumps({"tasks": [task]}, indent=2)
    invalid.write_text(source, encoding="utf-8")
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    assert invalid.read_text(encoding="utf-8") == source
    assert (vdir / "tasks.md").exists()
    committed = [ev for ev in store.events("RUN") if ev.type == "taskgraph.committed"][-1]
    assert committed.payload["task_ids"] == ["T-001"]


def test_island_gate_rechecks_six_tuple_and_passes_only_when_closed(tmp_path):
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [_task()]}, sort_keys=True), encoding="utf-8"
    )
    store = _store(repo)
    raw = (vdir / "tasks.json").read_text(encoding="utf-8")
    _graph(store, _task(), raw)
    executor = _executor(repo, store)
    command = Command("check_island_1", command_id="C-ISLAND")
    executor._do_check_island_1(command, store.state("RUN"), None, False)
    assert list(store.events("RUN"))[-1].payload == {"check": "island_1"}

    (vdir / "architecture.md").write_text(
        "# Architecture\n\n- **FR-0001**: owner=app, surface=cli\n",
        encoding="utf-8",
    )
    executor._do_check_island_1(command, store.state("RUN"), None, False)
    failed = list(store.events("RUN"))[-1]
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "island"


def test_manifest_persists_replays_and_ready_selection_is_serial(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    first = _task()
    second = {
        **_task("tests/unit/test_second.py::test_second"),
        "task_id": "T-002",
        "batch": "2",
        "depends_on": ["T-001"],
    }
    raw = json.dumps({"tasks": [second, first]}, sort_keys=True)
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {
            "task_count": 2,
            "task_ids": ["T-001", "T-002"],
            "tasks": [second, first],
            "path": "tasks.json",
            "digest": hashlib.sha256(raw.encode()).hexdigest(),
            "validate_status": "pass",
        },
    )
    executor = _executor(repo, store)
    command = Command("select_task", command_id="C-SELECT")
    executor._do_select_task(command, store.state("RUN"), None, False)
    events = list(store.events("RUN"))
    assert len([ev for ev in events if ev.type == "writelock.granted"]) == 1
    assert len([ev for ev in events if ev.type == "task.started"]) == 1
    state = store.state("RUN")
    assert state.current_task_id == "T-001"
    assert state.current_manifest["task_id"] == "T-001"
    replay = store.state("RUN")
    assert replay.current_manifest == state.current_manifest
    executor._do_select_task(command, replay, None, True)
    events = list(store.events("RUN"))
    assert len([ev for ev in events if ev.type == "task.started"]) == 1

    store.append("RUN", "v0.5", "task.completed", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-001"})
    executor._do_select_task(command, store.state("RUN"), None, False)
    started = [ev for ev in store.events("RUN") if ev.type == "task.started"]
    assert [ev.payload["task_id"] for ev in started] == ["T-001", "T-002"]
    assert store.state("RUN").current_task_metadata["task_id"] == "T-002"


def test_select_task_after_recovered_reentry_ignores_abandoned_started(tmp_path):
    """B53 (#69): stage.recovered(M-IMPL) is a residency boundary for the
    in-flight-lease guard. A task.started from the abandoned cycle (never
    completed, rolled back through M-DESIGN, re-entered via B32 recovery)
    must not strand TASK_DISPATCH -- the pre-fix boundary scan only counted
    stage.entered, so the stale start looked in-flight and _do_select_task
    returned silently on every command (run 01M0S0FQ hot loop, ~120ms per
    select_task). FR-0150: stale starts remain selectable."""
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    task = _task()
    _graph(store, task)
    # abandoned cycle: T-001 started, never completed
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    store.append(
        "RUN",
        "v0.5",
        "stage.rolled_back",
        {"from_stage": "M-IMPL", "to_stage": "M-DESIGN", "reason": "stub_gap"},
    )
    store.append("RUN", "v0.5", "stage.entered", {"stage": "M-DESIGN"})
    # B32 forward recovery re-enters M-IMPL (the CURRENT residency boundary)
    store.append(
        "RUN",
        "v0.5",
        "stage.recovered",
        {"stage": "M-IMPL", "from_stage": "M-DESIGN", "reason": "mis-typed stub_gap"},
    )
    # fresh residency re-commits its taskgraph (stage.recovered reuses the
    # fresh-cycle reset, clearing task_refs)
    _graph(store, task)
    executor = _executor(repo, store)
    state = store.state("RUN")
    assert state.current_task_id is None  # recovery reset the stage cycle
    executor._do_select_task(
        Command("select_task", command_id="C-SELECT"), state, None, False
    )
    started = [ev for ev in store.events("RUN") if ev.type == "task.started"]
    # T-001 re-selected in the fresh residency (clean budget re-run)
    assert [ev.payload["task_id"] for ev in started] == ["T-001", "T-001"]
    assert store.state("RUN").current_task_id == "T-001"


def test_red_ref_free_attempt_skips_live_slot_of_same_residency(tmp_path):
    """B54 (#70): a same-residency re-checkpoint (Prism red_defect retry,
    human.retry round) must allocate the NEXT free R slot, not crash. The
    old walk raised on a slot live this residency -- run 01M0S0FQ T-001:
    slots 1-3 orphaned by the rollback, slot 4 checkpointed in this
    residency, the red_defect retry's checkpoint (attempt 2) walked into
    live slot 4 and killed the trac run process."""
    repo = _repo(tmp_path)
    store = _store(repo)
    executor = _executor(repo, store)
    # physical refs: slots 1-4 all taken (1-3 orphaned, 4 live this residency)
    sha = _git(repo, "rev-parse", "HEAD")
    for slot in (1, 2, 3, 4):
        _git(repo, "update-ref", f"refs/trac/rgr/RUN/T-001/{slot}/red", sha)
    # a live checkpoint event for slot 4 in THIS residency
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": "T-001", "attempt": 4, "r_sha": sha, "ref": "refs/trac/rgr/RUN/T-001/4/red"},
    )
    # retry's checkpoint requests attempt 2 -> walks orphans 2, 3 AND live 4 -> 5
    assert executor._red_ref_free_attempt("T-001", 2) == 5
    # fresh task with no refs at all keeps its requested slot
    assert executor._red_ref_free_attempt("T-002", 1) == 1


def test_refactor_diff_reconstructable_from_working_tree(tmp_path):
    """B55 (#71): the OpenCode backend never captures diff_ref for RGR
    refactor phases (GREEN outcomes carry diff_ref=None too); the gate must
    accept a working-tree reconstruction -- RED/GREEN's _validated_diff
    fallback -- instead of parking every real refactor with a contract_error
    (run 01M0S0FQ T-001: refactor changed tracks/adapters/base.py with
    diff_ref=None -> escalation park)."""
    repo = _repo(tmp_path)
    store = _store(repo)
    executor = _executor(repo, store)
    target = repo / "tracks" / "app.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "tracks/app.py")
    _git(repo, "commit", "-m", "app")
    # committed and unmodified: no diff to reconstruct
    assert not executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/app.py"]}, []
    )
    # refactor change sitting uncommitted in the main tree -> reconstructable
    target.write_text("x = 2\n", encoding="utf-8")
    assert executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/app.py"]}, []
    )
    # union with observed_changed covers outcomes whose changed_paths are
    # empty while the tree drifted
    assert executor._refactor_diff_reconstructable(
        {"changed_paths": []}, ["tracks/app.py"]
    )
    # paths with no on-disk change are not reconstructable (fail-closed kept)
    assert not executor._refactor_diff_reconstructable(
        {"changed_paths": ["tracks/absent.py"]}, []
    )


def test_every_m_impl_assignment_materializes_role_contracts(tmp_path):
    repo = _repo(tmp_path)
    _contract(repo)
    _docs(repo)
    store = _store(repo)
    task = _task()
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {
            "task_count": 1,
            "tasks": [task],
            "path": "tasks.json",
            "digest": "graph",
            "validate_status": "pass",
        },
    )
    executor = _executor(repo, store)
    backend = _RecordingBackend(
        {"status": "failed", "artifact_ref": None, "self_report": "bounded"}
    )
    executor.backend = backend
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "archer",
                "substate": "PLANNING",
                "assignment": {"skills": ["tracks-discuz"]},
            },
        )
    )
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "prism",
                "substate": "PRISM_PLAN",
                "assignment": {"skills": ["tracks-prism-impl"]},
            },
        )
    )
    executor._do_select_task(Command("select_task"), store.state("RUN"), None, False)
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "devon",
                "substate": "RED",
                "assignment": {"phase": "red", "skills": ["tracks-devon-rgr"]},
            },
        )
    )
    executor.issue(
        Command(
            "dispatch_agent",
            {
                "role": "shield",
                "substate": "WRITE",
                "assignment": {"skills": ["tracks-discuz"]},
            },
        )
    )
    assignments = {role: assignment for role, _sub, assignment in backend.assignments}
    assert assignments["devon"]["task_id"] == "T-001"
    assert assignments["devon"]["manifest"]["allowed_paths"]
    assert assignments["devon"]["pre_dirty_snapshot"] == {}
    # Single carriage (operator OOB 2026-09-06): the pre-dirty snapshot rides
    # assignment top-level; the manifest copy is stripped at materialization.
    assert "pre_dirty_snapshot" not in assignments["devon"]["manifest"]
    assert assignments["devon"]["result_identity"]
    assert assignments["devon"]["phase"] == "red"
    # Declared envelope (IF-ENVELOPE-001 injection face): prism dispatches
    # with a registered payload schema carry the authoritative declaration.
    assert assignments["prism"]["envelope"]["kind"] == "prism:plan"
    assert assignments["prism"]["envelope_version"] == 2
    assert assignments["prism"]["envelope"]["payload_schema"]["verdict"]["enum"] == (
        "pass",
        "revise",
    )
    assert assignments["prism"]["criteria_pack"] == {
        "name": "tracks-prism-impl",
        "version": "0.1",
    }
    assert assignments["shield"]["test_tasks"] == [
        {
            "ac_id": "AC-FR0001-01",
            "layers": ["integration"],
            "if_ids": ["IF-IMPL-001"],
        }
    ]
    dispatches = [ev for ev in store.events("RUN") if ev.type == "command.issued"]
    assert all("assignment" in ev.payload["command"]["params"] for ev in dispatches)


def test_empty_red_and_green_fail_closed_without_commits(tmp_path):
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append(
        "RUN", "v0.5", "outcome.received", _structured_outcome("red", ["tests/unit/test_app.py"])
    )
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False
    )
    assert _git(repo, "rev-parse", "HEAD") == base
    assert not any(ev.type == "red.checkpointed" for ev in store.events("RUN"))

    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN", "v0.5", "outcome.received", _structured_outcome("green", ["tracks/app.py"], "R")
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    assert _git(repo, "rev-parse", "HEAD") == base
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_empty_diff_with_no_change_reason_emits_green_no_change(tmp_path):
    """B38 (#39): a GREEN resubmit with no worktree diff and an explicit
    no_change_reason (implementation already in the baseline) must emit
    green.no_change instead of cycling through impl_defect -> DIAGNOSE ->
    GREEN. No commit is performed."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome("green", [], "R")
    outcome["no_change_reason"] = "implementation already on disk from baseline"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )

    assert _git(repo, "rev-parse", "HEAD") == base
    emitted = [ev for ev in store.events("RUN") if ev.type == "green.no_change"]
    assert len(emitted) == 1
    assert emitted[0].payload["task_id"] == "T-001"
    assert emitted[0].payload["reason"] == "implementation already on disk from baseline"
    assert not any(ev.type == "verdict.failed" for ev in store.events("RUN"))
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_empty_diff_without_no_change_reason_stays_fail_closed(tmp_path):
    """B38 (#39): without an explicit no_change_reason, an empty captured diff
    must keep the fail-closed verdict.failed(impl_defect) — the reason is a
    programming contract, not an accident to paper over."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _structured_outcome("green", [], "R"))
    executor = _executor(repo, store)
    base = _git(repo, "rev-parse", "HEAD")

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )

    assert _git(repo, "rev-parse", "HEAD") == base
    fails = [ev for ev in store.events("RUN") if ev.type == "verdict.failed"]
    assert len(fails) == 1
    assert fails[0].payload["check"] == "impl_defect"
    assert not any(ev.type == "green.no_change" for ev in store.events("RUN"))
    assert not any(ev.type == "green.committed" for ev in store.events("RUN"))


def test_commit_green_no_change_reconcile_idempotent(tmp_path):
    """B38 (#39): green.no_change already persisted for the command_id must
    NOT be re-emitted when reconcile=True (crash between event commit and
    return). Mirrors _do_recover_stage idempotency."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    manifest = {
        "task_id": "T-001",
        "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
        "forbidden_paths": [".tracks/projects/**"],
    }
    store.append(
        "RUN", "v0.5", "taskgraph.committed", {"task_count": 1, "tasks": [task], "digest": "graph"}
    )
    store.append(
        "RUN", "v0.5", "task.started", {"task_id": "T-001", "task": task, "manifest": manifest}
    )
    store.append("RUN", "v0.5", "red.checkpointed", {"r_sha": "R"})
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    outcome = _structured_outcome("green", [], "R")
    outcome["no_change_reason"] = "already in baseline"
    store.append("RUN", "v0.5", "outcome.received", outcome)
    executor = _executor(repo, store)
    command = Command("commit_green", command_id="C-G-REPLAY")
    stale_state = store.state("RUN")

    executor._do_commit_green(command, stale_state, None, False)
    executor._do_commit_green(command, stale_state, None, True)

    emitted = [ev for ev in store.events("RUN") if ev.type == "green.no_change"]
    assert len(emitted) == 1

