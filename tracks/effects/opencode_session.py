"""D-42 session lifecycle mixin for OpencodeBackend (supersedes #44's
per-agent simplification of D-39).

Three-layer mechanism (user ruling 2026-09-19; full design:
.tracks/projects/v0.9/oob/2026-09-19-session-lifecycle.md):

1. Keying — sessions are tracked per *work unit*, not per agent name:
   ``(run_id, role, work_unit, task_id)`` where the work unit derives from
   (role, substate) via :func:`_session_key`. Version isolation stays at the
   per-run registry file; stage/task transitions naturally start fresh.
2. Reuse — every re-dispatch within a key resumes the session (infra retries,
   format-error re-emits, verdict revise loops alike); FR-11 evidence
   injection does the correcting. This REPLACES D-39's attempt-fresh
   boundary.
3. Compaction — the registry records each session's context size (the last
   ``step_finish`` event's ``tokens.input``); a pre-dispatch check compacts
   at >= 85% of the model's context window (``opencode models --verbose``
   catalog) and drops the session when compaction fails or does not shrink
   it. miss / provider-overflow text / non_zero_exit hard-crash all drop the
   session (the runtime re-injects everything the agent needs; the agent
   only rebuilds its own exploration/thinking/conclusions).

Relies on the host for ``repo``/``run_id`` and on ``_run`` from the run
mixin. ``kernel`` stays pure — session tracking is effects-layer freedom
(D-39).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .opencode_core import iter_json_events

# Work-unit families: substates that share one session key because the
# context between them is critical to the next dispatch (D-42 principle 2).
_DEVON_RGR = frozenset({"RED", "GREEN", "REFACTOR", "DIAGNOSE"})
_SHIELD_MTEST_WRITE = frozenset({"WRITE", "NO_DIFF_EXPLAIN"})
_ARCHER_DESIGN = frozenset({"DRAFT", "RESPOND"})

_COMPACT_PCT_DEFAULT = 85
_MODELS_JSON_BLOCK = re.compile(r"^(\S+/\S+)\s*\n(\{.*?\n\})", re.MULTILINE | re.DOTALL)


def _session_key(role: str, substate: str, assignment: dict | None) -> str:
    """Derive the work-unit session key for one dispatch (D-42).

    ``role``/``substate`` come from the dispatch; ``task_id`` from the
    materialized assignment's task block (M-IMPL writer/review dispatches
    carry it). Families: Devon RGR shares the task session (green needs the
    red context); Shield's M-TEST WRITE/NO_DIFF_EXPLAIN share the writing
    unit; everything else keys on (role, substate[, task]).
    """
    task = assignment.get("task") if isinstance(assignment, dict) else None
    task_id = task.get("task_id") if isinstance(task, dict) else None
    task_id = str(task_id) if task_id else None
    if role == "devon" and substate in _DEVON_RGR:
        return f"devon:task:{task_id or '-'}"
    if role == "shield" and substate in _SHIELD_MTEST_WRITE:
        return "shield:mtest-write"
    if role == "archer" and substate in _ARCHER_DESIGN:
        return "archer:design"
    base = f"{role}:{substate or '-'}"
    return f"{base}:{task_id}" if task_id else base


def _step_ctx_tokens(part: dict) -> int | None:
    """One step_finish part's prompt footprint: input + cache.read +
    cache.write. ``tokens.input`` alone is only the UNCACHED fresh input."""
    t = part.get("tokens")
    if not isinstance(t, dict) or not isinstance(t.get("input"), int):
        return None
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    total = t["input"]
    for key in ("read", "write"):
        value = cache.get(key)
        if isinstance(value, int):
            total += value
    return total


def _last_ctx_tokens(stdout: str) -> int | None:
    """The session's context size after a dispatch: the LAST step_finish
    event's prompt footprint — input plus cache reads/writes.
    Practice calibration (2026-09-19, run 01M2QTJB T-006 GREEN):
    ``tokens.input`` alone was 1001 while cache.read was 277504 (real
    context ~278K) — using it alone made the 85% compaction threshold
    unreachable and silently killed the loop; the provider's window
    enforcement sees the full prompt, cache included."""
    tokens = None
    for event in iter_json_events(stdout):
        if not isinstance(event, dict) or event.get("type") != "step_finish":
            continue
        part = event.get("part")
        if isinstance(part, dict):
            value = _step_ctx_tokens(part)
            if value is not None:
                tokens = value
    return tokens


class OpencodeSessionMixin:
    """Work-unit-keyed session registry + health-checked dispatch."""

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

    @staticmethod
    def _normalize_session_entry(value) -> dict | None:
        """v2 条目归一：``None`` = 丢弃（legacy 纯字符串 / 畸形条目）。"""
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("session_id"), str)
            or not value.get("session_id")
        ):
            return None
        ctx = value.get("ctx_tokens")
        return {
            "session_id": value["session_id"],
            "ctx_tokens": ctx if isinstance(ctx, int) else None,
            "compacted": bool(value.get("compacted")),
        }

    def _read_registry(self) -> dict | None:
        """读注册表文件；缺失/损坏/非对象返回 None（fail-open：弃用旧
        session，逐次全新）。"""
        path = self._sessions_path()
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _load_sessions(self) -> dict[str, dict]:
        """Registry v2 (D-42): ``{key: {"session_id", "ctx_tokens",
        "compacted"}}``. Legacy #44 entries (plain string values) are DROPPED
        on load — cold-start is minute-level cheap and the runtime
        re-injects everything the agent needs."""
        if self._sessions is not None:
            return self._sessions
        data: dict[str, dict] = {}
        if self.run_id is not None:
            loaded = self._read_registry()
            if loaded:
                for key, value in loaded.items():
                    entry = self._normalize_session_entry(value)
                    if entry is not None:
                        data[str(key)] = entry
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

    def _session_for(self, key: str) -> dict | None:
        if not self._session_reuse_enabled():
            return None
        return self._load_sessions().get(key)

    def _record_session(self, key: str, session_id: str | None) -> None:
        if not self._session_reuse_enabled() or not session_id:
            return
        sessions = self._load_sessions()
        entry = sessions.get(key)
        if not isinstance(entry, dict) or entry.get("session_id") != session_id:
            sessions[key] = {"session_id": session_id, "ctx_tokens": None,
                             "compacted": False}
            self._save_sessions()

    def _record_ctx_tokens(self, key: str, tokens: int | None) -> None:
        """Post-dispatch context measurement (D-42 layer 3). When a
        compaction was attempted and the new measurement did not shrink the
        session, the session is dropped — compaction that does not shrink is
        the poison signature (compact-forever loop guard)."""
        if not self._session_reuse_enabled() or not isinstance(tokens, int):
            return
        sessions = self._load_sessions()
        entry = sessions.get(key)
        if not isinstance(entry, dict):
            return
        prev = entry.get("ctx_tokens")
        entry["ctx_tokens"] = tokens
        if entry.get("compacted"):
            if isinstance(prev, int) and tokens >= prev:
                self._clear_session(key)
                return
            entry["compacted"] = False
        self._save_sessions()

    def _clear_session(self, key: str) -> None:
        """session 失效（not found / overflow / hard-crash / 压缩无效）时
        弃用：下一次派发全新开始，不携带旧上下文。"""
        if not self._session_reuse_enabled():
            return
        sessions = self._load_sessions()
        if key in sessions:
            del sessions[key]
            self._save_sessions()

    # -- model context limits (D-42 ruling 1: read provider/model settings) --

    def _effective_model(self, name: str) -> str | None:
        """The agent's actual model: explicit backend override, else the
        per-agent declaration in .opencode/opencode.json (what opencode
        itself resolves at spawn)."""
        model = self._resolve_model(name)
        if model:
            return model
        try:
            cfg = json.loads(
                (Path(self.repo) / ".opencode" / "opencode.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            return None
        agents = cfg.get("agent") if isinstance(cfg, dict) else None
        if not isinstance(agents, dict):
            return None
        for key in (name, name.lower()):
            entry = agents.get(key)
            if isinstance(entry, dict) and entry.get("model"):
                return str(entry["model"])
        return None

    def _model_ctx_limits(self) -> dict[str, int]:
        """provider/model -> limit.context from ``opencode models
        --verbose`` (the models.dev catalog; process-cached). Parse failure
        is fail-open: no limits -> no compaction, the overflow health check
        still backstops."""
        cached = getattr(self, "_model_limits", None)
        if cached is not None:
            return cached
        limits: dict[str, int] = {}
        try:
            proc = subprocess.run(
                ["opencode", "models", "--verbose"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            for match in _MODELS_JSON_BLOCK.finditer(proc.stdout or ""):
                try:
                    info = json.loads(match.group(2))
                except json.JSONDecodeError:
                    continue
                limit = (info.get("limit") or {}).get("context")
                if isinstance(limit, int) and limit > 0:
                    limits[match.group(1)] = limit
        except (OSError, subprocess.SubprocessError):
            limits = {}
        self._model_limits = limits
        return limits

    def _maybe_compact(self, key: str, model: str | None) -> None:
        """Pre-dispatch threshold check (D-42 layer 3): compact at >= 85% of
        the model's context window; drop the session when compaction fails
        or was already attempted at this size (the loop guard lives in
        _record_ctx_tokens)."""
        if not self._session_reuse_enabled():
            return
        entry = self._session_for(key)
        sid = (entry or {}).get("session_id")
        if not sid:
            return
        ctx = (entry or {}).get("ctx_tokens")
        if not isinstance(ctx, int) or ctx <= 0:
            return
        limit = self._model_ctx_limits().get(model or "")
        if not limit:
            return
        try:
            pct = int(os.environ.get("TRAC_SESSION_COMPACT_PCT",
                                     str(_COMPACT_PCT_DEFAULT)))
        except ValueError:
            pct = _COMPACT_PCT_DEFAULT
        if ctx * 100 < limit * pct:
            return
        print(
            f"  [session] {key}: ctx {ctx} >= {pct}% of {model} window "
            f"({limit}) — compacting",
            file=sys.stderr,
            flush=True,
        )
        if entry.get("compacted"):
            # Already compacted at this size and it did not shrink below
            # the threshold on the last measurement.
            self._clear_session(key)
            return
        if self._run_compact(sid):
            entry["compacted"] = True
            self._save_sessions()
        else:
            print(
                f"  [session] {key}: compact failed — dropping session "
                "(next dispatch cold-starts)",
                file=sys.stderr,
                flush=True,
            )
            self._clear_session(key)

    def _run_compact(self, session_id: str) -> bool:
        """Invoke opencode's built-in compact command on the session.
        Fail-closed: any non-zero exit or error event means failure (the
        caller drops the session — correctness never depends on compaction
        succeeding)."""
        try:
            proc = subprocess.run(
                [
                    "opencode", "run", "--session", session_id,
                    "--command", "compact", "--dir", str(self.repo),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        out = proc.stdout or ""
        return proc.returncode == 0 and '"type":"error"' not in out

    # -- dispatch entry ---------------------------------------------------------

    def _dispatch_with_session_health(
        self, name: str, prompt: str, root: Path, key: str | None = None
    ):
        """``_run`` + session health checks (act() 的派发入口，在
        ``_check_json`` 之前)：

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
        用关闭（run_id=None / 逃生开关）时跳过全部检查——旧版单次派发不
        变量逐字节保持。
        """
        key = key or name
        proc = self._run(name, prompt, cwd=root, key=key)
        if not self._session_reuse_enabled():
            return proc
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        json_stream = self._has_json_events(stdout)
        if self._miss_line(stdout) or self._miss_line(stderr):
            self._clear_session(key)
            proc = self._run(name, prompt, cwd=root, key=key)
        elif self._overflow_text(stdout, stderr, json_stream):
            self._clear_session(key)
        return proc

    @staticmethod
    def _extract_session_id(stdout: str) -> str | None:
        """从 opencode --format json 事件流提取 sessionID（事件顶层或
        part 内；同一派发的全部事件共享一个 id，取首个非空值）。"""
        for event in iter_json_events(stdout):
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
