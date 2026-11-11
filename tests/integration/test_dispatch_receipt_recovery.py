"""Real process interruption after a Shield file delivery (FR-0300)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.unit.helpers import git_repo, git_strip
from tracks.effects.opencode import OpencodeBackend
from tracks.effects.opencode_recovery import process_alive
from tracks.executor.executor import Executor
from tracks.kernel.envelope import build_assignment_envelope
from tracks.store import Store

CONTROLLER = """
import json, sys
from pathlib import Path
from tracks.effects.opencode import OpencodeBackend
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store
repo = Path(sys.argv[1])
assignment = json.loads(Path(sys.argv[2]).read_text())
store = Store(repo / '.tracks')
for check in ['impl_defect', 'test_defect']:
    store.append('RUN', 'v0.9', 'verdict.failed', {'check': check, 'attempt': 1})
ex = Executor(store, repo, 'RUN')
ex.backend = OpencodeBackend(repo, 'v0.9', run_id='RUN')
cmd = Command('dispatch_agent', {
    'role': 'shield', 'substate': 'WRITE', 'stage': 'M-IMPL',
    'assignment': assignment, 'objective': 'repair retained test',
}, command_id='C-REPAIR')
store.append('RUN', 'v0.9', 'command.issued', {'command': {
    'kind': cmd.kind, 'params': cmd.params, 'command_id': cmd.command_id,
}}, command_id=cmd.command_id)
ex._dispatch_agent_backend(cmd, store.state('RUN'), None, 'shield', 'WRITE', None, None, assignment)
"""


# AC-FR0300-01@v0.9 TRACKS-TRACE SIGKILL after delivery does not repeat agent work
@pytest.mark.parametrize("deliver", [True, False])
def test_killed_controller_recovers_delivered_repair_once(tmp_path, monkeypatch, deliver):
    repo = git_repo(tmp_path, gitignore=True)
    (repo / ".gitignore").write_text(".tracks/\n.opencode/\n")
    git_strip(repo, "add", ".gitignore")
    git_strip(repo, "commit", "-m", "ignore materialized agent files")
    (repo / "README.md").write_text("uncommitted human work\n")
    target = repo / "tests/integration/test_retained.py"
    reply = repo / ".tracks/runtime/inbox/C-REPAIR.json"
    reply.parent.mkdir(parents=True, exist_ok=True)
    assignment = {
        "phase": "shield_fix", "docs": [], "skills": [], "templates": [],
        "envelope": build_assignment_envelope("shield:write"),
        "result_file": {"result_path": str(reply)},
        "manifest": {"allowed_paths": ["tests/integration"]},
        "test_tasks": [{
            "ac_id": "AC-FR0300-01", "if_ids": ["IF-RECOVER-001"],
            "layers": ["integration"],
        }],
        "forbidden_paths": ["tests/unit"],
    }
    assignment_file = tmp_path / "assignment.json"
    assignment_file.write_text(json.dumps(assignment))
    marker = tmp_path / "invocations.txt"
    executable = tmp_path / "bin/opencode"
    executable.parent.mkdir()
    envelope = {
        "envelope": {"kind": "shield:write", "version": 2},
        "payload": {
            "artifact_manifest": {"include": [{
                "path": str(target.relative_to(repo)), "kind": "integration_test", "role": "test",
            }]},
            "suggested_commit_message": "repair retained tests",
        },
    }
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, signal\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).open('a').write('call\\n')\n"
        f"target = Path({str(target)!r})\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_text('def test_retained():\\n    assert 2 + 2 == 4\\n')\n"
        + (f"Path({str(reply)!r}).write_text({json.dumps(envelope)!r})\n" if deliver else "")
        +
        "os.kill(os.getppid(), signal.SIGKILL)\n"
    )
    executable.chmod(0o755)
    store = Store(repo / ".tracks")
    store.append("RUN", "v0.9", "story.requested", {})
    store.append("RUN", "v0.9", "stage.entered", {"stage": "M-IMPL"})
    store.close()
    env = dict(os.environ)
    env["PATH"] = str(executable.parent) + os.pathsep + env["PATH"]
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    killed = subprocess.run(
        [sys.executable, "-c", CONTROLLER, str(repo), str(assignment_file)],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    store = Store(repo / ".tracks")
    pending = store.state("RUN").pending
    assert pending["command_id"] == "C-REPAIR"
    receipt_event = [e for e in store.events("RUN") if e.type == "advisory.recorded"][-1]
    raw = (repo / ".tracks/runtime/blobs" / receipt_event.payload["receipt_ref"]).read_text()
    pid = json.loads(raw)["backend"]["pid"]
    deadline = time.monotonic() + 5
    while process_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not process_alive(pid)
    resumed = Executor(store, repo, "RUN")
    resumed.backend = OpencodeBackend(repo, "v0.9", run_id="RUN")
    if not deliver:
        assert resumed.backend.recover_act(json.loads(raw)["backend"]) is None
        assert not (repo / ".opencode/agents/Shield.md").exists()
        assert store.state("RUN").pending["command_id"] == "C-REPAIR"
        assert target.exists()
        assert (repo / "README.md").read_text() == "uncommitted human work\n"
        assert marker.read_text().splitlines() == ["call"]
        store.close()
        return

    def unexpected_dispatch(*args, **kwargs):
        raise AssertionError("completed repair must not call an Agent again")

    monkeypatch.setattr(resumed.backend, "act", unexpected_dispatch)
    resumed._recover()
    resumed._recover()
    assert marker.read_text().splitlines() == ["call"]
    assert len([e for e in store.events("RUN") if e.type == "outcome.received"]) == 1
    assert len([e for e in store.events("RUN") if e.type == "test.committed"]) == 1
    assert git_strip(repo, "diff", "--name-only", "--", "tests") == ""
    assert (repo / "README.md").read_text() == "uncommitted human work\n"
    assert not (repo / ".opencode/agents/Shield.md").exists()
    assert store.state("RUN").pending is None
    store.close()
