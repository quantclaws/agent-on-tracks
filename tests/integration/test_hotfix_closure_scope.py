"""Real mutation proof for a hotfix's version-qualified approval anchor."""

import difflib
import json
from pathlib import Path

from tests.unit.helpers import git_strip
from tracks.executor.executor import Executor
from tracks.kernel.events import Command
from tracks.store import Store

VERSION = "v0.12-hotfix-262"
REF = "AC-FR0001-01@v0.8"


def _host(repo):
    (repo / ".gitignore").write_text(".venv\n.tracks/runtime/\n__pycache__/\n.pytest_cache/\n")
    original = "def answer():\n    return 42\n"
    (repo / "answer.py").write_text(original)
    target = repo / "tests/unit/test_answer.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "from answer import answer\n\ndef test_answer():\n    assert answer() == 42\n"
        "\ndef test_control():\n    assert 2 + 2 == 4\n"
    )
    projects = repo / ".tracks/projects"
    (projects / "v0.8").mkdir(parents=True)
    (projects / "v0.8/acceptance.md").write_text("# Acceptance\n\n### AC-FR0001-01\n\n- answer is 42\n")
    template = Path(__file__).resolve().parents[2] / "tracks/assets/demo_host/tracks-project.toml"
    (projects / "project.toml").write_bytes(template.read_bytes())
    assets = repo / "tests/counterexamples" / VERSION
    assets.mkdir(parents=True)
    patch = "diff --git a/answer.py b/answer.py\n" + "".join(difflib.unified_diff(
        original.splitlines(keepends=True), original.replace("42", "0").splitlines(keepends=True),
        fromfile="a/answer.py", tofile="b/answer.py",
    ))
    (assets / "answer.patch").write_text(patch)
    (assets / "kill-manifest.json").write_text(json.dumps({"bindings": [{
        "ac": REF, "patch": "answer.patch", "test": "tests/unit/test_answer.py::test_answer",
        "control": "tests/unit/test_answer.py::test_control", "if_ref": "IF-ANSWER-001",
    }]}))
    git_strip(repo, "add", ".gitignore", "answer.py", "tests", ".tracks/projects")
    git_strip(repo, "commit", "-m", "approved hotfix mutation scenario")
    return git_strip(repo, "rev-parse", "HEAD")


# AC-FR0265-01@v0.7 TRACKS-TRACE real baseline/kill/control/rollback and idempotent resume
def test_hotfix_produces_real_proof_for_qualified_anchor(host_repo, monkeypatch):
    candidate = _host(host_repo)
    store = Store(host_repo / ".tracks")
    for kind, payload in [
        ("hotfix.requested", {"issue": 262, "scenario": "post-release"}),
        ("stage.entered", {"stage": "M-VERIFY"}),
        ("anchor.validated", {"acs": [REF]}),
        ("candidate.frozen", {"candidate_sha": candidate}),
    ]:
        store.append("RUN", VERSION, kind, payload)
    executor = Executor(store, host_repo, "RUN")
    followups = []
    monkeypatch.setattr(executor, "issue", followups.append)
    cmd = Command("produce_closure_evidence", {"candidate_sha": candidate}, command_id="C-CLOSURE")
    for _ in range(2):
        executor._do_produce_closure_evidence(cmd, store.state("RUN"), None, False)
    experiments = [e for e in store.events("RUN") if e.type == "mutation.experiment"]
    assert len(experiments) == 1
    assert experiments[0].payload["status"] == "passed"
    assert experiments[0].payload["target_kill"] == "verified"
    assert experiments[0].payload["controls"] == "green"
    assert experiments[0].payload["rollback"] == "clean"
    manifest = next(e for e in store.events("RUN") if e.type == "mutation.manifest")
    baseline = next(e for e in store.events("RUN") if e.type == "phase0.baseline_repaired")
    assert manifest.payload["ac"] == baseline.payload["ac"] == REF
    assert executor._closure_existing(candidate, [REF]) == {REF}
    assert [cmd.kind for cmd in followups] == ["run_local_gates", "run_local_gates"]
    assert (host_repo / "answer.py").read_text() == "def answer():\n    return 42\n"
    store.close()
