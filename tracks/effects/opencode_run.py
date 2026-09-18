"""Subprocess + JSON/manifest mixin for OpencodeBackend (ARCH §7).

Extracted from ``opencode.py`` for module-size compliance (C0302). The
``subprocess``/``select`` module attributes are also re-exported by
:mod:`tracks.effects.opencode` because tests patch them through that path;
patching the stdlib module attribute is seen here unchanged.
"""

from __future__ import annotations

import contextlib
import json
import os
import pty
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

from tracks import paths

from .opencode_core import OpencodeError, iter_json_events


class OpencodeRunMixin:
    """opencode spawn/stream pipeline, failure matrix and manifest parsing."""

    def _resolve_model(self, name: str) -> str | None:
        """Two-layer model resolution per dispatch (see __init__ docstring):
        explicit ``self.model`` wins; otherwise ``None`` means opencode
        resolves its own configured default (no ``--model`` flag). Model
        selection is an opencode-config concern, never hardcoded by tracks."""
        return self.model

    def _run(self, name: str, prompt: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        """Run opencode agent with streaming stdout and manifest detection.

        opencode 1.18 ``run --format json`` deadlocks on a pipe stdout (it
        interacts with the controlling terminal); the child therefore gets a
        pty for stdin/stdout so events actually flow. ``TRAC_AGENT_PTY=0``
        restores the legacy pipe behaviour.
        """
        cmd = self._build_cmd(name, prompt)
        env = {**os.environ, "OPENCODE_LOGGER_DIR": ".opencode/logs"}
        console_input = os.environ.get("TRAC_AGENT_CONSOLE_INPUT")
        log_path = self._debug_log_path(name) if self.debug else None
        log_fh = open(log_path, "w", buffering=1) if log_path else None  # noqa: SIM115
        master_fd = None
        try:
            proc, master_fd = self._spawn(cmd, env, log_fh, cwd)
        except OpencodeError:
            if log_fh:
                log_fh.close()
            raise

        stdin_writer = self._start_stdin_pump(proc, console_input, master_fd)
        stderr_reader, stderr_lines = self._start_stderr_pump(proc, log_fh)

        timeout = int(os.environ.get("TRAC_AGENT_TIMEOUT", "1800"))
        return self._await_run(
            name,
            cmd,
            proc,
            master_fd,
            log_fh,
            log_path,
            stderr_lines,
            stdin_writer,
            stderr_reader,
            timeout,
        )

    def _await_run(
        self,
        name,
        cmd,
        proc,
        master_fd,
        log_fh,
        log_path,
        stderr_lines,
        stdin_writer,
        stderr_reader,
        timeout,
    ):
        """Stream to completion, finalize, and record the resumable session."""
        stdout_chunks: list[str] = []
        manifest_found = False
        try:
            stdout_chunks, manifest_found, stalled = self._stream_stdout(proc, timeout, master_fd)
        except KeyboardInterrupt:
            self._kill_group(proc.pid)
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
            self._close_master(master_fd)
            if log_fh:
                log_fh.close()
            raise

        try:
            proc = self._finalize_run(
                cmd, proc, master_fd, log_fh, log_path, stderr_lines,
                stdin_writer, stderr_reader, stdout_chunks, manifest_found,
                stalled, timeout,
            )
        except OpencodeError as exc:
            self._recover_run_error(exc, name)
            raise
        # 成功路径：从事件流提取 sessionID 并记录（幂等，同 id 不重写）。
        self._record_session(name, self._extract_session_id(proc.stdout or ""))
        return proc

    def _recover_run_error(self, exc: OpencodeError, name: str) -> None:
        # D-39 轻版（#44）：infra 失败（timeout/provider）也记录 session
        # ——下一次 infra 重派以 --session 续传（断点续跑而非冷启动）。
        exc_out = getattr(exc, "stdout", "") or ""
        exc_err = getattr(exc, "stderr", "") or ""
        self._record_session(name, self._extract_session_id(exc_out))
        # Prism review A1：异常流携带 overflow 信号（provider 侧错误，
        # stderr/非 JSON）→ 弃用 session，否则 infra 重派会反复续传同
        # 一个将溢出的会话直至派发上限。
        if self._session_reuse_enabled() and self._overflow_text(
            exc_out, exc_err, self._has_json_events(exc_out)
        ):
            self._clear_session(name)
        elif self._session_reuse_enabled() and exc.failure_class == "non_zero_exit":
            # 2026-09-18 run 01M2QTJB（M-IMPL PLANNING）：进程硬崩（exit≠0，
            # 区别于 timeout/provider 的瞬态错误）是与 miss/overflow 同级的
            # 会话失效信号——复用会话的派发在回合中途死亡（opencode part
            # status=None：工具调用发起即死，最终 envelope 永远发不出），
            # 随后演变为秒级 exit-1 循环；infra backoff（无自行停车出口）
            # 无限重派同一毒化会话。弃用 session：下一次 infra 重派冷启动
            # （B18：不烧 agent attempt；盘上产物 + 重注入 assignment 承载
            # 全部必需状态——"the event log carries the history, not the
            # prompt"）。
            self._clear_session(name)

    def _finalize_run(
        self,
        cmd,
        proc,
        master_fd,
        log_fh,
        log_path,
        stderr_lines,
        stdin_writer,
        stderr_reader,
        stdout_chunks,
        manifest_found,
        stalled,
        timeout,
    ):
        """Kill/join/close, then classify. A stalled stream is infrastructure,
        not an agent outcome: the lenient _check_json signal branch would
        otherwise accept the partial JSON events as a complete outcome and
        burn agent attempts on impl_defect loops (run 01KZTHE7 T-017)."""
        if manifest_found or proc.poll() is None:
            self._kill_group(proc.pid)
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)

        self._join_pump_threads(stdin_writer, stderr_reader, log_fh)

        self._close_master(master_fd)
        if log_fh:
            log_fh.close()
            stderr = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        else:
            stderr = "".join(stderr_lines)
        stdout = "".join(stdout_chunks)
        if stalled and not manifest_found:
            raise OpencodeError(
                "timeout",
                f"agent stream inactive for {timeout}s; killed",
                exit_code=proc.returncode,
                stderr=stderr,
                stdout=stdout,
            )
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def _spawn(self, cmd, env, log_fh, cwd=None):
        """Spawn the opencode child. Returns (proc, master_fd_or_None).

        opencode 1.18 ``run --format json`` deadlocks on a pipe stdout (it
        interacts with the controlling terminal), so the child gets a pty for
        stdin/stdout unless ``TRAC_AGENT_PTY=0`` restores legacy pipes.
        """
        if os.environ.get("TRAC_AGENT_PTY", "1") == "0":
            return self._spawn_pipe(cmd, env, log_fh), None
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd or self.repo, env=env,
                stdin=slave_fd, stdout=slave_fd,
                stderr=log_fh if log_fh else subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except FileNotFoundError as err:
            self._close_master(master_fd)
            with contextlib.suppress(OSError):
                os.close(slave_fd)
            raise OpencodeError("opencode_missing", "opencode executable not found") from err
        os.close(slave_fd)
        return proc, master_fd

    def _spawn_pipe(self, cmd, env, log_fh, cwd=None):
        """Legacy pipe-mode spawn (no pty)."""
        try:
            return subprocess.Popen(
                cmd, cwd=cwd or self.repo, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=log_fh if log_fh else subprocess.PIPE,
                text=True, start_new_session=True,
            )
        except FileNotFoundError as err:
            raise OpencodeError("opencode_missing", "opencode executable not found") from err

    @staticmethod
    def _close_master(master_fd):
        """Close the pty master fd when open (idempotent no-op otherwise)."""
        if master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(master_fd)

    def _build_cmd(self, name: str, prompt: str) -> list[str]:
        cmd = ["opencode", "run", "--agent", name, "--format", "json",
               "--dir", str(self.repo), "--auto", prompt]
        # D-39 用户简化版（#44）：该 agent 在本 run 内已有 session 则续传
        # （上下文跨派发/重试接续，失败重派从"全量重建"坍缩为"增量指令"）。
        session = self._session_for(name)
        if session:
            cmd.extend(["--session", session])
        model = self._resolve_model(name)
        if model:
            cmd.extend(["--model", model])
        if self.debug:
            cmd.extend(["--print-logs", "--log-level", "DEBUG"])
        return cmd

    @staticmethod
    def _start_stdin_pump(proc, console_input, master_fd=None):
        """Write console_input to the child's stdin in a background thread.

        PTY mode writes to master_fd (the child reads the slave side) and must
        not close it: _stream_stdout keeps reading from it afterwards.
        """
        if console_input is None:
            # PTY mode owns stdin through master_fd, but closing an exposed
            # proc.stdin handle still signals EOF for pipe-mode/test doubles.
            with contextlib.suppress(AttributeError, OSError, ValueError):
                proc.stdin.close()
            return None

        def _write():
            try:
                if master_fd is not None and getattr(proc, "stdin", None) is None:
                    os.write(master_fd, console_input.encode("utf-8", "replace"))
                else:
                    proc.stdin.write(console_input)
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                with contextlib.suppress(AttributeError, OSError, ValueError):
                    proc.stdin.close()

        t = threading.Thread(target=_write, daemon=True)
        t.start()
        return t

    @staticmethod
    def _start_stderr_pump(proc, log_fh):
        """Capture stderr in a background thread (non-debug mode only)."""
        if log_fh:
            return None, []
        stderr_lines: list[str] = []

        def _read():
            try:
                while True:
                    chunk = proc.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_lines.append(chunk)
            except (OSError, ValueError):
                pass

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        return t, stderr_lines

    def _stream_stdout(self, proc, inactivity_timeout, master_fd=None):
        """Stream child output via os.read, detecting manifest in real-time.

        Reads from master_fd in PTY mode (proc.stdout is None there) or from
        proc.stdout in legacy pipe mode. Timeout is inactivity-based: fires
        only when no output is received for the given seconds. Returns
        (chunks, manifest_found, stalled); a stall must surface as an infra
        timeout, never as a truncated-but-accepted outcome.
        """
        chunks: list[str] = []
        buf = ""
        read_fd = master_fd if master_fd is not None else proc.stdout.fileno()
        last_activity = time.monotonic()
        stalled = False
        while True:
            ready, _, _ = select.select([read_fd], [], [], 5.0)
            if ready:
                last_activity = time.monotonic()
                try:
                    raw = os.read(read_fd, 65536)
                except OSError:
                    break
                if not raw:
                    break
                buf, found = self._process_stdout_chunk(raw, buf, chunks)
                if found:
                    return chunks, True, False
            elif proc.poll() is not None:
                self._drain_stdout(proc, chunks, read_fd)
                break
            elif time.monotonic() - last_activity > inactivity_timeout:
                stalled = True
                break
        # An unterminated final line (no trailing newline) is truncation
        # evidence: keep it so _check_json can classify the stream.
        if buf:
            chunks.append(buf)
        return chunks, False, stalled

    @staticmethod
    def _process_stdout_chunk(raw, buf, chunks):
        """Decode chunk, extract complete lines, check for manifest.
        Returns (updated_buf, manifest_found)."""
        text = raw.decode("utf-8", errors="replace")
        # pty ONLCR turns \n into \r\n; normalize back so downstream
        # line-wise JSON parsing sees the same stream as pipe mode.
        buf += text.replace("\r\n", "\n")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line += "\n"
            chunks.append(line)
            if OpencodeRunMixin._line_has_manifest(line):
                return buf, True
        return buf, False

    @staticmethod
    def _line_has_manifest(line):
        """Check if a stdout line is a text event with a valid manifest."""
        stripped = line.strip()
        if not stripped.startswith("{"):
            return False
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict) or event.get("type") != "text":
            return False
        payload, perr = OpencodeRunMixin._manifest_payload(event)
        if perr is not None:
            return False
        include, ierr = OpencodeRunMixin._manifest_include(payload)
        if ierr is not None:
            return False
        msg = payload.get("suggested_commit_message")
        return isinstance(msg, str) and msg.strip()

    @staticmethod
    def _drain_stdout(proc, chunks, read_fd=None):
        """Read remaining output after process exit."""
        try:
            if read_fd is not None:
                while True:
                    raw = os.read(read_fd, 65536)
                    if not raw:
                        break
                    chunks.append(raw.decode("utf-8", errors="replace").replace("\r\n", "\n"))
            else:
                remaining = proc.stdout.read()
                if remaining:
                    chunks.append(remaining)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _join_pump_threads(stdin_writer, stderr_reader, log_fh):
        """Reap background pump threads."""
        if stdin_writer is not None:
            with contextlib.suppress(Exception):
                stdin_writer.join(timeout=5)
        if stderr_reader is not None and not log_fh:
            with contextlib.suppress(Exception):
                stderr_reader.join(timeout=5)

    def _debug_log_path(self, name: str) -> Path:
        """Debug log file: .tracks/runtime/log/<agent>-<timestamp>.log"""
        from datetime import datetime, timezone

        home = paths.tracks_home(self.repo)
        log_dir = home / "runtime" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return log_dir / f"{name.lower()}-{ts}.log"

    @staticmethod
    def _kill_group(pid: int) -> None:
        # start_new_session=True -> the child leads its own process group.
        with contextlib.suppress(ProcessLookupError, PermissionError, TypeError, OSError):
            os.killpg(pid, signal.SIGKILL)

    @staticmethod
    def _abnormal_step_finish(proc: subprocess.CompletedProcess) -> bool:
        """True when opencode's final step ended abnormally (B17/#20 narrow:
        live T-003 GREEN — step_finish reason=unknown, exit 0, no evidence
        JSON; the session was provider-interrupted mid-implementation)."""
        last = None
        for event in iter_json_events(proc.stdout):
            if event.get("type") == "step_finish":
                last = event
        if not isinstance(last, dict):
            return False
        part = last.get("part")
        reason = part.get("reason") if isinstance(part, dict) else None
        return reason not in ("stop", "tool-calls", None)

    def _abnormal_step_result(
        self,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        """Infra-classified failure for an abnormal step finish — never burns
        the agent attempt budget (machine _INFRA_FAILURE_CLASSES digests it
        with bounded re-dispatch)."""
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": (
                "opencode session ended abnormally before delivering the "
                "final JSON (step_finish reason=unknown; provider "
                "interruption suspected)"
            ),
            "audit_evidence": "abnormal_step_finish: reason=unknown",
            # Dedicated class (NOT provider_unavailable): the turn ended
            # abnormally for an unspecified reason — provider cut, SDK
            # error, or context-edge mishandling all land here. The effect
            # (infra, no attempt burn) is right; the label must not claim a
            # diagnosis we do not have (B18/#20 bucket hygiene).
            "failure_class": "abnormal_step_finish",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _check_json(self, proc: subprocess.CompletedProcess) -> None:
        """Classify the exit; JSON is diagnostic-only (product = target diff)."""
        if proc.returncode < 0:  # killed by signal (SIGINT/kill-9 or manifest-kill)
            out = (proc.stdout or "").strip()
            # The streaming reader kills the process group after detecting
            # a valid manifest. The last line may be truncated by SIGKILL,
            # so use _has_json_events (lenient) instead of _parses_json (strict).
            # Accept if at least one valid JSON event was captured.
            if out and self._has_json_events(out):
                return
            raise OpencodeError(
                "signal",
                f"killed by signal {-proc.returncode}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
            )
        if proc.returncode != 0:
            if self._looks_provider_error(proc.stderr):
                raise OpencodeError(
                    "provider_unavailable",
                    "provider/model/credentials unavailable",
                    exit_code=proc.returncode,
                    stderr=proc.stderr,
                )
            raise OpencodeError(
                "non_zero_exit",
                f"opencode exited {proc.returncode}",
                exit_code=proc.returncode,
                stderr=proc.stderr,
            )
        # exit 0: stdout must be parseable JSON (events); truncation => failure.
        out = (proc.stdout or "").strip()
        if out and not self._parses_json(out):
            raise OpencodeError(
                "json_truncated", "stdout JSON stream unparseable", exit_code=0, stderr=proc.stderr
            )

    @staticmethod
    def _looks_provider_error(stderr: str) -> bool:
        low = (stderr or "").lower()
        return any(
            k in low
            for k in (
                "provider",
                "model",
                "credential",
                "unauthorized",
                "api key",
                "authentication",
                "401",
                "403",
                # quota / rate-limit signals (e.g. litellm code 4008)
                "quota",
                "exceeded",
                "rate_limit",
                "rate limit",
                "429",
                "4008",
            )
        )

    @staticmethod
    def _has_json_events(out: str) -> bool:
        """Lenient check: at least one valid JSON event line in stdout.

        Unlike _parses_json, this tolerates truncated lines (e.g. the last
        line cut short by SIGKILL). Used by _check_json's signal branch.
        """
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    json.loads(stripped)
                    return True
                except json.JSONDecodeError:
                    continue
        return False

    @staticmethod
    def _parses_json(out: str) -> bool:
        try:
            json.loads(out)
            return True
        except json.JSONDecodeError:
            pass
        # NDJSON fallback: opencode --format json emits one JSON event per
        # line, but may also emit non-JSON log lines on stdout (e.g.
        # "[Opencode Logger] Plugin initialized!"). A line starting with
        # "{" that fails to parse is a truncated JSON event -> reject; a
        # non-"{" line is diagnostic log noise -> skip. At least one valid
        # JSON event line is required. Exit 0 already confirmed the process
        # completed; true truncation (process killed) surfaces as a signal
        # or non-zero exit before this check runs.
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if not lines:
            return False
        has_json = False
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("{"):
                try:
                    json.loads(ln)
                except json.JSONDecodeError:
                    return False
                has_json = True
            else:
                try:
                    json.loads(ln)
                    has_json = True
                except json.JSONDecodeError:
                    pass
        return has_json

    @staticmethod
    def _extract_manifest(
        proc: subprocess.CompletedProcess,
    ) -> tuple[dict | None, str | None, str | None]:
        """Parse Shield WRITE manifest from text events.

        Scans ALL type=text events in reverse order, not just the last.
        When opencode --auto enters a post-completion loop, the last text
        event may be "already completed" prose, not the manifest.
        """
        text_events = OpencodeRunMixin._all_text_events(proc)
        if not text_events:
            return None, None, "no type=text events found in stdout"
        last_error = "no text event contained a valid manifest"
        for text_event in reversed(text_events):
            payload, error = OpencodeRunMixin._manifest_payload(text_event)
            if error is not None:
                last_error = error
                continue
            include, error = OpencodeRunMixin._manifest_include(payload)
            if error is None:
                commit_msg = payload.get("suggested_commit_message")
                if isinstance(commit_msg, str) and commit_msg.strip():
                    return {"include": include}, commit_msg, None
                error = "suggested_commit_message must be a non-empty string"
            if "artifact_manifest" in payload or "include" in payload:
                # Manifest-shaped but invalid: this is the real diagnosis
                # and must not be masked by earlier prose events that fail
                # to parse as JSON.
                return None, None, error
            last_error = error
        return None, None, last_error

    @staticmethod
    def _all_text_events(
        proc: subprocess.CompletedProcess,
    ) -> list[dict]:
        """Return all type=text events from stdout, in chronological order."""
        out = (proc.stdout or "").strip()
        events: list[dict] = []
        for line in out.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "text":
                events.append(event)
        return events

    @staticmethod
    def _final_text_event(
        proc: subprocess.CompletedProcess,
    ) -> dict | None:
        """Return the last type=text event (kept for backward compat)."""
        events = OpencodeRunMixin._all_text_events(proc)
        return events[-1] if events else None

    @staticmethod
    def _manifest_payload(
        text_event: dict,
    ) -> tuple[dict | None, str | None]:
        part = text_event.get("part")
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            return None, "final type=text event must contain part.text"
        text = part["text"].strip()
        payload = OpencodeRunMixin._first_json_object(text)
        if payload is None:
            return None, "final part.text must be a raw JSON object"
        # Declared envelope reply (FR-0278-01): the assignment demands exactly
        # one tracks-envelope block whose payload IS the manifest. Unwrap it so
        # the declared writer contract and the legacy manifest extraction read
        # the same object.
        if isinstance(payload.get("envelope"), dict) and isinstance(
            payload.get("payload"), dict
        ):
            payload = payload["payload"]
        return payload, None

    @staticmethod
    def _effective_len(text: str) -> int:
        """Length after stripping trailing space and ``` fence."""
        t = text.rstrip()
        if t.endswith("```"):
            t = t[:-3].rstrip()
        return len(t)

    @staticmethod
    def _is_tail_error(exc: json.JSONDecodeError, text: str) -> bool:
        return exc.pos >= OpencodeRunMixin._effective_len(text)

    @staticmethod
    def _repair_at(
        text: str, start: int, decoder: json.JSONDecoder, exc: json.JSONDecodeError
    ) -> tuple[dict, int] | None:
        """Tail missing-brace: try 1-3 `}` on the tail slice."""
        if not OpencodeRunMixin._is_tail_error(exc, text):
            return None
        eff = OpencodeRunMixin._effective_len(text)
        seg = text[start:eff]
        for k in (1, 2, 3):
            cand = seg + "}" * k
            try:
                obj, end = decoder.raw_decode(cand, 0)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and end == len(cand):
                return obj, eff
        return None

    @staticmethod
    def _decode_at(text: str, pos: int, decoder: json.JSONDecoder):
        """Decode at *pos* with tail-repair fallback."""
        try:
            return decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            return OpencodeRunMixin._repair_at(text, pos, decoder, exc)

    @staticmethod
    def _update_payload(
        obj, required_keys: tuple[str, ...], payload, shaped
    ) -> tuple[dict | None, dict | None]:
        if isinstance(obj, dict):
            payload = obj
            if required_keys and all(k in obj for k in required_keys):
                shaped = obj
        return payload, shaped

    @staticmethod
    def _first_json_object(text: str, required_keys: tuple[str, ...] = ()) -> dict | None:
        """Extract the last top-level JSON object from *text*."""
        try:
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            pass
        dec = json.JSONDecoder()
        i = 0
        n = len(text)
        payload: dict | None = None
        shaped: dict | None = None
        while i < n:
            if text[i] != "{":
                i += 1
                continue
            res = OpencodeRunMixin._decode_at(text, i, dec)
            if res is None:
                i += 1
                continue
            obj, end = res
            payload, shaped = OpencodeRunMixin._update_payload(
                obj, required_keys, payload, shaped
            )
            i = end
        return shaped if shaped is not None else payload

    @staticmethod
    def _manifest_include(
        payload: dict,
    ) -> tuple[list | None, str | None]:
        raw_manifest = payload.get("artifact_manifest")
        if not isinstance(raw_manifest, dict):
            # A dropped closing brace relocates ``include`` to the top
            # level; accept that shape so one brace slip does not waste a
            # dispatch attempt.
            if isinstance(payload.get("include"), list):
                raw_manifest = payload
            else:
                return None, "artifact_manifest must be an object"
        include = raw_manifest.get("include")
        if not isinstance(include, list) or not include:
            return None, "artifact_manifest.include must be a non-empty list"
        for index, item in enumerate(include):
            error = OpencodeRunMixin._manifest_item_error(index, item)
            if error is not None:
                return None, error
        return include, None

    @staticmethod
    def _manifest_item_error(index: int, item: object) -> str | None:
        if not isinstance(item, dict):
            return f"artifact_manifest.include[{index}] must be an object"
        for field in ("path", "kind", "role"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                return f"artifact_manifest.include[{index}].{field} must be a non-empty string"
        if Path(item["path"]).is_absolute():
            return f"artifact_manifest.include[{index}].path must be repo-relative"
        return None

    def _manifest_malformed_result(
        self,
        error: str,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        # When opencode exits 0 but the manifest is malformed, check whether
        # the root cause is a provider error (e.g. quota exceeded mid-stream).
        # opencode logs stream errors to stderr but continues, so the manifest
        # is simply truncated.  Re-classify as provider_unavailable so the
        # runtime can retry instead of wasting dispatch attempts.
        stderr = proc.stderr or ""
        if self._looks_provider_error(stderr):
            return {
                "status": "failed",
                "artifact_ref": None,
                "self_report": f"provider unavailable: {error}",
                "failure_class": "provider_unavailable",
                "audit_evidence": f"provider_unavailable: {error}",
                "agent_io": self._capture_io(proc, prompt, console_input),
            }
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": f"manifest malformed: {error}",
            "failure_class": "manifest_malformed",
            "audit_evidence": f"manifest_malformed: {error}",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }
