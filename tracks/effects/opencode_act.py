"""Dispatch/result mixin for OpencodeBackend (AgentBackend ``act``).

Extracted from ``opencode.py`` for module-size compliance (C0302).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tracks.effects.audit import Auditor
from tracks.effects.devon_evidence import extract_devon_evidence
from tracks.effects.envelope_reply import (
    declared_result_path,
    is_declared_assignment,
)

from .opencode_audit import _capture_target_diffs
from .opencode_core import AGENT_NAME, OpencodeError, redact
from .opencode_session import _session_key


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


@dataclass(frozen=True)
class _PreparedAct:
    """Immutable inputs of one ``act`` dispatch."""

    role: str
    substate: str
    assignment: dict | None
    name: str
    root: Path
    doc_paths: list[Path]
    prompt: str
    console_input: str | None
    reviewer_assignment: bool
    cleanup_infos: list
    key: str


@dataclass(frozen=True)
class _ActAudit:
    """Auditor + baselines resolved before the agent subprocess spawns."""

    auditor: Auditor
    baseline: set[str]
    scaffold_baseline: set[str] | None
    author: bool


class OpencodeActMixin:
    """The dispatch main chain + failure/result construction."""

    def act(
        self,
        role: str,
        substate: str,
        doc: str | None,
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
        # Reviewer verdict derivation: M-TEST review substates end in
        # "_REVIEW"; v0.5 M-IMPL Prism review substates do not (PRISM_PLAN/
        # PRISM_RED/PRISM_FINAL). Without the derivation the opencode backend
        # produces no result["verdict"], the executor's _emit_dispatch_verdict
        # silently returns, no prism.verdict event is emitted, and the loop
        # deadlocks (reviewer_dispatched=True awaiting a verdict nothing will
        # produce - run 01KZTHE7 PRISM_PLAN, 2026-08-15, twice). DIAGNOSE is
        # excluded: its verdict is a classification routed via
        # _emit_diagnose_verdict, not a pass/revise document verdict.
        request = _PreparedAct(
            role=role,
            substate=substate,
            assignment=assignment,
            name=name,
            root=root,
            doc_paths=doc_paths,
            prompt=prompt,
            console_input=console_input,
            reviewer_assignment=substate.endswith("_REVIEW")
            or substate in ("PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "VERIFY_FINAL"),
            cleanup_infos=cleanup_infos,
            key=_session_key(role, substate, assignment),
        )
        try:
            return self._run_act(request)
        finally:
            for info in cleanup_infos:
                self._cleanup_materialized(info)

    def _run_act(self, req: _PreparedAct) -> dict:
        proc = None
        try:
            cleanup_infos = req.cleanup_infos
            # Materialize into the dispatch's effective root: a worktree
            # session discovers .opencode from its own cwd (see _materialize).
            cleanup_infos.append(self._materialize(req.name, root=req.root))
            self._materialize_skills(req.assignment, cleanup_infos, root=req.root)
            self._materialize_templates(req.assignment, cleanup_infos, root=req.root)
            # IF-ENVELOPE-002: after ALL materialization writes, bind the
            # actual prepared artifacts (path+sha+token) + prompt and run the
            # Runtime-owned parity gate over every selected face — BEFORE the
            # agent subprocess is spawned. A rejection returns a failed
            # result (no spawn, no external invocation); the finally in
            # ``act`` still cleans up. The repo root is trusted for agent
            # scratch files; only writes outside it are over-reach (M-DESIGN
            # author dispatches are additionally bounded by their Scaffold
            # 宣言, see the audit below). The target diff stays authoritative.
            prepared = self._prepare_parity(
                cleanup_infos, req.assignment, req.prompt, name=req.name
            )
            if not prepared.ok:
                return prepared.failure
            audit = self._prepare_act_audit(req)
            proc = self._dispatch_with_session_health(
                req.name, req.prompt, req.root, key=req.key
            )
            self._check_json(proc)
            if self._abnormal_step_finish(proc):
                # B17/#20 narrow (live T-003 GREEN): an interrupted session
                # is infrastructure, not an agent semantic failure — do not
                # let it reach DIAGNOSE as impl_defect and burn the budget.
                return self._attach_raw_output(
                    self._abnormal_step_result(proc, req.prompt, req.console_input),
                    proc,
                    req.assignment,
                )
            # D-35 (SC-D35 §2.2): M-TEST/M-IMPL reviewers deliver findings via
            # a structured final JSON payload. Parse before the audits so the
            # discussion gate can exempt a structured revise.
            review_failure = self._review_channel_failure(
                req.role, req.substate, req.assignment, proc, req.prompt, req.console_input
            )
            if review_failure is not None:
                return self._attach_raw_output(review_failure, proc, req.assignment)
            manifest_info, manifest_error = self._manifest_for_dispatch(
                req.role,
                req.substate,
                proc,
                req.prompt,
                req.console_input,
            )
            if manifest_error is not None:
                return self._attach_raw_output(manifest_error, proc, req.assignment)
            result = self._audited_result(
                audit,
                proc,
                req.role,
                req.substate,
                req.doc_paths,
                req.prompt,
                req.console_input,
                req.reviewer_assignment,
                structured_findings=self._has_structured_findings(
                    req.role, req.substate, req.assignment, proc
                ),
                result_path=declared_result_path(req.assignment),
            )
            return self._attach_raw_output(
                self._attach_dispatch_metadata(
                    self._merge_review_payload(
                        result,
                        req.role,
                        req.substate,
                        req.assignment,
                        proc,
                        req.prompt,
                        req.console_input,
                    ),
                    req.role,
                    req.substate,
                    manifest_info,
                    req.assignment,
                    parity=prepared.evidence,
                ),
                proc,
                req.assignment,
            )
        except OpencodeError as exc:
            return self._attach_raw_output(
                self._opencode_error_result(
                    exc,
                    proc,
                    req.prompt,
                    req.console_input,
                    req.doc_paths,
                    req.reviewer_assignment,
                ),
                proc,
                req.assignment,
            )
        except OSError as exc:
            return self._attach_raw_output(
                self._filesystem_error_result(exc, proc, req.prompt, req.console_input),
                proc,
                req.assignment,
            )

    def _prepare_act_audit(self, req: _PreparedAct) -> _ActAudit:
        agent_dest = req.cleanup_infos[0]["dest"]
        allowed = self._allowed_paths(
            req.doc_paths, agent_dest, req.role, req.substate, req.assignment, root=req.root
        )
        # B64 (#82): assignment-injected ownership veto (repo-relative
        # patterns; e.g. Shield's SHIELD_FIX domain excludes the run's
        # red_test_paths). Data-driven — the backend applies what the
        # executor derived from Archer-authored manifests.
        forbidden = self._assignment_forbidden_paths(req.assignment)
        auditor = Auditor(req.root, allowed=allowed, forbidden=forbidden)
        baseline = auditor.baseline()
        author = req.substate in ("DRAFT", "RESPOND")
        # File-granular baseline for the batch B scaffold subset rule: a
        # directory-level status entry present here must not mask files the
        # agent creates inside it during the run.
        scaffold_baseline = (
            auditor.file_level(baseline) if author and len(req.doc_paths) > 1 else None
        )
        return _ActAudit(auditor, baseline, scaffold_baseline, author)

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

    def _audited_result(
        self,
        state: _ActAudit,
        proc: subprocess.CompletedProcess,
        role: str,
        substate: str,
        doc_paths: list[Path],
        prompt: str,
        console_input: str | None,
        reviewer_assignment: bool,
        structured_findings: bool = False,
        result_path: str | None = None,
    ) -> dict:
        diff_ref = _capture_target_diffs(state.auditor, doc_paths, substate, proc)
        # Every dispatch gets ONE atomic audit/rollback decision (Blocker 2):
        # doc-delta + over-reach (+ batch B scaffold) checks run together; if
        # any fails, every agent-changed path is rolled back in one force pass.
        if role == "shield" and substate == "WRITE":
            guard = self._shield_write_audit(
                state.auditor,
                state.baseline,
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
                state.auditor,
                state.baseline,
                state.scaffold_baseline,
                doc_paths,
                role,
                state.author,
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
            state.author,
            reviewer_assignment,
            result_path=result_path,
        )

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
        result_path: str | None = None,
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
                    # #174: the result file's payload IS the evidence when
                    # the dispatch declared one (file first, text fallback).
                    result_path=result_path,
                )
            )
        if name == "Prism" and substate == "DIAGNOSE":
            # Real-channel DIAGNOSE (the fake channel uses the simulate
            # token): without a verdict the outcome deadlocks (run 01KZTHE7
            # T-008). The full diagnosis payload rides along so the fixer
            # dispatch gets the actual analysis, not just the label.
            diagnosis = self._diagnose_classification_from(proc, result_path)
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
