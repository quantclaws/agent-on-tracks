from tests.unit.test_m_impl_runtime_support import (
    _PLAN_WITH_TWO_FLOWS,
    Command,
    _b83_task,
    _docs,
    _executor,
    _repo,
    _runtime,
    _store,
    _task,
    json,
    pytest,
)


def test_expand_unit_refs_node_exact_and_file_expansion(tmp_path):
    """NODE ref selects exactly that node; FILE ref expands to every
    collected node in the file (explicit whole-file declaration)."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_a.py::test_two",
        "tests/unit/test_b.py::test_three",
    ]
    assert executor._expand_unit_refs(
        ["tests/unit/test_a.py::test_one"], inventory
    ) == ["tests/unit/test_a.py::test_one"]
    assert executor._expand_unit_refs(["tests/unit/test_a.py"], inventory) == [
        "tests/unit/test_a.py::test_one",
        "tests/unit/test_a.py::test_two",
    ]


def test_expand_unit_refs_absent_ref_fails_closed(tmp_path):
    """B50 fail-closed: a unit_ref absent from the unit collect raises
    TestSelectError (both unknown file and unknown node variants)."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = ["tests/unit/test_a.py::test_one"]
    with pytest.raises(TestSelectError, match="absent from unit collect"):
        executor._expand_unit_refs(["tests/unit/test_missing.py"], inventory)
    with pytest.raises(TestSelectError, match="absent from unit collect"):
        executor._expand_unit_refs(["tests/unit/test_a.py::test_other"], inventory)


def test_acceptance_anchors_resolve_and_schema1_skips_cross_check(tmp_path):
    """Legacy (schema-1) graphs resolve their mapped acceptance refs against
    the integration inventory without the §8 cross-check."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    # No test-plan text at all: schema-1 must still resolve (skip cross-check).
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_a"],
        inventory,
        "",
        schema=1,
    )
    assert anchors == ["tests/integration/test_app.py::test_flow_a"]
    # FILE ref expands to the whole file.
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py"], inventory, "", schema=1
    )
    assert anchors == inventory


def test_acceptance_anchors_schema2_absent_from_inventory_fails_closed(tmp_path):
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    with pytest.raises(TestSelectError, match="absent from integration collect"):
        executor._acceptance_anchors(
            ["tests/integration/test_app.py::test_flow_a"],
            ["tests/integration/test_other.py::test_x"],
            "",
            schema=2,
        )


def test_acceptance_anchors_schema2_declared_must_be_planned_row_target(tmp_path):
    """Schema-2 cross-check: a declared anchor absent from every §8
    integration row target fails closed (binding is declared AND planned)."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    # flow_a IS a §8 row target -> resolves.
    assert executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_a"],
        inventory,
        _PLAN_WITH_TWO_FLOWS,
        schema=2,
    ) == ["tests/integration/test_app.py::test_flow_a"]
    # A node that exists in the inventory but is NOT a §8 target -> fail.
    with pytest.raises(TestSelectError, match="not a test-plan §8 integration row target"):
        executor._acceptance_anchors(
            ["tests/integration/test_app.py::test_flow_b", "tests/integration/test_app.py::test_flow_a"],
            inventory,
            _PLAN_WITH_TWO_FLOWS.replace("test_flow_b", "test_flow_c"),
            schema=2,
        )


def test_acceptance_anchors_schema2_file_declaration_covers_planned_nodes(tmp_path):
    """PRISM-B49B50-R1-01 mirror: a FILE-level declaration against a §8 row
    that names individual NODE targets must pass the gate cross-check --
    same whole-file coverage the commit-time closure applies."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py"],
        inventory,
        _PLAN_WITH_TWO_FLOWS,
        schema=2,
    )
    assert anchors == inventory


def test_acceptance_anchors_schema2_node_declaration_with_file_target_row(tmp_path):
    """PRISM-B49B50-R1-01 mirror (reverse): a NODE declaration against a §8
    row whose target is the whole FILE is planned (file target subsumes its
    nodes)."""
    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_app.py::test_flow_b",
    ]
    plan_file_target = (
        "# Test plan\n\n## 8. AC Coverage\n\n"
        "| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_app.py | "
        "IF-IMPL-001 |\n"
    )
    anchors = executor._acceptance_anchors(
        ["tests/integration/test_app.py::test_flow_b"],
        inventory,
        plan_file_target,
        schema=2,
    )
    assert anchors == ["tests/integration/test_app.py::test_flow_b"]


def test_acceptance_anchors_schema2_undeclared_node_still_fails_closed(tmp_path):
    """The FILE/NODE coverage relaxation must not open a hole: a NODE anchor
    in a file §8 never mentions (neither the file nor any of its nodes) is
    still rejected."""
    from tracks.executor.m_impl_runtime import TestSelectError

    executor = _runtime(tmp_path)
    inventory = [
        "tests/integration/test_app.py::test_flow_a",
        "tests/integration/test_unrelated.py::test_x",
    ]
    with pytest.raises(TestSelectError, match="not a test-plan §8 integration row target"):
        executor._acceptance_anchors(
            ["tests/integration/test_unrelated.py::test_x"],
            inventory,
            _PLAN_WITH_TWO_FLOWS,
            schema=2,
        )


def test_integration_row_targets_parse_every_plus_separated_item(tmp_path):
    """Bug 3 (run 01M0S0FQ T-001, 2026-08-24): a multi-test §8 cell
    (``A + B + C``) must yield per-item targets, and a NODE item binds that
    node exactly -- never the whole first file."""
    executor = _runtime(tmp_path)
    targets = executor._integration_row_targets(
        "`tests/integration/test_app.py::test_flow_a` + `tests/integration/test_other.py`"
    )
    assert targets == [
        ("tests/integration/test_app.py", "tests/integration/test_app.py::test_flow_a"),
        ("tests/integration/test_other.py", None),
    ]


def test_integration_row_targets_bare_filename_normalizes_under_integration(tmp_path):
    executor = _runtime(tmp_path)
    targets = executor._integration_row_targets("test_app.py::test_flow_a + `test_app.py`")
    assert targets == [
        ("tests/integration/test_app.py", "tests/integration/test_app.py::test_flow_a"),
        ("tests/integration/test_app.py", None),
    ]


def test_task_node_maps_legacy_test_refs_by_layer_prefix():
    """_task_node: a legacy raw payload (only test_refs) is layer-routed by
    path prefix into unit_refs/acceptance_refs (196cbc9 convention)."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    node = MImplRuntimeMixin._task_node(
        {
            "task_id": "T-001",
            "issue_number": 1,
            "description": "slice",
            "ac_refs": ["AC-FR0001-01"],
            "fr_refs": ["FR-0001"],
            "if_ids": ["IF-IMPL-001"],
            "test_refs": [
                "tests/unit/test_app.py::test_app",
                "tests/integration/test_app.py",
            ],
            "scope_boundary": "tracks/app.py",
            "depends_on": [],
            "batch": "1",
            "parallel": False,
            "budget": 3,
        }
    )
    assert node.schema == 1
    assert node.unit_refs == ("tests/unit/test_app.py::test_app",)
    assert node.acceptance_refs == ("tests/integration/test_app.py",)


def test_task_node_schema2_payload_keeps_explicit_split():
    """A schema-2 raw payload carries its explicit split: the fields pass
    through untouched (no prefix re-routing over the declaration)."""
    from tracks.executor.m_impl_runtime import MImplRuntimeMixin

    node = MImplRuntimeMixin._task_node(
        {
            "task_id": "T-001",
            "issue_number": 1,
            "description": "slice",
            "ac_refs": ["AC-FR0001-01"],
            "fr_refs": ["FR-0001"],
            "if_ids": ["IF-IMPL-001"],
            "test_refs": [],
            "unit_refs": ["tests/unit/test_app.py::test_app"],
            "acceptance_refs": ["tests/integration/test_app.py"],
            "schema": 2,
            "scope_boundary": "tracks/app.py",
            "depends_on": [],
            "batch": "1",
            "parallel": False,
            "budget": 3,
        }
    )
    assert node.schema == 2
    assert node.unit_refs == ("tests/unit/test_app.py::test_app",)
    assert node.acceptance_refs == ("tests/integration/test_app.py",)


def test_scope_replan_retains_only_payload_equivalent_completions(tmp_path):
    """B83 (#83): after a scope-failure replan, retained completions are
    derived from EVENT history (last taskgraph.committed payload), not from
    live state -- the scope route clears taskgraph_committed/task_refs before
    the replacement commit, so a state-only guard would retain nothing and
    re-run every task. Only payload-equivalent completed tasks carry over;
    the merged-away T-006 and the never-completed T-007 must not."""
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TG")

    t1 = _task()
    t6 = _b83_task("T-006", "tracks/registry.py", "tests/unit/test_r.py::test_r")
    t7 = _b83_task("T-007", "tracks/parity.py", "tests/unit/test_p.py::test_p")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t6, t7]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    first = [ev for ev in store.events("RUN") if ev.type == "taskgraph.committed"][-1]
    assert first.payload["retained_completed_task_ids"] == []

    # T-001 and T-006 complete; T-007 starts, holds the lease, then its
    # TASK_REVIEW fails the scope gate (kernel routes to PLANNING).
    for tid in ("T-001", "T-006"):
        store.append("RUN", "v0.5", "writelock.granted", {"task_id": tid, "manifest": {}})
        store.append(
            "RUN",
            "v0.5",
            "task.started",
            {"task_id": tid, "task": {"task_id": tid}, "manifest": {"task_id": tid}},
        )
        store.append("RUN", "v0.5", "task.completed", {"task_id": tid})
        store.append("RUN", "v0.5", "writelock.released", {"task_id": tid})
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-007", "manifest": {}})
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-007", "task": {"task_id": "T-007"}, "manifest": {"task_id": "T-007"}},
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "scope", "reason": "outside manifest", "evidence": '["tracks/registry.py"]'},
    )
    state = store.state("RUN")
    assert state.substate == "PLANNING"  # scope routes to Archer, not Devon
    assert state.taskgraph_committed is False
    assert state.current_task_id is None
    assert state.writelock_held is True  # stale lease — released at selection

    # Archer replan: T-001 kept verbatim; T-006+T-007 merged into T-014.
    t14 = _b83_task("T-014", "tracks/merged.py", "tests/unit/test_m.py::test_m")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t14]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    second = [ev for ev in store.events("RUN") if ev.type == "taskgraph.committed"][-1]
    assert second.payload["retained_completed_task_ids"] == ["T-001"]
    state = store.state("RUN")
    assert state.tasks_completed == 1
    assert state.retained_completed_task_ids == ["T-001"]
    assert state.tasks_total == 2


def test_scope_replan_releases_stale_lease_and_selects_merged_task(tmp_path):
    """B83 (#83): after the replacement commit, the old generation's started
    tasks (merged-away T-006, scope-failed T-007) must not block selection,
    and the stale writelock must be RELEASED -- not resurrected with the old
    manifest. The merged task is selected under the NEW graph."""
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TG")

    t1 = _task()
    t6 = _b83_task("T-006", "tracks/registry.py", "tests/unit/test_r.py::test_r")
    t7 = _b83_task("T-007", "tracks/parity.py", "tests/unit/test_p.py::test_p")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t6, t7]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    for tid in ("T-001", "T-006"):
        store.append("RUN", "v0.5", "writelock.granted", {"task_id": tid, "manifest": {}})
        store.append(
            "RUN",
            "v0.5",
            "task.started",
            {"task_id": tid, "task": {"task_id": tid}, "manifest": {"task_id": tid}},
        )
        store.append("RUN", "v0.5", "task.completed", {"task_id": tid})
        store.append("RUN", "v0.5", "writelock.released", {"task_id": tid})
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-007", "manifest": {}})
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-007", "task": {"task_id": "T-007"}, "manifest": {"task_id": "T-007"}},
    )
    store.append(
        "RUN",
        "v0.5",
        "verdict.failed",
        {"check": "scope", "reason": "outside manifest", "evidence": '["tracks/registry.py"]'},
    )

    t14 = _b83_task("T-014", "tracks/merged.py", "tests/unit/test_m.py::test_m")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t14]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    commit_seq = max(
        ev.seq for ev in store.events("RUN") if ev.type == "taskgraph.committed"
    )

    select = Command("select_task", command_id="C-SELECT")
    # First pass: stale T-007 lease predates the replacement commit — release
    # it (taskgraph_replaced), do NOT resurrect task.started for T-007.
    executor._do_select_task(select, store.state("RUN"), None, False)
    started_after = [
        ev
        for ev in store.events("RUN")
        if ev.type == "task.started" and ev.seq > commit_seq
    ]
    assert started_after == []
    released = [
        ev
        for ev in store.events("RUN")
        if ev.type == "writelock.released" and ev.seq > commit_seq
    ]
    assert [ev.payload["task_id"] for ev in released] == ["T-007"]
    assert store.state("RUN").writelock_held is False

    # Second pass: old-generation starts no longer gate selection; T-001 is
    # retained-complete, so the merged T-014 is selected under the new graph.
    executor._do_select_task(select, store.state("RUN"), None, False)
    started_after = [
        ev
        for ev in store.events("RUN")
        if ev.type == "task.started" and ev.seq > commit_seq
    ]
    assert [ev.payload["task_id"] for ev in started_after] == ["T-014"]
    state = store.state("RUN")
    assert state.current_task_id == "T-014"
    assert state.current_manifest["task_id"] == "T-014"
    assert state.tasks_completed == 1  # T-001 retained; T-014 still running


def test_replacement_commit_without_scope_route_clears_inflight_lease(tmp_path):
    """#139: a replacement taskgraph committed WITHOUT the scope-failure
    route (operator/RULING commit_taskgraph, run 01M19FJV: graph 81d33fa5
    landed while T-042 was in flight) must clear the in-flight task lease --
    otherwise _do_select_task's current_task_id guard no-ops forever and the
    command-stall detector aborts the loop (loop.aborted seq 3653/3674/3695)."""
    repo = _repo(tmp_path)
    vdir = _docs(repo)
    store = _store(repo)
    executor = _executor(repo, store)
    command = Command("commit_taskgraph", command_id="C-TG")

    t1 = _task()
    t7 = _b83_task("T-007", "tracks/parity.py", "tests/unit/test_p.py::test_p")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t7]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    # T-001 completes; T-007 goes in-flight (started, writelock held) and is
    # NEVER completed -- and no scope verdict routes the replan.
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-001", "manifest": {}})
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-001", "task": {"task_id": "T-001"}, "manifest": {"task_id": "T-001"}},
    )
    store.append("RUN", "v0.5", "task.completed", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.released", {"task_id": "T-001"})
    store.append("RUN", "v0.5", "writelock.granted", {"task_id": "T-007", "manifest": {}})
    store.append(
        "RUN",
        "v0.5",
        "task.started",
        {"task_id": "T-007", "task": {"task_id": "T-007"}, "manifest": {"task_id": "T-007"}},
    )
    assert store.state("RUN").current_task_id == "T-007"

    # The replacement graph lands via a direct commit (operator/RULING path).
    t14 = _b83_task("T-014", "tracks/merged.py", "tests/unit/test_m.py::test_m")
    (vdir / "tasks.json").write_text(
        json.dumps({"tasks": [t1, t14]}, sort_keys=True), encoding="utf-8"
    )
    executor._do_commit_taskgraph(command, store.state("RUN"), None, False)
    commit_seq = max(
        ev.seq for ev in store.events("RUN") if ev.type == "taskgraph.committed"
    )

    state = store.state("RUN")
    assert state.current_task_id is None, "#139: stale lease must be cleared"
    assert state.current_task_metadata is None
    assert state.current_manifest is None

    select = Command("select_task", command_id="C-SELECT")
    # First pass: stale T-007 writelock predates the replacement commit --
    # release it (taskgraph_replaced); the in-flight task is NOT resurrected.
    executor._do_select_task(select, store.state("RUN"), None, False)
    released = [
        ev
        for ev in store.events("RUN")
        if ev.type == "writelock.released" and ev.seq > commit_seq
    ]
    assert [ev.payload["task_id"] for ev in released] == ["T-007"]
    assert store.state("RUN").writelock_held is False

    # Second pass: selection is no longer gated by the stale lease -- the
    # merged T-014 is selected under the new graph (the old tight loop
    # produced zero task.started events after the replacement commit).
    executor._do_select_task(select, store.state("RUN"), None, False)
    started_after = [
        ev
        for ev in store.events("RUN")
        if ev.type == "task.started" and ev.seq > commit_seq
    ]
    assert [ev.payload["task_id"] for ev in started_after] == ["T-014"]
    assert store.state("RUN").current_task_id == "T-014"

