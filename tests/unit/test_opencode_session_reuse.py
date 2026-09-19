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


# -- _miss_line / _overflow_text（Prism review B1：内容不开火） -------------------


def test_miss_line_exact_signature():
    assert OpencodeBackend._miss_line("Error: Session not found") is True
    assert OpencodeBackend._miss_line("error: session NOT FOUND\r\n") is True
    # 行级精确：agent 正文提及（评审/文档讨论）不触发
    assert OpencodeBackend._miss_line("we discussed 'session not found' cases") is False
    assert OpencodeBackend._miss_line("Error: Session not found: extra") is False
    assert OpencodeBackend._miss_line("") is False


def test_overflow_text_stderr_always_scanned():
    for text in (
        "maximum context length exceeded",
        "prompt is too long",
        "context window full",
        "input too long for model",
    ):
        assert OpencodeBackend._overflow_text("", text, json_stream=True) is True, text
    assert OpencodeBackend._overflow_text("", "provider 429 rate limit", True) is False


def test_overflow_text_ignores_agent_prose_in_json_stream():
    """B1 反例：成功 JSON 流的正文（含 "context window"）不是信号。"""
    prose = '{"type":"text","part":{"text":"the context window is 128k"}}'
    assert OpencodeBackend._overflow_text(prose, "", json_stream=True) is False


def test_overflow_text_scans_stdout_when_not_json():
    """非 JSON stdout（错误输出形态）参与扫描。"""
    assert OpencodeBackend._overflow_text("Error: prompt is too long", "", False) is True


# -- 持久化 roundtrip -------------------------------------------------------------


def test_record_session_persists_across_backend_instances(tmp_path):
    b1 = _backend(tmp_path)
    b1._record_session("Devon", "ses_d1")
    # 新实例（模拟 trac run 进程重启）：文件是共享载体
    b2 = _backend(tmp_path)
    assert b2._session_for("Devon")["session_id"] == "ses_d1"
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
    assert b._session_for("Devon")["session_id"] == "ses_d2"


def test_clear_session_drops_entry(tmp_path):
    b = _backend(tmp_path)
    b._record_session("Devon", "ses_d1")
    b._clear_session("Devon")
    assert b._session_for("Devon") is None
    # 再实例化确认文件层面已删
    assert _backend(tmp_path)._session_for("Devon") is None


# -- _recover_run_error：non_zero_exit 弃用毒化会话（2026-09-18 run 01M2QTJB） ----


def test_recover_non_zero_exit_clears_session(tmp_path):
    """复用会话的进程硬崩（exit≠0）与 miss/overflow 同级失效：弃用，下次冷启动。"""
    from tracks.effects.opencode_core import OpencodeError

    b = _backend(tmp_path)
    b._record_session("Archer", "ses_poison")
    b._recover_run_error(
        OpencodeError("non_zero_exit", "opencode exited 1"), "Archer"
    )
    assert b._session_for("Archer") is None
    assert _backend(tmp_path)._session_for("Archer") is None


def test_recover_transient_infra_failure_keeps_session(tmp_path):
    """timeout/provider/signal/json_truncated 等瞬态错误仍走 D-39 续传。"""
    from tracks.effects.opencode_core import OpencodeError

    b = _backend(tmp_path)
    b._record_session("Archer", "ses_live")
    for cls in ("timeout", "provider_unavailable", "signal", "json_truncated"):
        b._recover_run_error(OpencodeError(cls, "x"), "Archer")
    assert b._session_for("Archer")["session_id"] == "ses_live"


def test_corrupted_session_file_fails_open(tmp_path):
    path = _backend(tmp_path)._sessions_path()
    path.write_text("{ not json", encoding="utf-8")
    b = _backend(tmp_path)
    assert b._session_for("Devon") is None
    b._record_session("Devon", "ses_new")  # 损坏后可正常重建
    assert _backend(tmp_path)._session_for("Devon")["session_id"] == "ses_new"


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
    assert data["Devon"]["session_id"] == "ses_d1"
    assert data["Shield"]["session_id"] == "ses_s1"
    assert data["Devon"]["ctx_tokens"] is None


# -- D-42 会话生命周期：工作单元键 / 压缩闭环 --------------------------------------


def test_session_key_work_units():
    """键=(role, work_unit, task_id)：RGR 同任务同键、Shield 写作单元、
    task 后缀、其余按 substate。"""
    from tracks.effects.opencode_session import _session_key

    task = {"task": {"task_id": "T-001"}}
    assert _session_key("devon", "RED", task) == "devon:task:T-001"
    assert _session_key("devon", "GREEN", task) == "devon:task:T-001"
    assert _session_key("devon", "REFACTOR", task) == "devon:task:T-001"
    assert _session_key("devon", "RED", None) == "devon:task:-"
    assert _session_key("shield", "WRITE", None) == "shield:mtest-write"
    assert _session_key("shield", "NO_DIFF_EXPLAIN", None) == "shield:mtest-write"
    assert _session_key("shield", "SHIELD_FIX", task) == "shield:SHIELD_FIX:T-001"
    assert _session_key("archer", "DRAFT", None) == "archer:design"
    assert _session_key("archer", "RESPOND", None) == "archer:design"
    assert _session_key("archer", "PLANNING", None) == "archer:PLANNING"
    assert _session_key("prism", "PRISM_RED", task) == "prism:PRISM_RED:T-001"
    assert _session_key("prism", "NO_DIFF_REVIEW", None) == "prism:NO_DIFF_REVIEW"


def test_legacy_string_registry_entries_are_dropped(tmp_path):
    """#44 旧格式（纯字符串值）在加载时丢弃：冷启动 + runtime 重注入。"""
    path = _backend(tmp_path)._sessions_path()
    path.write_text(
        json.dumps({"Archer": "ses_legacy", "Prism": "ses_keep"}),
        encoding="utf-8",
    )
    b = _backend(tmp_path)
    assert b._session_for("Archer") is None
    assert b._session_for("Prism") is None


def test_last_ctx_tokens_from_step_finish_stream():
    from tracks.effects.opencode_session import _last_ctx_tokens

    stream = (
        '{"type":"step_start","sessionID":"s"}\n'
        '{"type":"step_finish","sessionID":"s","part":{"tokens":{"input":100}}}\n'
        '{"type":"step_finish","sessionID":"s","part":{"tokens":{"input":749019,"output":127}}}\n'
    )
    assert _last_ctx_tokens(stream) == 749019
    assert _last_ctx_tokens("") is None
    assert _last_ctx_tokens('{"type":"text","part":{"text":"x"}}') is None


def test_record_ctx_tokens_drops_session_when_compact_did_not_shrink(tmp_path):
    """压缩后尺寸未缩（>= 前值）= 压缩无效信号：弃会话，杜绝压缩死循环。"""
    b = _backend(tmp_path)
    b._record_session("devon:task:T-001", "ses_b")
    b._record_ctx_tokens("devon:task:T-001", 900_000)
    entry = b._session_for("devon:task:T-001")
    entry["compacted"] = True
    b._save_sessions()
    b._record_ctx_tokens("devon:task:T-001", 950_000)  # 没缩反而涨
    assert b._session_for("devon:task:T-001") is None


def test_record_ctx_tokens_resets_compacted_flag_after_shrink(tmp_path):
    b = _backend(tmp_path)
    b._record_session("k", "ses_c")
    b._record_ctx_tokens("k", 900_000)
    entry = b._session_for("k")
    entry["compacted"] = True
    b._save_sessions()
    b._record_ctx_tokens("k", 100_000)  # 压缩生效
    assert b._session_for("k")["compacted"] is False
    assert b._session_for("k")["ctx_tokens"] == 100_000


def test_maybe_compact_compacts_at_threshold(tmp_path, monkeypatch):
    """达到 85% 阈值触发压缩并标记；压缩失败弃会话。"""
    b = _backend(tmp_path)
    calls = []

    def fake_compact(sid):
        calls.append(sid)
        return True

    monkeypatch.setattr(b, "_run_compact", fake_compact)
    monkeypatch.setattr(
        b, "_model_ctx_limits", lambda: {"napi/hy4": 1_000_000}
    )
    b._record_session("devon:task:T-001", "ses_d")
    b._record_ctx_tokens("devon:task:T-001", 860_000)  # >= 85% of 1M
    b._maybe_compact("devon:task:T-001", "napi/hy4")
    assert calls == ["ses_d"]
    assert b._session_for("devon:task:T-001")["compacted"] is True

    # 压缩失败 → 弃会话
    b._record_session("devon:task:T-002", "ses_e")
    b._record_ctx_tokens("devon:task:T-002", 860_000)
    monkeypatch.setattr(b, "_run_compact", lambda sid: False)
    b._maybe_compact("devon:task:T-002", "napi/hy4")
    assert b._session_for("devon:task:T-002") is None


def test_maybe_compact_below_threshold_or_unknown_model_is_noop(tmp_path, monkeypatch):
    b = _backend(tmp_path)
    monkeypatch.setattr(
        b, "_model_ctx_limits", lambda: {"napi/hy4": 1_000_000}
    )
    monkeypatch.setattr(b, "_run_compact", lambda sid: (_ for _ in ()).throw(AssertionError))
    b._record_session("k", "ses_f")
    b._record_ctx_tokens("k", 500_000)  # 50%：不动
    b._maybe_compact("k", "napi/hy4")
    assert b._session_for("k")["session_id"] == "ses_f"
    b._maybe_compact("k", None)  # 模型未知：fail-open 不压缩
    assert b._session_for("k")["session_id"] == "ses_f"


def test_maybe_compact_clears_when_already_compacted_at_size(tmp_path, monkeypatch):
    """上次压缩未把尺寸压回阈值下：本次直接弃会话，不再压缩。"""
    b = _backend(tmp_path)
    monkeypatch.setattr(b, "_model_ctx_limits", lambda: {"m": 1_000_000})
    b._record_session("k", "ses_g")
    entry = b._session_for("k")
    entry["ctx_tokens"] = 860_000
    entry["compacted"] = True
    b._save_sessions()
    b._maybe_compact("k", "m")
    assert b._session_for("k") is None


def test_effective_model_reads_per_agent_config(tmp_path):
    b = _backend(tmp_path)
    cfg = tmp_path / ".opencode"
    cfg.mkdir()
    (cfg / "opencode.json").write_text(
        json.dumps({"agent": {"Archer": {"model": "napi/hy4"}}}),
        encoding="utf-8",
    )
    assert b._effective_model("Archer") == "napi/hy4"
    assert b._effective_model("Unknown") is None
    b.model = "explicit/override"
    assert b._effective_model("Archer") == "explicit/override"
