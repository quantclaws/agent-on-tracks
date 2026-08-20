"""D-39 用户简化版（#44）session 复用：纯函数与持久化单元测试。

集成级（真 subprocess 管线 + fake opencode stand-in）见
``tests/integration/test_session_reuse.py``。
"""

import json

from tracks.effects.opencode import OpencodeBackend


def _backend(tmp_path, run_id="RUN-1"):
    return OpencodeBackend(tmp_path, "v0.1", run_id=run_id)


# -- _extract_session_id -------------------------------------------------------


def test_extract_session_id_top_level():
    stream = (
        '{"type": "step_start", "sessionID": "ses_abc"}\n'
        '{"type": "step_finish", "sessionID": "ses_abc"}\n'
    )
    assert OpencodeBackend._extract_session_id(stream) == "ses_abc"


def test_extract_session_id_inside_part():
    stream = '{"type": "text", "part": {"sessionID": "ses_part", "text": "x"}}\n'
    assert OpencodeBackend._extract_session_id(stream) == "ses_part"


def test_extract_session_id_tolerates_log_noise_and_truncation():
    stream = (
        "[Opencode Logger] Plugin initialized!\n"
        '{"type": "step_start", "sessionID": "ses_x"}\n'
        '{"type": "tool_use", "partial":'  # 截断行：跳过，不崩
    )
    assert OpencodeBackend._extract_session_id(stream) == "ses_x"


def test_extract_session_id_absent_returns_none():
    assert OpencodeBackend._extract_session_id('{"type": "step_start"}\n') is None
    assert OpencodeBackend._extract_session_id("") is None
    assert OpencodeBackend._extract_session_id("not json at all") is None


# -- _session_miss / _context_overflow ------------------------------------------


def test_session_miss_detection():
    assert OpencodeBackend._session_miss("Error: Session not found", "") is True
    assert OpencodeBackend._session_miss("", "error: session NOT FOUND\n") is True
    assert OpencodeBackend._session_miss('{"type": "step_start"}', "") is False


def test_context_overflow_detection():
    for text in (
        "maximum context length exceeded",
        "prompt is too long",
        "context window full",
        "input too long for model",
    ):
        assert OpencodeBackend._context_overflow("", text) is True, text
    assert OpencodeBackend._context_overflow("", "provider 429 rate limit") is False


# -- 持久化 roundtrip -------------------------------------------------------------


def test_record_session_persists_across_backend_instances(tmp_path):
    b1 = _backend(tmp_path)
    b1._record_session("Devon", "ses_d1")
    # 新实例（模拟 trac run 进程重启）：文件是共享载体
    b2 = _backend(tmp_path)
    assert b2._session_for("Devon") == "ses_d1"
    cmd = b2._build_cmd("Devon", "do work")
    assert cmd[cmd.index("--session") + 1] == "ses_d1"


def test_record_session_idempotent_no_rewrite(tmp_path):
    b = _backend(tmp_path)
    b._record_session("Devon", "ses_d1")
    path = b._sessions_path()
    first_mtime = path.stat().st_mtime_ns
    b._record_session("Devon", "ses_d1")  # 同 id：不重写文件
    assert path.stat().st_mtime_ns == first_mtime
    b._record_session("Devon", "ses_d2")  # 新 id：更新
    assert b._session_for("Devon") == "ses_d2"


def test_clear_session_drops_entry(tmp_path):
    b = _backend(tmp_path)
    b._record_session("Devon", "ses_d1")
    b._clear_session("Devon")
    assert b._session_for("Devon") is None
    # 再实例化确认文件层面已删
    assert _backend(tmp_path)._session_for("Devon") is None


def test_corrupted_session_file_fails_open(tmp_path):
    path = _backend(tmp_path)._sessions_path()
    path.write_text("{ not json", encoding="utf-8")
    b = _backend(tmp_path)
    assert b._session_for("Devon") is None
    b._record_session("Devon", "ses_new")  # 损坏后可正常重建
    assert _backend(tmp_path)._session_for("Devon") == "ses_new"


# -- 开关 --------------------------------------------------------------------------


def test_run_id_none_disables_reuse(tmp_path):
    b = OpencodeBackend(tmp_path, "v0.1")  # 无 run_id：旧行为
    assert b._session_reuse_enabled() is False
    b._record_session("Devon", "ses_d1")  # no-op
    b._clear_session("Devon")  # no-op
    assert "--session" not in b._build_cmd("Devon", "work")
    # 不落任何 session 文件
    assert not (tmp_path / ".tracks" / "runtime" / "sessions").exists()


def test_env_kill_switch_disables_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_SESSION_REUSE", "0")
    b = _backend(tmp_path)
    assert b._session_reuse_enabled() is False
    assert "--session" not in b._build_cmd("Devon", "work")


def test_sessions_file_content_shape(tmp_path):
    b = _backend(tmp_path)
    b._record_session("Devon", "ses_d1")
    b._record_session("Shield", "ses_s1")
    data = json.loads(b._sessions_path().read_text(encoding="utf-8"))
    assert data == {"Devon": "ses_d1", "Shield": "ses_s1"}
