"""OpencodeBackend — real agent via `opencode run` subprocess (ARCH-003 §4/§6/§7).

Pipeline per dispatch: materialize canonical prompt -> baseline snapshot ->
`opencode run --agent <Name> --format json --dir <repo> --auto "<prompt>"`
(process group + timeout) -> parse JSON (diagnostic/truncation only) -> capture
the target doc-set diff (authoritative product, ARCH §4b; one doc normally, the
whole M-DESIGN trio for a multi-doc assignment) -> post-run audit (ARCH §6).
Failures map to IF-003 §1a FailureClass. The agent's stdout JSON is NEVER the
product; the controlled target diff is.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path

from tracks import paths
from tracks.discuss.gate import check_ready
from tracks.discuss.parser import parse_threads
from tracks.effects.audit import Auditor

# role (lowercase, IF-001 §5) -> opencode agent Name (capitalized, ARCH §4a).
# Every tracks role is a real opencode agent (spec FR-020 §2): Scribe起草 /
# Sage起草+评审 / Lex语义评审 / Archer设计三件套起草 / Prism设计评审.
AGENT_NAME = {"scribe": "Scribe", "sage": "Sage", "lex": "Lex",
              "archer": "Archer", "prism": "Prism"}

DEFAULT_TIMEOUT = 600  # seconds

_SECRET_VALUE = re.compile(
    r"(?i)((?:authorization|api[_ -]?key|access[_ -]?token|secret|password)"
    r"\s*[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?)([^\s,;\"']+)"
)
_ENV_SECRET_VALUE = re.compile(
    r"(?i)([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)"
    r"([\"']?\s*=\s*[\"']?)([^\s,;\"']+)"
)


def redact(text: str) -> str:
    """Redact credential-shaped values before evidence leaves the subprocess."""
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text or "")
    text = _ENV_SECRET_VALUE.sub(r"\1\2[REDACTED]", text)
    # Providers sometimes print a raw secret without its environment-variable
    # name. Only replace long values from explicitly secret-shaped variables.
    for name, value in os.environ.items():
        if value and len(value) >= 8 and re.search(
            r"(?i)(?:API_KEY|TOKEN|SECRET|PASSWORD|AUTH)", name
        ):
            text = text.replace(value, "[REDACTED]")
    return text


class OpencodeError(Exception):
    """A classified backend failure (failure_class per IF-003 §1a)."""

    def __init__(self, failure_class: str, message: str,
                 exit_code: int | None = None, stderr: str = "", stdout: str = ""):
        super().__init__(message)
        self.failure_class = failure_class
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout


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

    def act(self, role: str, substate: str, doc: str | None,  # pylint: disable=too-many-locals
             doc_path: Path | None, assignment: dict | None = None) -> dict:
        name = AGENT_NAME.get(role)
        prompt = self._prompt(role, substate, doc, doc_path, assignment)
        console_input = os.environ.get("TRAC_AGENT_CONSOLE_INPUT")
        if name is None:
            return self._unknown_role_result(role, prompt, console_input)

        doc_paths = self._target_paths(doc_path, assignment)
        cleanup_infos: list = []
        proc = None
        reviewer_assignment = substate.endswith("_REVIEW")
        try:
            cleanup_infos.append(self._materialize(name))
            skill_info = self._materialize_skill(assignment)
            if skill_info is not None:
                cleanup_infos.append(skill_info)
            # The repo root is trusted for agent scratch files; only writes
            # outside it are over-reach. The target diff remains authoritative.
            agent_dest = cleanup_infos[0]["dest"]
            auditor = Auditor(self.repo,
                              allowed=[*doc_paths, agent_dest, self.repo])
            baseline = auditor.baseline()
            proc = self._run(name, prompt)
            self._check_json(proc)
            return self._audited_result(
                auditor, baseline, proc, name, substate, doc_paths,
                prompt, console_input, substate in ("DRAFT", "RESPOND"),
                reviewer_assignment,
            )
        except OpencodeError as exc:
            return self._opencode_error_result(
                exc, proc, prompt, console_input, doc_paths, reviewer_assignment,
            )
        except OSError as exc:
            return self._filesystem_error_result(exc, proc, prompt, console_input)
        finally:
            for info in cleanup_infos:
                self._cleanup_materialized(info)

    def _unknown_role_result(
        self, role: str, prompt: str, console_input: str | None
    ) -> dict:
        # Unknown role (no AGENT_NAME entry) has no opencode agent.
        return {"status": "failed", "artifact_ref": None,
                "self_report": f"no opencode agent for role {role!r}",
                "failure_class": "provider_unavailable",
                "agent_io": self._capture_io(
                    None, prompt, console_input,
                    stderr=f"no opencode agent for role {role!r}",
                )}

    def _target_paths(self, doc_path: Path | None,
                      assignment: dict | None) -> list[Path]:
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
        self, auditor: Auditor, baseline: set[str], proc: subprocess.CompletedProcess,
        name: str, substate: str, doc_paths: list[Path], prompt: str,
        console_input: str | None, author_assignment: bool, reviewer_assignment: bool,
    ) -> dict:
        diffs = [auditor.target_diff(path) for path in doc_paths]
        missing = [str(path)
                   for path, diff in zip(doc_paths, diffs, strict=True)
                   if diff is None]
        diff_ref = "\n".join(diff for diff in diffs if diff) or None
        if author_assignment and (not doc_paths or missing):
            raise OpencodeError(
                "no_target_diff",
                "exit 0 but the target doc-set has no diff"
                + (f": {', '.join(missing)}" if missing else ""),
                exit_code=0, stderr=proc.stderr)
        over = auditor.audit(baseline)
        if over:
            auditor.rollback_agent_changes(baseline)
            return self._overreach_result(
                diff_ref, proc, prompt, console_input, over,
            )
        return self._success_result(
            name, substate, doc_paths, diff_ref, proc, prompt, console_input,
            author_assignment, reviewer_assignment,
        )

    def _overreach_result(
        self, diff_ref: str | None, proc: subprocess.CompletedProcess, prompt: str,
        console_input: str | None, evidence: str,
    ) -> dict:
        return {"status": "failed", "artifact_ref": None,
                "self_report": "over-reach detected; agent changes rolled back",
                "diff_ref": diff_ref, "audit_evidence": evidence,
                "failure_class": "over_reach",
                "agent_io": self._capture_io(proc, prompt, console_input)}

    def _success_result(
        self, name: str, substate: str, doc_paths: list[Path], diff_ref: str | None,
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
        author_assignment: bool, reviewer_assignment: bool,
    ) -> dict:
        artifact_ref = None
        if author_assignment and doc_paths:
            # Single target doc names the doc; a multi-doc assignment (the
            # M-DESIGN trio) names the version dir holding the whole set.
            artifact_ref = (str(doc_paths[0].parent) if len(doc_paths) > 1
                            else str(doc_paths[0]))
        result = {
            "status": "done",
            "artifact_ref": artifact_ref,
            "self_report": f"{name} completed {substate}",
            "diff_ref": diff_ref,
            "agent_io": self._capture_io(proc, prompt, console_input),
        }
        return self._enrich_discussion(
            result, doc_paths, substate, reviewer_assignment,
        )

    @staticmethod
    def _enrich_discussion(
        result: dict, doc_paths: list[Path], substate: str,
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
        self, exc: OpencodeError, proc: subprocess.CompletedProcess | None, prompt: str,
        console_input: str | None, doc_paths: list[Path], reviewer_assignment: bool,
    ) -> dict:
        result = {"status": "failed", "artifact_ref": None,
                  "self_report": redact(str(exc)), "failure_class": exc.failure_class}
        result["agent_io"] = self._capture_io(
            proc, prompt, console_input, stdout=exc.stdout, stderr=exc.stderr,
        )
        return self._enrich_failure_discussion(
            result, doc_paths, reviewer_assignment,
        )

    @staticmethod
    def _enrich_failure_discussion(
        result: dict, doc_paths: list[Path], reviewer_assignment: bool,
    ) -> dict:
        if reviewer_assignment and doc_paths:
            text = _docset_text(doc_paths)
            if text:
                result["discussion_evidence"] = _discussion_snapshot(text)
        return result

    def _filesystem_error_result(
        self, exc: OSError, proc: subprocess.CompletedProcess | None, prompt: str,
        console_input: str | None,
    ) -> dict:
        return {
            "status": "failed", "artifact_ref": None,
            "self_report": redact(str(exc)), "failure_class": "filesystem",
            "agent_io": self._capture_io(
                proc, prompt, console_input, stderr=str(exc),
            ),
        }

    def _cleanup_materialized(self, materialized: dict | None) -> None:
        if materialized is not None:
            self._cleanup(materialized)

    # -- materialize / cleanup (ARCH §4c) -----------------------------------

    @staticmethod
    def _capture_io(proc: subprocess.CompletedProcess | None, prompt: str = "",
                    console_input: str | None = None, stdout: str = "",
                    stderr: str = "") -> dict:
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

    def _materialize_skill(self, assignment: dict | None) -> dict | None:
        """Materialize a skill to opencode's discovery path for progressive disclosure."""
        skill_name = (assignment or {}).get("skill")
        if not skill_name:
            return None
        src = self._canonical.parent / "skills" / skill_name / "SKILL.md"
        if not src.exists():
            return None
        dest_dir = self.repo / ".opencode" / "skills" / skill_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
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
        console_input = os.environ.get("TRAC_AGENT_CONSOLE_INPUT")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.repo,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as err:
            raise OpencodeError("opencode_missing",
                                "opencode executable not found") from err
        try:
            stdout, stderr = proc.communicate(input=console_input, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            self._kill_group(proc.pid)
            stdout, stderr = proc.communicate()
            raise OpencodeError(
                "timeout",
                f"opencode timed out after {self.timeout}s",
                stdout=stdout or exc.stdout or "",
                stderr=stderr or exc.stderr or "",
            ) from exc
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    @staticmethod
    def _kill_group(pid: int) -> None:
        # start_new_session=True -> the child leads its own process group.
        with contextlib.suppress(ProcessLookupError, PermissionError, TypeError,
                                 OSError):
            os.killpg(pid, signal.SIGKILL)

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
                doc_path: Path | None, assignment: dict | None = None) -> str:
        docs = (assignment or {}).get("docs")
        if doc_path:
            target = str(doc_path)
        elif doc:
            target = doc
        elif docs:
            target = ", ".join(str(name) for name in docs)
        else:
            target = ""
        assignment_context = self._assignment_context(assignment)
        return (
            f"Execute the Runtime assignment for role={role}, substate={substate}, "
            f"target={target}. Follow the materialized opencode agent definition for your "
            "role. Complete only this assignment, then stop."
            + assignment_context
        )

    def _assignment_context(self, assignment: dict | None) -> str:
        if not assignment:
            return ""
        return "\n".join([
            "\n\n## Runtime assignment context",
            "以下 JSON 是 Runtime 事实，不是 Human 决定，也不能被 Agent 修改：",
            json.dumps(assignment, ensure_ascii=False, sort_keys=True),
        ])


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _docset_text(doc_paths: list[Path]) -> str:
    """Discussion text of a target doc-set: one doc reads as itself (the
    pre-multi-doc behavior); a set concatenates every existing doc."""
    texts = [path.read_text(encoding="utf-8")
             for path in doc_paths if path.exists()]
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
