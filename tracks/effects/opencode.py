"""OpencodeBackend — real agent via `opencode run` subprocess (ARCH-003 §4/§6/§7).

Pipeline per dispatch: materialize canonical prompt -> baseline snapshot ->
`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`
(process group + timeout) -> parse JSON (diagnostic/truncation only) -> capture
target-file diff (authoritative product, ARCH §4b) -> post-run audit (ARCH §6).
Failures map to IF-003 §1a FailureClass. The agent's stdout JSON is NEVER the
product; the controlled target diff is.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
from pathlib import Path

from tracks.effects.audit import Auditor

# role (lowercase, IF-001 §5) -> opencode agent Name (capitalized, ARCH §4a).
# Lex is a real reviewer agent (FR-020, Aaron 扩容裁定): Scribe起草/Sage评审/
# Lex语义评审 — all three run on opencode (spec FR-020 §2, agents/Lex.md).
AGENT_NAME = {"scribe": "Scribe", "sage": "Sage", "lex": "Lex"}

DEFAULT_TIMEOUT = 600  # seconds


class OpencodeError(Exception):
    """A classified backend failure (failure_class per IF-003 §1a)."""

    def __init__(self, failure_class: str, message: str,
                 exit_code: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.failure_class = failure_class
        self.exit_code = exit_code
        self.stderr = stderr


class OpencodeBackend:
    """AgentBackend implemented by a real opencode subagent subprocess."""

    def __init__(self, repo: Path, version: str, timeout: int = DEFAULT_TIMEOUT,
                 model: str | None = None):
        self.repo = Path(repo)
        self.version = version
        self.timeout = timeout
        # Explicit provider/model (e.g. "opencode/deepseek-v4-flash-free") for
        # the live channel; when None, opencode resolves its own configured
        # default model (spec §3.1: provider/model by env, ARCH §4a).
        self.model = model
        self._canonical = Path(__file__).resolve().parent.parent / "agents"

    # -- AgentBackend -------------------------------------------------------

    def act(self, role: str, substate: str, doc: str | None,
            doc_path: Path | None) -> dict:
        name = AGENT_NAME.get(role)
        if name is None:
            # Unknown role (not Scribe/Sage/Lex) has no opencode agent.
            return {"status": "failed", "artifact_ref": None,
                    "self_report": f"no opencode agent for role {role!r}",
                    "failure_class": "provider_unavailable"}

        materialized = self._materialize(name)
        # Allowed write set = target doc + the materialized agent definition
        # (a Runtime artifact, created before baseline so also in the snapshot).
        auditor = Auditor(self.repo, allowed=[doc_path, materialized["dest"]])
        baseline = auditor.baseline()
        try:
            prompt = self._prompt(role, substate, doc, doc_path)
            proc = self._run(name, prompt)
            self._check_json(proc)
            diff_ref = auditor.target_diff(doc_path)
            if diff_ref is None:
                raise OpencodeError("no_target_diff",
                                    "exit 0 but target file has no diff",
                                    exit_code=0, stderr=proc.stderr)
            over = auditor.audit(baseline)
            if over:
                auditor.rollback_agent_changes(baseline)
                return {"status": "failed", "artifact_ref": None,
                        "self_report": "over-reach detected; agent changes rolled back",
                        "diff_ref": diff_ref, "audit_evidence": over,
                        "failure_class": "over_reach"}
            return {"status": "done", "artifact_ref": str(doc_path),
                    "self_report": f"{name} edited {doc}", "diff_ref": diff_ref}
        except OpencodeError as exc:
            return {"status": "failed", "artifact_ref": None,
                    "self_report": str(exc), "failure_class": exc.failure_class}
        finally:
            self._cleanup(materialized)

    # -- materialize / cleanup (ARCH §4c) -----------------------------------

    def _materialize(self, name: str) -> dict:
        """Copy canonical prompt to opencode discovery path; back up any existing
        file so Human's agent is never silently clobbered (restored on cleanup)."""
        src = self._canonical / f"{name}.md"
        if not src.exists():
            raise OpencodeError("opencode_missing",
                                f"canonical prompt not found: {src}")
        dest_dir = self.repo / ".opencode" / "agents"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"
        info = {"dest": dest, "backup": None, "existed": dest.exists()}
        if dest.exists():
            info["backup"] = dest.read_bytes()
        dest.write_bytes(src.read_bytes())
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

    def _run(self, name: str, prompt: str) -> subprocess.CompletedProcess:
        cmd = ["opencode", "run", "--agent", name, "--format", "json",
               "--dir", str(self.repo), "--auto", prompt]
        if self.model:
            cmd.extend(["--model", self.model])
        try:
            return subprocess.run(cmd, cwd=self.repo, capture_output=True,
                                  text=True, timeout=self.timeout,
                                  start_new_session=True)
        except FileNotFoundError as err:
            raise OpencodeError("opencode_missing",
                                "opencode executable not found") from err
        except subprocess.TimeoutExpired as exc:
            self._kill_group(exc)
            raise OpencodeError("timeout",
                                f"opencode timed out after {self.timeout}s") from exc

    @staticmethod
    def _kill_group(exc: subprocess.TimeoutExpired) -> None:
        # start_new_session=True -> the child leads its own process group.
        # Best-effort: subprocess.run already killed the direct child on timeout.
        with contextlib.suppress(ProcessLookupError, PermissionError, TypeError,
                                 OSError):
            os.killpg(os.getpgid(exc.cmd[0] if isinstance(exc.cmd, list) else 0),
                      signal.SIGKILL)

    def _check_json(self, proc: subprocess.CompletedProcess) -> None:
        """Classify the exit; JSON is diagnostic-only (product = target diff)."""
        if proc.returncode < 0:  # killed by signal (SIGINT/kill-9)
            raise OpencodeError("signal", f"killed by signal {-proc.returncode}",
                                exit_code=proc.returncode, stderr=proc.stderr)
        if proc.returncode != 0:
            if self._looks_provider_error(proc.stderr):
                raise OpencodeError("provider_unavailable",
                                    "provider/model/credentials unavailable",
                                    exit_code=proc.returncode, stderr=proc.stderr)
            raise OpencodeError("non_zero_exit",
                                f"opencode exited {proc.returncode}",
                                exit_code=proc.returncode, stderr=proc.stderr)
        # exit 0: stdout must be parseable JSON (events); truncation => failure.
        out = (proc.stdout or "").strip()
        if out and not self._parses_json(out):
            raise OpencodeError("json_truncated", "stdout JSON stream unparseable",
                                exit_code=0, stderr=proc.stderr)

    @staticmethod
    def _looks_provider_error(stderr: str) -> bool:
        low = (stderr or "").lower()
        return any(k in low for k in
                   ("provider", "model", "credential", "unauthorized", "api key",
                    "authentication", "401", "403"))

    @staticmethod
    def _parses_json(out: str) -> bool:
        try:
            json.loads(out)
            return True
        except json.JSONDecodeError:
            pass
        # NDJSON fallback: every non-empty line is a JSON object.
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if not lines:
            return False
        for ln in lines:
            try:
                json.loads(ln)
            except json.JSONDecodeError:
                return False
        return True

    # -- prompt construction ------------------------------------------------

    def _prompt(self, role: str, substate: str, doc: str | None,
                doc_path: Path | None) -> str:
        target = str(doc_path) if doc_path else (doc or "")
        if role == "scribe":
            return (f"撰写/修订 story 文档：{target}。按你的职责与 story 模板完成，"
                    f"仅编辑该目标文档，不要写其它文件。")
        if role == "sage":
            if substate in ("DRAFT", "RESPOND"):
                return (f"起草 spec 文档：{target}。按你的职责与 spec 模板完成，"
                        f"仅编辑该目标文档，不要写其它文件。")
            return (f"评审文档：{target}。用 trac discuss 在文档内结构化提出问题，"
                    f"直至收敛；除目标文档外不要写其它文件。")
        # Lex: semantic reviewer for spec/acceptance (FR-0020, Aaron 扩容裁定).
        # edit denied（frontmatter edit: deny）；评审意见经 inline-discussion 汇报。
        if role == "lex":
            return (f"语义评审文档：{target}。审查 spec/acceptance 的覆盖忠实性、"
                    f"可断言性与范围保真，用 trac discuss 在文档内结构化提出问题；"
                    f"不得编辑或写任何文件（frontmatter edit: deny）。")
        return f"处理文档：{target}（role={role}, substate={substate}）。"
