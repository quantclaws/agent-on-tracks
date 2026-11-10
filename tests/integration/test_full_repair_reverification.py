"""A committed repair closes stale failures only through executed FULLs."""

from pathlib import Path

from tests.unit.helpers import git_strip
from tracks.executor.executor import Executor
from tracks.executor.test_select import ledger_is_clean, rebuild_ledger
from tracks.kernel.events import Command
from tracks.store import Store


def _candidate(repo):
    (repo / ".gitignore").write_text(".venv\n.tracks/runtime/\n__pycache__/\n.pytest_cache/\n")
    for layer in ("unit", "integration", "e2e"):
        target = repo / f"tests/{layer}/test_{layer}.py"
        target.parent.mkdir(parents=True)
        target.write_text("from answer import answer\n\ndef test_answer():\n    assert answer() == 42\n")
    (repo / "answer.py").write_text("def answer():\n    return 0\n")
    projects = repo / ".tracks/projects"
    projects.mkdir(parents=True)
    template = Path(__file__).resolve().parents[2] / "tracks/assets/demo_host/tracks-project.toml"
    (projects / "project.toml").write_bytes(template.read_bytes())
    git_strip(repo, "add", ".gitignore", "tests", "answer.py", ".tracks/projects/project.toml")
    git_strip(repo, "commit", "-m", "failing candidate")


# AC-FR0253-05@v0.6 TRACKS-TRACE real FULL proof and replay, no Agent call
def test_repaired_candidate_reexecutes_full_and_proves_old_ledger(host_repo, monkeypatch):
    repo = host_repo
    _candidate(repo)
    store = Store(repo / ".tracks")
    for kind, payload in [
        ("story.requested", {}),
        ("stage.entered", {"stage": "M-IMPL"}),
        ("baseline.frozen", {"digest": "approved-baseline", "status": "current"}),
        ("taskgraph.committed", {"tasks": [{"task_id": "T-1"}], "task_count": 1}),
        ("task.completed", {"task_id": "T-1"}),
    ]:
        store.append("RUN", "v0.6", kind, payload)
    executor = Executor(store, repo, "RUN")
    executor.issue(Command("check_island_2", {"stage": "M-IMPL"}))
    before = store.state("RUN")
    assert before.substate == "DIAGNOSE"
    assert before.ledger_open == 3

    (repo / "answer.py").write_text("def answer():\n    return 42\n")
    git_strip(repo, "add", "answer.py")
    git_strip(repo, "commit", "-m", "fix answer\n\nTracks-OOB: repair measured failures")
    repaired = git_strip(repo, "rev-parse", "HEAD")
    store.append("RUN", "v0.6", "oob.accepted", {"sha": repaired, "files": ["answer.py"]})
    resumed = Executor(store, repo, "RUN")
    monkeypatch.setattr(resumed, "_sm02_pre_decide", lambda state: False)
    action, _ = resumed._run_loop_preflight()
    assert action == "continue"
    after = store.state("RUN")
    assert after.island_2_passed and after.substate == "EXIT"
    assert after.ledger_open == 0
    assert ledger_is_clean(rebuild_ledger(store.events("RUN")))
    rounds = [e for e in store.events("RUN") if e.type == "full.executed"]
    assert [e.payload["round"] for e in rounds] == ["FULL_1", "full_settlement", "fallback_full"]
    assert rounds[-1].payload["execution_commit"] == repaired
    assert rounds[-1].payload["serves_as_full_f"] is True
    assert not any(e.type == "command.issued" and e.payload["command"]["kind"] == "dispatch_agent"
                   for e in store.events("RUN"))
    store.close()
    replay = Store(repo / ".tracks")
    restarted = Executor(replay, repo, "RUN")
    assert not restarted._reverify_repaired_full_candidate(replay.state("RUN"))
    assert replay.state("RUN").island_2_passed
    replay.close()
