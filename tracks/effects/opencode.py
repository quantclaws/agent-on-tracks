"""OpencodeBackend — real agent via `opencode run` subprocess (ARCH-003 §4/§6/§7).

Pipeline per dispatch: materialize canonical prompt (+ skills + document
templates named by the assignment) -> baseline snapshot ->
`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`
(process group) -> parse JSON (diagnostic/truncation only) -> capture
the target doc-set diff (authoritative product, ARCH §4b; one doc normally, the
whole M-DESIGN trio for a multi-doc assignment) -> post-run audit (ARCH §6:
over-reach; for M-DESIGN author dispatches, that host-project writes stay
within the architecture.md Scaffold 宣言 manifest (batch B); for author DRAFT
dispatches, that the agent resolved every discussion thread it initiated in the
target doc-set; for reviewer dispatches, that a revise verdict anchors its
findings — at least one open discussion thread the reviewer initiated, live
run042). Failures map to IF-003 §1a FailureClass. The agent's stdout JSON is
NEVER the product; the controlled target diff is.
"""

from __future__ import annotations

import contextlib
import json
import os
import pty
import re
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

from tracks import paths, templating
from tracks.discuss.delta import is_discussion_delta
from tracks.discuss.gate import check_ready
from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads
from tracks.effects.adjudicate import adjudicate
from tracks.effects.audit import Auditor, _rel
from tracks.effects.devon_evidence import extract_devon_evidence
from tracks.effects.dispatch_parity import (
    PreparedContext,
    agent_face,
    bind_artifact,
    check_prepared_parity,
    is_declared,
    parity_evidence,
    parity_failure_result,
    readback_mismatches,
    skill_face,
    template_face,
)
from tracks.effects.envelope_reply import is_declared_assignment
from tracks.kernel.envelope import (
    REVIEW_FINDING_FIELDS,
    REVIEW_SUMMARY_MAX,
    EnvelopeFormatError,
    parse_agent_output,
    validate_review_finding,
    validate_review_payload,
)
from tracks.kernel.m_impl import DIAGNOSE_CLASSIFICATIONS
from tracks.project import COMMENTABLE_DOCS, layout_paths
from tracks.scaffold import _scaffold_declared_paths

AGENT_NAME = {
    "scribe": "Scribe",
    "sage": "Sage",
    "lex": "Lex",
    "archer": "Archer",
    "prism": "Prism",
    "shield": "Shield",
    "devon": "Devon",
}

_SECRET_VALUE = re.compile(
    r"(?i)((?:authorization|api[_ -]?key|access[_ -]?token|secret|password)"
    r"\s*[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?)([^\s,;\"']+)"
)
_ENV_SECRET_VALUE = re.compile(
    r"(?i)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)"
    r"([\"']?\s*=\s*[\"']?)([^\s,;\"']+)"
)

_OPENCODE_PREFIX = ".opencode/"
_GROUND_TRUTH_PREFIX = "tests/ground_truth/"


def redact(text: str) -> str:
    """Redact credential-shaped values before evidence leaves the subprocess."""
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text or "")
    text = _ENV_SECRET_VALUE.sub(r"\1\2[REDACTED]", text)
    # Providers sometimes print a raw secret without its environment-variable
    # name. Only replace long values from explicitly secret-shaped variables.
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and re.search(r"(?i)(?:API_KEY|TOKEN|SECRET|PASSWORD|AUTH)", name)
        ):
            text = text.replace(value, "[REDACTED]")
    return text


class OpencodeError(Exception):
    """A classified backend failure (failure_class per IF-003 §1a)."""

    def __init__(
        self,
        failure_class: str,
        message: str,
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
    ):
        super().__init__(message)
        self.failure_class = failure_class
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


class OpencodeBackend:
    """AgentBackend implemented by a real opencode subagent subprocess."""

    envelope_version = 2

    def __init__(
        self,
        repo: Path,
        version: str,
        model: str | None = None,
        debug: bool = False,
        run_id: str | None = None,
    ):
        self.repo = Path(repo)
        self.version = version
        # Model resolution is two-layer per dispatch (spec §3.1, ARCH §4a):
        # (1) explicit self.model (TRAC_AGENT_MODEL via select_backend) wins;
        # (2) else no --model flag, so opencode resolves its own configured
        #     default (agent/project config, never hardcoded by tracks).
        self.model = model
        self.debug = debug
        # D-39 用户简化版（#44）：session 复用。run_id 非空时，每个 agent
        # 在该 run 内有且只有一个 opencode session——所有派发（含 infra
        # 重派与 attempt 重试）一律 `--session <id>` 续传，id 持久化在
        # .tracks/runtime/sessions/<run_id>.json（跨 trac run 进程重启存
        # 活）。run_id 为 None（测试/一次性调用）时行为与旧版逐次全新
        # 派发完全一致。
        self.run_id = run_id
        self._sessions: dict[str, str] | None = None  # agent name -> session id（惰性加载）
        self._canonical = Path(__file__).resolve().parent.parent / "agents"

    # -- D-39 用户简化版（#44）：session 复用 --------------------------------

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
    def _assignment_forbidden_paths(assignment: dict | None) -> list[str]:
        """B64 (#82): repo-relative forbidden patterns from the assignment
        (executor-injected; see Executor._red_test_scope). Returns [] when
        the dispatch carries no ownership veto."""
        value = (assignment or {}).get("forbidden_paths")
        if not isinstance(value, list):
            return []
        return [p for p in value if isinstance(p, str) and p.strip()]

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

    def _dispatch_doc_paths(
        self,
        role: str,
        doc_path: Path | None,
        assignment: dict | None,
        worktree: Path | None,
        root: Path,
    ) -> list[Path]:
        """Target doc paths for a dispatch, shifted into the writer's view.

        Devon always works on its manifest paths; other writer dispatches
        (Shield WRITE) get the executor's main-tree absolutes shifted into
        the isolated worktree so the Auditor matches what it observes there
        (B1 Prism blocker fix)."""
        doc_paths = self._target_paths(doc_path, assignment)
        if role == "devon":
            return [
                root / path
                for path in (assignment or {}).get("manifest", {}).get("allowed_paths", [])
            ]
        if worktree is not None:
            return [
                root / p.relative_to(self.repo)
                if p.is_absolute() and p.is_relative_to(self.repo)
                else p
                for p in doc_paths
            ]
        return doc_paths

    # -- AgentBackend -------------------------------------------------------

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,  # pylint: disable=too-many-locals
        doc_path: Path | None,
        assignment: dict | None = None,
        worktree: Path | None = None,
    ) -> dict:
        # IF-ENVELOPE-002 prerequisite: every result dict returning through
        # this method carries the agent's exact final reply text as
        # raw_output (declared dispatches only) — see _attach_raw_output;
        # the Runtime's collection face classifies that text itself.
        # B1 (issue #2): a writer dispatch's whole repo view (spawn cwd,
        # Auditor baseline, Devon's manifest paths) shifts to the isolated
        # worktree so the agent literally works there; every other role
        # keeps observing the main tree.
        root = Path(worktree) if worktree is not None else self.repo
        name = AGENT_NAME.get(role)
        prompt = self._prompt(role, substate, doc, doc_path, assignment)
        console_input = os.environ.get("TRAC_AGENT_CONSOLE_INPUT")
        if name is None:
            return self._unknown_role_result(role, prompt, console_input)

        doc_paths = self._dispatch_doc_paths(role, doc_path, assignment, worktree, root)
        cleanup_infos: list = []
        proc = None
        # Reviewer verdict derivation: M-TEST review substates end in
        # "_REVIEW"; v0.5 M-IMPL Prism review substates do not (PRISM_PLAN/
        # PRISM_RED/PRISM_FINAL). Without the derivation the opencode backend
        # produces no result["verdict"], the executor's _emit_dispatch_verdict
        # silently returns, no prism.verdict event is emitted, and the loop
        # deadlocks (reviewer_dispatched=True awaiting a verdict nothing will
        # produce - run 01KZTHE7 PRISM_PLAN, 2026-08-15, twice). DIAGNOSE is
        # excluded: its verdict is a classification routed via
        # _emit_diagnose_verdict, not a pass/revise document verdict.
        reviewer_assignment = substate.endswith("_REVIEW") or substate in (
            "PRISM_PLAN",
            "PRISM_RED",
            "PRISM_FINAL",
            "VERIFY_FINAL",
        )
        try:
            cleanup_infos.append(self._materialize(name))
            self._materialize_skills(assignment, cleanup_infos)
            self._materialize_templates(assignment, cleanup_infos)
            # IF-ENVELOPE-002: after ALL materialization writes, bind the
            # actual prepared artifacts (path+sha+token) + prompt and run the
            # Runtime-owned parity gate over every selected face — BEFORE the
            # agent subprocess is spawned. A rejection returns a failed
            # result (no spawn, no external invocation); the finally below
            # still cleans up. The repo root is trusted for agent scratch
            # files; only writes outside it are over-reach (M-DESIGN author
            # dispatches are additionally bounded by their Scaffold 宣言, see
            # the audit below). The target diff remains authoritative.
            prepared = self._prepare_parity(cleanup_infos, assignment, prompt, name=name)
            if not prepared.ok:
                return prepared.failure
            agent_dest = cleanup_infos[0]["dest"]
            allowed = self._allowed_paths(
                doc_paths, agent_dest, role, substate, assignment, root=root
            )
            # B64 (#82): assignment-injected ownership veto (repo-relative
            # patterns; e.g. Shield's SHIELD_FIX domain excludes the run's
            # red_test_paths). Data-driven — the backend applies what the
            # executor derived from Archer-authored manifests.
            forbidden = self._assignment_forbidden_paths(assignment)
            auditor = Auditor(root, allowed=allowed, forbidden=forbidden)
            baseline = auditor.baseline()
            author = substate in ("DRAFT", "RESPOND")
            # File-granular baseline for the batch B scaffold subset rule: a
            # directory-level status entry present here must not mask files the
            # agent creates inside it during the run.
            scaffold_baseline = (
                auditor.file_level(baseline) if author and len(doc_paths) > 1 else None
            )
            proc = self._dispatch_with_session_health(name, prompt, root)
            self._check_json(proc)
            if self._abnormal_step_finish(proc):
                # B17/#20 narrow (live T-003 GREEN): an interrupted session
                # is infrastructure, not an agent semantic failure — do not
                # let it reach DIAGNOSE as impl_defect and burn the budget.
                return self._attach_raw_output(
                    self._abnormal_step_result(proc, prompt, console_input),
                    proc,
                    assignment,
                )
            # D-35 (SC-D35 §2.2): M-TEST/M-IMPL reviewers deliver findings via
            # a structured final JSON payload. Parse before the audits so the
            # discussion gate can exempt a structured revise.
            review_failure = self._review_channel_failure(
                role, substate, assignment, proc, prompt, console_input
            )
            if review_failure is not None:
                return self._attach_raw_output(review_failure, proc, assignment)
            manifest_info, manifest_error = self._manifest_for_dispatch(
                role,
                substate,
                proc,
                prompt,
                console_input,
            )
            if manifest_error is not None:
                return self._attach_raw_output(manifest_error, proc, assignment)
            result = self._audited_result(
                auditor,
                baseline,
                scaffold_baseline,
                proc,
                role,
                substate,
                doc_paths,
                prompt,
                console_input,
                author,
                reviewer_assignment,
                structured_findings=self._has_structured_findings(
                    role, substate, assignment, proc
                ),
            )
            return self._attach_raw_output(
                self._attach_dispatch_metadata(
                    self._merge_review_payload(
                        result, role, substate, assignment, proc, prompt, console_input
                    ),
                    role,
                    substate,
                    manifest_info,
                    assignment,
                    parity=prepared.evidence,
                ),
                proc,
                assignment,
            )
        except OpencodeError as exc:
            return self._attach_raw_output(
                self._opencode_error_result(
                    exc,
                    proc,
                    prompt,
                    console_input,
                    doc_paths,
                    reviewer_assignment,
                ),
                proc,
                assignment,
            )
        except OSError as exc:
            return self._attach_raw_output(
                self._filesystem_error_result(exc, proc, prompt, console_input),
                proc,
                assignment,
            )
        finally:
            for info in cleanup_infos:
                self._cleanup_materialized(info)

    def _manifest_for_dispatch(
        self,
        role: str,
        substate: str,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> tuple[tuple[dict | None, str | None] | None, dict | None]:
        if role != "shield" or substate != "WRITE":
            return None, None
        manifest, commit_msg, error = self._extract_manifest(proc)
        if error is not None:
            return None, self._manifest_malformed_result(error, proc, prompt, console_input)
        return (manifest, commit_msg), None

    @staticmethod
    def _attach_dispatch_metadata(
        result: dict,
        role: str,
        substate: str,
        manifest_info: tuple[dict | None, str | None] | None,
        assignment: dict | None,
        parity: dict | None = None,
    ) -> dict:
        if result.get("status") == "done" and manifest_info is not None:
            manifest, commit_msg = manifest_info
            result["artifact_manifest"] = manifest
            result["suggested_commit_message"] = commit_msg
        # D-29 anti-self-report triple ③: echo the assigned criteria-pack
        # identity so the executor's mismatch check passes (mirrors
        # FakeBackend lines 120-123).  Only Prism M-TEST PRISM_REVIEW
        # carries a criteria pack today.
        if role == "prism" and substate in (
            "PRISM_REVIEW",
            "PRISM_PLAN",
            "PRISM_RED",
            "PRISM_FINAL",
            "VERIFY_FINAL",
            "DIAGNOSE",
        ):
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result.setdefault("criteria_pack", dict(assigned_pack))
        # IF-ENVELOPE-002: on a passed declared dispatch, attach the prepared
        # parity provenance (consistent face map + actual-bytes artifact
        # manifest + prompt digest) as audit evidence.
        if parity is not None:
            result.setdefault("parity", parity)
        return result

    def _allowed_paths(
        self,
        doc_paths: list[Path],
        agent_dest: Path,
        role: str,
        substate: str,
        assignment: dict | None = None,
        root: Path | None = None,
    ) -> list[Path | str | None]:
        """Audit whitelist = code dirs (project.toml [layout]) + commentable docs + agent_dest.

        ``root`` is the Auditor's repo (the isolated worktree for writer
        dispatches, else the main tree) — every path MUST resolve under
        it or the audit mis-flags every legal write as over-reach (B1
        Prism blocker fix). agent_dest stays a main-repo deployment
        path on purpose: it is gitignored, so the worktree's git status
        never reports writes there.
        """
        base = root if root is not None else self.repo
        commentable = self._commentable_doc_paths(role, base)
        if role == "shield":
            allowed = [base / d for d in layout_paths(base, "shield")]
            if substate == "WRITE":
                # WRITE target docs (incl. acceptance.md, not a COMMENTABLE_DOCS
                # entry) are whitelisted; replies there are discussion-checked.
                allowed = [*doc_paths, *allowed]
            # SHIELD_FIX may name diagnosed test files outside the Shield
            # [layout] dirs (e.g. a unit-level RED contract test); the
            # dispatch grants exactly those via manifest.allowed_paths and
            # the audit must honor the grant (Fix-M pattern, shield side).
            manifest_paths = self._manifest_allowed_paths(assignment, base)
            return [*commentable, *allowed, agent_dest, *manifest_paths]
        if role == "devon":
            devon_dirs = [base / d for d in layout_paths(base, "devon")]
            manifest_paths = self._manifest_allowed_paths(assignment, base)
            return [*commentable, agent_dest, *devon_dirs, *manifest_paths]
        return [*doc_paths, agent_dest, base]

    @staticmethod
    def _manifest_allowed_paths(assignment: dict | None, root: Path | None = None) -> list[Path]:
        """Explicit write whitelist from the assignment manifest (Archer-authored
        per-task allowed_paths); audit must honor it in addition to the coarse
        [layout] dirs, else a compliant out-of-layout write (e.g. a CI workflow
        path) is mis-flagged as over-reach and force-rolled-back."""
        manifest = (assignment or {}).get("manifest") or {}
        base = root if root is not None else Path(".")
        return [base / p for p in (manifest.get("allowed_paths") or [])]

    def _unknown_role_result(self, role: str, prompt: str, console_input: str | None) -> dict:
        # Unknown role (no AGENT_NAME entry) has no opencode agent.
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": f"no opencode agent for role {role!r}",
            "failure_class": "provider_unavailable",
            "agent_io": self._capture_io(
                None,
                prompt,
                console_input,
                stderr=f"no opencode agent for role {role!r}",
            ),
        }

    def _target_paths(self, doc_path: Path | None, assignment: dict | None) -> list[Path]:
        """Doc set of this dispatch: the explicit target doc, else the
        assignment's ``docs`` set resolved against the version dir (a multi-doc
        M-DESIGN DRAFT legitimately writes all three design docs, flow.md §8).
        Single-doc stages are unaffected: they always carry ``doc_path``."""
        if doc_path is not None:
            return [doc_path]
        docs = (assignment or {}).get("docs")
        if not docs:
            return []
        vdir = paths.version_dir(paths.tracks_home(self.repo), self.version)
        return [vdir / str(name) for name in docs]

    def _audited_result(
        self,
        auditor: Auditor,
        baseline: set[str],
        scaffold_baseline: set[str] | None,
        proc: subprocess.CompletedProcess,
        role: str,
        substate: str,
        doc_paths: list[Path],
        prompt: str,
        console_input: str | None,
        author_assignment: bool,
        reviewer_assignment: bool,
        structured_findings: bool = False,
    ) -> dict:
        diff_ref = _capture_target_diffs(auditor, doc_paths, substate, proc)
        # Every dispatch gets ONE atomic audit/rollback decision (Blocker 2):
        # doc-delta + over-reach (+ batch B scaffold) checks run together; if
        # any fails, every agent-changed path is rolled back in one force pass.
        if role == "shield" and substate == "WRITE":
            guard = self._shield_write_audit(
                auditor,
                baseline,
                doc_paths,
                diff_ref,
                proc,
                prompt,
                console_input,
            )
            if guard is not None:
                return guard
        elif (
            guard := self._non_shield_write_audit(
                auditor,
                baseline,
                scaffold_baseline,
                doc_paths,
                role,
                author_assignment,
                diff_ref,
                proc,
                prompt,
                console_input,
            )
        ) is not None:
            return guard
        audit = self._discussion_audit_result(
            role,
            substate,
            doc_paths,
            diff_ref,
            proc,
            prompt,
            console_input,
            reviewer_assignment,
            structured_findings=structured_findings,
        )
        if audit is not None:
            return audit
        return self._success_result(
            AGENT_NAME[role],
            substate,
            doc_paths,
            diff_ref,
            proc,
            prompt,
            console_input,
            author_assignment,
            reviewer_assignment,
        )

    def _non_shield_write_audit(
        self,
        auditor: Auditor,
        baseline: set[str],
        scaffold_baseline: set[str] | None,
        doc_paths: list[Path],
        role: str,
        author_assignment: bool,
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Atomic post-write audit for every non-Shield role (Shield WRITE
        parity, Blocker 2): the commentable-doc discussion-only check and the
        generic over-reach + batch B scaffold checks are judged together, so
        the generic guard never early-returns and leaves an allowed-but-
        illegal doc edit behind.  Any failure force-rolls-back every agent-
        attributable path in ONE pass; pre-existing Human dirty content is
        restored byte-identical from the pre-dispatch snapshot.

        Blocker 1: the commentable-doc set is not filtered by post-state
        ``exists()`` - a doc the agent deleted or swapped for an exception
        type is still audited (``_check_doc_deltas`` is type-aware/no-follow),
        so deletions and type swaps roll back."""
        agent_changed = auditor.agent_changed_paths(baseline)
        doc_offending = self._check_doc_deltas(
            auditor, self._commentable_doc_paths(role), agent_changed
        )
        # ``_is_allowed`` only matches exact/prefix paths; repo-root trust
        # (``"."`` in the allowed set) must short-circuit to no over-reach,
        # mirroring Auditor.audit's FR-030 coarse-grain semantics.
        if "." in auditor.allowed:
            over_paths: list[str] = []
        else:
            over_paths = sorted(p for p in agent_changed if not auditor._is_allowed(p))
        scaffold_offending = self._scaffold_offending(
            auditor, scaffold_baseline, doc_paths, author_assignment
        )
        if not (doc_offending or over_paths or scaffold_offending):
            return None
        if scaffold_offending and not (doc_offending or over_paths):
            # Only the undeclared scaffold writes are non-compliant; the
            # declared target docs are compliant and MUST be preserved (user
            # contract: revert only non-compliant files, never the framework
            # files inside the declared doc-set). Roll back ONLY the offending
            # paths and tell the Agent exactly what was reverted and why, so
            # the re-dispatch does not recreate them. Restoring to the
            # pre-dispatch snapshot (not HEAD) is handled by the Auditor's
            # baseline capture for any pre-dirty offending path.
            rolled = auditor.rollback_agent_changes(
                baseline, new_changes=set(scaffold_offending), force=True
            )
            return self._undeclared_scaffold_result(
                diff_ref,
                scaffold_offending,
                proc,
                prompt,
                console_input,
                rolled=rolled,
            )
        # A non-discussion doc edit or a true over-reach invalidates the whole
        # run (not just the offending paths): force-roll back every
        # agent-attributable path in one pass, restoring Human's pre-dispatch
        # dirty content byte-identical from the snapshot.
        auditor.rollback_agent_changes(baseline, new_changes=agent_changed, force=True)
        return self._overreach_result(
            diff_ref,
            proc,
            prompt,
            console_input,
            evidence=self._overreach_evidence(doc_offending, over_paths),
        )

    @staticmethod
    def _overreach_evidence(doc_offending: list[str], over_paths: list[str]) -> str:
        """Combined evidence string for a failed write audit."""
        parts: list[str] = []
        if doc_offending:
            parts.append("non-discussion edit to " + ", ".join(sorted(doc_offending)))
        if over_paths:
            parts.append("over-reach: " + ", ".join(over_paths))
        return "; ".join(parts)

    def _commentable_doc_paths(self, role: str, root: Path | None = None) -> list[Path]:
        """Every COMMENTABLE_DOCS path for the role, including ones missing at
        dispatch or gone post-run (deleted/type-swapped docs must be audited)."""
        cdocs = COMMENTABLE_DOCS.get(role)
        if not cdocs:
            return []
        vdir = paths.version_dir(paths.tracks_home(root or self.repo), self.version)
        return [vdir / d for d in cdocs]

    def _scaffold_offending(
        self,
        auditor: Auditor,
        scaffold_baseline: set[str] | None,
        doc_paths: list[Path],
        author_assignment: bool,
    ) -> list[str]:
        """batch B scaffold offending paths (M-DESIGN author only): every
        run-produced write outside the doc-set, ``.opencode/**`` and
        ``tests/ground_truth/**`` that the freshly-written architecture.md
        Scaffold 宣言 does not enumerate."""
        if scaffold_baseline is None or not author_assignment or len(doc_paths) <= 1:
            return []
        arch = next((p for p in doc_paths if p.name == "architecture.md"), None)
        declared = (
            _scaffold_declared_paths(arch.read_text(encoding="utf-8"))
            if arch is not None and arch.exists()
            else set()
        )
        docset = {_rel(self.repo, path) for path in doc_paths}
        new_files = auditor.file_level(auditor.modified_files()) - scaffold_baseline
        return sorted(
            path
            for path in new_files
            if path not in docset
            and not path.startswith((_OPENCODE_PREFIX, _GROUND_TRUTH_PREFIX))
            and path not in declared
        )

    def _shield_write_audit(
        self,
        auditor: Auditor,
        baseline: set[str],
        doc_paths: list[Path],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Shield WRITE atomic audit: doc-delta + over-reach.

        Rollback granularity (D-37 disposition, v0.5 minimal landing):
        - Pure over-reach (over_paths only, adjudicated DENY or not adjudicated):
          revert ONLY the offending paths - the same partial pattern as the
          scaffold branch - so compliant test-asset writes survive in the
          working tree for the re-dispatch to build on (D-37: "回滚保留合规,
          拒绝理由必回流"; the 74-minute compliant rewrite of run 01KZTHE7
          attempt 3 was force-discarded over one wiki-file edit on 2026-08-15).
        - Doc-delta failure (bypassing the discussion protocol on a commentable
          doc) still invalidates the whole run: the write protocol itself was
          violated, so the tree is not trustworthy (fail-closed, unchanged)."""
        agent_changed = auditor.agent_changed_paths(baseline)
        doc_offending = self._check_doc_deltas(auditor, doc_paths, agent_changed)
        over_paths = sorted(p for p in agent_changed if not auditor._is_allowed(p))
        if doc_offending or over_paths:
            if over_paths and not doc_offending and adjudicate(self, "shield", over_paths):
                return None
            if over_paths and not doc_offending:
                auditor.rollback_agent_changes(
                    baseline, new_changes=set(over_paths), force=True
                )
            else:
                auditor.rollback_agent_changes(
                    baseline, new_changes=agent_changed, force=True
                )
            return self._overreach_result(
                diff_ref,
                proc,
                prompt,
                console_input,
                evidence=self._overreach_evidence(doc_offending, over_paths),
            )
        return None

    def _check_doc_deltas(
        self,
        auditor: Auditor,
        doc_paths: list[Path],
        agent_changed: set[str],
    ) -> list[str]:
        """Return repo-relative paths of assignment docs whose post-agent delta
        is NOT a canonical discussion reply.

        For each doc:
        - **Type-aware touch detection** (no-follow): a symlink or directory
          replacement is always offending.  For regular files, the agent
          touched the doc only if its current bytes differ from the pre-dispatch
          snapshot (pre-dirty) or it entered ``agent_changed`` (clean->changed).
        - **Delta validation against the pre-dispatch snapshot** (Blocker 1):
          when the doc was pre-dirty, the base text is the snapshot bytes (NOT
          HEAD); when clean at dispatch, HEAD is the correct base.  A canonical
          discussion reply appended to Human-dirty body content thus passes;
          removing/replacing Human content fails.
        """
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return [
            _rel(self.repo, doc)
            for doc in doc_paths
            if self._doc_offending(auditor, doc, agent_changed, head)
        ]

    def _doc_offending(
        self,
        auditor: Auditor,
        doc_path: Path,
        agent_changed: set[str],
        head: str,
    ) -> bool:
        """True if this assignment doc's post-agent delta is NOT a canonical
        discussion reply.

        Type-aware, no-follow (Blocker 3): a symlink or directory replacement
        is always offending.  For regular files, delegates to the pre-dirty
        (snapshot-based) or clean (HEAD-based) delta check."""
        if doc_path.is_symlink() or doc_path.is_dir():
            return True
        rel = _rel(self.repo, doc_path)
        snap = auditor.baseline_content(rel)
        if snap is not None:
            return self._predirty_doc_offending(doc_path, snap)
        if rel in agent_changed:
            return self._clean_doc_offending(doc_path, rel, head)
        return False

    def _predirty_doc_offending(self, doc_path: Path, snap: bytes) -> bool:
        """Blocker 1: a pre-dirty doc's delta is validated against the
        pre-dispatch byte snapshot, NOT HEAD.  A canonical discussion reply
        appended to Human-dirty body content passes; removing/replacing Human
        content fails."""
        try:
            current = doc_path.read_bytes()
        except OSError:
            return True
        if current == snap:
            return False  # not touched
        return not is_discussion_delta(snap, current)

    def _clean_doc_offending(
        self,
        doc_path: Path,
        rel: str,
        head: str,
    ) -> bool:
        """Clean at dispatch, now changed: HEAD is the correct base.  A doc
        new at HEAD (untracked) or whose delta is not discussion-only is
        offending."""
        try:
            current = doc_path.read_bytes()
        except OSError:
            return True
        head_bytes = _load_head_bytes(self.repo, rel, head)
        return head_bytes is None or not is_discussion_delta(head_bytes, current)

    def _discussion_audit_result(
        self,
        role: str,
        substate: str,
        doc_paths: list[Path],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
        reviewer_assignment: bool,
        structured_findings: bool = False,
    ) -> dict | None:
        """Discussion-state audits (flow.md 不变量 6 / arch.md 永不信自述): the
        outcome is classified from the doc's discussion state, never the
        agent's report. Returns the failed-outcome result, or None to pass.

        D-35 (SC-D35 §2.1): M-TEST/M-IMPL reviewers may deliver findings via
        the structured manifest channel instead of doc-anchored threads;
        ``structured_findings`` marks a validated payload, which exempts the
        revise from the doc-thread requirement. M-DESIGN never sets it
        (doc-anchored channel unchanged, AC-FR0240-04)."""
        if substate == "DRAFT":
            # Author DRAFT (live run041): every thread the author initiated
            # must be resolved before the dispatch may finish. RESPOND and
            # reviewer dispatches are exempt: replies/findings legitimately
            # stay open for the other party's next round.
            offending = _unresolved_role_threads(doc_paths, role)
            if offending:
                return self._unresolved_threads_result(
                    diff_ref,
                    offending,
                    proc,
                    prompt,
                    console_input,
                )
        if reviewer_assignment:
            # Reviewer contract (live run042): a REVISE verdict must anchor
            # every blocking finding as a discussion thread the reviewer
            # INITIATED via `trac discuss start`; a bare revise gives the
            # author's RESPOND nothing anchored to reply to. Verdict revise
            # with no open reviewer-initiated thread in the doc-set ->
            # classified failure; verdict pass stays legal without threads.
            ready, blockers = check_ready(_docset_text(doc_paths))
            if (
                not ready
                and not _unresolved_role_threads(doc_paths, role)
                and not structured_findings
            ):
                return self._revise_without_findings_result(
                    diff_ref,
                    role,
                    blockers,
                    proc,
                    prompt,
                    console_input,
                )
        return None

    def _overreach_result(
        self,
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
        evidence: str,
    ) -> dict:
        # D-37 lesson (run 01KZTHE7, 2026-08-15): the generic message hides the
        # offending path from the escalation reason and the loop log; humans
        # had to dig the outcome blob to learn which file tripped the audit.
        # Keep audit_evidence verbatim in self_report so FR-0210 last_failure,
        # the escalation reason and the log line all carry the concrete path.
        # AC-FR0237-01/02 (T-018): declare rollback='atomic' (whole-round
        # rollback, no partial writes survive) and a structured rejected_paths
        # list so the report/audit trail can render WHICH paths were rejected
        # and THAT the rollback was atomic.  The paths are recovered from the
        # evidence string (the single source produced by _overreach_evidence),
        # so callers thread no parallel structure.
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": (
                f"{evidence}; agent changes rolled back"
                if evidence
                else "over-reach detected; agent changes rolled back"
            ),
            "diff_ref": diff_ref,
            "audit_evidence": evidence,
            "failure_class": "over_reach",
            "rollback": "atomic",
            "rejected_paths": self._paths_from_evidence(evidence),
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    @staticmethod
    def _paths_from_evidence(evidence: str) -> list[str]:
        """Recover the offending paths from an over-reach evidence string.

        The evidence format is the closed contract produced by
        ``_overreach_evidence``: up to two clauses joined by ``"; "`` -- a
        ``non-discussion edit to <p1>, <p2>`` clause (doc-offending paths) and
        an ``over-reach: <p3>, <p4>`` clause (out-of-scope paths).  Recovering
        the structured list here lets ``_overreach_result`` carry
        ``rejected_paths`` without every caller threading a parallel list,
        while keeping the evidence string the single human-readable source.
        Returns an empty list for evidence that carries no parseable paths
        (e.g. a bare ``"over-reach evidence"`` message from a guard test).
        """
        if not evidence:
            return []
        paths: list[str] = []
        for clause in evidence.split("; "):
            if clause.startswith("non-discussion edit to "):
                rest = clause[len("non-discussion edit to "):]
                paths.extend(p for p in rest.split(", ") if p)
            elif clause.startswith("over-reach: "):
                rest = clause[len("over-reach: "):]
                paths.extend(p for p in rest.split(", ") if p)
        return paths

    def _undeclared_scaffold_result(
        self,
        diff_ref: str | None,
        offending: list[str],
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
        rolled: list[str] | None = None,
    ) -> dict:
        paths = ", ".join(offending)
        rolled_paths = ", ".join(rolled) if rolled else paths
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": (
                f"undeclared scaffold writes: {paths}; the non-compliant file(s) "
                f"({rolled_paths}) were rolled back and the declared target "
                f"docs were preserved. Do not recreate {paths} on retry - they "
                f"are not enumerated in the architecture.md Scaffold 宣言."
            ),
            "diff_ref": diff_ref,
            "audit_evidence": (
                f"undeclared_scaffold: {paths}; rolled_back: {rolled_paths} "
                f"(not declared in architecture.md Scaffold 宣言; declared "
                f"target docs preserved)"
            ),
            "failure_class": "undeclared_scaffold",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _unresolved_threads_result(
        self,
        diff_ref: str | None,
        offending: list[str],
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        threads = ", ".join(offending)
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": f"author-initiated discussion thread(s) unresolved: {threads}",
            "diff_ref": diff_ref,
            "audit_evidence": f"unresolved_threads: {threads}",
            "failure_class": "unresolved_threads",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _revise_without_findings_result(
        self,
        diff_ref: str | None,
        role: str,
        blockers: tuple,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        # No `verdict` key: a bare revise is not a produced verdict, so the
        # executor emits no verdict event and the machine's failed-outcome
        # retry path applies (attempt consumed, evidence into re-dispatch).
        threads = ", ".join(blockers)
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": "revise verdict must anchor findings via `trac discuss "
            f"start`: open each blocking finding as a discussion "
            f"thread before returning revise (open: {threads})",
            "diff_ref": diff_ref,
            "audit_evidence": (
                f"revise_without_findings: verdict=revise, open "
                f"thread(s) {threads}, none initiated by reviewer "
                f"{AGENT_NAME[role]}"
            ),
            "failure_class": "revise_without_findings",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _success_result(
        self,
        name: str,
        substate: str,
        doc_paths: list[Path],
        diff_ref: str | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
        author_assignment: bool,
        reviewer_assignment: bool,
    ) -> dict:
        artifact_ref = None
        if author_assignment and doc_paths:
            # Single target doc names the doc; a multi-doc assignment (the
            # M-DESIGN trio) names the version dir holding the whole set.
            artifact_ref = str(doc_paths[0].parent) if len(doc_paths) > 1 else str(doc_paths[0])
        result = {
            "status": "done",
            "artifact_ref": artifact_ref,
            "self_report": f"{name} completed {substate}",
            "diff_ref": diff_ref,
            "agent_io": self._capture_io(proc, prompt, console_input),
        }
        if name == "Devon" and substate in ("RED", "GREEN", "REFACTOR"):
            result.update(
                extract_devon_evidence(
                    proc,
                    self._final_text_event,
                    # Shape-aware (2026-08-27): Devon manifests carry
                    # phase/changed_paths; context-echo JSON echoed after
                    # the manifest must not win the pick.
                    lambda t: self._first_json_object(t, ("phase", "changed_paths")),
                )
            )
        if name == "Prism" and substate == "DIAGNOSE":
            # Real-channel DIAGNOSE (the fake channel uses the simulate
            # token): without a verdict the outcome deadlocks (run 01KZTHE7
            # T-008). The full diagnosis payload rides along so the fixer
            # dispatch gets the actual analysis, not just the label.
            diagnosis = self._diagnose_classification_from(proc)
            if diagnosis is not None:
                result["verdict"] = diagnosis["classification"]
                result["diagnosis"] = {
                    "reason": diagnosis.get("reason") or "",
                    "evidence": diagnosis.get("evidence") or "",
                }
        return self._enrich_discussion(
            result,
            doc_paths,
            substate,
            reviewer_assignment,
        )

    # #89 单一真相源在 kernel（assignment 注入 + 校验共用）；类属性保留
    # 为既有引用点的稳定别名。
    _DIAGNOSE_CLASSIFICATIONS = DIAGNOSE_CLASSIFICATIONS

    # D-35 (SC-D35 §2.2) structured review payload contract. Single-source
    # delegation: the field sets and the 140-char caps are owned by
    # kernel.envelope (the schema injected into dispatch assignments), so
    # the mechanical checks and the declared schema cannot drift apart.
    _REVIEW_FINDING_FIELDS = REVIEW_FINDING_FIELDS
    _REVIEW_SUMMARY_MAX = REVIEW_SUMMARY_MAX
    _REVIEW_SUBSTATES = (
        "PRISM_REVIEW",
        "PRISM_PLAN",
        "PRISM_RED",
        "PRISM_FINAL",
        "VERIFY_FINAL",
    )
    _REVIEW_KEYS = (
        "review_summary",
        "findings",
        "review_body",
        "defect_classification",
        "discussion_refs",
        "review_ref",
        "candidate_sha",
    )

    @classmethod
    def _review_dispatch(cls, role: str, substate: str, assignment: dict | None) -> bool:
        """A Prism review dispatch whose stage is known (D-35: the executor
        threads ``stage`` into the assignment context)."""
        return (
            role == "prism"
            and substate in cls._REVIEW_SUBSTATES
            and (assignment or {}).get("stage") is not None
        )

    @classmethod
    def _structured_channel(cls, role: str, substate: str, assignment: dict | None) -> bool:
        """M-TEST/M-IMPL review: the structured payload is REQUIRED on revise.
        M-DESIGN keeps the doc-anchored channel (AC-FR0240-04)."""
        return (
            cls._review_dispatch(role, substate, assignment)
            and (assignment or {}).get("stage") in ("M-TEST", "M-IMPL", "M-VERIFY")
        )

    def _review_channel_failure(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Early D-35 gate: a malformed structured payload fails the dispatch
        before the audits run (strict channel only; M-DESIGN never rejects)."""
        if not self._structured_channel(role, substate, assignment):
            return None
        payload, error = self._prism_review_payload_from(proc)
        if substate == "VERIFY_FINAL" and payload is None and error is None:
            error = "VERIFY_FINAL requires a valid pass/revise review payload"
        if error is None:
            return None
        return self._review_payload_malformed_result(error, proc, prompt, console_input)

    def _has_structured_findings(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
    ) -> bool:
        """Whether the structured payload carries validated findings — the
        discussion gate exempts such a revise from the doc-thread rule."""
        if not self._structured_channel(role, substate, assignment):
            return False
        payload, error = self._prism_review_payload_from(proc)
        return error is None and bool(payload and payload.get("findings"))

    def _merge_review_payload(
        self,
        result: dict,
        role: str,
        substate: str,
        assignment: dict | None,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        """Merge the structured review payload into a done result.

        Strict channel (M-TEST/M-IMPL): the payload's verdict is Prism's own
        judgment and overrides the discussion-derived one; a revise with no
        payload is a contract violation (AC-FR0240-01). M-DESIGN: payload
        fields are threaded through when present but never required and never
        rejected (AC-FR0240-04)."""
        if not self._review_dispatch(role, substate, assignment):
            return result
        if result.get("status") != "done":
            return result
        payload, _ = self._prism_review_payload_from(proc)
        if payload is None:
            if (
                self._structured_channel(role, substate, assignment)
                and result.get("verdict") == "revise"
            ):
                return self._review_payload_malformed_result(
                    "revise verdict on the structured channel must end with the "
                    "review JSON payload (verdict/review_summary/findings/"
                    "review_body; SC-D35 §2.2)",
                    proc,
                    prompt,
                    console_input,
                )
            return result
        for key in self._REVIEW_KEYS:
            if key in payload:
                result[key] = payload[key]
        if not self._structured_channel(role, substate, assignment):
            return result
        # PRISM-D35-R1-ADV4 (R2-04 wording): a JSON "pass" must not override
        # a derived revise while the docset discussion is not ready —
        # check_ready covers ALL open threads on the docset (any initiator),
        # so a leftover thread holds the revise until someone closes it
        # (bounded by the attempt budget; re-dispatch targets the author,
        # not the reviewer, so no self-deadlock).
        if payload["verdict"] == "pass" and result.get("verdict") == "revise":
            return result
        result["verdict"] = payload["verdict"]
        return result

    @classmethod
    def _validate_review_finding(cls, finding: object, idx: int) -> str | None:
        # Canonical semantics live in kernel.envelope (the injected schema).
        return validate_review_finding(finding, idx)

    @classmethod
    def _validate_review_payload(cls, payload: dict) -> str | None:
        # Canonical semantics live in kernel.envelope (the injected schema).
        return validate_review_payload(payload)

    @classmethod
    def _envelope_reply_payload(
        cls, text: object
    ) -> tuple[dict | None, str | None, bool]:
        """IF-ENVELOPE-001: when the final reply carries a tracks-envelope
        block, the payload IS the structured reply (single deterministic
        path, payload schema validated against the kernel registry). Returns
        ``(payload, error, found)``; ``found=False`` keeps the legacy
        bare-JSON channels — a missing block is a compliance miss, not
        ambiguity, and the Runtime's declared-dispatch gate owns the
        hard-fail policy for structural breakage."""
        if not isinstance(text, str) or "tracks-envelope" not in text:
            return None, None, False
        try:
            parsed = parse_agent_output(text)
        except EnvelopeFormatError as exc:
            if exc.kind == "no_envelope_block":
                return None, None, False
            return None, f"envelope format error ({exc.kind}): {exc.detail}", True
        payload = parsed.get("payload")
        return (payload if isinstance(payload, dict) else None), None, True

    @classmethod
    def _review_payload_from_reply(cls, text: object) -> tuple[dict | None, str | None]:
        """Structured reply payload selection (single deterministic path).

        Envelope block present: the block's payload IS the reply, validated
        against the kernel registry. No block: legacy shape-aware bare-JSON
        scan (declared-dispatch hard enforcement stays with the Runtime
        gate). Returns ``(payload, None)`` when found, ``(None, error)`` on
        a contract violation, ``(None, None)`` when no payload was attempted
        (legal absence — callers decide whether that is allowed)."""
        payload, env_error, env_found = cls._envelope_reply_payload(text)
        if env_found:
            if env_error is not None:
                return None, env_error
            if not isinstance(payload, dict) or "verdict" not in payload:
                return None, (
                    "envelope payload for a review kind must carry "
                    "verdict/review_summary/findings/review_body (SC-D35 §2.2)"
                )
            return payload, None
        if not isinstance(text, str):
            return None, None
        # Shape-aware first (the review JSON carries "verdict"); the blind
        # last-wins pick stays as fallback so the existing legal-absence
        # semantics ("verdict" not in payload) is preserved.
        payload = cls._first_json_object(text.strip(), ("verdict",)) or (
            cls._first_json_object(text.strip())
        )
        if not isinstance(payload, dict) or "verdict" not in payload:
            return None, None
        return payload, None

    @classmethod
    def _prism_review_payload_from(
        cls,
        proc: subprocess.CompletedProcess,
    ) -> tuple[dict | None, str | None]:
        """Extract + validate the structured review JSON from the reviewer's
        final reply (SC-D35 §2.2, same final-text bare-JSON convention as
        DIAGNOSE). Returns ``(payload, None)`` when valid, ``(None, error)``
        on a contract violation, and ``(None, None)`` when no payload was
        attempted (legal absence — callers decide whether that is allowed)."""
        event = cls._final_text_event(proc)
        part = event.get("part") if isinstance(event, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        payload, error = cls._review_payload_from_reply(text)
        if error is not None or payload is None:
            return None, error
        if payload.get("verdict") not in ("pass", "revise"):
            return None, (
                f"review payload verdict must be 'pass'|'revise', "
                f"got {payload.get('verdict')!r}"
            )
        error = cls._validate_review_payload(payload)
        if error is not None:
            return None, error
        return payload, None

    def _review_payload_malformed_result(
        self,
        error: str,
        proc: subprocess.CompletedProcess,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": f"review payload malformed: {error}",
            "audit_evidence": f"review_payload_malformed: {error}",
            "failure_class": "manifest_malformed",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

    def _diagnose_payload_from_reply(self, text: object) -> dict | None:
        """DIAGNOSE payload selection — envelope block first (single
        deterministic path), legacy shape-aware bare-JSON scan otherwise."""
        payload, env_error, env_found = self._envelope_reply_payload(text)
        if env_found:
            if env_error is None and isinstance(payload, dict):
                return payload
            return None
        if not isinstance(text, str):
            return None
        # Shape-aware first (the diagnostic carries "classification"); blind
        # last-wins stays as fallback for the no-payload path.
        payload = self._first_json_object(text.strip(), ("classification",)) or (
            self._first_json_object(text.strip())
        )
        return payload if isinstance(payload, dict) else None

    def _diagnose_classification_from(self, proc) -> dict | None:
        """Extract the skill-contract DIAGNOSE JSON ({"classification",
        "reason", "evidence"}) from the Prism final reply. The full payload
        flows onward so the fixer dispatch receives the diagnostic's actual
        analysis, not just the classification label."""
        event = self._final_text_event(proc)
        part = event.get("part") if isinstance(event, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        payload = self._diagnose_payload_from_reply(text)
        if not isinstance(payload, dict):
            return None
        if payload.get("classification") not in self._DIAGNOSE_CLASSIFICATIONS:
            return None
        return payload

    @staticmethod
    def _enrich_discussion(
        result: dict,
        doc_paths: list[Path],
        substate: str,
        reviewer_assignment: bool,
    ) -> dict:
        if reviewer_assignment or substate == "TRIAGE":
            text = _docset_text(doc_paths)
            result["discussion_evidence"] = _discussion_snapshot(text)
            if reviewer_assignment:
                ready, _ = check_ready(text)
                result["verdict"] = "pass" if ready else "revise"
        return result

    def _opencode_error_result(
        self,
        exc: OpencodeError,
        proc: subprocess.CompletedProcess | None,
        prompt: str,
        console_input: str | None,
        doc_paths: list[Path],
        reviewer_assignment: bool,
    ) -> dict:
        result = {
            "status": "failed",
            "artifact_ref": None,
            "self_report": redact(str(exc)),
            "failure_class": exc.failure_class,
        }
        result["agent_io"] = self._capture_io(
            proc,
            prompt,
            console_input,
            stdout=exc.stdout,
            stderr=exc.stderr,
        )
        return self._enrich_failure_discussion(
            result,
            doc_paths,
            reviewer_assignment,
        )

    @staticmethod
    def _enrich_failure_discussion(
        result: dict,
        doc_paths: list[Path],
        reviewer_assignment: bool,
    ) -> dict:
        if reviewer_assignment and doc_paths:
            text = _docset_text(doc_paths)
            if text:
                result["discussion_evidence"] = _discussion_snapshot(text)
        return result

    def _filesystem_error_result(
        self,
        exc: OSError,
        proc: subprocess.CompletedProcess | None,
        prompt: str,
        console_input: str | None,
    ) -> dict:
        return {
            "status": "failed",
            "artifact_ref": None,
            "self_report": redact(str(exc)),
            "failure_class": "filesystem",
            "agent_io": self._capture_io(
                proc,
                prompt,
                console_input,
                stderr=str(exc),
            ),
        }

    def _cleanup_materialized(self, materialized: dict | None) -> None:
        if materialized is not None:
            self._cleanup(materialized)

    def _prepare_parity(
        self, cleanup_infos: list, assignment: dict | None, prompt: str, name: str | None = None
    ) -> PreparedContext:
        """(E) IF-ENVELOPE-002 preparation/check seam, run after ALL
        materialization writes and immediately before the agent spawn.

        Builds the artifact manifest bound to the actual written bytes
        (path + sha256 + frontmatter token), read-backs every prepared path
        from disk (a raced overwrite between preparation and execution is a
        dispatch failure on its own evidence), and asks the kernel parity
        gate to compare every selected face — the four static faces plus
        every selected artifact face (agent definition + every named
        skill/template, ALL of them, not just the first) — against the
        Runtime validator. A selection whose materialization produced no
        entry (absent canonical file) is a genuinely absent REQUIRED face —
        no file/version evidence is invented for it. A rejection returns a
        failed result carrying the manifest and mismatches for audit
        (``parity_rejected``); the caller returns it without spawning and
        the existing cleanup finally still runs. Undeclared dispatches skip
        the gate entirely (today's semantics).
        """
        if not is_declared_assignment(assignment):
            return PreparedContext(ok=True)
        entries = [
            info["manifest"]
            for info in cleanup_infos
            if info is not None and info.get("manifest")
        ]
        expected = ([agent_face(name)] if name else []) + [
            skill_face(skill_name)
            for skill_name in _skill_names(assignment)
        ] + [template_face(kind) for kind in _template_kinds(assignment)]
        verdict = check_prepared_parity(
            assignment,
            entries,
            expected,
            prompt=prompt,
            readback=readback_mismatches(entries),
        )
        if verdict["consistent"]:
            return PreparedContext(ok=True, evidence=parity_evidence(verdict))
        return PreparedContext(
            ok=False,
            failure=parity_failure_result(assignment, verdict["mismatches"], evidence=verdict),
        )

    # -- materialize / cleanup (ARCH §4c) -----------------------------------

    @staticmethod
    def _capture_io(
        proc: subprocess.CompletedProcess | None,
        prompt: str = "",
        console_input: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> dict:
        if proc is not None:
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        stdout = _text(stdout)
        stderr = _text(stderr)
        raw_stdout, raw_stderr = stdout, stderr
        console = None if console_input is None else _text(console_input)
        return {
            "stdout": redact(stdout),
            "stderr": redact(stderr),
            "stdout_bytes": len(raw_stdout.encode("utf-8")),
            "stderr_bytes": len(raw_stderr.encode("utf-8")),
            "prompt": redact(prompt),
            "console_input": redact(console) if console is not None else None,
        }

    def _materialize(self, name: str) -> dict:
        """Copy canonical prompt to opencode discovery path; back up any existing
        file so Human's agent is never silently clobbered (restored on cleanup)."""
        src = self._canonical / f"{name}.md"
        if not src.exists():
            raise OpencodeError("opencode_missing", f"canonical prompt not found: {src}")
        dest_dir = self.repo / ".opencode" / "agents"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": agent_face(name),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        # IF-ENVELOPE-002 prepare binding: hash+token from the bytes actually
        # written (never re-scanned from the canonical source).
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _materialize_skills(self, assignment: dict | None, cleanup_infos: list) -> None:
        """Materialize every skill of the assignment (batch B: ``skills`` list,
        backward compatible with the single ``skill`` string); each materialized
        skill registers for cleanup the moment it is written (ARCH §4c)."""
        for skill_name in _skill_names(assignment):
            cleanup_infos.append(self._materialize_skill(skill_name))

    def _materialize_skill(self, skill_name: str) -> dict | None:
        """Materialize one skill to opencode's discovery path for progressive
        disclosure."""
        src = self._canonical.parent / "skills" / skill_name / "SKILL.md"
        if not src.exists():
            return None
        dest_dir = self.repo / ".opencode" / "skills" / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": skill_face(skill_name),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _materialize_templates(self, assignment: dict | None, cleanup_infos: list) -> None:
        """Materialize the assignment's canonical document templates into the
        host repo (live run043: a host repo has no tracks/templates/, so the
        templates travel with the dispatch exactly like the agent definition
        and skill; backup/restore on cleanup, ARCH §4c). Each file registers
        for cleanup the moment it is written, so a missing canonical kind
        mid-way still cleans up its predecessors."""
        declared = is_declared(assignment)
        for kind in _template_kinds(assignment):
            info = self._materialize_template(kind, declared=declared)
            if info is not None:
                cleanup_infos.append(info)

    def _materialize_template(self, kind: str, declared: bool = False) -> dict | None:
        """Copy canonical tracks/templates/{kind}.md to .opencode/templates/;
        a requested kind without a canonical template is a dispatch failure
        (parity with a missing canonical prompt), never a silent skip.

        On a DECLARED dispatch a missing canonical template is a genuinely
        absent REQUIRED parity face: materialize nothing and let the
        Runtime-owned gate reject it as ``missing`` (no invented file/version
        evidence); undeclared dispatches keep today's infra classification."""
        src = templating.template_path(kind)
        if not src.exists():
            if declared:
                return None
            raise OpencodeError("opencode_missing", f"canonical template not found: {src}")
        dest_dir = self.repo / ".opencode" / "templates"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.md"
        info = {
            "dest": dest,
            "backup": None,
            "existed": dest.exists(),
            "face": template_face(kind),
        }
        if dest.exists():
            info["backup"] = dest.read_bytes()
        data = src.read_bytes()
        dest.write_bytes(data)
        info["manifest"] = bind_artifact(info["face"], dest, data)
        return info

    def _cleanup(self, info: dict) -> None:
        dest = info["dest"]
        try:
            if info["existed"] and info["backup"] is not None:
                dest.write_bytes(info["backup"])  # restore Human's agent
            elif not info["existed"] and dest.exists():
                dest.unlink()  # remove what we created
        except OSError:
            pass  # terminal cleanup is best-effort

    # -- subprocess + failure matrix (ARCH §7) ------------------------------

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
            raise
        # 成功路径：从事件流提取 sessionID 并记录（幂等，同 id 不重写）。
        self._record_session(name, self._extract_session_id(proc.stdout or ""))
        return proc

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
            if OpencodeBackend._line_has_manifest(line):
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
        payload, perr = OpencodeBackend._manifest_payload(event)
        if perr is not None:
            return False
        include, ierr = OpencodeBackend._manifest_include(payload)
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
        for line in (proc.stdout or "").splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
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

    # -- BOOT-ATTRIBUTION-001: Shield WRITE manifest extraction -------------

    @staticmethod
    def _extract_manifest(
        proc: subprocess.CompletedProcess,
    ) -> tuple[dict | None, str | None, str | None]:
        """Parse Shield WRITE manifest from text events.

        Scans ALL type=text events in reverse order, not just the last.
        When opencode --auto enters a post-completion loop, the last text
        event may be "already completed" prose, not the manifest.
        """
        text_events = OpencodeBackend._all_text_events(proc)
        if not text_events:
            return None, None, "no type=text events found in stdout"
        last_error = "no text event contained a valid manifest"
        for text_event in reversed(text_events):
            payload, error = OpencodeBackend._manifest_payload(text_event)
            if error is not None:
                last_error = error
                continue
            include, error = OpencodeBackend._manifest_include(payload)
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
        events = OpencodeBackend._all_text_events(proc)
        return events[-1] if events else None

    @staticmethod
    def _manifest_payload(
        text_event: dict,
    ) -> tuple[dict | None, str | None]:
        part = text_event.get("part")
        if not isinstance(part, dict) or not isinstance(part.get("text"), str):
            return None, "final type=text event must contain part.text"
        text = part["text"].strip()
        payload = OpencodeBackend._first_json_object(text)
        if payload is None:
            return None, "final part.text must be a raw JSON object"
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
        return exc.pos >= OpencodeBackend._effective_len(text)

    @staticmethod
    def _repair_at(
        text: str, start: int, decoder: json.JSONDecoder, exc: json.JSONDecodeError
    ) -> tuple[dict, int] | None:
        """Tail missing-brace: try 1-3 `}` on the tail slice."""
        if not OpencodeBackend._is_tail_error(exc, text):
            return None
        eff = OpencodeBackend._effective_len(text)
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
            return OpencodeBackend._repair_at(text, pos, decoder, exc)

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
            res = OpencodeBackend._decode_at(text, i, dec)
            if res is None:
                i += 1
                continue
            obj, end = res
            payload, shaped = OpencodeBackend._update_payload(
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
            error = OpencodeBackend._manifest_item_error(index, item)
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

    # -- prompt construction ------------------------------------------------

    def _prompt(
        self,
        role: str,
        substate: str,
        doc: str | None,
        doc_path: Path | None,
        assignment: dict | None = None,
    ) -> str:
        docs = (assignment or {}).get("docs")
        if doc_path:
            target = str(doc_path)
        elif doc:
            target = doc
        elif docs:
            resolved = self._target_paths(doc_path, assignment)
            target = ", ".join(str(p) for p in resolved)
        else:
            target = ""
        assignment_context = self._assignment_context(assignment)
        return (
            f"Execute the Runtime assignment for role={role}, substate={substate}, "
            f"target={target}. Follow the materialized opencode agent definition for your "
            "role. Complete only this assignment, then stop." + assignment_context
        )

    def _assignment_context(self, assignment: dict | None) -> str:
        if not assignment:
            return ""
        lines = [
            "\n\n## Runtime assignment context",
            "以下 JSON 是 Runtime 事实，不是 Human 决定，也不能被 Agent 修改：",
            json.dumps(assignment, ensure_ascii=False, sort_keys=True),
        ]
        kinds = _template_kinds(assignment)
        if kinds:
            names = ", ".join(f"{kind}.md" for kind in kinds)
            lines.append(
                f"Runtime 已将本次 assignment 的文档模板物化到 .opencode/templates/（{names}）；"
                "草稿必须严格按对应模板起草，完整保留 YAML frontmatter。"
            )
        return "\n".join(lines)


    @classmethod
    def _final_reply_text(cls, proc: subprocess.CompletedProcess) -> str | None:
        """The agent's exact final reply text, verbatim (never normalized)."""
        event = cls._final_text_event(proc)
        part = event.get("part") if isinstance(event, dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        return text if isinstance(text, str) else None


    def _attach_raw_output(
        self,
        result: dict,
        proc: subprocess.CompletedProcess | None,
        assignment: dict | None = None,
    ) -> dict:
        """Attach the agent's exact original final reply text as raw_output.

        Declared dispatches only (IF-ENVELOPE-002 prerequisite): the
        Runtime's collection face classifies this text itself, so malformed
        and empty replies are preserved verbatim for that classification.
        The backend never reconstructs raw text from the normalized
        verdict/payload and never synthesizes a reply. Undeclared results
        are untouched — the legacy channels keep their exact shape.
        """
        if proc is None or not is_declared_assignment(assignment):
            return result
        if result.get("raw_output") is not None:
            return result  # an already-carried raw_output is never clobbered
        text = self._final_reply_text(proc)
        if text is not None:
            result["raw_output"] = text
        return result

    def finalize_act(
        self,
        result: dict,
        role: str,
        substate: str,
        assignment: dict | None = None,
    ) -> dict:
        """No-op: the real backend's raw_output is the actual subprocess reply
        attached verbatim at the transport return paths (see
        ``_attach_raw_output``); it is never re-encoded or reconstructed."""
        return result




def _skill_names(assignment: dict | None) -> list[str]:
    """Skills this dispatch materializes: an explicit ``skills`` list (batch B:
    the M-DESIGN author assignment carries several) wins over the legacy single
    ``skill`` string; falsy entries are skipped, [] means no skill."""
    assignment = assignment or {}
    if assignment.get("skills") is not None:
        return [name for name in assignment["skills"] if name]
    skill = assignment.get("skill")
    return [skill] if skill else []


def _template_kinds(assignment: dict | None) -> list[str]:
    """Document template kinds this dispatch materializes: an explicit
    ``templates`` list (M-DESIGN trio) wins over the single ``template_kind``
    (single-doc stages); None entries are skipped, None means no template."""
    assignment = assignment or {}
    if assignment.get("templates") is not None:
        return [kind for kind in assignment["templates"] if kind]
    kind = assignment.get("template_kind")
    return [kind] if kind else []


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _capture_target_diffs(
    auditor: Auditor, doc_paths: list[Path], substate: str, proc: subprocess.CompletedProcess
) -> str | None:
    """Capture the controlled diff of every target doc (the authoritative
    product) BEFORE any audit rollback can touch it, then enforce the
    author-must-produce contract on the captured set."""
    diffs = [auditor.target_diff(path) for path in doc_paths]
    _require_target_diff(doc_paths, diffs, substate, proc)
    return "\n".join(diff for diff in diffs if diff) or None


def _load_head_bytes(repo: Path, rel: str, head: str) -> bytes | None:
    """Load the content of ``rel`` as of ``head`` from git, or None if the
    path is not tracked at that commit (new/untracked file)."""
    proc = subprocess.run(
        ["git", "show", f"{head}:{rel}"],
        cwd=repo,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _require_target_diff(
    doc_paths: list[Path], diffs: list[str | None], substate: str, proc: subprocess.CompletedProcess
) -> None:
    """An author dispatch must leave a controlled diff; exit 0 with no product
    is classified, never trusted.

    DRAFT must produce a diff on EVERY doc of its target set (a draft that
    skips a doc has no product for it). RESPOND revises only the docs that
    carry findings - the reviewer may have flagged a subset, so a diff on ANY
    doc of the set satisfies the contract; a true no-change RESPOND (zero
    diffs across the whole set) still fails."""
    if substate not in ("DRAFT", "RESPOND"):
        return
    if substate == "RESPOND":
        # Any diff in the set is a real revision; only zero diffs across the
        # whole set is the no-product failure.
        if not doc_paths or not any(d is not None for d in diffs):
            raise OpencodeError(
                "no_target_diff",
                "exit 0 but the target doc-set has no diff",
                exit_code=0,
                stderr=proc.stderr,
            )
        return
    missing = [str(path) for path, diff in zip(doc_paths, diffs, strict=True) if diff is None]
    if not doc_paths or missing:
        raise OpencodeError(
            "no_target_diff",
            "exit 0 but the target doc-set has no diff"
            + (f": {', '.join(missing)}" if missing else ""),
            exit_code=0,
            stderr=proc.stderr,
        )


def _unresolved_role_threads(doc_paths: list[Path], role: str) -> list[str]:
    """Threads in the target doc-set INITIATED by ``role`` and not resolved.

    Audit predicate: ``speaker_key(initiator) == speaker_key(role)`` and
    ``status != "resolved"`` (open and reopen both block, as in the discussion
    gate), checked across the WHOLE doc-set. For author DRAFT dispatches any
    such thread is an offence (closure unverified); for reviewer dispatches
    these threads ARE the anchored findings a revise verdict must carry. With
    a single target doc the offenders are thread ids; a multi-doc set (the
    M-DESIGN trio) names them ``doc:T-NNN`` so the ids stay unambiguous."""
    role_key = speaker_key(role)
    multi = len(doc_paths) > 1
    offending = []
    for path in doc_paths:
        if not path.exists():
            continue
        for thread in parse_threads(path.read_text(encoding="utf-8")):
            if speaker_key(thread.initiator) == role_key and thread.status != "resolved":
                label = f"{path.name}:{thread.thread_id}" if multi else thread.thread_id
                offending.append(label)
    return offending


def _docset_text(doc_paths: list[Path]) -> str:
    """Discussion text of a target doc-set: one doc reads as itself (the
    pre-multi-doc behavior); a set concatenates every existing doc."""
    texts = [path.read_text(encoding="utf-8") for path in doc_paths if path.exists()]
    return "\n\n".join(texts)


def _discussion_snapshot(text: str) -> dict:
    ready, blockers = check_ready(text)

    def comment(node):
        return {
            "speaker": node.speaker,
            "body": node.body,
            "depth": node.depth,
            "children": [comment(child) for child in node.children],
        }

    return {
        "ready": ready,
        "blockers": list(blockers),
        "threads": [
            {
                "thread_id": thread.thread_id,
                "initiator": thread.initiator,
                "status": thread.status,
                "reply_count": thread.reply_count,
                "root": comment(thread.root),
            }
            for thread in parse_threads(text)
        ],
    }
