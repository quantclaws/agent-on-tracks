from tests.unit.test_m_impl_runtime_support import (
    _RGR_GREEN_DIFF,
    RGR_GREEN_DIFF,
    RGR_RED_DIFF,
    Command,
    _b83_task,
    _docs,
    _executor,
    _git,
    _graph,
    _green_outcome,
    _repo,
    _started_task_store,
    _store,
    _structured_outcome,
    _task,
    create_green_commit,
    json,
    red_base_sha,
    verify_lineage,
)


def test_redefined_same_id_task_is_not_retained(tmp_path):
    """B83 (#83): a completed task whose payload is REDEFINED in the new
    graph (same id, changed scope) must re-run — its old completion must not
    satisfy the new task nor block a fresh completion."""
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TG")

    t1 = _task()
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-001", "manifest": {}})
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-001", "task": t1, "manifest": {"task_id": "T-001"}},
    )
    store.append("RUN", "v0.5", "task.completed", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-001"})
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "scope", "reason": "outside manifest", "evidence": '["tracks/x.py"]'},
    )

    t1_redefined = {**t1, "scope_boundary": "tracks/app2.py"}
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1_redefined]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    second = [ev for ev in store.events("RUN") if ev.type == "taskgraph.committed"][-1]
    assert second.payload["retained_completed_task_ids"] == []
    state = store.state("RUN")
    assert state.tasks_completed == 0

    executor._do_select_task(
        Command("select_task", command_id="C-SELECT"), state, None, False
    )
    state = store.state("RUN")
    assert state.current_task_id == "T-001"  # re-selected for a clean re-run


def test_stale_lease_release_not_suppressed_by_old_generation_release(tmp_path):
    """B88 (#88): the writelock recover idempotency check must be scoped to
    releases AFTER the current lease. An all-time scan finds an earlier
    release for the same task id and skips emitting — writelock_held stays
    True forever and select_task livelocks (run 01M0S0FQ T-010: first
    lease released, second lease from the RGR retry never released, then
    the scope-failure replan committed a new graph under it)."""
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TG")

    t1 = _task()
    t10 = _b83_task("T-010", "tracks/mut.py", "tests/unit/test_mut.py::test_mut")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t10]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    # cycle 1: T-010 leased and RELEASED (RGR retry round finished cleanly)
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-010", "manifest": {}})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-010"})
    # cycle 2: T-010 leased again (retry), NEVER released, loop crashed
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-010", "manifest": {}})
    # scope failure of another task -> replan commits a new graph
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "scope", "reason": "outside manifest", "evidence": '["tracks/x.py"]'},
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    state = store.state("RUN")
    assert state.writelock_held is True

    select = Command("select_task", command_id="C-SELECT")
    # the cycle-1 release must NOT suppress the release of the cycle-2 lease
    executor._do_select_task(select, store.state("RUN"), None, False)
    lease2_seq = max(
        ev.seq
        for ev in store.events("RUN")
        if ev.type == "writelock.granted" and ev.payload.get("task_id") == "T-010"
    )
    releases = [
        ev
        for ev in store.events("RUN")
        if ev.type == "writelock.released" and ev.seq > lease2_seq
    ]
    assert [ev.payload["task_id"] for ev in releases] == ["T-010"]
    assert store.state("RUN").writelock_held is False
    # next pass selects normally again
    executor._do_select_task(select, store.state("RUN"), None, False)
    state = store.state("RUN")
    assert state.current_task_id is not None  # selection resumed


def test_task_review_no_change_passes_when_no_green_committed(tmp_path):
    """B84 (#84): a green.no_change for the current task makes TASK_REVIEW
    emit verdict.passed(task_review) — no new G exists, scope/lineage/secret/
    provenance checks are vacuous (GREEN_GATE already programmatically verified
    the behavior). Budget check still runs."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("red", ["tests/unit/test_app.py"],
                            classification="assertion_failure",
                            verdict="assertion_failure", diff_ref=RGR_RED_DIFF),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"), None, False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    # green.no_change for the current task — no green.committed
    store.append("RUN", "v0.5", "green.no_change",
                 {"task_id": task["task_id"], "reason": "implementation already on disk"})
    store.append("RUN", "v0.5", "refactor.no_change",
                 {"task_id": task["task_id"], "reason": "no improvements"})
    state = store.state("RUN")
    assert state.substate == "TASK_REVIEW"
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state, None, False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, "TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.passed", (
        f"green.no_change should pass, got {new_events[-1]}"
    )
    assert new_events[-1].payload["check"] == "task_review"


def test_task_review_fails_on_old_task_green_committed(tmp_path):
    """B84 (#84): a green.committed for a DIFFERENT task (old generation) with
    no green event for the current task must fail-closed with check=lineage
    and reason "green.committed lacks task identity" — never re-use the old
    task's G for trailer verification."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    _graph(store, task)
    store.append(
        "RUN", "v0.5", "task.started",
        {"task_id": task["task_id"], "task": task,
         "manifest": {"task_id": task["task_id"],
                      "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
                      "forbidden_paths": [".tracks/projects/**"]}},
    )
    executor = _executor(repo, store)
    # RED checkpoint → GREEN
    store.append("RUN", "v0.5", "outcome.received",
                 _structured_outcome("red", ["tests/unit/test_app.py"],
                                     classification="assertion_failure",
                                     verdict="assertion_failure", diff_ref=RGR_RED_DIFF))
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"), None, False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    # Build a green.committed for the current task (T-001) — this is the
    # legitimate path. But we then simulate a B83-style replan: the current
    # task changes to T-014 (merged from T-006+T-007), with no green event
    # of its own.
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    g = create_green_commit(
        repo=str(repo), run_id="RUN", task_id=task["task_id"], attempt=1,
        impl_diff=RGR_GREEN_DIFF, base_sha=base_sha, r_sha=r_sha,
        issue_number=task["issue_number"], ac_refs=["AC-FR0001-01", "FR-0001"],
    )
    store.append("RUN", "v0.5", "green.committed",
                 {"g_sha": g.sha, "task_id": task["task_id"], "attempt": 1,
                  "r_sha": r_sha, "base_sha": base_sha, "trailers": g.trailers})
    _git(repo, "reset", "--hard", g.sha)
    # Now simulate a B83 replan: T-001 is done, T-014 is the new task with
    # no green event. The state's current_task_id is T-014.
    t14 = {**task, "task_id": "T-014", "scope_boundary": "tracks/merged.py"}
    store.append("RUN", "v0.5", "taskgraph.committed",
                 {"task_count": 1, "task_ids": ["T-014"], "tasks": [t14],
                  "digest": "graph2", "validate_status": "pass"})
    store.append("RUN", "v0.5", "task.completed", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-014", "manifest": {}})
    store.append("RUN", "v0.5", "task.started",
                 {"task_id": "T-014", "task": t14,
                  "manifest": {"task_id": "T-014",
                               "allowed_paths": ["tracks/merged.py"],
                               "forbidden_paths": [".tracks/projects/**"]}})
    store.append("RUN", "v0.5", "refactor.no_change",
                 {"task_id": "T-014", "reason": "no improvements"})
    state = store.state("RUN")
    assert state.current_task_id == "T-014", state.current_task_id
    assert state.substate == "TASK_REVIEW", state.substate
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state, None, False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, "TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.failed", (
        f"wrong task green.committed must fail closed, got {new_events[-1].type}"
    )
    assert new_events[-1].payload["check"] == "lineage", (
        f"expected check=lineage, got {new_events[-1].payload}"
    )
    assert "lacks task identity" in new_events[-1].payload.get("reason", ""), (
        f"reason must mention 'lacks task identity', got {new_events[-1].payload.get('reason')}"
    )


def test_task_review_no_change_fails_on_missing_task(tmp_path):
    """B84 (#84): no-change path where task lookup fails must fail-closed
    with check=lineage — never silently pass when task identity is missing."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("red", ["tests/unit/test_app.py"],
                            classification="assertion_failure",
                            verdict="assertion_failure", diff_ref=RGR_RED_DIFF),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"), None, False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    # Re-start with a task_id that has no backing task metadata — _lookup_task
    # will return None because neither task_refs nor current_task_metadata
    # carries this task_id.
    store.append("RUN", "v0.5", "task.started",
                 {"task_id": "T-GHOST", "manifest": {"task_id": "T-GHOST",
                  "allowed_paths": [], "forbidden_paths": []}})
    store.append("RUN", "v0.5", "green.no_change",
                 {"task_id": "T-GHOST", "reason": "implementation already on disk"})
    store.append("RUN", "v0.5", "refactor.no_change",
                 {"task_id": "T-GHOST", "reason": "no improvements"})
    state = store.state("RUN")
    assert state.current_task_id == "T-GHOST"
    assert state.substate == "TASK_REVIEW"
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state, None, False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, "TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.failed", (
        f"missing task should fail closed, got {new_events[-1]}"
    )
    assert new_events[-1].payload["check"] == "lineage", (
        f"expected check=lineage, got {new_events[-1].payload}"
    )
    assert "task lookup failed" in new_events[-1].payload.get("reason", "").lower(), (
        f"reason must mention task lookup failure, got {new_events[-1].payload.get('reason')}"
    )


def test_task_review_fails_on_no_green_committed_event(tmp_path):
    """B84 (#84): when no green.committed event exists and no green.no_change
    matches the current task, TASK_REVIEW must fail-closed with check=lineage
    and reason 'no green.committed event found'."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    _graph(store, task)
    store.append(
        "RUN", "v0.5", "task.started",
        {"task_id": task["task_id"], "task": task,
         "manifest": {"task_id": task["task_id"],
                      "allowed_paths": ["tracks/app.py", "tests/unit/test_app.py"],
                      "forbidden_paths": [".tracks/projects/**"]}},
    )
    executor = _executor(repo, store)
    store.append("RUN", "v0.5", "outcome.received",
                 _structured_outcome("red", ["tests/unit/test_app.py"],
                                     classification="assertion_failure",
                                     verdict="assertion_failure", diff_ref=RGR_RED_DIFF))
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"),
        store.state("RUN"), None, False,
    )
    r_sha = [e for e in store.events("RUN") if e.type == "red.checkpointed"][-1].payload["r_sha"]
    base_sha = red_base_sha(str(repo), r_sha)
    assert base_sha is not None
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append("RUN", "v0.5", "outcome.received", _green_outcome(task, r_sha))
    store.append("RUN", "v0.5", "verdict.passed", {"check": "green"})
    # green.no_change for a DIFFERENT task — state transitions to TASK_REVIEW,
    # but the current task has no matching green event at all.
    store.append("RUN", "v0.5", "green.no_change",
                 {"task_id": "T-OTHER", "reason": "other task changed"})
    store.append("RUN", "v0.5", "refactor.no_change",
                 {"task_id": task["task_id"], "reason": "no improvements"})
    state = store.state("RUN")
    assert state.current_task_id == task["task_id"]
    assert state.substate == "TASK_REVIEW"
    before = len(list(store.events("RUN")))
    executor._do_run_task_gates(
        Command("run_task_gates", {"gate": "TASK_REVIEW"}, command_id="C-REV"),
        state, None, False,
    )
    new_events = list(store.events("RUN"))[before:]
    assert new_events, "TASK_REVIEW must emit a verdict"
    assert new_events[-1].type == "verdict.failed", (
        f"no green event should fail closed, got {new_events[-1]}"
    )
    assert new_events[-1].payload["check"] == "lineage", (
        f"expected check=lineage, got {new_events[-1].payload}"
    )
    assert "no green.committed event found" in new_events[-1].payload.get("reason", ""), (
        f"reason must mention 'no green.committed event found', got {new_events[-1].payload.get('reason')}"
    )


def test_red_checkpoint_revise_round_reissue_not_deduped(tmp_path):
    """B90 (#91): a red.checkpointed recorded in an EARLIER review round must
    not feed the idempotency guard once a newer dispatch outcome exists for
    the task. Run 01M0S0FQ T-013 (2026-08-27) livelocked exactly here: an
    orphan pre-replan ref held slot 2, so _red_ref_free_attempt (B54/#70)
    inflated the round-1 checkpoint event to attempt=3; the PRISM_RED revise
    consumed an attempt (current_attempt 1->2), the round-2 RED_CHECKPOINT
    computed attempt=3 again, the guard matched the stale event and silently
    returned -- decide() re-issued checkpoint_red 20x until the B86/B88
    stall breaker tripped."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    # Orphan ref from a pre-replan generation holds slot 2 (B54 skips it).
    _git(repo, "update-ref", "refs/trac/rgr/RUN/T-001/2/red", "HEAD")
    # attempt 1 fails red_invalid -> current_attempt=1.
    store.append(
        "RUN", "v0.5", "verdict.failed",
        {"check": "red_invalid", "reason": "boom", "task_id": "T-001", "attempt": 1},
        task_id="T-001",
    )
    # Round 1 passes: executor attempt=current_attempt+1=2, slot 2 taken ->
    # event records attempt=3 (the inflation that later collides).
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R1"), store.state("RUN"), None, False
    )
    first = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(first) == 1
    assert first[0].payload["attempt"] == 3

    # PRISM_RED revise consumes an attempt -> current_attempt=2, back to RED.
    store.append(
        "RUN", "v0.5", "prism.verdict",
        {"verdict": "revise", "defect_classification": "red_defect"},
        task_id="T-001",
    )
    # Round 2: fresh outcome for the task -> RED_CHECKPOINT waits on a NEW
    # checkpoint; the round-1 event is stale for dedup purposes.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R2"), store.state("RUN"), None, False
    )
    reds = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(reds) == 2, (
        "revise round must re-issue a fresh checkpoint instead of silently "
        "no-oping on the earlier round's inflated-attempt event"
    )
    assert reds[-1].payload["attempt"] == 4  # fresh slot, R immutability kept
    assert _git(repo, "rev-parse", reds[-1].payload["ref"]) == reds[-1].payload["r_sha"]


def test_red_checkpoint_double_fire_still_deduped_after_current_outcome(tmp_path):
    """B90 (#91) counterweight: the crash-replay double-fire (no newer
    outcome between the two executions) must stay a silent no-op -- the new
    task-scoped floor only demotes checkpoints that predate the current
    round's dispatch outcome."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R1"), store.state("RUN"), None, False
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R1b"), store.state("RUN"), None, False
    )
    reds = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(reds) == 1


def test_commit_green_binds_r_ref_not_shield_fix_after_test_defect_round(tmp_path):
    """B91 (#92): after a test_defect round the runtime commits the Shield
    fix (test.committed) and SM-01.14 re-points state.r_tree_identity at the
    fix COMMIT. The G lineage must still bind the immutable R ref family:
    Tracks-R = the latest red.checkpointed r_sha and Tracks-Attempt = its
    slot, or TASK_REVIEW verify_lineage can never validate the G (run
    01M0S0FQ T-013, 2026-08-27: Tracks-R=shield sha 5b72700 vs slot-4 ref
    d826d34 -> awaiting rollback)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    # Round-1 RED checkpoint on slot 1 (natural).
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    r_sha, slot = red.payload["r_sha"], red.payload["attempt"]
    # Shield fix commit: SM-01.14 re-points the identity at the fix commit.
    shield = _git(repo, "rev-parse", "HEAD")
    store.append(
        "RUN", "v0.5", "test.committed", {"commit_sha": shield, "test_count": 1}
    )
    state = store.state("RUN")
    assert state.r_tree_identity == shield  # SM-01.14 semantics intact
    # GREEN on top of the fix commit.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], r_sha, diff_ref=_RGR_GREEN_DIFF),
        task_id="T-001",
    )
    state = store.state("RUN")
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), state, None, False
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    assert green.payload["trailers"]["Tracks-R"] == r_sha
    message = _git(repo, "log", "--format=%B", "-1", green.payload["g_sha"])
    assert f"Tracks-R: {r_sha}" in message, (
        "G must bind the immutable R ref sha, not the Shield fix commit"
    )
    assert f"Tracks-Attempt: {slot}" in message
    assert shield not in message or f"Tracks-R: {shield}" not in message
    assert green.payload["attempt"] == slot


def test_shield_fix_rebaselines_family_and_g_binds_the_fix_slot(tmp_path):
    """B91 follow-up (re-baseline): the sanctioned Shield fix commit joins the
    immutable R family as a FRESH slot (red.checkpointed with
    sanction=shield_fix); the subsequent G binds THAT slot exactly -- Tracks-R
    = the fix commit sha, Tracks-Attempt = the new slot -- and verify_lineage
    proves it end-to-end. The regression baseline and the lineage anchor stop
    diverging: the trailer names the frozen tree that actually gated the G
    (run 01M0S0FQ T-013 post-mortem, 2026-08-27)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    assert red.payload["attempt"] == 1
    # Sanctioned Shield fix commit (test.committed re-points SM-01.14): a
    # real child commit on the working branch, like the incident's 5b72700.
    (repo / "tests" / "unit").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "unit" / "test_fix.py").write_text(
        "def test_fix():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "Shield fix: T-001 attempt 1")
    shield = _git(repo, "rev-parse", "HEAD")
    store.append(
        "RUN", "v0.5", "test.committed", {"commit_sha": shield, "test_count": 1}
    )
    executor._rebaseline_red_family("T-001", shield, "C-FIX")
    reds = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(reds) == 2
    rebased = reds[-1]
    assert rebased.payload["sanction"] == "shield_fix"
    assert rebased.payload["r_sha"] == shield
    new_slot = rebased.payload["attempt"]
    assert new_slot == 2
    # The immutable ref exists and points at the fix commit (BS-06 CAS).
    assert _git(repo, "rev-parse", rebased.payload["ref"]) == shield
    # A retried shield round over the SAME sha must not fork duplicate slots.
    executor._rebaseline_red_family("T-001", shield, "C-FIX2")
    reds = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(reds) == 2
    # GREEN on top of the fix commit: exact-match resolution now binds slot 2.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], shield, diff_ref=_RGR_GREEN_DIFF),
        task_id="T-001",
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    assert green.payload["trailers"]["Tracks-R"] == shield
    assert green.payload["attempt"] == new_slot
    message = _git(repo, "log", "--format=%B", "-1", green.payload["g_sha"])
    assert f"Tracks-R: {shield}" in message
    assert f"Tracks-Attempt: {new_slot}" in message
    # End-to-end: TASK_REVIEW's own proof accepts the rebaselined lineage.
    events = [
        {"type": ev.type, "payload": ev.payload, "seq": i}
        for i, ev in enumerate(store.events("RUN"))
    ]
    proof = verify_lineage(
        str(repo), "RUN", "T-001", new_slot, green.payload["g_sha"], events
    )
    assert proof.r_ref_exists
    assert proof.g_trailers_valid
    assert proof.event_order_valid
    assert proof.r_before_g


def test_shield_fix_rebaseline_noop_without_red_family(tmp_path):
    """B91 follow-up: a shield fix for a task with NO checkpointed RED must
    not conjure a family out of a fix commit -- B91's no-checkpoint lineage
    guard still fails closed on such a G."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    shield = _git(repo, "rev-parse", "HEAD")
    executor._rebaseline_red_family("T-001", shield, "C-FIX")
    reds = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert reds == []


def test_shield_fix_commit_rebaselines_family_through_executor_wiring(tmp_path):
    """B91 follow-up wiring (review P1, 2026-08-27): the mid-M-IMPL SHIELD_FIX
    commit path itself (_emit_shield_commit: result/role/state/cmd — the real
    executor entry, not the mixin called directly) must re-baseline the R
    family. Every other re-baseline test drives _rebaseline_red_family
    directly, so deleting the hook would pass the suite and silently
    reintroduce B91 (run 01M0S0FQ T-013)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    # RED checkpoint slot 1: the family the re-baseline joins.
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "red", ["tests/unit/test_app.py"],
            classification="assertion_failure", verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
        task_id="T-001",
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False
    )
    # Park the cycle at SHIELD_FIX: the first test_defect verdict routes to
    # DIAGNOSE (four-way attribution); the attributed re-verdict opens the
    # sanctioned Shield fix round.
    for _ in range(2):
        store.append(
            "RUN", "v0.5", "verdict.failed",
            {"check": "test_defect", "reason": "seed missing", "task_id": "T-001", "attempt": 1},
            task_id="T-001",
        )
    state = store.state("RUN")
    assert state.stage == "M-IMPL" and state.substate == "SHIELD_FIX"
    # The fix: a dirty tests/ file, manifest matching the observation.
    fix = repo / "tests" / "unit" / "test_fix.py"
    fix.parent.mkdir(parents=True, exist_ok=True)
    fix.write_text("def test_fix():\n    assert True\n", encoding="utf-8")
    result = {
        "status": "done",
        "artifact_manifest": {"include": [{"path": "tests/unit/test_fix.py"}]},
    }
    ok = executor._emit_shield_commit(
        result, "shield", state, Command("dispatch_agent", command_id="C-FIX"), "T-001"
    )
    assert ok, "shield fix commit must succeed"
    # The wiring: test.committed AND the sanctioned re-baseline checkpoint.
    assert [e for e in store.events("RUN") if e.type == "test.committed"]
    reds = [e for e in store.events("RUN") if e.type == "red.checkpointed"]
    assert len(reds) == 2, "the fix commit must join the R family as a new slot"
    assert reds[-1].payload["sanction"] == "shield_fix"
    assert reds[-1].payload["attempt"] == 2
    assert _git(repo, "rev-parse", reds[-1].payload["ref"]) == reds[-1].payload["r_sha"]

