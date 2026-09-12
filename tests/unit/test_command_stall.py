"""B86/B88（#77 批次 1）：非派发命令紧循环（STALL）熔断测试。

r1 实证：decide() 在守卫 bug 下 3Hz/280ms 无限重发同一 kind 命令
（commit_green 烧 1600+ 事件、select_task 烧 900+，95% CPU）。本熔断跟踪
连续同 kind+同 task_id/gate 标识的 command.issued；任何非 command.issued
进展事件即清零；计数 ≥ STALL_COMMAND_LIMIT → loop.aborted(command_stall)
+ CommandStallError（CLI 捕获转非零）。dispatch_agent 不计入。
"""

import pytest

from tracks.executor.executor import Executor
from tracks.executor.stall import (
    STALL_COMMAND_LIMIT,
    CommandStallError,
    CommandStallTracker,
)
from tracks.kernel.events import Command
from tracks.store import Store


def _issued(kind, params=None, command_id="C"):
    return {
        "command": {"kind": kind, "params": params or {}, "command_id": command_id}
    }


def _observe_seq(tracker, events):
    for type, payload in events:
        tracker.observe(type, payload)
    return tracker


# -- 纯 tracker 单元 -----------------------------------------------------------


def test_same_kind_20_consecutive_trips():
    t = CommandStallTracker()
    for i in range(19):
        t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}, f"C{i}"))
        assert not t.should_trip()
    t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}, "C19"))
    assert t.should_trip()


def test_same_kind_identical_task_id_counts_across_cycles():
    """同 kind 同 task_id：跨轮累计；不同 task_id 重置为 1。"""
    t = CommandStallTracker()
    t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    t.observe("command.issued", _issued("commit_green", {"task_id": "T2"}))
    assert t._count == 1  # task_id 变化：新序列
    for _ in range(19):
        t.observe("command.issued", _issued("commit_green", {"task_id": "T2"}))
    assert t.should_trip()


def test_gate_identity_distinguishes_gates():
    """run_task_gates 以 gate 标识分组（RED_GATE 与 GREEN_GATE 不混计）。"""
    t = CommandStallTracker()
    t.observe("command.issued", _issued("run_task_gates", {"gate": "RED_GATE"}))
    t.observe("command.issued", _issued("run_task_gates", {"gate": "RED_GATE"}))
    t.observe("command.issued", _issued("run_task_gates", {"gate": "GREEN_GATE"}))
    assert t._count == 1  # 新 gate：新序列
    assert t._kind == "run_task_gates"


def test_progress_event_resets_count():
    t = CommandStallTracker()
    for _ in range(10):
        t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    t.observe("verdict.failed", {"check": "impl_defect", "task_id": "T1"})
    t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    assert not t.should_trip()  # 进展事件清零后重新计数


def test_any_non_issued_event_resets_count():
    t = CommandStallTracker()
    for _ in range(10):
        t.observe("command.issued", _issued("select_task"))
    t.observe("task.started", {"task_id": "T9"})
    t.observe("command.issued", _issued("select_task"))
    assert t._count == 1


def test_dispatch_agent_never_counts():
    t = CommandStallTracker()
    for _ in range(50):
        t.observe(
            "command.issued",
            _issued("dispatch_agent", {"role": "devon", "substate": "RED"}),
        )
    assert not t.should_trip()
    assert t._count == 0


def test_different_kinds_do_not_accumulate():
    t = CommandStallTracker()
    for _ in range(25):
        t.observe("command.issued", _issued("select_task"))
        t.observe("command.issued", _issued("commit_taskgraph"))
    assert not t.should_trip()  # 交替 kind：每次重置为 1


def test_summary_reports_kind_count_and_last_ids():
    t = CommandStallTracker()
    for i in range(25):
        t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}, f"CID{i}"))
    summary = t.summary()
    assert "kind=commit_green" in summary
    assert "count=25" in summary
    assert "CID22" in summary and "CID24" in summary  # 最后 command_id 摘要
    assert "CID0" not in summary  # 只保留最后几个


def test_custom_limit_respected():
    t = CommandStallTracker(limit=3)
    for _ in range(2):
        t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    assert not t.should_trip()
    t.observe("command.issued", _issued("commit_green", {"task_id": "T1"}))
    assert t.should_trip()


def test_loop_aborted_event_resets_count():
    """loop.aborted 走统一漏斗后重置计数（不影响已触发的判定）。"""
    t = CommandStallTracker()
    for _ in range(5):
        t.observe("command.issued", _issued("select_task"))
    t.observe("loop.aborted", {"reason": "code_drift", "detail": "x"})
    assert t._count == 0


# -- run_loop 集成 ------------------------------------------------------------


def _minimal_executor(tmp_path) -> tuple[Executor, Store, str]:
    """M-STORY 起跑的空 run（decide 被 monkeypatch 接管，state 仅供 loop 跑）。"""
    repo = tmp_path / "host"
    repo.mkdir()
    home = repo / ".tracks"
    home.mkdir(exist_ok=True)
    store = Store(home)
    run_id = "RUN-STALL"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    return Executor(store, repo, run_id), store, run_id


def _noop_handler(cmd, state, task_id, reconcile):
    return None


def test_run_loop_trips_on_same_kind_stall(tmp_path, monkeypatch):
    """守卫 bug 签名：decide 无限返回同一非派发命令，handler 零进展事件
    → 计数 ≥ 阈值 → loop.aborted(command_stall) + CommandStallError。"""
    ex, store, run_id = _minimal_executor(tmp_path)
    monkeypatch.setattr(
        ex,
        "_do_commit_green",
        _noop_handler,  # handler 不发任何进展事件（B86 guard 短路签名）
    )
    monkeypatch.setattr(
        "tracks.executor.run_loop.decide",
        lambda s: Command(
            kind="commit_green", params={"stage": "M-IMPL", "task_id": "T1"}
        ),
    )
    with pytest.raises(CommandStallError, match="commit_green"):
        ex.run_loop()
    events = list(store.events(run_id))
    assert events[-1].type == "loop.aborted"
    assert events[-1].payload["reason"] == "command_stall"
    assert "kind=commit_green" in events[-1].payload["detail"]
    # 恰好发出 limit 个 command.issued 后停车（+1 个 loop.aborted）
    issued = [e for e in events if e.type == "command.issued"]
    assert len(issued) == STALL_COMMAND_LIMIT


def test_run_loop_rotating_commands_no_trip(tmp_path, monkeypatch):
    """正常轮换命令（不同 kind 交替）不熔断；decide 最终返回 None 正常收尾。"""
    ex, store, run_id = _minimal_executor(tmp_path)
    monkeypatch.setattr(ex, "_do_commit_green", _noop_handler)
    monkeypatch.setattr(ex, "_do_select_task", _noop_handler)
    calls = {"n": 0}
    kinds = ["commit_green", "select_task"]

    def _decide(s):
        calls["n"] += 1
        if calls["n"] > 30:
            return None
        kind = kinds[calls["n"] % 2]
        return Command(kind=kind, params={"stage": "M-IMPL", "task_id": "T1"})

    monkeypatch.setattr("tracks.executor.run_loop.decide", _decide)
    state = ex.run_loop()  # 不抛错，正常返回
    assert state is not None
    events = list(store.events(run_id))
    assert not [e for e in events if e.type == "loop.aborted"]


def test_run_loop_no_trip_when_progress_resets(tmp_path, monkeypatch):
    """同 kind 命令之间夹进展事件 → 计数清零，不熔断。"""
    ex, store, run_id = _minimal_executor(tmp_path)
    calls = {"n": 0, "issues": 0}

    def _do_commit_green(cmd, state, task_id, reconcile):
        calls["issues"] += 1
        if calls["issues"] % 2 == 0:
            ex._emit("verdict.failed", {"check": "impl_defect", "task_id": "T1"})
        return None

    monkeypatch.setattr(ex, "_do_commit_green", _do_commit_green)

    def _decide(s):
        calls["n"] += 1
        if calls["n"] > 25:
            return None
        return Command(kind="commit_green", params={"task_id": "T1"})

    monkeypatch.setattr("tracks.executor.run_loop.decide", _decide)
    state = ex.run_loop()  # 不抛错、不熔断，正常收尾
    assert state is not None
    events = list(store.events(run_id))
    assert not [e for e in events if e.type == "loop.aborted"]


def test_run_loop_aborts_banner_actionable(tmp_path, monkeypatch, capsys):
    """banner 含处置指引（kill 后带证据开 issue）。"""
    ex, store, run_id = _minimal_executor(tmp_path)
    monkeypatch.setattr(ex, "_do_select_task", _noop_handler)
    monkeypatch.setattr(
        "tracks.executor.run_loop.decide",
        lambda s: Command(kind="select_task", params={"stage": "M-IMPL"}),
    )
    with pytest.raises(CommandStallError):
        ex.run_loop()
    err = capsys.readouterr().err
    assert "command stall detected" in err
    assert "kind=select_task" in err
    assert "tight-loop" in err


# -- 红绿：limit 改大 → 集成测试失败 -----------------------------------------


def test_limit_raise_still_runs_forever_signals_disabled_guard(tmp_path, monkeypatch):
    """禁用/调大 limit 后同一紧循环不再被熔断：decide 限制轮数后返回 None，
    run_loop 正常收尾且无 loop.aborted——与默认阈值行为形成红绿对照。"""
    ex, store, run_id = _minimal_executor(tmp_path)
    monkeypatch.setattr(ex, "_do_commit_green", _noop_handler)
    monkeypatch.setattr(
        "tracks.executor.run_loop.STALL_COMMAND_LIMIT",
        1000,
    )
    calls = {"n": 0}

    def _decide(s):
        calls["n"] += 1
        # 默认阈值 20 早就熔断；这里 limit=1000 下 25 轮仍不熔断 → 正常收尾。
        if calls["n"] > 25:
            return None
        return Command(kind="commit_green", params={"task_id": "T1"})

    monkeypatch.setattr("tracks.executor.run_loop.decide", _decide)
    state = ex.run_loop()
    assert state is not None
    events = list(store.events(run_id))
    assert not [e for e in events if e.type == "loop.aborted"]


# -- review follow-ups (M1/M2) -------------------------------------------------


def test_m1_cli_run_exits_1_on_command_stall(tmp_path, monkeypatch, capsys):
    """M1: cmd_run must translate CommandStallError into exit 1 with the
    stall banner (no traceback), mirroring the B43 drift-abort chain."""
    import tracks.cli.main as cli_main
    import tracks.cli.run_cmd as cli_run
    from tracks.executor.stall import CommandStallError

    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setattr(cli_run.paths, "tracks_home", lambda r: tmp_path / "home")

    class _FakeState:
        status = "active"
        version = "v0.5"

    class _FakeStore:
        def active_run(self):
            return "RUN"

        def active_hotfix_run(self):
            return None

        def state(self, run_id):
            return _FakeState()

    monkeypatch.setattr(cli_run, "Store", lambda home: _FakeStore())

    def _boom(self):
        raise CommandStallError("command stall detected: kind=commit_green count=20")

    class _FakeExecutor:
        def __init__(self, store, repo, run_id, **kwargs):
            pass

        def run_loop(self):
            _boom(None)

    monkeypatch.setattr(cli_run, "Executor", _FakeExecutor)
    rc = cli_main.cmd_run(repo)
    out = capsys.readouterr()
    assert rc == 1
    assert "command stall" in (out.err + out.out)
    assert "Traceback" not in (out.err + out.out)


def test_m2_dispatch_agent_interleave_does_not_reset_count(tmp_path):
    """M2: dispatch_agent is neither counted NOR a progress reset — a
    dispatch sandwiched inside a commit_green tight loop must not defer
    the trip (the dispatch's own budget covers repeated dispatches)."""
    from tracks.executor.stall import STALL_COMMAND_LIMIT, CommandStallTracker

    tracker = CommandStallTracker()
    half = STALL_COMMAND_LIMIT // 2

    def _cmd(kind, cid, task_id=None):
        params = {"task_id": task_id} if task_id else {}
        return {"command": {"kind": kind, "command_id": cid, "params": params}}

    for i in range(half):
        tracker.observe("command.issued", _cmd("commit_green", f"C{i}", "T-1"))
    tracker.observe("command.issued", _cmd("dispatch_agent", "D1"))
    for i in range(half, STALL_COMMAND_LIMIT):
        tracker.observe("command.issued", _cmd("commit_green", f"C{i}", "T-1"))
    assert tracker.should_trip() is True
