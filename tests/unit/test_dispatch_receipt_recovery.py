"""AC-FR0300-01: recover a delivered repair without calling the Agent again."""

import copy
import json

import pytest

from tests.unit.helpers import git_repo, git_strip
from tracks.effects import opencode_recovery
from tracks.effects.opencode import OpencodeBackend
from tracks.executor.executor import Executor
from tracks.kernel.envelope import EnvelopeFormatError, build_assignment_envelope
from tracks.kernel.events import Command
from tracks.store import Store


def _interrupted_repair(tmp_path, monkeypatch, executor=None):
    repo = executor.repo if executor else git_repo(tmp_path, gitignore=True)
    (repo / ".gitignore").write_text(".tracks/\n.opencode/\n")
    git_strip(repo, "add", ".gitignore")
    git_strip(repo, "commit", "-m", "ignore runtime materialization")
    (repo / "README.md").write_text("pre-existing human edit\n")
    result_path = repo / ".tracks/runtime/inbox/C-REPAIR.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    assignment = {
        "phase": "shield_fix", "docs": [], "skills": [], "templates": [],
        "envelope": build_assignment_envelope("shield:write"),
        "result_file": {"result_path": str(result_path)},
        "forbidden_paths": ["tests/unit"],
        "manifest": {"allowed_paths": ["tests/integration"]},
        "test_tasks": [{
            "ac_id": "AC-FR0300-01", "if_ids": ["IF-RECOVER-001"],
            "layers": ["integration"],
        }],
    }
    backend = OpencodeBackend(repo, "v0.9", run_id="RUN")
    receipts = []
    backend.dispatch_receipt_sink = lambda data: receipts.append(copy.deepcopy(data))
    target = repo / "tests/integration/test_recovered.py"

    def child(*args, **kwargs):
        backend._record_dispatch_pid(999999999)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_recovered():\n    assert True\n")
        result_path.write_text(json.dumps({
            "envelope": {"kind": "shield:write", "version": 2},
            "payload": {
                "artifact_manifest": {"include": [{
                    "path": str(target.relative_to(repo)),
                    "kind": "integration_test", "role": "test",
                }]},
                "suggested_commit_message": "repair retained tests",
            },
        }))
        raise KeyboardInterrupt

    monkeypatch.setattr(backend, "_dispatch_with_session_health", child)
    monkeypatch.setattr(opencode_recovery, "process_alive", lambda pid: False)
    return repo, backend, assignment, receipts, target, result_path


def _deliver(backend, assignment):
    with pytest.raises(KeyboardInterrupt):
        backend.act("shield", "WRITE", None, None, assignment)


# AC-FR0300-01@v0.9 TRACKS-TRACE delivered repair survives control-process interruption
def test_delivery_recovers_with_original_audit_baseline(tmp_path, monkeypatch):
    repo, backend, assignment, receipts, target, result_path = _interrupted_repair(
        tmp_path, monkeypatch
    )
    _deliver(backend, assignment)
    restored = OpencodeBackend(repo, "v0.9", run_id="RUN")
    result = restored.recover_act(receipts[-1])
    assert result["status"] == "done"
    assert result["artifact_manifest"]["include"][0]["path"] == str(target.relative_to(repo))
    assert result["agent_io"]["stdout"] == ""
    assert result["agent_io"]["delivery"]["process_exit_observed"] is False
    assert (repo / "README.md").read_text() == "pre-existing human edit\n"
    assert result_path.exists()


# AC-FR0300-01@v0.9 TRACKS-TRACE changed candidates and unattributed work remain intact
@pytest.mark.parametrize("drift", ["head", "extra_file", "human_edit", "missing_pid"])
def test_recovery_rejects_changed_identity_without_rollback(tmp_path, monkeypatch, drift):
    repo, backend, assignment, receipts, target, _ = _interrupted_repair(tmp_path, monkeypatch)
    _deliver(backend, assignment)
    if drift == "head":
        git_strip(repo, "commit", "--allow-empty", "-m", "new candidate")
    elif drift == "extra_file":
        (repo / "notes.txt").write_text("new operator work\n")
    elif drift == "human_edit":
        (repo / "README.md").write_text("new operator edit\n")
    else:
        receipts[-1].pop("pid")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        backend.recover_act(receipts[-1])
    assert target.read_bytes() == before
    if drift == "human_edit":
        assert (repo / "README.md").read_text() == "new operator edit\n"


# AC-FR0300-01@v0.9 TRACKS-TRACE active work and incomplete replies never become success
def test_active_or_malformed_delivery_is_not_accepted(tmp_path, monkeypatch):
    _, backend, assignment, receipts, _, result_path = _interrupted_repair(tmp_path, monkeypatch)
    _deliver(backend, assignment)
    monkeypatch.setattr(opencode_recovery, "process_alive", lambda pid: True)
    assert backend.recover_act(receipts[-1]) == {"recovery_pending": True}
    monkeypatch.setattr(opencode_recovery, "process_alive", lambda pid: False)
    result_path.write_text("{}")
    with pytest.raises(EnvelopeFormatError):
        backend.recover_act(receipts[-1])
    result_path.unlink()
    assert backend.recover_act(receipts[-1]) is None


# AC-FR0300-01@v0.9 TRACKS-TRACE head drift cannot cause a second concurrent Agent
def test_active_original_process_is_retained_even_when_head_moves(tmp_path, monkeypatch):
    repo, backend, assignment, receipts, _, _ = _interrupted_repair(tmp_path, monkeypatch)
    _deliver(backend, assignment)
    git_strip(repo, "commit", "--allow-empty", "-m", "new candidate while child is active")
    monkeypatch.setattr(opencode_recovery, "process_alive", lambda pid: True)
    assert backend.recover_act(receipts[-1]) == {"recovery_pending": True}


# AC-FR0300-01@v0.9 TRACKS-TRACE unfinished work cannot redefine the audit baseline
def test_missing_delivery_preserves_and_rejects_unattributed_work(tmp_path, monkeypatch):
    repo, backend, assignment, receipts, _, result_path = _interrupted_repair(tmp_path, monkeypatch)
    _deliver(backend, assignment)
    result_path.unlink()
    (repo / "README.md").write_text("unattributed edit after interruption\n")
    with pytest.raises(ValueError, match="scope"):
        backend.recover_act(receipts[-1])
    assert (repo / "README.md").read_text() == "unattributed edit after interruption\n"


# AC-FR0300-01@v0.9 TRACKS-TRACE trusted receipt preserves the original pending command
def test_executor_recovery_reuses_delivery_and_resolves_pending_once(tmp_path, monkeypatch):
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    store.append("RUN", "v0.9", "story.requested", {})
    store.append("RUN", "v0.9", "stage.entered", {"stage": "M-IMPL"})
    executor = Executor(store, repo, "RUN")
    _, backend, assignment, receipts, _, _ = _interrupted_repair(
        tmp_path, monkeypatch, executor
    )
    executor.backend = backend
    cmd = Command("dispatch_agent", {
        "role": "shield", "substate": "WRITE", "assignment": assignment,
    }, command_id="C-REPAIR")
    store.append("RUN", "v0.9", "command.issued", {"command": {
        "kind": cmd.kind, "params": cmd.params, "command_id": cmd.command_id,
    }}, command_id=cmd.command_id)
    with executor._capture_dispatch_receipt(cmd, assignment, None, None):
        _deliver(backend, assignment)
    assert receipts == []  # executor's trusted sink replaced the test observer
    assert store.state("RUN").pending["command_id"] == "C-REPAIR"
    accepted = []

    def consume(ctx, result):
        accepted.append(result)
        store.append("RUN", "v0.9", "outcome.received", {
            "role": "shield", "status": "done",
        }, command_id=ctx.cmd.command_id)

    monkeypatch.setattr(executor, "_finalize_agent_outcome", consume)
    executor._recover()
    executor._recover()
    assert len(accepted) == 1
    assert accepted[0]["status"] == "done"
    assert store.state("RUN").pending is None
    store.close()


# AC-FR0300-01@v0.9 TRACKS-TRACE unrelated advisories keep their normal WAL semantics
@pytest.mark.parametrize("kind,area", [
    ("dispatch_agent", "ci_readback"), ("record_backlog", "dispatch_recovery"),
])
def test_only_dispatch_receipts_preserve_pending(tmp_path, kind, area):
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    store.append("RUN", "v0.9", "command.issued", {
        "command": {"kind": kind, "params": {}, "command_id": "C"},
    }, command_id="C")
    store.append("RUN", "v0.9", "advisory.recorded", {"area": area}, command_id="C")
    assert store.state("RUN").pending is None
    store.close()


# AC-FR0300-01@v0.9 TRACKS-TRACE receipt bytes and command identity are independently checked
def test_changed_receipt_bytes_or_command_binding_cannot_be_accepted(tmp_path):
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    executor = Executor(store, repo, "RUN")
    cmd = Command("dispatch_agent", {}, command_id="C")
    ref = store.write_audit_blob({"binding": "another-command"})
    with pytest.raises(ValueError, match="another command"):
        executor._load_dispatch_receipt(cmd, ref)
    (repo / ".tracks/runtime/blobs" / ref).write_text("altered receipt")
    with pytest.raises(ValueError, match="digest mismatch"):
        executor._load_dispatch_receipt(cmd, ref)
    store.close()


# AC-FR0300-01@v0.9 TRACKS-TRACE failure to persist the baseline prevents Agent execution
def test_audit_storage_failure_prevents_dispatch(tmp_path, monkeypatch):
    repo = git_repo(tmp_path, gitignore=True)
    store = Store(repo / ".tracks")
    executor = Executor(store, repo, "RUN")
    _, backend, assignment, _, target, reply = _interrupted_repair(tmp_path, monkeypatch, executor)
    executor.backend = backend
    monkeypatch.setattr(store, "write_audit_blob", lambda payload: None)
    cmd = Command("dispatch_agent", {}, command_id="C")
    with executor._capture_dispatch_receipt(cmd, assignment, None, None):
        result = backend.act("shield", "WRITE", None, None, assignment)
    assert result["status"] == "failed"
    assert result["failure_class"] == "filesystem"
    assert not target.exists()
    assert not reply.exists()
    store.close()


# AC-FR0300-01@v0.9 TRACKS-TRACE PID-record failure cannot orphan the new child
@pytest.mark.parametrize("failure", [RuntimeError("storage failed"), KeyboardInterrupt()])
def test_pid_record_failure_reaps_spawned_child(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace

    backend = OpencodeBackend(tmp_path, "v0.9")
    backend._active_dispatch_receipt = {}
    killed = []
    joined = []
    proc = SimpleNamespace(pid=123, wait=lambda timeout: joined.append(timeout))
    monkeypatch.setattr(backend, "_spawn", lambda *a: (proc, None))
    monkeypatch.setattr(backend, "_kill_group", killed.append)

    def fail(pid):
        raise failure

    monkeypatch.setattr(backend, "_record_dispatch_pid", fail)
    with pytest.raises(type(failure)):
        backend._run("Shield", "repair tests")
    assert killed == [123]
    assert joined == [5]
