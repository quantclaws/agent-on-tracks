"""Behavior coverage for the runtime entry command face (``run_cmd``).

Targets start arg parsing / backlog queueing / unmerged-branch gate,
``trac run`` startup smoke + terminal outcomes + drift/stall handovers,
overlay parsing, startup smoke file listing, the attribution unstage, and
the triage/review Human checkpoint pipelines (SM-01.2/.6, FR-04, FR-0283).
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.helpers import git, git_repo
from tracks.cli import run_cmd
from tracks.cli.run_cmd import (
    _do_human_pipeline,
    _ls_tracks_py,
    _parse_action_actor,
    _parse_run_args,
    _parse_start_args,
    _pipeline_outcome,
    _positive_dispatch_limit,
    _read_assignment_overlay,
    _review_action_params,
    _review_checkpoint,
    _scope_staged_attribution,
    _stage_doc,
    _terminal_run_outcome,
    _triage_artifacts,
    _triage_checkpoint,
    _unmerged_branches,
    cmd_review,
    cmd_run,
    cmd_start,
    cmd_triage,
)
from tracks.executor.code_stamp import RuntimeCodeDriftError
from tracks.executor.stall import CommandStallError
from tracks.store import Store


def _seed(store, run_id, version="v0.1", stage="M-STORY"):
    store.append(run_id, version, "story.requested", {"raw_chars": 1})
    store.append(run_id, version, "stage.entered", {"stage": stage})


# ---------------------------------------------------------------------------
# start args / unmerged branches
# ---------------------------------------------------------------------------


def test_parse_start_args():
    assert _parse_start_args(("v0.1",)) == ("v0.1", None)
    assert _parse_start_args(("v0.1", "--confirm")) == ("v0.1", "confirm")
    assert _parse_start_args(("v0.1", "--cancel")) == ("v0.1", "cancel")
    with pytest.raises(SystemExit):
        _parse_start_args(("v0.1", "extra"))


def test_unmerged_branches_lists_release_only(tmp_path):
    repo = git_repo(tmp_path)
    git(repo, "checkout", "-b", "releases/v1")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-m", "branch work")
    git(repo, "checkout", "main")
    git(repo, "branch", "feature/other")
    assert _unmerged_branches(repo) == ["releases/v1"]


def test_cmd_start_empty_stdin(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "host"
    repo.mkdir()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert cmd_start(repo, "v0.1") == 1
    assert "empty stdin" in capsys.readouterr().err


def test_cmd_start_queues_when_active_run_exists(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "ACTIVE")
    monkeypatch.setattr("sys.stdin", io.StringIO("do the thing"))
    assert cmd_start(repo, "v0.2") == 0
    out = capsys.readouterr().out
    backlog = [e for e in store.events(store.latest_run()) if e.type == "backlog.recorded"]
    store.close()
    assert "requirement queued to backlog" in out
    assert backlog[-1].payload["reason"] == "active_run"


def test_cmd_start_unmerged_gate_confirm_cancel(tmp_path, monkeypatch, capsys):
    repo = git_repo(tmp_path)
    git(repo, "checkout", "-b", "releases/v0.1")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    git(repo, "add", "x.txt")
    git(repo, "commit", "-m", "branch work")
    git(repo, "checkout", "main")
    monkeypatch.setattr("sys.stdin", io.StringIO("req"))
    assert cmd_start(repo, "v0.1") == 2
    assert "unmerged release branches detected" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO("req"))
    assert cmd_start(repo, "v0.1", "--cancel") == 0
    assert "start cancelled" in capsys.readouterr().out


def test_cmd_start_full_flow_and_worktree_sweep(tmp_path, monkeypatch, capsys):
    repo = git_repo(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("build it"))
    monkeypatch.setattr(
        "tracks.executor.worktree.sweep_worktrees", lambda repo_str: ["/tmp/leaked"]
    )
    assert cmd_start(repo, "v0.1") == 0
    out = capsys.readouterr().out
    assert "started on releases/v0.1" in out
    assert "swept 1 stale worktree(s)" in out
    store = Store(repo / ".tracks")
    run_ids = store.conn.execute("SELECT run_id FROM runs").fetchall()
    events = [e for rid in run_ids for e in store.events(rid[0])]
    story = repo / ".tracks" / "projects" / "v0.1" / "story.md"
    store.close()
    assert story.exists()
    assert any(e.type == "worktree.swept" for e in events)
    assert any(e.type == "stage.exited" for e in events)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_scope_staged_attribution_unstages_foreign(tmp_path):
    repo = git_repo(tmp_path)
    allowed = repo / ".tracks" / "projects" / "v0.1" / "story.md"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("s", encoding="utf-8")
    foreign = repo / "operator.txt"
    foreign.write_text("f", encoding="utf-8")
    git(repo, "add", ".")
    assert _scope_staged_attribution(repo, "v0.1", "triage") == 0
    staged = git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert staged == [str(allowed.relative_to(repo))]
    assert foreign.read_text(encoding="utf-8") == "f"


def test_parse_action_actor():
    assert _parse_action_actor(("go",), "u") == ("go", None)
    assert _parse_action_actor(("no-go", "--actor", "A"), "u") == ("no_go", "A")
    assert _parse_action_actor((), "u") is None
    assert _parse_action_actor(("a", "b", "c", "d"), "u") is None
    assert _parse_action_actor(("a", "wrong", "c"), "u") is None


def test_parse_run_args_and_dispatch_limit():
    assert _parse_run_args(()) == (None, None)
    assert _parse_run_args(("--resume",)) == (None, None)
    assert _parse_run_args(("--resume", "--max-dispatches", "3")) == (None, 3)
    assert _parse_run_args(("--max-dispatches", "3")) == (None, 3)
    with pytest.raises(ValueError, match="only once"):
        _parse_run_args(("--max-dispatches", "1", "--max-dispatches", "2"))
    with pytest.raises(ValueError, match="usage"):
        _parse_run_args(("--max-dispatches",))
    assert _positive_dispatch_limit(None) is None
    with pytest.raises(ValueError, match="positive integer"):
        _positive_dispatch_limit("abc")
    with pytest.raises(ValueError, match="positive integer"):
        _positive_dispatch_limit("0")


def test_read_assignment_overlay_errors(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        _read_assignment_overlay(str(tmp_path / "nope.json"))
    with pytest.raises(ValueError, match="cannot read"):
        _read_assignment_overlay(str(tmp_path))
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid assignment overlay JSON"):
        _read_assignment_overlay(str(bad))
    listed = tmp_path / "list.json"
    listed.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be an object"):
        _read_assignment_overlay(str(listed))
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert _read_assignment_overlay(str(good)) == {"a": 1}


def test_ls_tracks_py_git_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr(run_cmd, "git", lambda *a, **k: (_ for _ in ()).throw(OSError("no git")))
    assert _ls_tracks_py(tmp_path) == []


def test_ls_tracks_py_filters_non_python_and_cache(tmp_path):
    repo = git_repo(tmp_path)
    pkg = repo / "tracks" / "__pycache__"
    pkg.mkdir(parents=True)
    (repo / "tracks" / "a.py").write_text("X=1\n", encoding="utf-8")
    (repo / "tracks" / "note.txt").write_text("x", encoding="utf-8")
    (pkg / "cached.py").write_text("X=2\n", encoding="utf-8")
    rels = _ls_tracks_py(repo)
    assert rels == ["tracks/a.py"]
    assert run_cmd._tracks_python_files(repo) == [repo / "tracks" / "a.py"]


def test_terminal_run_outcome_cases(capsys):
    empty = SimpleNamespace(latest_run=lambda: None)
    assert _terminal_run_outcome(empty) is None

    active = SimpleNamespace(
        latest_run=lambda: "R",
        state=lambda rid: SimpleNamespace(status="active", terminal_state=None),
    )
    assert _terminal_run_outcome(active) is None

    cancelled = SimpleNamespace(
        latest_run=lambda: "R",
        state=lambda rid: SimpleNamespace(status="completed", terminal_state="cancelled"),
    )
    assert _terminal_run_outcome(cancelled) == 1
    assert "run is cancelled" in capsys.readouterr().err

    done = SimpleNamespace(
        latest_run=lambda: "R",
        state=lambda rid: SimpleNamespace(
            status="completed",
            terminal_state="done",
            stage="M-STORY",
            substate="EXIT",
            awaiting=None,
            current_attempt=0,
            last_failure=None,
        ),
    )
    assert _terminal_run_outcome(done) == 0
    assert "run R: stage=M-STORY" in capsys.readouterr().out


def test_stage_doc_and_checkpoint_helpers():
    assert _stage_doc("M-STORY") == "story.md"
    assert _stage_doc("NOPE") is None
    assert _triage_artifacts("story.md") == (["story.md"], ["template"])
    assert _triage_artifacts(None) == ([], [])

    revise = _review_action_params("revise", "M-STORY")
    assert revise["requires_diff"] is True
    assert revise["commit_label"] == "M-STORY: human revise"
    assert revise["verdict"] == "comment"
    plain = _review_action_params("no-comment", "M-STORY")
    assert plain["forbid_diff"] is True
    assert plain["verdict"] == "no_comment"


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------


def test_cmd_run_parse_error_and_no_active_run(tmp_path, capsys):
    repo = tmp_path / "host"
    repo.mkdir()
    assert cmd_run(repo, "--bogus") == 1
    assert "usage: trac run" in capsys.readouterr().err
    assert cmd_run(repo) == 1
    assert "no active run" in capsys.readouterr().err


def test_cmd_run_terminal_completed_and_cancelled(tmp_path, capsys):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "DONE")
    store.append("DONE", "v0.1", "run.completed", {"terminal_state": "done"})
    assert cmd_run(repo) == 0
    assert "run DONE:" in capsys.readouterr().out
    store.close()

    repo2 = tmp_path / "host2"
    repo2.mkdir()
    store2 = Store(repo2 / ".tracks")
    _seed(store2, "CANCEL")
    store2.append("CANCEL", "v0.1", "run.completed", {"terminal_state": "cancelled"})
    assert cmd_run(repo2) == 1
    assert "run is cancelled" in capsys.readouterr().err
    store2.close()


def test_cmd_run_loop_success_prints_state(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "RUN")

    class _Done:
        def __init__(self, *a, **k):
            pass

        def run_loop(self):
            return SimpleNamespace(
                stage="M-STORY",
                substate="EXIT",
                status="completed",
                awaiting=None,
                current_attempt=0,
                last_failure=None,
            )

    monkeypatch.setattr(run_cmd, "Executor", _Done)
    assert cmd_run(repo) == 0
    assert "run RUN: stage=M-STORY" in capsys.readouterr().out
    store.close()


def test_cmd_run_handover_on_code_drift(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "RUN")

    class _Drifting:
        def __init__(self, *a, **k):
            pass

        def run_loop(self):
            raise RuntimeCodeDriftError("drift")

    monkeypatch.setattr(run_cmd, "Executor", _Drifting)
    assert cmd_run(repo) == 0
    assert "run handover: tracks/** code drift" in capsys.readouterr().err
    store.close()


def test_cmd_run_aborts_on_command_stall(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "RUN")

    class _Stalling:
        def __init__(self, *a, **k):
            pass

        def run_loop(self):
            raise CommandStallError("stall")

    monkeypatch.setattr(run_cmd, "Executor", _Stalling)
    assert cmd_run(repo) == 1
    assert "run aborted: command stall" in capsys.readouterr().err
    store.close()


def test_cmd_run_smoke_parks_broken_tree(tmp_path, capsys):
    repo = tmp_path / "host"
    repo.mkdir()
    (repo / "tracks").mkdir()
    (repo / "tracks" / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    store = Store(repo / ".tracks")
    _seed(store, "RUN")
    assert cmd_run(repo) == 1
    assert "trac run 拒绝启动" in capsys.readouterr().err
    aborted = [e for e in store.events("RUN") if e.type == "loop.aborted"]
    store.close()
    assert aborted and aborted[-1].payload["reason"] == "startup_smoke"


# ---------------------------------------------------------------------------
# _do_human_pipeline / checkpoints
# ---------------------------------------------------------------------------


def _store_ctx(store, run_id="RUN"):
    store.home = getattr(store, "home", Path("/tmp"))

    @contextlib.contextmanager
    def _ctx(repo):
        yield (store.home, store, run_id)

    return _ctx


def _no_run_ctx():
    @contextlib.contextmanager
    def _ctx(repo):
        yield None

    return _ctx


def test_do_human_pipeline_submits_and_returns(tmp_path, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    _seed(store, "RUN")
    state = store.state("RUN")
    monkeypatch.setattr(run_cmd, "_scope_staged_attribution", lambda repo, version, label: 0)
    calls = {}

    class _Ex:
        def __init__(self, store_arg, repo_arg, run_id):
            calls["init"] = (store_arg, repo_arg, run_id)

        def submit_human_result(self, **kwargs):
            calls["submit"] = kwargs

        def run_pipeline(self):
            return SimpleNamespace(awaiting=None)

    monkeypatch.setattr(run_cmd, "Executor", _Ex)
    checkpoint = _review_checkpoint(repo, state, "A", "revise")
    rc, result = _do_human_pipeline(repo, state, store, "RUN", checkpoint)
    store.close()
    assert rc == 0
    assert result[0].awaiting is None
    assert result[1] == state.awaiting
    assert calls["submit"]["verdict"] == "comment"
    assert calls["submit"]["allowed_paths"] == ["story.md"]
    assert calls["submit"]["domain_event_type"] == "human.review"


def test_do_human_pipeline_early_reject(tmp_path, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    monkeypatch.setattr(run_cmd, "_scope_staged_attribution", lambda *a: 5)
    state = SimpleNamespace(version="v0.1", awaiting="triage", stage="M-STORY")
    rc, result = _do_human_pipeline(
        repo, state, SimpleNamespace(), "RUN", SimpleNamespace(label="triage")
    )
    assert (rc, result) == (5, None)


def test_triage_checkpoint_shape(tmp_path):
    repo = tmp_path / "host"
    repo.mkdir()
    state = SimpleNamespace(stage="M-STORY", substate="TRIAGE")
    checkpoint = _triage_checkpoint(repo, state, "Actor", "go")
    assert checkpoint.label == "triage"
    assert checkpoint.event_type == "human.triage"
    assert checkpoint.payload == {"actor": "Actor"}
    assert checkpoint.verdict == "go"
    assert checkpoint.artifacts == ["story.md"]
    assert checkpoint.checks == ["template"]
    assert checkpoint.commit_label == "M-STORY: human triage (go)"


# ---------------------------------------------------------------------------
# cmd_triage / cmd_review
# ---------------------------------------------------------------------------


def test_cmd_triage_error_paths(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    assert cmd_triage(repo) == 1
    assert "usage: trac triage" in capsys.readouterr().err
    assert cmd_triage(repo, "--bogus") == 1
    assert "usage: trac triage" in capsys.readouterr().err
    assert cmd_triage(repo, "maybe") == 1
    assert "usage: trac triage" in capsys.readouterr().err

    monkeypatch.setattr(run_cmd, "active_run_context", _no_run_ctx())
    assert cmd_triage(repo, "go") == 1
    assert "no active run" in capsys.readouterr().err


class _FakeStore:
    def __init__(self, state):
        self._state = state
        self.home = Path("/tmp")

    def state(self, run_id):
        return self._state


def test_cmd_triage_gate_and_success(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    fake = _FakeStore(SimpleNamespace(awaiting="review", stage="M-STORY", version="v0.1"))
    monkeypatch.setattr(run_cmd, "active_run_context", _store_ctx(fake))
    assert cmd_triage(repo, "go") == 1
    assert "run not awaiting triage" in capsys.readouterr().err

    fake._state = SimpleNamespace(awaiting="triage", stage="M-STORY", version="v0.1")
    seen = {}

    def _fake_pipeline(repo_arg, state, store, run_id, checkpoint):
        seen["checkpoint"] = checkpoint
        return 0, (SimpleNamespace(awaiting=None), "triage")

    monkeypatch.setattr(run_cmd, "_do_human_pipeline", _fake_pipeline)
    assert cmd_triage(repo, "go", "--actor", "A") == 0
    out = capsys.readouterr().out
    assert "triage recorded: go" in out
    assert seen["checkpoint"].payload == {"actor": "A"}


def test_cmd_triage_pipeline_failure_and_early_rc(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    fake = _FakeStore(SimpleNamespace(awaiting="triage", stage="M-STORY", version="v0.1"))
    monkeypatch.setattr(run_cmd, "active_run_context", _store_ctx(fake))
    monkeypatch.setattr(
        run_cmd,
        "_do_human_pipeline",
        lambda *a: (0, (SimpleNamespace(awaiting="triage", last_failure={"reason": "bad doc"}), "triage")),
    )
    assert cmd_triage(repo, "go") == 1
    assert "triage pipeline failed: bad doc" in capsys.readouterr().err

    monkeypatch.setattr(
        run_cmd, "_do_human_pipeline", lambda *a: (2, None)
    )
    assert cmd_triage(repo, "go") == 2


def test_cmd_review_error_paths_and_success(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    assert cmd_review(repo) == 1
    assert "usage: trac review" in capsys.readouterr().err
    assert cmd_review(repo, "--bogus") == 1
    assert "usage: trac review" in capsys.readouterr().err
    assert cmd_review(repo, "bogus") == 1
    assert "usage: trac review" in capsys.readouterr().err

    fake = _FakeStore(SimpleNamespace(awaiting="triage", stage="M-STORY", version="v0.1"))
    monkeypatch.setattr(run_cmd, "active_run_context", _store_ctx(fake))
    assert cmd_review(repo, "revise") == 1
    assert "run not awaiting review" in capsys.readouterr().err

    fake._state = SimpleNamespace(awaiting="review", stage="M-STORY", version="v0.1")
    seen = {}

    def _fake_pipeline(repo_arg, state, store, run_id, checkpoint):
        seen["checkpoint"] = checkpoint
        return 0, (SimpleNamespace(awaiting=None), "review")

    monkeypatch.setattr(run_cmd, "_do_human_pipeline", _fake_pipeline)
    assert cmd_review(repo, "no-comment") == 0
    assert "review recorded: no_comment" in capsys.readouterr().out
    assert seen["checkpoint"].verdict == "no_comment"

    monkeypatch.setattr(run_cmd, "_do_human_pipeline", lambda *a: (4, None))
    assert cmd_review(repo, "revise") == 4


def test_cmd_review_no_active_run(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "host"
    repo.mkdir()
    monkeypatch.setattr(run_cmd, "active_run_context", _no_run_ctx())
    assert cmd_review(repo, "revise") == 1
    assert "no active run" in capsys.readouterr().err


def test_pipeline_outcome_direct(capsys):
    moved = (SimpleNamespace(awaiting=None, last_failure=None), "triage")
    assert _pipeline_outcome(moved, "triage", "triage recorded: go") == 0
    assert "triage recorded: go" in capsys.readouterr().out

    stuck = (SimpleNamespace(awaiting="triage", last_failure={}), "triage")
    assert _pipeline_outcome(stuck, "triage", "x") == 1
    assert "triage pipeline failed: unknown" in capsys.readouterr().err
