"""D-39 用户简化版（#44）session 复用：act() 级集成行为（L2 stand-in）。

复用 ``test_opencode_backend.STANDIN`` 的 fake opencode（``session_stateful``
行为）：真 subprocess 管线（_build_cmd → spawn → JSON 流 → _check_json）全
程真实，只有 opencode 本体是替身。
"""

import json
import os
import stat

import pytest

from tests.integration.test_opencode_backend import STANDIN
from tracks.effects.opencode import OpencodeBackend

RUN_ID = "RUN-SESSION-TEST"


@pytest.fixture
def fake_opencode(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "opencode"
    exe.write_text(STANDIN, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "session-argv.jsonl"


def _backend(host_repo):
    return OpencodeBackend(host_repo, "v0.2", run_id=RUN_ID)


def _invocations(marker):
    return [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines() if line]


def _session_args(argv):
    return argv[argv.index("--session") + 1] if "--session" in argv else None


def _dispatch(backend, marker, behavior="session_stateful"):
    os.environ["FAKE_OPENCODE_BEHAVIOR"] = behavior
    os.environ["FAKE_OPENCODE_SESSION_MARKER"] = str(marker)
    try:
        return backend.act("scribe", "TRIAGE", "story.md", None)
    finally:
        os.environ.pop("FAKE_OPENCODE_BEHAVIOR", None)
        os.environ.pop("FAKE_OPENCODE_SESSION_MARKER", None)


def test_second_dispatch_continues_recorded_session(
    fake_opencode, host_repo, monkeypatch, marker
):
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP", raising=False)
    b = _backend(host_repo)
    out1 = _dispatch(b, marker)
    out2 = _dispatch(b, marker)
    assert out1["status"] == "done" and out2["status"] == "done"
    invocations = _invocations(marker)
    assert len(invocations) == 2
    assert _session_args(invocations[0]) is None  # 首次：无 session → 全新
    assert _session_args(invocations[1]) == "ses_fake_stable_0001"  # 续传
    # 持久化文件按 run_id 落盘，agent 键为 opencode 名（Scribe）
    data = json.loads(
        (host_repo / ".tracks" / "runtime" / "sessions" / f"{RUN_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert data == {"Scribe": "ses_fake_stable_0001"}


def test_stale_session_falls_back_to_fresh_dispatch(
    fake_opencode, host_repo, monkeypatch, marker
):
    """--session 指向不存在的会话 → 降级全新派发一次，并记录新 id。"""
    monkeypatch.setenv("FAKE_OPENCODE_SESSION_DROP", "1")
    b = _backend(host_repo)
    b._record_session("Scribe", "ses_stale_dead")  # 预置过期 session
    out = _dispatch(b, marker)
    assert out["status"] == "done"
    invocations = _invocations(marker)
    assert len(invocations) == 2
    assert _session_args(invocations[0]) == "ses_stale_dead"  # 先尝试续传
    assert _session_args(invocations[1]) is None  # miss → 弃用，全新派发
    # 新 session 已入账：下一次派发续传新 id
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP")
    _dispatch(b, marker)
    invocations = _invocations(marker)
    assert _session_args(invocations[2]) == "ses_fake_stable_0001"


def test_session_survives_backend_restart(
    fake_opencode, host_repo, monkeypatch, marker
):
    """trac run 进程重启（新 backend 实例）后续传同一 session。"""
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP", raising=False)
    _dispatch(_backend(host_repo), marker)
    _dispatch(_backend(host_repo), marker)  # 新实例：从文件读取 session
    invocations = _invocations(marker)
    assert _session_args(invocations[0]) is None
    assert _session_args(invocations[1]) == "ses_fake_stable_0001"


def test_run_id_none_keeps_legacy_dispatch(
    fake_opencode, host_repo, monkeypatch, marker
):
    """无 run_id：永不携带 --session、不落 sessions 文件（旧行为逐字节）。"""
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP", raising=False)
    b = OpencodeBackend(host_repo, "v0.2")  # 无 run_id
    _dispatch(b, marker)
    _dispatch(b, marker)
    invocations = _invocations(marker)
    assert all(_session_args(argv) is None for argv in invocations)
    assert not (host_repo / ".tracks" / "runtime" / "sessions").exists()


def test_prose_with_trigger_phrases_is_content_not_signal(
    fake_opencode, host_repo, monkeypatch, marker
):
    """Prism review B1 反例：成功 JSON 流的 agent 正文含 "session not
    found"/"context window"——不触发清 session、不触发重派。"""
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP", raising=False)
    b = _backend(host_repo)
    _dispatch(b, marker, behavior="session_prose")
    _dispatch(b, marker, behavior="session_prose")
    invocations = _invocations(marker)
    # 两次派发各一次调用：无健康检查重派
    assert len(invocations) == 2
    # session 正常记录并续传（未被误清）
    assert _session_args(invocations[0]) is None
    assert _session_args(invocations[1]) == "ses_fake_prose_0001"


def test_run_id_none_with_trigger_prose_dispatches_exactly_once(
    fake_opencode, host_repo, monkeypatch, marker
):
    """B1 不变量反例：run_id=None + 正文含触发短语 → 恰好一次派发
    （旧版单次派发不变量，健康检查完全跳过）。"""
    monkeypatch.delenv("FAKE_OPENCODE_SESSION_DROP", raising=False)
    b = OpencodeBackend(host_repo, "v0.2")  # 无 run_id
    _dispatch(b, marker, behavior="session_prose")
    invocations = _invocations(marker)
    assert len(invocations) == 1
    assert _session_args(invocations[0]) is None
