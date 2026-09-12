"""Behavior coverage for the Opencode subprocess/JSON mixin.

Drives spawn/await/finalize failure windows, stdin/stderr pumps, stdout
streaming (manifest detection, drain, inactivity stall), exit
classification, manifest extraction/validation and the lenient JSON
helpers (ARCH §7, B17/#20, B18, D-39).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracks.effects import opencode_run as run_mod
from tracks.effects.opencode_core import OpencodeError
from tracks.effects.opencode_run import OpencodeRunMixin
from tracks.effects.opencode_session import OpencodeSessionMixin


class _Host(OpencodeSessionMixin, OpencodeRunMixin):
    def __init__(self, tmp_path: Path, *, model=None, debug=False, run_id="RUN"):
        self.repo = tmp_path
        self.run_id = run_id
        self.model = model
        self.debug = debug
        self._sessions = None

    def _capture_io(self, proc, prompt, console_input):
        return {
            "stdout": getattr(proc, "stdout", None),
            "prompt": prompt,
            "console": console_input,
        }


class _Proc:
    def __init__(self, *, poll_value=None, returncode=0, stdout="", stderr=""):
        self.pid = 4242
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self._poll_value = poll_value
        self.waited = False

    def poll(self):
        return self._poll_value


@pytest.fixture
def host(tmp_path):
    return _Host(tmp_path)


def _manifest_line(msg="do it"):
    payload = {
        "artifact_manifest": {
            "include": [{"path": "a.py", "kind": "code", "role": "impl"}]
        },
        "suggested_commit_message": msg,
    }
    event = {"type": "text", "part": {"text": json.dumps(payload)}}
    return json.dumps(event) + "\n"


# ---------------------------------------------------------------------------
# _run / _await_run / _finalize_run
# ---------------------------------------------------------------------------


def test_run_spawn_error_closes_log(host, tmp_path, monkeypatch):
    log = tmp_path / "debug.log"

    def _spawn(*a, **k):
        raise OpencodeError("opencode_missing", "gone")

    host.debug = True
    monkeypatch.setattr(host, "_debug_log_path", lambda name: log)
    monkeypatch.setattr(host, "_spawn", _spawn)
    with pytest.raises(OpencodeError):
        host._run("devon", "prompt")
    assert log.exists()


def test_run_happy_path_wires_pumps(host, monkeypatch):
    proc = _Proc()
    sentinel = subprocess.CompletedProcess(["opencode"], 0, "", "")
    monkeypatch.setattr(host, "_spawn", lambda *a: (proc, None))
    monkeypatch.setattr(host, "_start_stdin_pump", lambda *a: "stdin-writer")
    monkeypatch.setattr(host, "_start_stderr_pump", lambda *a: ("stderr-reader", []))
    captured = {}

    def _await(*args):
        captured["args"] = args
        return sentinel

    monkeypatch.setattr(host, "_await_run", _await)
    assert host._run("devon", "prompt") is sentinel
    assert captured["args"][-1] == 1800


def test_await_run_keyboard_interrupt_closes_log(host, monkeypatch):
    class _Log:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    log = _Log()
    monkeypatch.setattr(host, "_kill_group", lambda pid: None)
    monkeypatch.setattr(host, "_stream_stdout", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        host._await_run("devon", ["opencode"], _Proc(), None, log, None, [], None, None, 10)
    assert log.closed is True


def test_spawn_pty_success_returns_master(host, monkeypatch):
    proc = _Proc()
    closed = []
    monkeypatch.delenv("TRAC_AGENT_PTY", raising=False)
    monkeypatch.setattr(run_mod.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(run_mod.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(run_mod.os, "close", lambda fd: closed.append(fd))
    result, master = host._spawn(["opencode"], {}, None)
    assert result is proc and master == 10
    assert closed == [11]


def test_await_run_keyboard_interrupt_kills_and_reraises(host, monkeypatch):
    killed = []
    monkeypatch.setattr(host, "_kill_group", lambda pid: killed.append(pid))
    monkeypatch.setattr(host, "_stream_stdout", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    proc = _Proc()
    with pytest.raises(KeyboardInterrupt):
        host._await_run("devon", ["opencode"], proc, None, None, None, [], None, None, 10)
    assert killed == [4242]


def test_await_run_finalize_error_recovers_and_raises(host, monkeypatch):
    seen = []
    monkeypatch.setattr(host, "_stream_stdout", lambda *a: ([], False, False))
    monkeypatch.setattr(host, "_finalize_run", lambda *a: (_ for _ in ()).throw(OpencodeError("timeout", "t")))
    monkeypatch.setattr(host, "_recover_run_error", lambda exc, name: seen.append((exc.failure_class, name)))
    with pytest.raises(OpencodeError):
        host._await_run("devon", ["opencode"], _Proc(), None, None, None, [], None, None, 10)
    assert seen == [("timeout", "devon")]


def test_await_run_success_records_session(host, monkeypatch):
    recorded = []
    proc = subprocess.CompletedProcess(["opencode"], 0, '{"sessionID": "s1"}\n', "")
    monkeypatch.setattr(host, "_stream_stdout", lambda *a: ([], False, False))
    monkeypatch.setattr(host, "_finalize_run", lambda *a: proc)
    monkeypatch.setattr(host, "_record_session", lambda name, sid: recorded.append((name, sid)))
    assert host._await_run("devon", ["opencode"], proc, None, None, None, [], None, None, 10) is proc
    assert recorded == [("devon", "s1")]


def test_recover_run_error_records_and_clears_on_overflow(host, monkeypatch):
    recorded, cleared = [], []
    monkeypatch.setattr(host, "_record_session", lambda name, sid: recorded.append((name, sid)))
    monkeypatch.setattr(host, "_clear_session", lambda name: cleared.append(name))
    monkeypatch.setattr(host, "_overflow_text", lambda *a: True)
    exc = OpencodeError("provider_unavailable", "boom", stdout='{"sessionID": "s9"}\n')
    host._recover_run_error(exc, "devon")
    assert recorded == [("devon", "s9")]
    assert cleared == ["devon"]


def test_finalize_run_kills_and_returns_completed(host, monkeypatch):
    killed = []
    monkeypatch.setattr(host, "_kill_group", lambda pid: killed.append(pid))
    proc = _Proc(poll_value=None, returncode=0)
    result = host._finalize_run(
        ["opencode"], proc, None, None, None, ["err-line"], None, None, ["out\n"], True, False, 10
    )
    assert killed == [4242]
    assert result.stdout == "out\n"
    assert result.stderr == "err-line"


def test_finalize_run_reads_log_file(host, tmp_path, monkeypatch):
    log = tmp_path / "d.log"
    log.write_text("logged\n", encoding="utf-8")
    fh = log.open("a")
    monkeypatch.setattr(host, "_kill_group", lambda pid: None)
    proc = _Proc(poll_value=0, returncode=0)
    result = host._finalize_run(
        ["opencode"], proc, None, fh, log, [], None, None, [], True, False, 10
    )
    assert result.stderr == "logged\n"


def test_finalize_run_stall_raises_timeout(host, monkeypatch):
    monkeypatch.setattr(host, "_kill_group", lambda pid: None)
    proc = _Proc(poll_value=0, returncode=0)
    with pytest.raises(OpencodeError) as exc:
        host._finalize_run(
            ["opencode"], proc, None, None, None, [], None, None, ["partial"], False, True, 7
        )
    assert exc.value.failure_class == "timeout"
    assert exc.value.stdout == "partial"


# ---------------------------------------------------------------------------
# spawn / pumps
# ---------------------------------------------------------------------------


def test_spawn_pipe_mode_returns_none_master(host, monkeypatch):
    monkeypatch.setenv("TRAC_AGENT_PTY", "0")
    sentinel = object()
    monkeypatch.setattr(host, "_spawn_pipe", lambda *a: sentinel)
    assert host._spawn(["opencode"], {}, None) == (sentinel, None)


def test_spawn_pty_missing_binary_closes_fds(host, monkeypatch):
    closed = []
    monkeypatch.delenv("TRAC_AGENT_PTY", raising=False)
    monkeypatch.setattr(run_mod.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(run_mod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(run_mod.os, "close", lambda fd: closed.append(fd))
    with pytest.raises(OpencodeError) as exc:
        host._spawn(["opencode"], {}, None)
    assert exc.value.failure_class == "opencode_missing"
    assert 10 in closed and 11 in closed


def test_spawn_pipe_success_and_missing(host, monkeypatch):
    proc = object()
    monkeypatch.setattr(run_mod.subprocess, "Popen", lambda *a, **k: proc)
    assert host._spawn_pipe(["opencode"], {}, None) is proc
    monkeypatch.setattr(run_mod.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(OpencodeError):
        host._spawn_pipe(["opencode"], {}, None)


def test_build_cmd_session_model_debug(host, monkeypatch):
    host.model = "gpt-x"
    host.debug = True
    monkeypatch.setattr(host, "_session_for", lambda name: "s1")
    cmd = host._build_cmd("devon", "prompt")
    assert "--session" in cmd and "s1" in cmd
    assert "--model" in cmd and "gpt-x" in cmd
    assert "--print-logs" in cmd and "DEBUG" in cmd
    assert cmd[-3:] == ["--print-logs", "--log-level", "DEBUG"]
    assert "prompt" in cmd


class _FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


def test_stdin_pump_none_closes_stdin(host):
    proc = SimpleNamespace(stdin=_FakeStdin())
    assert host._start_stdin_pump(proc, None) is None
    assert proc.stdin.closed is True


def test_stdin_pump_thread_writes_to_stdin(host):
    stdin = _FakeStdin()
    proc = SimpleNamespace(stdin=stdin)
    t = host._start_stdin_pump(proc, "hello")
    assert isinstance(t, threading.Thread)
    t.join(timeout=5)
    assert stdin.written == ["hello"]
    assert stdin.closed is True


def test_stdin_pump_pty_master_write(host, monkeypatch):
    written = []
    monkeypatch.setattr(run_mod.os, "write", lambda fd, data: written.append((fd, data)))
    proc = SimpleNamespace()
    t = host._start_stdin_pump(proc, "hi", master_fd=9)
    t.join(timeout=5)
    assert written == [(9, b"hi")]


def test_stdin_pump_swallows_write_errors(host):
    class _BadStdin:
        def write(self, data):
            raise OSError("closed")

        def flush(self):
            raise AssertionError("flush must not run")

        def close(self):
            pass

    t = host._start_stdin_pump(SimpleNamespace(stdin=_BadStdin()), "hi")
    t.join(timeout=5)


def test_stderr_pump_with_log_returns_empty(host):
    assert host._start_stderr_pump(_Proc(), object()) == (None, [])


def test_stderr_pump_reads_chunks(host):
    class _Err:
        def __init__(self):
            self.chunks = ["a", "b", ""]

        def read(self, n):
            return self.chunks.pop(0)

    proc = SimpleNamespace(stderr=_Err())
    reader, lines = host._start_stderr_pump(proc, None)
    reader.join(timeout=5)
    assert lines == ["a", "b"]


def test_stderr_pump_swallows_oserror(host):
    class _Err:
        def read(self, n):
            raise OSError("closed")

    proc = SimpleNamespace(stderr=_Err())
    reader, lines = host._start_stderr_pump(proc, None)
    reader.join(timeout=5)
    assert lines == []


# ---------------------------------------------------------------------------
# _stream_stdout / _process_stdout_chunk / _drain_stdout
# ---------------------------------------------------------------------------


def test_stream_stdout_manifest_found(host, monkeypatch):
    line = _manifest_line()
    monkeypatch.setattr(run_mod.select, "select", lambda *a: ([9], [], []))
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: line.encode())
    chunks, found, stalled = host._stream_stdout(_Proc(), 10, master_fd=9)
    assert found is True and stalled is False
    assert chunks == [line]


def test_stream_stdout_oserror_breaks(host, monkeypatch):
    monkeypatch.setattr(run_mod.select, "select", lambda *a: ([9], [], []))
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: (_ for _ in ()).throw(OSError("gone")))
    assert host._stream_stdout(_Proc(), 10, master_fd=9) == ([], False, False)


def test_stream_stdout_drains_on_exit(host, monkeypatch):
    monkeypatch.setattr(run_mod.select, "select", lambda *a: ([], [], []))
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: b"")
    chunks, found, stalled = host._stream_stdout(_Proc(poll_value=0), 10, master_fd=9)
    assert (chunks, found, stalled) == ([], False, False)


def test_stream_stdout_inactivity_stall(host, monkeypatch):
    monotonic = iter([0.0, 100.0])
    monkeypatch.setattr(run_mod.select, "select", lambda *a: ([], [], []))
    monkeypatch.setattr(run_mod.time, "monotonic", lambda: next(monotonic))
    assert host._stream_stdout(_Proc(poll_value=None), 10, master_fd=9) == ([], False, True)


def test_stream_stdout_keeps_unterminated_tail(host, monkeypatch):
    reads = iter([b"partial", b""])
    monkeypatch.setattr(run_mod.select, "select", lambda *a: ([9], [], []))
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: next(reads))
    monkeypatch.setattr(run_mod.time, "monotonic", lambda: 0.0)
    proc = _Proc(poll_value=0)
    chunks, found, stalled = host._stream_stdout(proc, 10, master_fd=9)
    assert chunks == ["partial"]


def test_process_stdout_chunk_splits_lines_and_normalizes_crlf():
    chunks = []
    buf, found = OpencodeRunMixin._process_stdout_chunk(b"a\r\nb\nrest", "", chunks)
    assert chunks == ["a\n", "b\n"]
    assert buf == "rest"
    assert found is False


def test_process_stdout_chunk_finds_manifest_and_stops():
    chunks = []
    buf, found = OpencodeRunMixin._process_stdout_chunk(
        (_manifest_line() + "after\n").encode(), "", chunks
    )
    assert found is True
    assert buf == "after\n"


def test_line_has_manifest_variants():
    assert OpencodeRunMixin._line_has_manifest("prose") is False
    assert OpencodeRunMixin._line_has_manifest("{not json") is False
    assert OpencodeRunMixin._line_has_manifest(json.dumps({"type": "other"})) is False
    assert OpencodeRunMixin._line_has_manifest(json.dumps({"type": "text"})) is False
    bad_part = json.dumps({"type": "text", "part": "nope"})
    assert OpencodeRunMixin._line_has_manifest(bad_part) is False
    bad_include = json.dumps({"type": "text", "part": {"text": '{"artifact_manifest": {}}'}})
    assert OpencodeRunMixin._line_has_manifest(bad_include) is False
    no_msg = json.dumps(
        {
            "type": "text",
            "part": {
                "text": json.dumps(
                    {
                        "artifact_manifest": {
                            "include": [{"path": "a", "kind": "k", "role": "r"}]
                        }
                    }
                )
            },
        }
    )
    assert OpencodeRunMixin._line_has_manifest(no_msg) is False
    assert OpencodeRunMixin._line_has_manifest(_manifest_line()) == "do it"


def test_drain_stdout_fd_and_pipe_and_oserror(monkeypatch):
    chunks = []
    reads = iter([b"x", b""])
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: next(reads))
    OpencodeRunMixin._drain_stdout(_Proc(), chunks, read_fd=9)
    assert chunks == ["x"]

    chunks = []
    OpencodeRunMixin._drain_stdout(SimpleNamespace(stdout=SimpleNamespace(read=lambda: "tail")), chunks)
    assert chunks == ["tail"]

    chunks = []
    monkeypatch.setattr(run_mod.os, "read", lambda fd, n: (_ for _ in ()).throw(OSError("x")))
    OpencodeRunMixin._drain_stdout(_Proc(), chunks, read_fd=9)
    assert chunks == []


def test_join_pump_threads(host):
    class _T:
        def __init__(self):
            self.joined = False

        def join(self, timeout=None):
            self.joined = True

    writer, reader = _T(), _T()
    host._join_pump_threads(writer, reader, None)
    assert writer.joined and reader.joined
    reader2 = _T()
    host._join_pump_threads(None, reader2, object())
    assert reader2.joined is False


def test_debug_log_path(host):
    path = host._debug_log_path("Devon")
    assert path.parent.name == "log"
    assert path.name.startswith("devon-")
    assert path.parent.exists()


def test_kill_group_suppresses_errors():
    OpencodeRunMixin._kill_group(4000000)
    OpencodeRunMixin._kill_group(None)


# ---------------------------------------------------------------------------
# _check_json and exit classification
# ---------------------------------------------------------------------------


def test_check_json_signal_with_json_events_passes(host):
    proc = subprocess.CompletedProcess([], -9, '{"type":"step_start"}\n{"trunc', "")
    host._check_json(proc)


def test_check_json_signal_without_json_raises(host):
    proc = subprocess.CompletedProcess([], -9, "garbage", "")
    with pytest.raises(OpencodeError) as exc:
        host._check_json(proc)
    assert exc.value.failure_class == "signal"


def test_check_json_provider_and_nonzero(host):
    proc = subprocess.CompletedProcess([], 1, "", "credentials missing")
    with pytest.raises(OpencodeError) as exc:
        host._check_json(proc)
    assert exc.value.failure_class == "provider_unavailable"

    proc = subprocess.CompletedProcess([], 3, "", "strange failure")
    with pytest.raises(OpencodeError) as exc:
        host._check_json(proc)
    assert exc.value.failure_class == "non_zero_exit"


def test_check_json_exit_zero_truncation(host):
    proc = subprocess.CompletedProcess([], 0, '{"partial": ', "")
    with pytest.raises(OpencodeError) as exc:
        host._check_json(proc)
    assert exc.value.failure_class == "json_truncated"


def test_looks_provider_error_and_json_helpers():
    assert OpencodeRunMixin._looks_provider_error("Rate Limit hit") is True
    assert OpencodeRunMixin._looks_provider_error("all good") is False
    assert OpencodeRunMixin._looks_provider_error(None) is False

    assert OpencodeRunMixin._has_json_events('{"bad\n{"ok": 1}') is True
    assert OpencodeRunMixin._has_json_events("no json") is False

    assert OpencodeRunMixin._parses_json("123") is True
    assert OpencodeRunMixin._parses_json("   ") is False
    assert OpencodeRunMixin._parses_json('"log"\n{"type": "x"}') is True
    assert OpencodeRunMixin._parses_json('noise\n{"a": 1}') is True


def test_abnormal_step_finish_variants():
    def _proc(stdout):
        return subprocess.CompletedProcess([], 0, stdout, "")

    assert OpencodeRunMixin._abnormal_step_finish(_proc("")) is False
    assert (
        OpencodeRunMixin._abnormal_step_finish(
            _proc(json.dumps({"type": "step_finish", "part": {"reason": "stop"}}))
        )
        is False
    )
    assert (
        OpencodeRunMixin._abnormal_step_finish(
            _proc(json.dumps({"type": "step_finish", "part": "nope"}))
        )
        is False
    )
    assert (
        OpencodeRunMixin._abnormal_step_finish(
            _proc(json.dumps({"type": "step_finish", "part": {"reason": "unknown"}}))
        )
        is True
    )


def test_abnormal_step_result_and_manifest_malformed(host):
    proc = subprocess.CompletedProcess([], 0, "", "quota exceeded")
    result = host._abnormal_step_result(proc, "p", None)
    assert result["failure_class"] == "abnormal_step_finish"
    assert result["agent_io"]["prompt"] == "p"

    provider = host._manifest_malformed_result("bad manifest", proc, "p", None)
    assert provider["failure_class"] == "provider_unavailable"

    default = host._manifest_malformed_result(
        "bad manifest", subprocess.CompletedProcess([], 0, "", "plain"), "p", None
    )
    assert default["failure_class"] == "manifest_malformed"


# ---------------------------------------------------------------------------
# manifest extraction
# ---------------------------------------------------------------------------


def test_extract_manifest_no_text_events():
    proc = subprocess.CompletedProcess([], 0, '{"type": "other"}\n', "")
    assert OpencodeRunMixin._extract_manifest(proc) == (
        None,
        None,
        "no type=text events found in stdout",
    )


def test_extract_manifest_shape_error_is_terminal():
    payload = {"artifact_manifest": {"include": []}}
    event = {"type": "text", "part": {"text": json.dumps(payload)}}
    proc = subprocess.CompletedProcess([], 0, json.dumps(event) + "\n", "")
    manifest, msg, error = OpencodeRunMixin._extract_manifest(proc)
    assert manifest is None and msg is None
    assert "non-empty list" in error


def test_extract_manifest_empty_commit_message_is_terminal():
    payload = {
        "artifact_manifest": {
            "include": [{"path": "a", "kind": "k", "role": "r"}]
        },
        "suggested_commit_message": "   ",
    }
    event = {"type": "text", "part": {"text": json.dumps(payload)}}
    proc = subprocess.CompletedProcess([], 0, json.dumps(event) + "\n", "")
    manifest, msg, error = OpencodeRunMixin._extract_manifest(proc)
    assert manifest is None and msg is None
    assert error == "suggested_commit_message must be a non-empty string"


def test_extract_manifest_last_error_fallback():
    event = {"type": "text", "part": {"text": "no json here"}}
    proc = subprocess.CompletedProcess([], 0, json.dumps(event) + "\n", "")
    assert OpencodeRunMixin._extract_manifest(proc) == (
        None,
        None,
        "final part.text must be a raw JSON object",
    )


def test_extract_manifest_success_skips_prose_and_bad_commit():
    good = {"type": "text", "part": {"text": json.dumps({"artifact_manifest": {"include": [{"path": "a", "kind": "k", "role": "r"}]}}) + "\n" + json.dumps({"artifact_manifest": {"include": [{"path": "a", "kind": "k", "role": "r"}]}, "suggested_commit_message": "msg"})}}
    proc = subprocess.CompletedProcess([], 0, json.dumps(good) + "\n", "")
    manifest, msg, error = OpencodeRunMixin._extract_manifest(proc)
    assert error is None and msg == "msg"
    assert manifest == {"include": [{"path": "a", "kind": "k", "role": "r"}]}


def test_all_text_events_and_final_event():
    proc = subprocess.CompletedProcess(
        [],
        0,
        'noise\n{"type": "text", "part": {"text": "{}"}}\n{broken\n{"type": "other"}\n',
        "",
    )
    events = OpencodeRunMixin._all_text_events(proc)
    assert len(events) == 1
    assert OpencodeRunMixin._final_text_event(proc) == events[0]
    assert OpencodeRunMixin._final_text_event(subprocess.CompletedProcess([], 0, "", "")) is None


def test_manifest_payload_variants():
    assert OpencodeRunMixin._manifest_payload({}) == (
        None,
        "final type=text event must contain part.text",
    )
    assert OpencodeRunMixin._manifest_payload({"part": {"text": "prose"}}) == (
        None,
        "final part.text must be a raw JSON object",
    )
    payload, error = OpencodeRunMixin._manifest_payload({"part": {"text": '{"a": 1}'}})
    assert error is None and payload == {"a": 1}


def test_manifest_include_errors_and_top_level_shape():
    assert OpencodeRunMixin._manifest_include({}) == (
        None,
        "artifact_manifest must be an object",
    )
    assert OpencodeRunMixin._manifest_include({"artifact_manifest": {"include": []}}) == (
        None,
        "artifact_manifest.include must be a non-empty list",
    )
    include, error = OpencodeRunMixin._manifest_include(
        {"include": [{"path": "a", "kind": "k", "role": "r"}]}
    )
    assert error is None and include == [{"path": "a", "kind": "k", "role": "r"}]
    include, error = OpencodeRunMixin._manifest_include(
        {
            "artifact_manifest": {
                "include": [
                    {"path": "a", "kind": "k", "role": "r"},
                    {"path": "b", "kind": "k"},
                ]
            }
        }
    )
    assert include is None and "include[1].role" in error


def test_manifest_item_error_variants():
    assert "must be an object" in OpencodeRunMixin._manifest_item_error(0, "nope")
    assert "path must be a non-empty string" in OpencodeRunMixin._manifest_item_error(
        0, {"kind": "k", "role": "r"}
    )
    assert "must be repo-relative" in OpencodeRunMixin._manifest_item_error(
        0, {"path": "/abs.py", "kind": "k", "role": "r"}
    )
    assert OpencodeRunMixin._manifest_item_error(
        0, {"path": "rel.py", "kind": "k", "role": "r"}
    ) is None


def test_repair_and_decode_helpers():
    import json as _json

    decoder = _json.JSONDecoder()
    assert OpencodeRunMixin._effective_len("x```  ") == 1
    exc = _json.JSONDecodeError("msg", '{"a": 1', 7)
    assert OpencodeRunMixin._is_tail_error(exc, '{"a": 1') is True
    assert OpencodeRunMixin._repair_at('{"a": 1', 0, decoder, exc) == ({"a": 1}, 7)
    assert OpencodeRunMixin._repair_at("prefix", 0, decoder, _json.JSONDecodeError("m", "x", 0)) is None

    nested = '{"a": {"b": 1}'
    needs_two = _json.JSONDecodeError("msg", nested, len(nested))
    assert OpencodeRunMixin._repair_at(nested, 0, decoder, needs_two) == (
        {"a": {"b": 1}},
        len(nested),
    )

    tail = '{"a": '
    hopeless = _json.JSONDecodeError("msg", tail, len(tail))
    assert OpencodeRunMixin._repair_at(tail, 0, decoder, hopeless) is None

    assert OpencodeRunMixin._decode_at("nope", 0, decoder) is None
    assert OpencodeRunMixin._first_json_object('{"a": 1}') == {"a": 1}
    assert OpencodeRunMixin._first_json_object("no object") is None
    assert OpencodeRunMixin._first_json_object("{x") is None
    assert OpencodeRunMixin._update_payload({"a": 1}, (), None, None) == ({"a": 1}, None)
    assert OpencodeRunMixin._update_payload({"a": 1}, ("a", "b"), None, None) == ({"a": 1}, None)
    assert OpencodeRunMixin._update_payload({"a": 1}, ("a",), None, None) == ({"a": 1}, {"a": 1})
