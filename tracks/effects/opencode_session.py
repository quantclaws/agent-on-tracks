"""D-39 user-simplified (#44) session reuse mixin for OpencodeBackend.

Extracted from ``opencode.py`` for module-size compliance (C0302). Relies on
the host for ``repo``/``run_id`` and on ``_run``/``_has_json_events`` from the
run mixin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class OpencodeSessionMixin:
    """Agent-name -> opencode session registry + health-checked dispatch."""

    def _session_reuse_enabled(self) -> bool:
        """run_id 已提供且未显式关闭（TRAC_AGENT_SESSION_REUSE=0 为操作者
        逃生开关——session 复用若在 live 通道表现异常，可零代码回退）。"""
        return self.run_id is not None and (
            os.environ.get("TRAC_AGENT_SESSION_REUSE", "1").strip() != "0"
        )

    def _sessions_path(self) -> Path:
        """每个 run 一个 session 注册表：<tracks_home>/runtime/sessions/
        <run_id>.json。跨 trac run 进程重启存活（文件是共享载体）。"""
        from tracks import paths as tracks_paths

        home = tracks_paths.tracks_home(self.repo)
        d = home / "runtime" / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.run_id}.json"

    def _load_sessions(self) -> dict[str, str]:
        if self._sessions is not None:
            return self._sessions
        data: dict[str, str] = {}
        if self.run_id is not None:
            path = self._sessions_path()
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = {
                            str(k): str(v) for k, v in loaded.items() if v
                        }
                except (OSError, json.JSONDecodeError):
                    data = {}  # 损坏文件：弃用旧 session，逐次全新（fail-open）
        self._sessions = data
        return data

    def _save_sessions(self) -> None:
        if self.run_id is None or self._sessions is None:
            return
        path = self._sessions_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._sessions, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(path)  # 原子替换：崩溃不产生半写文件

    def _session_for(self, name: str) -> str | None:
        if not self._session_reuse_enabled():
            return None
        return self._load_sessions().get(name)

    def _record_session(self, name: str, session_id: str | None) -> None:
        if not self._session_reuse_enabled() or not session_id:
            return
        sessions = self._load_sessions()
        if sessions.get(name) != session_id:
            sessions[name] = session_id
            self._save_sessions()

    def _clear_session(self, name: str) -> None:
        """session 失效（not found / context overflow）时弃用：下一次派发
        全新开始，不携带旧上下文。"""
        if not self._session_reuse_enabled():
            return
        sessions = self._load_sessions()
        if name in sessions:
            del sessions[name]
            self._save_sessions()

    def _dispatch_with_session_health(self, name: str, prompt: str, root: Path):
        """``_run`` + D-39 用户简化版（#44）session 健康检查（act() 的
        派发入口，在 ``_check_json`` 之前）：

        ① session miss——``--session`` 指向的会话不存在（存储被清理/跨
           机器）。降级：弃用旧 id，同一 prompt 全新派发一次。
        ② context overflow——session 累积上下文超限。弃用 session（下次
           派发全新开始）；错误本身仍走 ``_check_json`` →
           provider_unavailable → executor infra 重派（B18：不烧 agent
           attempt）。

        Prism review B1（r1 教训复刻：启发式不得对"内容"开火）：agent 散
        文永远在 stdout 的 JSON 事件里——携带有效 JSON 事件的流是真实派
        发输出，正文含 "session not found"/"context window" 等短语属内容
        而非信号，绝不触发。信号判定只对非 JSON 流（真实 miss = exit 0 +
        非 JSON 错误行；真实 overflow = provider 侧错误）进行；session 复
        用关闭（run_id=None / 逃生开关）时跳过全部检查——旧版单次派发
        不变量逐字节保持。
        """
        proc = self._run(name, prompt, cwd=root)
        if not self._session_reuse_enabled():
            return proc
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        json_stream = self._has_json_events(stdout)
        if self._miss_line(stdout) or self._miss_line(stderr):
            self._clear_session(name)
            proc = self._run(name, prompt, cwd=root)
        elif self._overflow_text(stdout, stderr, json_stream):
            self._clear_session(name)
        return proc

    @staticmethod
    def _extract_session_id(stdout: str) -> str | None:
        """从 opencode --format json 事件流提取 sessionID（事件顶层或
        part 内；同一派发的全部事件共享一个 id，取首个非空值）。"""
        for line in (stdout or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            sid = event.get("sessionID")
            if not sid and isinstance(event.get("part"), dict):
                sid = event["part"].get("sessionID")
            if sid:
                return str(sid)
        return None

    @staticmethod
    def _miss_line(text: str) -> bool:
        """`--session <id>` 指向的 session 已不存在（存储被清理/跨机器）。
        真实签名（实测）：独立一行 `Error: Session not found`（exit 0，
        非 JSON 流）。行级精确匹配——agent 正文里的 "session not found"
        字样（评审/文档讨论）是内容不是信号（Prism review B1）。"""
        for line in (text or "").splitlines():
            if line.strip().lower() == "error: session not found":
                return True
        return False

    @staticmethod
    def _overflow_text(stdout: str, stderr: str, json_stream: bool) -> bool:
        """session 累积上下文超限的 provider 侧信号。命中则弃用该
        session——下一次 infra 重派自然全新开始（B18 语境：这类失败属
        infra，不烧 agent attempt）。

        agent 散文只在 stdout 的 JSON 事件里；stderr 是 opencode/provider
        错误输出，永不含 agent 正文。stdout 仅在**非 JSON 流**（已是错误
        输出形态）时参与扫描（Prism review B1：内容不开火）。"""
        texts = [(stderr or "").lower()]
        if not json_stream:
            texts.append((stdout or "").lower())
        return any(
            k in text
            for text in texts
            for k in (
                "context length",
                "context window",
                "prompt is too long",
                "maximum context",
                "input too long",
            )
        )
