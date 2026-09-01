"""OB-4 integration tests: tasks.md projection guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.result_checkpoint_support import _init_workspace
from tracks import paths
from tracks.executor import Executor
from tracks.executor.taskgraph import parse_tasks_json, render_tasks_md
from tracks.kernel.events import Command
from tracks.store import new_ulid


def _setup_m_impl(tmp_path: Path, version="v0.8"):
    repo, home, store, run_id, vdir = _init_workspace(tmp_path, version)
    # Minimal docs for taskgraph to be happy
    (vdir / "acceptance.md").write_text(
        "# Acceptance\n\n## FR-0001\n\n### AC-FR0001-01\n\n - c\n",
        encoding="utf-8",
    )
    (vdir / "interfaces.md").write_text(
        "# Interfaces\n\n## IF Registry\n\n| ID | Name |\n|---|---| \n| IF-IMPL-001 | x |\n",
        encoding="utf-8",
    )
    (vdir / "architecture.md").write_text(
        "# Architecture\n\n"
        "- tracks/foo.py owner=T-001 surface=foo composition=x wiring=y test=test_x evidence=e\n"
        "- AC-FR0001-01 FR-0001 if_ids IF-IMPL-001\n",
        encoding="utf-8",
    )
    (vdir / "test-plan.md").write_text(
        "# Test Plan\n\n## 8. AC Coverage\n\n| AC id | layer | test | IF |\n|---|---|---|---|\n"
        "| AC-FR0001-01 | integration | tests/integration/test_a.py::test_x | IF-IMPL-001 |\n",
        encoding="utf-8",
    )
    # Contract
    exe = sys.executable
    p = paths.project_toml_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"[unit]\nframework = \"pytest\"\npaths = [\"tests/unit/\"]\ncollect = \"{exe} -m pytest --collect-only -q tests/unit/\"\n"
        f'run = "{exe} -m pytest -q tests/unit/ --junitxml={{result}}"\n'
        f'run_selected = "{exe} -m pytest {{nodes}} -q --junitxml={{result}}"\n'
        'cwd = "."\n\n'
        f"[integration]\nframework = \"pytest\"\npaths = [\"tests/integration/\"]\ncollect = \"{exe} -m pytest --collect-only -q tests/integration/\"\n"
        f'run = "{exe} -m pytest -q tests/integration/ --junitxml={{result}}"\n'
        f'run_selected = "{exe} -m pytest {{nodes}} -q --junitxml={{result}}"\n'
        'cwd = "."\n\n'
        f"[e2e]\nframework = \"pytest\"\npaths = [\"tests/e2e/\"]\ncollect = \"{exe} -m pytest --collect-only -q tests/e2e/\"\n"
        f'run = "{exe} -m pytest -q tests/e2e/ --junitxml={{result}}"\n'
        f'run_selected = "{exe} -m pytest {{nodes}} -q --junitxml={{result}}"\n'
        'cwd = "."\n\n'
        '[nightly]\nschedule = "0 3 * * *"\nworkflow = ".github/workflows/nightly.yml"\njob = "nightly"\nlayers = ["unit","integration","e2e"]\npurpose = "x"\n\n'
        '[layout.devon]\nwritable = ["tracks/"]\n[layout.shield]\nwritable = ["tests/"]\n',
        encoding="utf-8",
    )
    # Tracks file
    (repo / "tracks").mkdir(exist_ok=True)
    (repo / "tracks" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tracks" / "foo.py").write_text("x=1\n", encoding="utf-8")
    (repo / "tests" / "integration").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "integration" / "test_a.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    # Tasks json + md
    data = {
        "schema": 2,
        "tasks": [
            {
                "task_id": "T-001",
                "issue_number": 1,
                "description": "task",
                "ac_refs": ["AC-FR0001-01"],
                "fr_refs": ["FR-0001"],
                "if_ids": ["IF-IMPL-001"],
                "scope_boundary": "tracks/foo.py",
                "depends_on": [],
                "batch": "1",
                "parallel": True,
                "budget": 2,
                "unit_refs": [],
                "acceptance_refs": ["tests/integration/test_a.py::test_x"],
            }
        ],
    }
    (vdir / "tasks.json").write_text(json.dumps(data), encoding="utf-8")
    # Write correct tasks.md via render
    tasks, _ = parse_tasks_json((vdir / "tasks.json").read_text(encoding="utf-8"))
    (vdir / "tasks.md").write_text(render_tasks_md(tasks), encoding="utf-8")
    # Commit baseline
    subprocess.run(["git", "add", "-A"], cwd=repo, check=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init", "--allow-empty"], cwd=repo, check=False, capture_output=True)
    # Event-payload task dicts carry the combined test_refs key (as the real
    # _task_payload does); the on-disk schema-2 tasks.json must NOT.
    payload_tasks = [
        dict(t, test_refs=list(t["acceptance_refs"])) for t in data["tasks"]
    ]
    # Create M-IMPL run state
    store.append(run_id, version, "stage.entered", {"stage": "M-IMPL"})
    store.append(
        run_id,
        version,
        "taskgraph.committed",
        {
            "task_count": 1,
            "task_ids": ["T-001"],
            "tasks": payload_tasks,
            "path": "tasks.json",
            "validate_status": "pass",
            "digest": "d",
            "tasks_md": "tasks.md",
            "anchor_probe": {"skipped_reason": "", "probes": [], "advisory": []},
            "satisfiability": {"violations": [], "advisories": []},
            "retained_completed_task_ids": [],
        },
    )
    # Need a task started to allow Devon dispatch

    ex = Executor(store, repo, run_id)
    ex.version = version
    # Manually emit writelock and task.started for Devon RED? For simplicity we will just
    # use store directly to set current_task state: append writelock.granted and task.started
    payload_task = payload_tasks[0]
    manifest = ex._task_manifest(
        ex._task_node(payload_task), store.state(run_id)
    )  # type: ignore[attr-defined]
    store.append(
        run_id,
        version,
        "writelock.granted",
        {"task_id": "T-001", "task": payload_task, "manifest": manifest},
    )
    store.append(
        run_id,
        version,
        "task.started",
        {"task_id": "T-001", "task": payload_task, "manifest": manifest},
    )
    return repo, home, store, run_id, vdir, ex


class _TamperBackend:
    """Backend that tampers tasks.md to a wrong content."""

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        # Determine repo via assignment? We capture via closure instead.
        # This backend expects to be given repo path via external monkeypatch,
        # but we will directly tamper the file in the active repo.
        # The active repo is passed via worktree or via global? We get it from
        # the test closure.
        return {"status": "done", "self_report": "tampered"}


@pytest.mark.integration
def test_tamper_turn_rejected_and_restored(tmp_path: Path):
    repo, home, store, run_id, vdir, ex = _setup_m_impl(tmp_path)

    # Prepare a tampering backend that writes wrong tasks.md
    tampered_content = "# TAMPERED\nWrong content\n"

    class TamperBackend:
        def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
            # Worktree path if isolated, else main repo
            target_root = Path(worktree) if worktree is not None else repo
            # Tamper tasks.md in the worktree/main
            # Need to resolve vdir relative to repo
            # vdir is repo/.tracks/projects/v0.8, worktree mirror has same structure via git worktree
            # For main repo tamper, write to repo's vdir
            md_path = target_root / ".tracks" / "projects" / "v0.8" / "tasks.md"
            if not md_path.exists():
                # fallback to main repo path (when in worktree, .tracks is not in worktree? but worktree has full copy)
                md_path = repo / ".tracks" / "projects" / "v0.8" / "tasks.md"
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(tampered_content, encoding="utf-8")
            return {"status": "done", "self_report": "tamper"}

    ex.backend = TamperBackend()

    # Build the assignment through the real materializer (the production
    # wiring for M-IMPL dispatches). A green dispatch requires the RED
    # checkpoint lineage (r_tree_identity), so replay red.checkpointed first
    # exactly as the machine would before reaching GREEN.
    store.append(
        run_id,
        "v0.8",
        "red.checkpointed",
        {"r_sha": "r" * 40, "task_id": "T-001", "sanction": None},
    )
    state = store.state(run_id)
    params = {
        "role": "devon",
        "substate": "GREEN",
        "stage": "M-IMPL",
        "assignment": {
            "kind": "GREEN",
            "task_id": "T-001",
            "manifest": ex._current_manifest() or {},
            "phase": "green",
        },
    }
    assignment = ex._materialize_m_impl_assignment(state, params, new_ulid())
    cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "devon",
            "substate": "GREEN",
            "stage": "M-IMPL",
            "assignment": assignment,
        },
        command_id=new_ulid(),
    )
    # Directly invoke dispatch path (creates worktree etc.)
    state = store.state(run_id)
    # Use the executor's dispatch handling (which will run backend in worktree, replay, then guard)
    ex._do_dispatch_agent(cmd, state, "T-001", False)

    events = list(store.events(run_id))
    fails = [e for e in events if e.type == "verdict.failed" and e.payload.get("check") == "tasks_md_projection"]
    assert fails, "tampering turn should be rejected with tasks_md_projection"
    restored = [e for e in events if e.type == "tasks_md.restored"]
    assert restored, "should emit tasks_md.restored"
    # File should be restored to correct rendering
    tasks, _ = parse_tasks_json((vdir / "tasks.json").read_text(encoding="utf-8"))
    expected = render_tasks_md(tasks)
    actual = (vdir / "tasks.md").read_text(encoding="utf-8")
    assert actual == expected, "tasks.md should be restored to correct projection"


@pytest.mark.integration
def test_inherited_dirty_self_healed_with_system_repaired(tmp_path: Path):
    repo, home, store, run_id, vdir, ex = _setup_m_impl(tmp_path)

    # Simulate inherited dirty: tasks.md mismatched but not touched this turn
    # Write tampered md before dispatch, commit it as part of pre state? But pre snapshot will capture it as dirty before.
    # For guard to treat as system_repaired, we need pre NOT containing tasks.md in diff,
    # but post mismatched. That means pre snapshot was taken before tamper? No, tamper is inherited from previous crash,
    # so the file was already tampered before this dispatch's pre snapshot.
    # However our pre snapshot is captured at dispatch issue time (via _dirty_snapshot at _materialize...).
    # If file was tampered before, pre will contain the tampered identity, and post will also contain same tampered (if agent doesn't touch md),
    # then is_in_diff will be False (since pre and post same tampered), and decision will be system_repaired -> self-heal.
    # So we tamper before creating dispatch, and have agtent do no md change.
    tasks, _ = parse_tasks_json((vdir / "tasks.json").read_text(encoding="utf-8"))
    correct = render_tasks_md(tasks)
    tampered = "# TAMPERED inherited\n"
    (vdir / "tasks.md").write_text(tampered, encoding="utf-8")
    # Don't commit, leave dirty — this is inherited dirty

    class NoopBackend:
        def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
            # Don't touch tasks.md
            target_root = Path(worktree) if worktree is not None else repo
            # Ensure we don't modify tasks.md; maybe touch another file to ensure dispatch does something
            dummy = target_root / "tracks" / "foo.py"
            # Modify foo.py slightly (agent's legitimate work) to have a diff
            if dummy.exists():
                dummy.write_text("x=2\n", encoding="utf-8")
            return {"status": "done", "self_report": "noop"}

    ex.backend = NoopBackend()
    # Build the assignment through the real materializer (production wiring)
    # after red.checkpointed so the green dispatch is valid.
    store.append(
        run_id,
        "v0.8",
        "red.checkpointed",
        {"r_sha": "r" * 40, "task_id": "T-001", "sanction": None},
    )
    state = store.state(run_id)
    # The pre_dirty_snapshot is captured by the materializer at this point,
    # so the tampered tasks.md (inherited dirty) lands in the baseline.
    params = {
        "role": "devon",
        "substate": "GREEN",
        "stage": "M-IMPL",
        "assignment": {
            "kind": "GREEN",
            "task_id": "T-001",
            "manifest": ex._current_manifest() or {},
            "phase": "green",
        },
    }
    assignment = ex._materialize_m_impl_assignment(state, params, new_ulid())
    pre_snap = assignment.get("pre_dirty_snapshot")
    # Verify pre contains tasks.md tampered
    rel_md = str((vdir / "tasks.md").relative_to(repo).as_posix())
    assert rel_md in pre_snap

    cmd = Command(
        kind="dispatch_agent",
        params={
            "role": "devon",
            "substate": "GREEN",
            "stage": "M-IMPL",
            "assignment": assignment,
        },
        command_id=new_ulid(),
    )
    state = store.state(run_id)
    ex._do_dispatch_agent(cmd, state, "T-001", False)

    events = list(store.events(run_id))
    # Should NOT have violation, but should have system_repaired
    fails = [e for e in events if e.type == "verdict.failed" and e.payload.get("check") == "tasks_md_projection"]
    assert not fails, "inherited dirty should not be violation"
    repaired = [e for e in events if e.type == "tasks_md.system_repaired"]
    assert repaired, "should emit system_repaired for inherited dirty"
    # File should be healed
    actual = (vdir / "tasks.md").read_text(encoding="utf-8")
    assert actual == correct
