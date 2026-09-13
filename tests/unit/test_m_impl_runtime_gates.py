from tests.unit.test_m_impl_runtime_support import (
    _RGR_GREEN_DIFF,
    RGR_GREEN_DIFF,
    RGR_RED_DIFF,
    Command,
    _checkpoint_red,
    _docs,
    _executor,
    _git,
    _impl_only_started_store,
    _repo,
    _run_red_gate,
    _started_task_store,
    _store,
    _structured_outcome,
    _task,
    decide,
    verify_lineage,
)


def test_red_gate_accepts_devon_test_dir_evidence_for_impl_only_manifest(tmp_path):
    """Fix G: RED evidence in a Devon test dir (tests/unit/) must not be
    rejected by the allowed_paths gate when the manifest grants only impl
    paths - frozen suites stay blocked by forbidden_paths."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    last = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert last.type == "verdict.passed"
    assert last.payload["check"] == "red_valid"


def test_red_gate_still_rejects_frozen_suite_evidence(tmp_path):
    """The devon-test-dir exemption must not open frozen suites: an evidence
    path under tests/integration/ is forbidden for Devon writes."""
    repo = _repo(tmp_path)
    task = _task()
    store = _impl_only_started_store(repo, task)
    executor = _executor(repo, store)
    last = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/integration/test_frozen.py"],
            classification="assertion_failure",
            verdict="assertion_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert last.type == "verdict.failed"
    assert last.payload["check"] == "red_invalid"
    assert "forbidden" in last.payload["reason"]


def test_rgr_public_attempt_one_and_identity_payloads(tmp_path):
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _checkpoint_red(executor, store)
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert len(red) == 1
    red_payload = red[0].payload
    assert red_payload["attempt"] == 1
    assert red_payload["task_id"] == task["task_id"]
    assert red_payload["ref"] == ("refs/trac/rgr/RUN/T-001/1/red")
    assert _git(repo, "rev-parse", red_payload["ref"]) == red_payload["r_sha"]

    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "green", ["tracks/app.py"], red_payload["r_sha"], diff_ref=_RGR_GREEN_DIFF
        ),
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"]
    assert len(green) == 1
    green_payload = green[0].payload
    # D-41: green.committed carries the GREEN_GATE selection evidence binding.
    # This flow never ran a passing GREEN_GATE, so both bind empty.
    assert green_payload == {
        "g_sha": green_payload["g_sha"],
        "task_id": task["task_id"],
        "attempt": 1,
        "r_sha": red_payload["r_sha"],
        "base_sha": green_payload["base_sha"],
        "trailers": {
            "Tracks-Task": task["task_id"],
            "Tracks-Attempt": "1",
            "Tracks-R": red_payload["r_sha"],
            "Tracks-Issue": "1",
            "Tracks-AC": "AC-FR0001-01,FR-0001",
        },
        "evidence_ids": [],
        "identity_basis": {},
    }
    message = _git(repo, "log", "--format=%B", "-1", green_payload["g_sha"])
    assert "Tracks-Attempt: 1" in message
    assert f"Tracks-R: {red_payload['r_sha']}" in message
    assert "Tracks-AC: AC-FR0001-01,FR-0001" in message
    assert "Tracks-FR:" not in message
    assert "Tracks-NFR:" not in message


def test_commit_green_reconcile_guard_noop_when_recorded_and_committed(tmp_path):
    """B56/#86 reconcile form: green.committed recorded + state.green_committed=True
    → guard no-ops (no duplicate git commit, no new event)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("red", ["tests/unit/test_app.py"], diff_ref=RGR_RED_DIFF),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=_RGR_GREEN_DIFF),
    )
    # First commit: emits green.committed, state in store now has green_committed=True
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False,
    )
    assert len([ev for ev in store.events("RUN") if ev.type == "green.committed"]) == 1

    # Reconcile replay: fresh state from store (green_committed=True), event recorded
    fresh_state = store.state("RUN")
    assert fresh_state.green_committed is True
    executor._do_commit_green(
        Command("commit_green", command_id="C-G-REPLAY"), fresh_state, None, False,
    )
    assert len([ev for ev in store.events("RUN") if ev.type == "green.committed"]) == 1


def test_commit_green_revise_lets_through_when_recorded_but_not_committed(tmp_path):
    """#86 revise form: green.committed recorded + state.green_committed=False
    (PRISM_FINAL revise or DIAGNOSE routed back to GREEN) → guard lets through,
    emits a NEW green.committed for the same lineage slot."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("red", ["tests/unit/test_app.py"], diff_ref=RGR_RED_DIFF),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=_RGR_GREEN_DIFF),
    )
    # First commit: emits green.committed
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False,
    )
    assert len([ev for ev in store.events("RUN") if ev.type == "green.committed"]) == 1

    # Simulate the revise re-dispatch: PRISM_FINAL revise(impl_defect) routes
    # back to GREEN (reducer clears green_committed) and Devon re-runs GREEN,
    # producing a fresh outcome with a modify diff (the impl revision).
    revise_diff = (
        "diff --git a/tracks/app.py b/tracks/app.py\n"
        "--- a/tracks/app.py\n"
        "+++ b/tracks/app.py\n"
        "@@ -1 +1,2 @@\n"
        " IMPLEMENTED = True\n"
        "+# revised\n"
    )
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome(
            "green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=revise_diff
        ),
    )
    revised_state = store.state("RUN")
    revised_state.green_committed = False
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), revised_state, None, False,
    )
    green_events = [ev for ev in store.events("RUN") if ev.type == "green.committed"]
    assert len(green_events) == 2, "revise must emit a second green.committed"
    assert green_events[0].payload["g_sha"] != green_events[1].payload["g_sha"], (
        "second commit must produce a different G (revised content)"
    )
    assert green_events[0].payload["attempt"] == green_events[1].payload["attempt"], (
        "same R slot must be reused"
    )


def test_commit_green_guard_first_commit_unaffected(tmp_path):
    """#86 regression: a first-time green commit (no prior green.committed event)
    must still pass through the guard normally."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("red", ["tests/unit/test_app.py"], diff_ref=RGR_RED_DIFF),
    )
    executor._do_checkpoint_red(
        Command("checkpoint_red", command_id="C-R"), store.state("RUN"), None, False,
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN", "v0.5", "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=_RGR_GREEN_DIFF),
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"), store.state("RUN"), None, False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"]
    assert len(green) == 1
    assert green[0].payload["task_id"] == task["task_id"]


def test_green_commit_binds_r_ref_slot_not_logical_attempt(tmp_path):
    """B56 (#72): B54's first-free-slot R allocation can place the immutable
    R ref at a slot above the logical attempt counter; G's Tracks-Attempt and
    green.committed.attempt must record that slot so TASK_REVIEW's
    verify_lineage resolves the ref the G trailer actually binds (run
    01M0S0FQ T-001: logical attempt 3, R checkpointed into slot 5, G trailer
    attempt 3 resolved the abandoned cycle's slot-3 R -> lineage park)."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    # Pre-occupy slots 1-2 with orphan refs (abandoned-cycle semantics): the
    # fresh residency's first checkpoint (logical attempt 1) must allocate
    # slot 3.
    base = _git(repo, "rev-parse", "HEAD")
    for slot in (1, 2):
        _git(repo, "update-ref", f"refs/trac/rgr/RUN/T-001/{slot}/red", base)
    _checkpoint_red(executor, store)
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    assert red.payload["attempt"] == 3  # first free slot, not logical 1

    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome(
            "green", ["tracks/app.py"], red.payload["r_sha"], diff_ref=RGR_GREEN_DIFF
        ),
    )
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    # G binds the R ref slot (3), not the logical attempt (1)
    assert green.payload["attempt"] == 3
    assert green.payload["trailers"]["Tracks-Attempt"] == "3"
    assert green.payload["trailers"]["Tracks-R"] == red.payload["r_sha"]
    message = _git(repo, "log", "--format=%B", "-1", green.payload["g_sha"])
    assert "Tracks-Attempt: 3" in message
    # TASK_REVIEW lineage resolves the ref the G trailer actually binds
    events = [
        {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
        for ev in store.events("RUN")
    ]
    proof = verify_lineage(
        str(repo),
        "RUN",
        task["task_id"],
        green.payload["attempt"],
        green.payload["g_sha"],
        events,
        issue_number=1,
        ac_refs=["AC-FR0001-01", "FR-0001"],
    )
    assert proof.g_trailers_valid
    assert proof.r_before_g


def test_r_lineage_attempt_falls_back_to_logical_attempt(tmp_path):
    """B56 (#72): without a matching red.checkpointed (r_sha unknown or no
    R identity at all) the lineage attempt falls back to the logical attempt
    -- pre-B54 behavior, fail-closed rather than guessing a slot."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 4, "r_sha": "R-live"},
    )
    assert executor._r_lineage_attempt(task["task_id"], "R-live", 1) == 4
    assert executor._r_lineage_attempt(task["task_id"], "R-unknown", 1) == 1
    assert executor._r_lineage_attempt(task["task_id"], None, 2) == 2
    assert executor._r_lineage_attempt(task["task_id"], "", 2) == 2


def test_green_commit_reconstructs_diff_from_cycle_outcome_union(tmp_path):
    """B58 (#74): GREEN's working-tree diff reconstruction must union the
    changed_paths of EVERY green outcome of the current RGR cycle, not just
    the last one. impl_defect re-dispatches report only their own delta (run
    01M0S0FQ T-001: dispatch 1 changed tracks/project.py, dispatch 3 changed
    tracks/adapters/base.py; the reconstructed G captured only base.py and
    the loader impl never reached any commit -- no gate caught it)."""
    repo = _repo(tmp_path)
    _docs(repo)
    store = _store(repo)
    task = _task()
    task["scope_boundary"] = "tracks/app.py, tracks/project.py"
    store.append(
        "RUN",
        "v0.5",
        "taskgraph.committed",
        {"task_count": 1, "tasks": [task], "digest": "graph"},
    )
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {
            "task_id": task["task_id"],
            "task": task,
            "manifest": {
                "task_id": task["task_id"],
                "allowed_paths": [
                    "tracks/app.py",
                    "tracks/project.py",
                    "tests/unit/test_app.py",
                ],
                "forbidden_paths": [".tracks/projects/**"],
            },
        },
    )
    executor = _executor(repo, store)
    _checkpoint_red(executor, store)
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})

    # Dispatch 1's impl (no diff_ref -- OpenCode backend contract, B55) is
    # still sitting uncommitted in the main tree when dispatch 2 is re-sent
    # after a GREEN_GATE impl_defect failure.
    project = repo / "tracks" / "project.py"
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text("ADAPTER = 'reference-pytest'\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/project.py"], red.payload["r_sha"]),
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "impl_defect", "attempt": 1, "reason": "TypeError in gate run"},
    )
    app = repo / "tracks" / "app.py"
    app.write_text("x = 1\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"]),
    )

    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    green = [ev for ev in store.events("RUN") if ev.type == "green.committed"][-1]
    # The union reconstruction captured BOTH dispatches' files in G.
    changed = _git(repo, "show", "--name-only", "--format=", green.payload["g_sha"])
    assert "tracks/project.py" in changed
    assert "tracks/app.py" in changed


def test_green_cycle_changed_paths_stops_at_last_checkpoint(tmp_path):
    """B58 (#74): the union is bounded by the last red.checkpointed -- green
    outcomes from a superseded (red_defect-retried) cycle must not leak into
    the new cycle's diff reconstruction."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/stale.py"]),
    )
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 1, "r_sha": "R-live"},
    )
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], "R-live"),
    )
    assert executor._green_cycle_changed_paths() == ["tracks/app.py"]
    # No green outcome in the current cycle -> None (caller falls back to the
    # last outcome's own changed_paths).
    store.append(
        "RUN",
        "v0.5",
        "red.checkpointed",
        {"task_id": task["task_id"], "attempt": 2, "r_sha": "R-live-2"},
    )
    assert executor._green_cycle_changed_paths() is None


def test_green_union_beats_no_change_when_earlier_dispatch_left_changes(tmp_path):
    """B58 (#74) / Prism OOB A01: a last outcome declaring no_change_reason
    must NOT short-circuit into green.no_change when an earlier green dispatch
    of the same cycle still has uncommitted worktree changes -- the union
    reconstruction surfaces a non-empty diff, so the real change gets
    committed instead of being silently dropped."""
    repo = _repo(tmp_path)
    store, task = _started_task_store(repo)
    executor = _executor(repo, store)
    _checkpoint_red(executor, store)
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][-1]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    # Dispatch 1 changed tracks/app.py (still uncommitted in the tree).
    app = repo / "tracks" / "app.py"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text("x = 1\n", encoding="utf-8")
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red.payload["r_sha"]),
    )
    # Dispatch 2 declares no change of its own.
    no_change = _structured_outcome("green", [], red.payload["r_sha"])
    no_change["no_change_reason"] = "already implemented"
    store.append("RUN", "v0.5", "outcome.received", no_change)
    executor._do_commit_green(
        Command("commit_green", command_id="C-G"),
        store.state("RUN"),
        None,
        False,
    )
    events = list(store.events("RUN"))
    assert not any(ev.type == "green.no_change" for ev in events)
    green = [ev for ev in events if ev.type == "green.committed"][-1]
    changed = _git(repo, "show", "--name-only", "--format=", green.payload["g_sha"])
    assert "tracks/app.py" in changed


def test_red_checkpoint_retry_uses_next_public_attempt(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "red_invalid", "attempt": 1},
    )
    _checkpoint_red(
        executor,
        store,
        classification="symbol_missing",
        verdict="symbol_missing",
        command_id="C-R2",
    )
    red = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]
    assert red[0].payload["attempt"] == 2
    assert red[0].payload["ref"].endswith("/2/red")


def test_stub_token_red_is_invalid_but_legal_red_classes_pass(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    executor = _executor(repo, store)
    failed = _run_red_gate(
        executor,
        store,
        _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            classification="stub_token_failure",
            verdict="stub_token_failure",
            diff_ref=RGR_RED_DIFF,
        ),
    )
    assert failed.type == "verdict.failed"
    assert failed.payload["check"] == "red_invalid"
    assert "stub_token_failure" in failed.payload["reason"]
    assert failed.payload["evidence"]
    assert store.state("RUN").current_attempt == 1
    assert store.state("RUN").substate == "RED"
    assert decide(store.state("RUN")).params["attempt"] == 2

    for classification in ("assertion_failure", "symbol_missing"):
        store.append(
            "RUN",
            "v0.5",
            "outcome.received",
            _structured_outcome(
                "red",
                ["tests/unit/test_app.py"],
                classification=classification,
                verdict=classification,
                diff_ref=RGR_RED_DIFF,
            ),
        )
        executor._do_run_task_gates(
            Command("run_task_gates", {"gate": "RED_GATE"}, command_id=f"C-{classification}"),
            store.state("RUN"),
            None,
            False,
        )
        assert list(store.events("RUN"))[-1].type == "verdict.passed"


def test_malformed_red_classifications_fail_closed(tmp_path):
    cases = (
        {},
        {"results": [{"classification": None}], "verdict": "assertion_failure"},
        {"results": [{"classification": "unknown"}], "verdict": "unknown"},
        {
            "results": [
                {"classification": "assertion_failure"},
                {"classification": "symbol_missing"},
            ],
            "verdict": "assertion_failure",
        },
    )
    for index, details in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        repo = _repo(case_dir)
        store, _ = _started_task_store(repo)
        executor = _executor(repo, store)
        outcome = _structured_outcome(
            "red",
            ["tests/unit/test_app.py"],
            diff_ref=RGR_RED_DIFF,
        )
        outcome.update(details)
        failed = _run_red_gate(executor, store, outcome)
        assert failed.type == "verdict.failed"
        assert failed.payload["check"] == "red_invalid"
        assert failed.payload["reason"]
        assert failed.payload["evidence"]


def test_rgr_reconcile_replay_emits_no_duplicate_events(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
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
    command = Command("checkpoint_red", command_id="C-R-REPLAY")
    stale_state = store.state("RUN")
    executor._do_checkpoint_red(command, stale_state, None, False)
    executor._do_checkpoint_red(command, stale_state, None, True)
    assert len([ev for ev in store.events("RUN") if ev.type == "red.checkpointed"]) == 1

    red_sha = [ev for ev in store.events("RUN") if ev.type == "red.checkpointed"][0].payload[
        "r_sha"
    ]
    store.append("RUN", "v0.5", "prism.verdict", {"verdict": "pass"})
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        _structured_outcome("green", ["tracks/app.py"], red_sha, diff_ref=_RGR_GREEN_DIFF),
    )
    green_command = Command("commit_green", command_id="C-G-REPLAY")
    stale_green_state = store.state("RUN")
    executor._do_commit_green(green_command, stale_green_state, None, False)
    # Reconcile replay: fresh state from store (reflects green.committed → green_committed=True)
    fresh_state = store.state("RUN")
    executor._do_commit_green(green_command, fresh_state, None, True)
    assert len([ev for ev in store.events("RUN") if ev.type == "green.committed"]) == 1


def test_test_defect_uses_public_shield_write_and_commits_tests(tmp_path):
    repo = _repo(tmp_path)
    store, _ = _started_task_store(repo)
    store.append(
        "RUN",
        "v0.5",
        "outcome.received",
        {
            "role": "devon",
            "status": "failed",
            "failure_class": "agent_failed",
            "self_report": "failed",
            "audit_evidence": "execution",
        },
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "test_defect", "attempt": 2},
    )
    executor = _executor(repo, store)

    class _ShieldWriteBackend:
        """Stub backend that writes a tests/ file (like the real Shield)."""

        def __init__(self):
            self.assignments = []
            from tracks.effects.fake import FakeBackend

            self._envelope = FakeBackend(repo, "v0.0")

        def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
            self.assignments.append((role, substate, assignment))
            tests_dir = repo / "tests" / "integration"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_shield_fix.py").write_text(
                "def test_shield_fix():\n    assert True\n",
                encoding="utf-8",
            )
            return {
                "status": "done",
                "artifact_ref": None,
                "self_report": "fixed",
                "artifact_manifest": {
                    "include": [
                        {
                            "path": "tests/integration/test_shield_fix.py",
                            "kind": "integration_test",
                            "role": "test",
                        }
                    ]
                },
                "suggested_commit_message": "M-TEST: shield fix",
            }

        def finalize_act(self, result, role, substate, assignment=None):
            # Declared shield:write dispatch: encode the simulated reply the
            # assignment demands (same seam as the fake/test doubles).
            return self._envelope.finalize_act(result, role, substate, assignment)

    backend = _ShieldWriteBackend()
    executor.backend = backend
    command = decide(store.state("RUN"))
    assert command.params["role"] == "shield"
    assert command.params["substate"] == "WRITE"
    executor.issue(command)
    committed = [ev for ev in store.events("RUN") if ev.type == "test.committed"]
    assert committed, "test.committed must be emitted after Shield fix"
    assert committed[0].payload["test_count"] > 0
