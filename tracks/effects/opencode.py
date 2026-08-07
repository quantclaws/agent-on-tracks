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
import re
import signal
import subprocess
from pathlib import Path

from tracks import paths, templating
from tracks.discuss.gate import check_ready
from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads
from tracks.effects.audit import Auditor, _rel
from tracks.scaffold import _scaffold_declared_paths

# role (lowercase, IF-001 §5) -> opencode agent Name (capitalized, ARCH §4a).
# Every tracks role is a real opencode agent (spec FR-020 §2): Scribe起草 /
# Sage起草+评审 / Lex语义评审 / Archer设计三件套起草 / Prism设计评审 /
# Shield集成/e2e测试编写 (v0.4 M-TEST, FR-0120).
AGENT_NAME = {"scribe": "Scribe", "sage": "Sage", "lex": "Lex",
              "archer": "Archer", "prism": "Prism", "shield": "Shield"}

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
# FR-0120 Shield write scope (RP-01): the four test-asset directories. Writes
# outside these (product code, interface stubs, design docs, ground truth) are
# over-reach and rolled back.
_SHIELD_SCOPE = ("tests/integration", "tests/e2e", "tests/assets",
                 "tests/counterexamples")


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

    def __init__(self, repo: Path, version: str, model: str | None = None):
        self.repo = Path(repo)
        self.version = version
        # Model resolution is two-layer per dispatch (spec §3.1, ARCH §4a):
        # (1) explicit self.model (TRAC_AGENT_MODEL via select_backend) wins;
        # (2) else no --model flag, so opencode resolves its own configured
        #     default (agent/project config, never hardcoded by tracks).
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
            self._materialize_skills(assignment, cleanup_infos)
            self._materialize_templates(assignment, cleanup_infos)
            # The repo root is trusted for agent scratch files; only writes
            # outside it are over-reach (M-DESIGN author dispatches are
            # additionally bounded by their Scaffold 宣言, see the audit
            # below). The target diff remains authoritative.
            agent_dest = cleanup_infos[0]["dest"]
            allowed = self._allowed_paths(doc_paths, agent_dest, role)
            auditor = Auditor(self.repo, allowed=allowed)
            baseline = auditor.baseline()
            author = substate in ("DRAFT", "RESPOND")
            # File-granular baseline for the batch B scaffold subset rule: a
            # directory-level status entry present here must not mask files the
            # agent creates inside it during the run.
            scaffold_baseline = (auditor.file_level(baseline)
                                 if author and len(doc_paths) > 1 else None)
            proc = self._run(name, prompt)
            self._check_json(proc)
            result = self._audited_result(
                auditor, baseline, scaffold_baseline, proc, role, substate,
                doc_paths, prompt, console_input, author, reviewer_assignment,
            )
            # D-29 anti-self-report triple ③: echo the assigned criteria-pack
            # identity so the executor's mismatch check passes (mirrors
            # FakeBackend lines 120-123).  Only Prism M-TEST PRISM_REVIEW
            # carries a criteria pack today.
            if role == "prism" and substate == "PRISM_REVIEW":
                assigned_pack = (assignment or {}).get("criteria_pack")
                if assigned_pack:
                    result.setdefault("criteria_pack", dict(assigned_pack))
            return result
        except OpencodeError as exc:
            return self._opencode_error_result(
                exc, proc, prompt, console_input, doc_paths, reviewer_assignment,
            )
        except OSError as exc:
            return self._filesystem_error_result(exc, proc, prompt, console_input)
        finally:
            for info in cleanup_infos:
                self._cleanup_materialized(info)

    def _allowed_paths(self, doc_paths: list[Path], agent_dest: Path,
                       role: str) -> list[Path | str | None]:
        """Build the audit allowed list. Shield (FR-0120) is scoped to the four
        test-asset directories (no full-repo trust); every other role keeps the
        repo-root trust (the agent_dest + target docs are always allowed)."""
        if role == "shield":
            return [self.repo / d for d in _SHIELD_SCOPE] + [agent_dest]
        return [*doc_paths, agent_dest, self.repo]

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
        self, auditor: Auditor, baseline: set[str],
        scaffold_baseline: set[str] | None,
        proc: subprocess.CompletedProcess, role: str, substate: str,
        doc_paths: list[Path], prompt: str, console_input: str | None,
        author_assignment: bool, reviewer_assignment: bool,
    ) -> dict:
        diff_ref = _capture_target_diffs(
            auditor, doc_paths, substate, proc)
        guard = self._write_guard_result(
            auditor, baseline, scaffold_baseline, doc_paths, author_assignment,
            diff_ref, proc, prompt, console_input,
        )
        if guard is not None:
            return guard
        audit = self._discussion_audit_result(
            role, substate, doc_paths, diff_ref, proc, prompt, console_input,
            reviewer_assignment,
        )
        if audit is not None:
            return audit
        return self._success_result(
            AGENT_NAME[role], substate, doc_paths, diff_ref, proc, prompt,
            console_input, author_assignment, reviewer_assignment,
        )

    def _write_guard_result(
        self, auditor: Auditor, baseline: set[str],
        scaffold_baseline: set[str] | None, doc_paths: list[Path],
        author_assignment: bool, diff_ref: str | None,
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
    ) -> dict | None:
        """Post-write audits: over-reach (ARCH §6) first, then the batch B
        scaffold subset rule when this dispatch carries a scaffold baseline;
        None passes both."""
        over = auditor.audit(baseline)
        if over:
            auditor.rollback_agent_changes(baseline)
            return self._overreach_result(
                diff_ref, proc, prompt, console_input, over,
            )
        if scaffold_baseline is None:
            return None
        return self._scaffold_audit_result(
            auditor, baseline, scaffold_baseline, doc_paths, author_assignment,
            diff_ref, proc, prompt, console_input,
        )

    def _discussion_audit_result(
        self, role: str, substate: str, doc_paths: list[Path],
        diff_ref: str | None, proc: subprocess.CompletedProcess, prompt: str,
        console_input: str | None, reviewer_assignment: bool,
    ) -> dict | None:
        """Discussion-state audits (flow.md 不变量 6 / arch.md 永不信自述): the
        outcome is classified from the doc's discussion state, never the
        agent's report. Returns the failed-outcome result, or None to pass."""
        if substate == "DRAFT":
            # Author DRAFT (live run041): every thread the author initiated
            # must be resolved before the dispatch may finish. RESPOND and
            # reviewer dispatches are exempt: replies/findings legitimately
            # stay open for the other party's next round.
            offending = _unresolved_role_threads(doc_paths, role)
            if offending:
                return self._unresolved_threads_result(
                    diff_ref, offending, proc, prompt, console_input,
                )
        if reviewer_assignment:
            # Reviewer contract (live run042): a REVISE verdict must anchor
            # every blocking finding as a discussion thread the reviewer
            # INITIATED via `trac discuss start`; a bare revise gives the
            # author's RESPOND nothing anchored to reply to. Verdict revise
            # with no open reviewer-initiated thread in the doc-set ->
            # classified failure; verdict pass stays legal without threads.
            ready, blockers = check_ready(_docset_text(doc_paths))
            if not ready and not _unresolved_role_threads(doc_paths, role):
                return self._revise_without_findings_result(
                    diff_ref, role, blockers, proc, prompt, console_input,
                )
        return None

    def _overreach_result(
        self, diff_ref: str | None, proc: subprocess.CompletedProcess, prompt: str,
        console_input: str | None, evidence: str,
    ) -> dict:
        return {"status": "failed", "artifact_ref": None,
                "self_report": "over-reach detected; agent changes rolled back",
                "diff_ref": diff_ref, "audit_evidence": evidence,
                "failure_class": "over_reach",
                "agent_io": self._capture_io(proc, prompt, console_input)}

    def _scaffold_audit_result(
        self, auditor: Auditor, baseline: set[str], scaffold_baseline: set[str],
        doc_paths: list[Path], author_assignment: bool, diff_ref: str | None,
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
    ) -> dict | None:
        """batch B scaffold audit: the M-DESIGN author dispatch (DRAFT/RESPOND
        with the multi-doc design set) is contractually bounded by the scaffold
        manifest it just wrote — every run-produced write outside the doc-set,
        ``.opencode/**`` and the ``tests/ground_truth/**`` exception must be an
        enumerated ``- path —`` bullet of the freshly-written architecture.md's
        Scaffold 宣言 section. Undeclared writes fail the dispatch as
        ``undeclared_scaffold``. Single-doc stages and reviewer dispatches keep
        the plain repo-root audit (returns None)."""
        if not author_assignment or len(doc_paths) <= 1:
            return None
        arch = next((p for p in doc_paths if p.name == "architecture.md"), None)
        declared = (_scaffold_declared_paths(arch.read_text(encoding="utf-8"))
                    if arch is not None and arch.exists() else set())
        docset = {_rel(self.repo, path) for path in doc_paths}
        new_files = (auditor.file_level(auditor.modified_files())
                     - scaffold_baseline)
        offending = sorted(
            path for path in new_files
            if path not in docset
            and not path.startswith((_OPENCODE_PREFIX, _GROUND_TRUTH_PREFIX))
            and path not in declared
        )
        if not offending:
            return None
        auditor.rollback_agent_changes(baseline, new_changes=new_files)
        return self._undeclared_scaffold_result(
            diff_ref, offending, proc, prompt, console_input,
        )

    def _undeclared_scaffold_result(
        self, diff_ref: str | None, offending: list[str],
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
    ) -> dict:
        paths = ", ".join(offending)
        return {"status": "failed", "artifact_ref": None,
                "self_report": f"undeclared scaffold writes: {paths}; "
                               "agent changes rolled back",
                "diff_ref": diff_ref,
                "audit_evidence": f"undeclared_scaffold: {paths}",
                "failure_class": "undeclared_scaffold",
                "agent_io": self._capture_io(proc, prompt, console_input)}

    def _unresolved_threads_result(
        self, diff_ref: str | None, offending: list[str],
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
    ) -> dict:
        threads = ", ".join(offending)
        return {"status": "failed", "artifact_ref": None,
                "self_report": f"author-initiated discussion thread(s) unresolved: {threads}",
                "diff_ref": diff_ref,
                "audit_evidence": f"unresolved_threads: {threads}",
                "failure_class": "unresolved_threads",
                "agent_io": self._capture_io(proc, prompt, console_input)}

    def _revise_without_findings_result(
        self, diff_ref: str | None, role: str, blockers: tuple,
        proc: subprocess.CompletedProcess, prompt: str, console_input: str | None,
    ) -> dict:
        # No `verdict` key: a bare revise is not a produced verdict, so the
        # executor emits no verdict event and the machine's failed-outcome
        # retry path applies (attempt consumed, evidence into re-dispatch).
        threads = ", ".join(blockers)
        return {"status": "failed", "artifact_ref": None,
                "self_report": "revise verdict must anchor findings via `trac discuss "
                               f"start`: open each blocking finding as a discussion "
                               f"thread before returning revise (open: {threads})",
                "diff_ref": diff_ref,
                "audit_evidence": (f"revise_without_findings: verdict=revise, open "
                                   f"thread(s) {threads}, none initiated by reviewer "
                                   f"{AGENT_NAME[role]}"),
                "failure_class": "revise_without_findings",
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

    def _materialize_skills(self, assignment: dict | None,
                            cleanup_infos: list) -> None:
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
        info = {"dest": dest, "backup": None, "existed": dest.exists()}
        if dest.exists():
            info["backup"] = dest.read_bytes()
        dest.write_bytes(src.read_bytes())
        return info

    def _materialize_templates(self, assignment: dict | None,
                               cleanup_infos: list) -> None:
        """Materialize the assignment's canonical document templates into the
        host repo (live run043: a host repo has no tracks/templates/, so the
        templates travel with the dispatch exactly like the agent definition
        and skill; backup/restore on cleanup, ARCH §4c). Each file registers
        for cleanup the moment it is written, so a missing canonical kind
        mid-way still cleans up its predecessors."""
        for kind in _template_kinds(assignment):
            cleanup_infos.append(self._materialize_template(kind))

    def _materialize_template(self, kind: str) -> dict:
        """Copy canonical tracks/templates/{kind}.md to .opencode/templates/;
        a requested kind without a canonical template is a dispatch failure
        (parity with a missing canonical prompt), never a silent skip."""
        src = templating.template_path(kind)
        if not src.exists():
            raise OpencodeError("opencode_missing",
                                f"canonical template not found: {src}")
        dest_dir = self.repo / ".opencode" / "templates"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{kind}.md"
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

    def _resolve_model(self, name: str) -> str | None:
        """Two-layer model resolution per dispatch (see __init__ docstring):
        explicit ``self.model`` wins; otherwise ``None`` means opencode
        resolves its own configured default (no ``--model`` flag). Model
        selection is an opencode-config concern, never hardcoded by tracks."""
        return self.model

    def _run(self, name: str, prompt: str) -> subprocess.CompletedProcess:
        cmd = ["opencode", "run", "--agent", name, "--format", "json",
               "--dir", str(self.repo), "--auto", prompt]
        model = self._resolve_model(name)
        if model:
            cmd.extend(["--model", model])
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
        # No production timeout: the Runtime Agent runs to completion and is
        # monitored via its output, never killed for elapsed time (prior
        # commit ddd2f71 lived only on a deleted release branch). Operator
        # cancellation (Ctrl-C) is honored: start_new_session=True gives the
        # child its own process group, so SIGINT to the operator's foreground
        # group does not reach it - we catch KeyboardInterrupt, terminate/kill
        # the child group, reap it, then re-raise so the operator stays in
        # control. This is operator cancellation, not a Runtime kill policy.
        try:
            stdout, stderr = proc.communicate(input=console_input)
        except KeyboardInterrupt:
            self._kill_group(proc.pid)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=5)
            raise
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
            resolved = self._target_paths(doc_path, assignment)
            target = ", ".join(str(p) for p in resolved)
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


def _capture_target_diffs(auditor: Auditor, doc_paths: list[Path],
                          substate: str,
                          proc: subprocess.CompletedProcess) -> str | None:
    """Capture the controlled diff of every target doc (the authoritative
    product) BEFORE any audit rollback can touch it, then enforce the
    author-must-produce contract on the captured set."""
    diffs = [auditor.target_diff(path) for path in doc_paths]
    _require_target_diff(doc_paths, diffs, substate, proc)
    return "\n".join(diff for diff in diffs if diff) or None


def _require_target_diff(doc_paths: list[Path], diffs: list[str | None],
                         substate: str,
                         proc: subprocess.CompletedProcess) -> None:
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
                exit_code=0, stderr=proc.stderr)
        return
    missing = [str(path)
               for path, diff in zip(doc_paths, diffs, strict=True)
               if diff is None]
    if not doc_paths or missing:
        raise OpencodeError(
            "no_target_diff",
            "exit 0 but the target doc-set has no diff"
            + (f": {', '.join(missing)}" if missing else ""),
            exit_code=0, stderr=proc.stderr)


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
