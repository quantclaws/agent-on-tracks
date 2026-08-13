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
from tracks.discuss.delta import is_discussion_delta
from tracks.discuss.gate import check_ready
from tracks.discuss.model import speaker_key
from tracks.discuss.parser import parse_threads
from tracks.effects.adjudicate import adjudicate
from tracks.effects.audit import Auditor, _rel
from tracks.effects.devon_evidence import extract_devon_evidence
from tracks.project import layout_paths
from tracks.scaffold import _scaffold_declared_paths

AGENT_NAME = {"scribe": "Scribe", "sage": "Sage", "lex": "Lex",
              "archer": "Archer", "prism": "Prism", "shield": "Shield",
              "devon": "Devon"}

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

    def __init__(self, repo: Path, version: str, model: str | None = None,
                 debug: bool = False):
        self.repo = Path(repo)
        self.version = version
        # Model resolution is two-layer per dispatch (spec §3.1, ARCH §4a):
        # (1) explicit self.model (TRAC_AGENT_MODEL via select_backend) wins;
        # (2) else no --model flag, so opencode resolves its own configured
        #     default (agent/project config, never hardcoded by tracks).
        self.model = model
        self.debug = debug
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
        if role == "devon":
            doc_paths = [self.repo / path for path in
                         (assignment or {}).get("manifest", {}).get(
                             "allowed_paths", [])]
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
            allowed = self._allowed_paths(doc_paths, agent_dest, role,
                                          substate, assignment)
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
            manifest_info, manifest_error = self._manifest_for_dispatch(
                role, substate, proc, prompt, console_input,
            )
            if manifest_error is not None:
                return manifest_error
            result = self._audited_result(
                auditor, baseline, scaffold_baseline, proc, role, substate,
                doc_paths, prompt, console_input, author, reviewer_assignment,
            )
            return self._attach_dispatch_metadata(
                result, role, substate, manifest_info, assignment,
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

    def _manifest_for_dispatch(
        self, role: str, substate: str, proc: subprocess.CompletedProcess,
        prompt: str, console_input: str | None,
    ) -> tuple[tuple[dict | None, str | None] | None, dict | None]:
        if role != "shield" or substate != "WRITE":
            return None, None
        manifest, commit_msg, error = self._extract_manifest(proc)
        if error is not None:
            return None, self._manifest_malformed_result(
                error, proc, prompt, console_input)
        return (manifest, commit_msg), None

    @staticmethod
    def _attach_dispatch_metadata(
        result: dict, role: str, substate: str,
        manifest_info: tuple[dict | None, str | None] | None,
        assignment: dict | None,
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
                "PRISM_REVIEW", "PRISM_PLAN", "PRISM_RED", "PRISM_FINAL",
                "DIAGNOSE"):
            assigned_pack = (assignment or {}).get("criteria_pack")
            if assigned_pack:
                result.setdefault("criteria_pack", dict(assigned_pack))
        return result

    def _allowed_paths(self, doc_paths: list[Path], agent_dest: Path,
                       role: str, substate: str,
                       assignment: dict | None = None
                       ) -> list[Path | str | None]:
        """Build the role-specific audit whitelist from project.toml [layout]."""
        if role == "shield":
            allowed = [self.repo / d for d in layout_paths(self.repo, "shield")] + [agent_dest]
            if substate == "WRITE":
                allowed = [*doc_paths, *allowed]
            return allowed
        if role == "devon":
            devon_dirs = [self.repo / d for d in layout_paths(self.repo, "devon")]
            return [*doc_paths, agent_dest, *devon_dirs]
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
        # Shield M-TEST WRITE gets one atomic audit/rollback decision
        # (Blocker 2): the generic over-reach guard must NOT early-return and
        # leave allowed doc edits behind.  Instead the doc-delta check and the
        # generic over-reach check run together; if either fails, every
        # agent-changed path is rolled back in one force pass.
        if role == "shield" and substate == "WRITE":
            guard = self._shield_write_audit(
                auditor, baseline, doc_paths, diff_ref, proc, prompt,
                console_input,
            )
            if guard is not None:
                return guard
        else:
            guard = self._write_guard_result(
                auditor, baseline, scaffold_baseline, doc_paths,
                author_assignment, diff_ref, proc, prompt, console_input,
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

    def _shield_write_audit(
        self, auditor: Auditor, baseline: set[str], doc_paths: list[Path],
        diff_ref: str | None, proc: subprocess.CompletedProcess, prompt: str,
        console_input: str | None,
    ) -> dict | None:
        """Shield M-TEST WRITE atomic audit (FR-0120): assignment docs
        (test-plan.md, interfaces.md, acceptance.md) may receive only canonical
        discussion replies - any body/prose edit or type swap is over-reach and
        rolls back the ENTIRE run (including allowed test-asset writes and
        out-of-scope writes), so a partially-valid run never persists.
        Test-asset writes are checkpointed separately by ResultCheckpoint.

        One atomic decision (Blocker 2): the doc-delta check and the generic
        over-reach check run together; if **either** fails, every agent-changed
        path is rolled back in one force pass.  This avoids the early-return
        trap where a generic over-reach rollback would leave allowed doc body
        edits behind.

        The post-agent doc delta is validated against the **pre-dispatch byte
        snapshot**, not HEAD (Blocker 1): a canonical discussion reply appended
        to Human-dirty body content must pass and preserve the body; removing/
        replacing Human content plus adding a discussion must fail and restore
        the pre-dispatch bytes.

        Path/symlink handling is lexical, no-follow, and type-aware (Blocker 3):
        a regular assignment doc replaced by a file/directory/dangling symlink
        is detected; rollback unlinks the replacement symlink without following
        it and restores the original from the pre-dispatch snapshot or HEAD.
        """
        agent_changed = auditor.agent_changed_paths(baseline)
        doc_offending = self._check_doc_deltas(auditor, doc_paths, agent_changed)
        over_paths = sorted(
            p for p in agent_changed if not auditor._is_allowed(p))
        if doc_offending or over_paths:
            if over_paths and not doc_offending and adjudicate(
                    self, "shield", over_paths):
                return None
            auditor.rollback_agent_changes(
                baseline, new_changes=agent_changed, force=True)
            parts: list[str] = []
            if doc_offending:
                parts.append("non-discussion edit to "
                             + ", ".join(sorted(doc_offending)))
            if over_paths:
                parts.append("over-reach: " + ", ".join(over_paths))
            return self._overreach_result(
                diff_ref, proc, prompt, console_input,
                evidence="; ".join(parts),
            )
        return None

    def _check_doc_deltas(
        self, auditor: Auditor, doc_paths: list[Path],
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
            ["git", "rev-parse", "HEAD"], cwd=self.repo,
            capture_output=True, text=True,
        ).stdout.strip()
        return [
            _rel(self.repo, doc)
            for doc in doc_paths
            if self._doc_offending(auditor, doc, agent_changed, head)
        ]

    def _doc_offending(
        self, auditor: Auditor, doc_path: Path, agent_changed: set[str],
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
        self, doc_path: Path, rel: str, head: str,
    ) -> bool:
        """Clean at dispatch, now changed: HEAD is the correct base.  A doc
        new at HEAD (untracked) or whose delta is not discussion-only is
        offending."""
        try:
            current = doc_path.read_bytes()
        except OSError:
            return True
        head_bytes = _load_head_bytes(self.repo, rel, head)
        return head_bytes is None or not is_discussion_delta(
            head_bytes, current)

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
        if name == "Devon" and substate in ("RED", "GREEN", "REFACTOR"):
            result.update(extract_devon_evidence(
                proc, self._final_text_event, self._first_json_object,
            ))
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
        # opencode-logger plugin defaults to <repo>/logs/opencode; redirect
        # it under .opencode/logs so the repo root stays clean.
        env = {**os.environ, "OPENCODE_LOGGER_DIR": ".opencode/logs"}
        model = self._resolve_model(name)
        if model:
            cmd.extend(["--model", model])
        if self.debug:
            cmd.extend(["--print-logs", "--log-level", "DEBUG"])
        console_input = os.environ.get("TRAC_AGENT_CONSOLE_INPUT")
        # Debug mode: redirect stderr (opencode logs via --print-logs) to a
        # log file under .tracks/runtime/log/. stdout (JSON events) is still
        # captured via PIPE for _check_json; the log file captures stderr in
        # real-time for live monitoring (tail -f). After the process exits,
        # we read the file back so _check_json/_looks_provider_error still
        # have the stderr string for failure classification.
        log_path = None
        log_fh = None
        if self.debug:
            log_path = self._debug_log_path(name)
            # File handle lifetime spans Popen+communicate; cannot use a context
            # manager here because subprocess writes to it asynchronously.
            log_fh = open(log_path, "w", buffering=1)  # line-buffered  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.repo,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_fh if log_fh else subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as err:
            if log_fh:
                log_fh.close()
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
            if self.debug:
                stdout, _ = proc.communicate(input=console_input)
            else:
                stdout, stderr = proc.communicate(input=console_input)
        except KeyboardInterrupt:
            self._kill_group(proc.pid)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=5)
            if log_fh:
                log_fh.close()
            raise
        if log_fh:
            log_fh.close()
            stderr = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

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
        with contextlib.suppress(ProcessLookupError, PermissionError, TypeError,
                                 OSError):
            os.killpg(pid, signal.SIGKILL)

    def _check_json(self, proc: subprocess.CompletedProcess) -> None:
        """Classify the exit; JSON is diagnostic-only (product = target diff)."""
        if proc.returncode < 0:  # killed by signal (SIGINT/kill-9)
            out = (proc.stdout or "").strip()
            # opencode may hang after output; accept valid stdout, fall through
            if not (out and self._parses_json(out)):
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
                    "authentication", "401", "403",
                    # quota / rate-limit signals (e.g. litellm code 4008)
                    "quota", "exceeded", "rate_limit", "rate limit",
                    "429", "4008"))

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
        """Parse Shield WRITE's raw JSON object from the final text event."""
        text_event = OpencodeBackend._final_text_event(proc)
        if text_event is None:
            return None, None, "final type=text event is missing"
        payload, error = OpencodeBackend._manifest_payload(text_event)
        if error is not None:
            return None, None, error
        include, error = OpencodeBackend._manifest_include(payload)
        if error is not None:
            return None, None, error

        commit_msg = payload.get("suggested_commit_message")
        if not isinstance(commit_msg, str) or not commit_msg.strip():
            return None, None, (
                "suggested_commit_message must be a non-empty string"
            )
        return {"include": include}, commit_msg, None

    @staticmethod
    def _final_text_event(
        proc: subprocess.CompletedProcess,
    ) -> dict | None:
        out = (proc.stdout or "").strip()
        for line in reversed(out.splitlines()):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "text":
                return event
        return None

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
    def _first_json_object(text: str) -> dict | None:
        """Extract the last top-level JSON object from *text*.

        Agents often wrap the manifest in Markdown prose.  We try the
        fast path first (entire text is JSON); if that fails we scan
        for top-level ``{`` positions and return the last dict found
        (manifests are at the end of agent output).
        """
        try:
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            pass
        decoder = json.JSONDecoder()
        i = 0
        n = len(text)
        payload: dict | None = None
        while i < n:
            if text[i] != "{":
                i += 1
                continue
            try:
                obj, end = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                i += 1
                continue
            if isinstance(obj, dict):
                payload = obj
            i = end
        return payload

    @staticmethod
    def _manifest_include(
        payload: dict,
    ) -> tuple[list | None, str | None]:
        raw_manifest = payload.get("artifact_manifest")
        if not isinstance(raw_manifest, dict):
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
                return (
                    f"artifact_manifest.include[{index}].{field} "
                    "must be a non-empty string"
                )
        if Path(item["path"]).is_absolute():
            return (
                f"artifact_manifest.include[{index}].path "
                "must be repo-relative"
            )
        return None

    def _manifest_malformed_result(
        self, error: str, proc: subprocess.CompletedProcess,
        prompt: str, console_input: str | None,
    ) -> dict:
        # When opencode exits 0 but the manifest is malformed, check whether
        # the root cause is a provider error (e.g. quota exceeded mid-stream).
        # opencode logs stream errors to stderr but continues, so the manifest
        # is simply truncated.  Re-classify as provider_unavailable so the
        # runtime can retry instead of wasting dispatch attempts.
        stderr = proc.stderr or ""
        if self._looks_provider_error(stderr):
            return {
                "status": "failed", "artifact_ref": None,
                "self_report": f"provider unavailable: {error}",
                "failure_class": "provider_unavailable",
                "audit_evidence": f"provider_unavailable: {error}",
                "agent_io": self._capture_io(proc, prompt, console_input),
            }
        return {
            "status": "failed", "artifact_ref": None,
            "self_report": f"manifest malformed: {error}",
            "failure_class": "manifest_malformed",
            "audit_evidence": f"manifest_malformed: {error}",
            "agent_io": self._capture_io(proc, prompt, console_input),
        }

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


def _load_head_bytes(repo: Path, rel: str, head: str) -> bytes | None:
    """Load the content of ``rel`` as of ``head`` from git, or None if the
    path is not tracked at that commit (new/untracked file)."""
    proc = subprocess.run(
        ["git", "show", f"{head}:{rel}"], cwd=repo,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


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
