"""D-36 浅版（#43）：OOB 提交通道的 executor 集成行为。

- run_loop 观察点：派发窗口内携带 ``Tracks-OOB`` trailer 的操作者提交
  → ``oob.accepted`` 事件（观察点单调前移，幂等）；未声明提交不发事件。
- worktree 回放反碾压守卫（B36 回归）：主树侧窗口内提交与 agent
  worktree 增量同名 → fail-closed 冲突报错，主树内容保留。
- 窗口内提交与 worktree 增量不相交 → 回放成功，两侧变更共存。
"""

import subprocess
from pathlib import Path

from tests.integration.result_checkpoint_support import _setup, _setup_m_test
from tests.m_test_support import make_m_test_dispatch_cmd


def _commit_main(repo, files: dict[str, str], trailer: str | None) -> None:
    """主树侧操作者提交：只提交指定文件（不动其他脏文件）。"""
    for rel, content in files.items():
        p = Path(repo) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "add", *files], cwd=repo, check=True, capture_output=True
    )
    args = ["git", "commit", "-m", "operator mid-dispatch fix"]
    if trailer:
        args += ["-m", f"Tracks-OOB: {trailer}"]
    subprocess.run(args, cwd=repo, check=True, capture_output=True)


class _CommittingBackend:
    """派发期间模拟操作者主树提交的替身后端（失败 outcome，快速收敛）。"""

    def __init__(self, repo, files: dict[str, str], trailer: str | None):
        self._repo = repo
        self._files = files
        self._trailer = trailer

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        _commit_main(self._repo, self._files, self._trailer)
        return {"status": "failed", "failure_class": "agent_failed", "self_report": "x"}


class _WorktreeWriterBackend:
    """Shield WRITE 替身：测试文件写进 worktree（真实 B1 隔离路径），
    并可在 act() 期间模拟操作者在主树提交。"""

    def __init__(self, repo, wt_files: dict[str, str], operator: dict | None = None):
        self._repo = Path(repo)
        self._wt_files = wt_files
        # operator: {"files": {...}, "trailer": str | None}
        self._operator = operator

    def act(self, role, substate, doc, doc_path, assignment=None, worktree=None):
        target = Path(worktree) if worktree is not None else self._repo
        for rel, content in self._wt_files.items():
            p = target / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        if self._operator is not None:
            _commit_main(self._repo, self._operator["files"], self._operator.get("trailer"))
        return {
            "status": "done",
            "artifact_ref": "tests",
            "self_report": "wrote",
            "artifact_manifest": {
                "include": [
                    {"path": rel, "kind": "test_asset", "role": "integration"}
                    for rel in self._wt_files
                ]
            },
            "suggested_commit_message": "M-TEST: shield write",
        }


# -- run_loop 观察点 ------------------------------------------------------------


def test_run_loop_emits_oob_accepted_for_trailer_commit(tmp_path):
    ex, store, run_id = _setup(tmp_path)
    ex.max_dispatches = 1
    ex.backend = _CommittingBackend(ex.repo, {"docs/x.md": "fix\n"}, trailer="mid-run runtime fix")
    ex.run_loop()
    oob_events = [e for e in store.events(run_id) if e.type == "oob.accepted"]
    # 恰好一次：loop-top 观察发出后观察点前移，finally 的再观察是 no-op
    assert len(oob_events) == 1
    payload = oob_events[0].payload
    assert payload["reason"] == "mid-run runtime fix"
    assert payload["files"] == ["docs/x.md"]
    assert len(payload["sha"]) == 40


def test_undeclared_operator_commit_emits_no_event(tmp_path):
    ex, store, run_id = _setup(tmp_path)
    ex.max_dispatches = 1
    ex.backend = _CommittingBackend(ex.repo, {"docs/x.md": "fix\n"}, trailer=None)
    ex.run_loop()
    assert [e for e in store.events(run_id) if e.type == "oob.accepted"] == []


def test_oob_accepted_event_replays_as_state_noop(tmp_path):
    """oob.accepted 不改变 machine 投影（append-only 记账，无 reducer）。"""
    from tracks.kernel.machine import project

    ex, store, run_id = _setup(tmp_path)
    before = project(store.events(run_id))
    store.append(run_id, ex.version, "oob.accepted", {"sha": "a" * 40, "reason": "r", "files": []})
    after = project(store.events(run_id))
    assert after.stage == before.stage
    assert after.status == before.status
    assert after.pending == before.pending


# -- worktree 回放反碾压守卫（B36 回归） ------------------------------------------


def test_replay_conflict_preserves_operator_commit(tmp_path):
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = _WorktreeWriterBackend(
        ex.repo,
        wt_files={"tests/integration/test_agent.py": "agent version\n"},
        operator={
            "files": {"tests/integration/test_agent.py": "operator version\n"},
            "trailer": "mid-run fix",
        },
    )
    ex.issue(make_m_test_dispatch_cmd())

    # 主树保留操作者版本：mirror 拒绝覆盖（B36 事故的反向断言）
    assert (ex.repo / "tests/integration/test_agent.py").read_text(encoding="utf-8") == (
        "operator version\n"
    )
    # fail-closed：verdict.failed(check=worktree) 且 reason 指名冲突文件
    fails = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert any(
        e.payload.get("check") == "worktree"
        and "test_agent.py" in e.payload.get("reason", "")
        and "conflict" in e.payload.get("reason", "")
        for e in fails
    ), [e.payload for e in fails]


def test_replay_disjoint_operator_commit_coexists(tmp_path):
    ex, store, run_id = _setup_m_test(tmp_path)
    ex.backend = _WorktreeWriterBackend(
        ex.repo,
        wt_files={"tests/integration/test_agent.py": "agent version\n"},
        operator={
            "files": {"docs/note.md": "operator note\n"},
            "trailer": "mid-run doc fix",
        },
    )
    ex.issue(make_m_test_dispatch_cmd())

    # 不相交：两侧变更共存，agent worktree 增量正常回放
    assert (ex.repo / "tests/integration/test_agent.py").read_text(encoding="utf-8") == (
        "agent version\n"
    )
    assert (ex.repo / "docs/note.md").read_text(encoding="utf-8") == "operator note\n"
    fails = [e for e in store.events(run_id) if e.type == "verdict.failed"]
    assert not any(e.payload.get("check") == "worktree" for e in fails), [
        e.payload for e in fails
    ]
