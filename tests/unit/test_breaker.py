"""B44（#46）：run 级熔断器单元测试。

r1 实证：最后 10 小时零任务进展仍在消耗（3 回滚 + 空转 + 整体作废 +
无限重派），无机制叫停。合同：rollbacks/同 task 失败/累计派发任一越限
→ run.breaker_tripped → awaiting_human/escalation + 损耗报告；human.retry
重置计数窗口。
"""


from tracks.executor.breaker import RunBreaker
from tracks.executor.executor import Executor
from tracks.kernel.events import EVENT_TYPES
from tracks.kernel.machine import project
from tracks.store import Store


def _note_seq(breaker, events):
    for type, payload in events:
        breaker.note(type, payload)
    return breaker


def _dispatch_payload():
    return {"command": {"kind": "dispatch_agent", "params": {}, "command_id": "C"}}


# -- 计数与窗口 ---------------------------------------------------------------


def test_note_counts_rollbacks_dispatches_failures():
    b = RunBreaker()
    _note_seq(
        b,
        [
            ("command.issued", _dispatch_payload()),
            ("command.issued", {"command": {"kind": "validate_document"}}),  # 非派发
            ("stage.rolled_back", {"reason": "stub_gap"}),
            ("verdict.failed", {"failure_class": "impl_defect", "task_id": "T-003"}),
            ("verdict.failed", {"failure_class": "impl_defect", "task_id": "T-003"}),
            ("verdict.failed", {"check": "manifest", "task_id": "T-007"}),
        ],
    )
    assert b.rollbacks == 1
    assert b.dispatches == 1
    assert b.verdict_failed == 3
    assert b.task_failures == {"T-003": 2, "T-007": 1}
    assert b.failure_classes == {"impl_defect": 2, "manifest": 1}


def test_human_retry_resets_window():
    b = RunBreaker()
    _note_seq(
        b,
        [
            ("stage.rolled_back", {}),
            ("stage.rolled_back", {}),
            ("command.issued", _dispatch_payload()),
            ("human.retry", {"actor": "Maestro"}),
            ("command.issued", _dispatch_payload()),
        ],
    )
    assert b.rollbacks == 0  # retry 清零
    assert b.dispatches == 1  # 只计 retry 之后


def test_human_recover_also_resets_window():
    b = RunBreaker()
    _note_seq(b, [("stage.rolled_back", {}), ("human.recover", {}), ("stage.rolled_back", {})])
    assert b.rollbacks == 1


def test_from_events_seeds_from_event_log():
    class Ev:
        def __init__(self, type, payload):
            self.type = type
            self.payload = payload

    events = [
        Ev("command.issued", _dispatch_payload()),
        Ev("stage.rolled_back", {}),
        Ev("human.retry", {}),
        Ev("command.issued", _dispatch_payload()),
        Ev("command.issued", _dispatch_payload()),
        Ev("stage.rolled_back", {}),
    ]
    b = RunBreaker.from_events(events)
    assert b.dispatches == 2  # 只计 retry 之后的两次
    assert b.rollbacks == 1


# -- 熔断判定 -------------------------------------------------------------------


def test_trips_on_rollback_threshold(monkeypatch):
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "2")
    b = _note_seq(RunBreaker(), [("stage.rolled_back", {}), ("stage.rolled_back", {})])
    trip = b.evaluate()
    assert trip is not None
    assert trip["condition"] == "rollbacks>=2"
    assert "dispatching halted" in trip["report"]
    assert trip["failure_classes"] == {}


def test_trips_on_per_task_failures(monkeypatch):
    monkeypatch.setenv("TRAC_BREAKER_TASK_FAILURES", "3")
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "0")
    b = RunBreaker()
    for _ in range(3):
        b.note("verdict.failed", {"failure_class": "stub_gap", "task_id": "T-003"})
    trip = b.evaluate()
    assert trip["condition"] == "task_failures:T-003>=3"
    assert trip["task_failures"] == {"T-003": 3}
    assert trip["failure_classes"] == {"stub_gap": 3}


def test_trips_on_dispatch_cap(monkeypatch):
    monkeypatch.setenv("TRAC_BREAKER_DISPATCHES", "3")
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "0")
    monkeypatch.setenv("TRAC_BREAKER_TASK_FAILURES", "0")
    b = RunBreaker()
    for _ in range(3):
        b.note("command.issued", _dispatch_payload())
    assert b.evaluate()["condition"] == "dispatches>=3"


def test_disabled_conditions_never_trip(monkeypatch):
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "0")
    monkeypatch.setenv("TRAC_BREAKER_TASK_FAILURES", "0")
    monkeypatch.setenv("TRAC_BREAKER_DISPATCHES", "0")
    b = _note_seq(RunBreaker(), [("stage.rolled_back", {})] * 10)
    for _ in range(10):
        b.note("verdict.failed", {"task_id": "T-1"})
        b.note("command.issued", _dispatch_payload())
    assert b.evaluate() is None


def test_below_thresholds_no_trip(monkeypatch):
    monkeypatch.delenv("TRAC_BREAKER_ROLLBACKS", raising=False)
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "2")
    b = _note_seq(RunBreaker(), [("stage.rolled_back", {})])
    assert b.evaluate() is None


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TRAC_BREAKER_ROLLBACKS", "not-a-number")
    b = _note_seq(RunBreaker(), [("stage.rolled_back", {}), ("stage.rolled_back", {})])
    assert b.evaluate()["condition"] == "rollbacks>=2"


# -- machine reducer + 事件注册 ---------------------------------------------------


def test_breaker_event_registered_and_reducer_parks_run(tmp_path):
    assert "run.breaker_tripped" in EVENT_TYPES
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    store.append(
        "RUN",
        "v0.1",
        "run.breaker_tripped",
        {"condition": "rollbacks>=2", "report": "loss report text"},
    )
    state = project(store.events("RUN"))
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"
    assert state.last_failure["check"] == "run_breaker"
    assert state.last_failure["reason"] == "rollbacks>=2"
    assert state.last_failure["evidence"] == "loss report text"


def test_executor_seeds_breaker_from_history(tmp_path):
    """进程重启后计数不丢：历史 rollback 立即触发熔断（默认阈值 2）。"""
    repo = tmp_path / "host"
    repo.mkdir()
    store = Store(repo / ".tracks")
    run_id = "RUN-BRK"
    store.append(run_id, "v0.1", "story.requested", {"raw_chars": 1})
    store.append(run_id, "v0.1", "stage.entered", {"stage": "M-STORY"})
    store.append(
        run_id, "v0.1", "stage.rolled_back", {"from_stage": "M-IMPL", "reason": "stub_gap", "to_stage": "M-DESIGN"}
    )
    store.append(
        run_id, "v0.1", "stage.rolled_back", {"from_stage": "M-IMPL", "reason": "stub_gap", "to_stage": "M-DESIGN"}
    )
    ex = Executor(store, repo, run_id)
    assert ex._breaker.rollbacks == 2

    class _Stub:
        def act(self, *a, **k):
            return {"status": "failed", "failure_class": "agent_failed"}

    ex.backend = _Stub()
    state = ex.run_loop()  # 首轮即熔断，零派发
    assert state.status == "awaiting_human"
    assert state.awaiting == "escalation"
    trips = [e for e in store.events(run_id) if e.type == "run.breaker_tripped"]
    assert len(trips) == 1
    assert trips[0].payload["condition"] == "rollbacks>=2"
    # 零派发：熔断先于任何 command.issued
    assert not [
        e
        for e in store.events(run_id)
        if e.type == "command.issued"
        and e.payload.get("command", {}).get("kind") == "dispatch_agent"
    ]
