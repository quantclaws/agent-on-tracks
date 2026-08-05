"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.effects import select_backend
from tracks.effects.github import GithubIssuesError, issue_items, select_issue_backend
from tracks.executor.validate import parse_test_tasks, required_ac_ids, validate_document
from tracks.frontmatter import doc_body_sha, set_frontmatter_field
from tracks.kernel.events import Command
from tracks.kernel.machine import State, decide
from tracks.scaffold import _scaffold_declared_paths
from tracks.store import Store, new_ulid


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def _commit_if_staged(repo: Path, message: str) -> subprocess.CompletedProcess | None:
    """Attempt to commit staged changes (check=False). Returns None if nothing
    was staged, or the CompletedProcess so the caller can inspect ``.returncode``
    for pre-commit hook rejection without crashing."""
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return None
    return git(repo, "commit", "-m", message, check=False)


def _hook_output(proc: subprocess.CompletedProcess, limit: int = 4096) -> str:
    """Combined stdout+stderr from a failed ``git commit``, truncated."""
    combined = (proc.stdout or "") + (proc.stderr or "")
    return combined[:limit]


_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})


def _agent_io_evidence(store: Store, assignment: dict, captured: dict) -> dict:
    """Persist input/output evidence without making it a workflow failure."""
    input_payload = dict(assignment)
    for key in ("prompt", "console_input"):
        if key in captured:
            input_payload[key] = captured[key]
    input_ref = store.write_audit_blob(input_payload)
    output_raw = json.dumps(captured, ensure_ascii=False, sort_keys=True)
    output_ref = store.write_audit_blob(captured)
    digest = hashlib.sha256(output_raw.encode("utf-8")).hexdigest()
    gaps = []
    if input_ref is None:
        gaps.append("assignment_blob_write_failed")
    if output_ref is None:
        gaps.append("agent_output_blob_write_failed")
    evidence = {
        "input_ref": input_ref,
        "output_ref": output_ref,
        "output_digest": digest,
        "output_summary": {
            "stdout_bytes": captured.get("stdout_bytes", 0),
            "stderr_bytes": captured.get("stderr_bytes", 0),
        },
        "audit_completeness": "partial" if gaps else "complete",
    }
    if gaps:
        evidence["audit_gaps"] = gaps
    return evidence


def _dispatch_payload(store: Store, params: dict, result: dict) -> dict:
    payload = {
        "role": params["role"],
        "status": result["status"],
        "artifact_ref": result.get("artifact_ref"),
        "self_report": result["self_report"],
    }
    captured = result.get("agent_io")
    if captured is not None:
        payload["agent_io"] = _agent_io_evidence(store, dict(params), captured)
    for key in ("diff_ref", "audit_evidence", "failure_class", "verdict",
                "discussion_evidence"):
        if result.get(key) is not None:
            payload[key] = result[key]
    return payload


# Stage-transition table (design §1, single source of truth): EXIT seal -> next
# stage.entered; stages absent here (M-TEST) end the run at the M-IMPL
# boundary: run.completed(terminal_state="boundary") (SM-05.6 / v0.4: M-IMPL is
# not implemented, so the run completes after M-TEST EXIT).
_NEXT_STAGE = {"M-STORY": "M-SPEC", "M-SPEC": "M-ACC",
               "M-ACC": "M-REQ-APPROVAL", "M-REQ-APPROVAL": "M-DESIGN",
               "M-DESIGN": "M-TEST"}

# doc -> (committed event type, body-sha payload key)
_COMMITTED_EVENT = {
    "story.md": ("story.committed", "story_sha"),
    "spec.md": ("spec.committed", "spec_sha"),
    "acceptance.md": ("acceptance.committed", "acceptance_sha"),
    # v0.3 design trio: one event type serves all three docs; the payload's
    # `doc` field (see _emit_committed) identifies which one.
    "architecture.md": ("design.committed", "architecture_sha"),
    "interfaces.md": ("design.committed", "interfaces_sha"),
    "test-plan.md": ("design.committed", "test_plan_sha"),
}

# reviewer role -> its verdict event type
_VERDICT_EVENT = {"sage": "sage.verdict", "lex": "lex.verdict",
                  "prism": "prism.verdict"}

# D-29 criteria pack identity (architecture.md §3.4): echoed by Prism and
# read back by the executor to enforce the anti-self-report triple.
_CRITERIA_PACK = {"name": "test-asset-criteria", "version": "0.1"}

# IF-004 §1g RedClass closed set (legit = the test fails for the right reason).
_LEGIT_RED = frozenset({"assertion_failure", "stub_token_failure", "symbol_missing"})
# Failure keywords -> illegit Red class (collection/syntax/fixture/import).
_ILLEGIT_KEYWORDS = (
    "ImportError", "ModuleNotFoundError", "SyntaxError",
    "FixtureLookupError", "collection error", "ERROR collecting",
)


def classify_red(test_id: str, returncode: int, stdout: str, stderr: str) -> str:
    """FR-0050 classify a single test failure (IF-004 §1g, pure).

    Based on returncode + keyword matching (no traceback structure parsing):
    - ``NotImplementedError("IF-`` -> ``stub_token_failure`` (legit).
    - ``AssertionError``/``assert`` -> ``assertion_failure`` (legit).
    - ImportError/ModuleNotFoundError/SyntaxError/FixtureLookupError/collection
      error -> ``collection_error`` (illegit).
    - returncode == 0 (test should fail but passed) -> ``unexpected_pass``.
    - otherwise -> ``unclassified`` (illegit, enters DIAGNOSE).
    """
    combined = f"{stdout}\n{stderr}"
    if returncode == 0:
        return "unexpected_pass"
    if 'NotImplementedError("IF-' in combined or "NotImplementedError('IF-" in combined:
        return "stub_token_failure"
    if "AssertionError" in combined or "\nassert " in combined or "assert " in combined:
        return "assertion_failure"
    for kw in _ILLEGIT_KEYWORDS:
        if kw in combined:
            return "collection_error"
    if "AttributeError" in combined or "NameError" in combined:
        return "symbol_missing"
    return "unclassified"


def _parse_collected_count(stdout: str) -> int:
    """Parse the test count from pytest --collect-only -q output (last line
    like ``N tests collected`` or ``N errors``)."""
    m = re.findall(r"(\d+) tests? collected", stdout)
    if m:
        return int(m[-1])
    m = re.findall(r"(\d+) errors?", stdout)
    return int(m[-1]) if m else 0


def _short_detail(output: str, limit: int = 200) -> str:
    """Truncate pytest output for the red.validated findings detail field."""
    output = output.strip()
    return output[:limit] + ("..." if len(output) > limit else "")


class Executor:
    """Drives one run: project -> decide -> issue -> execute -> observe."""

    def __init__(self, store: Store, repo: Path, run_id: str,
                 assignment_overlay: dict | None = None,
                 max_dispatches: int | None = None):
        if assignment_overlay is not None and not isinstance(assignment_overlay, dict):
            raise TypeError("assignment_overlay must be a dict or None")
        if max_dispatches is not None and (
                isinstance(max_dispatches, bool)
                or not isinstance(max_dispatches, int)
                or max_dispatches < 1):
            raise ValueError("max_dispatches must be a positive integer or None")
        self.store = store
        self.repo = repo
        self.run_id = run_id
        self.version = store.state(run_id).version or ""
        self.backend = select_backend(repo, self.version)
        self.assignment_overlay = deepcopy(assignment_overlay)
        self.max_dispatches = max_dispatches
        self._issue_backend = None  # lazy: created on first create_issues

    def _emit(self, type: str, payload: dict,
              command_id: str | None = None, task_id: str | None = None):
        return self.store.append(self.run_id, self.version, type, payload,
                                 command_id=command_id, task_id=task_id)

    def _emit_commit_failure(self, proc: subprocess.CompletedProcess,
                             state: State, command_id: str) -> None:
        """D-30/F-1: pre-commit hook rejected the commit -> emit the established
        failure-evidence event (verdict.failed -> s.last_failure via
        _on_verdict_failed) carrying the hook's combined output, so decide()
        re-dispatches the agent to fix the deliverable (FR-11)."""
        self._emit_commit_failure_evidence(
            state, command_id, "pre-commit hook rejected the commit",
            _hook_output(proc),
        )

    def _emit_commit_failure_evidence(self, state: State, command_id: str,
                                      reason: str, evidence: str) -> None:
        self._emit("verdict.failed",
                   {"check": "commit",
                     "reason": reason,
                     "evidence": evidence,
                     "attempt": state.current_attempt + 1},
                   command_id=command_id)

    def _doc_path(self, doc: str) -> Path:
        return paths.version_dir(self.store.home, self.version) / doc

    # -- main loop (FR-29/FR-30) -------------------------------------------

    def run_loop(self) -> State:
        pending = self.store.state(self.run_id).pending
        recovered_dispatch = self._recover()
        dispatches = 1 if recovered_dispatch else 0
        # Bounded mode is one substate + retries (see _dispatch_gate): a
        # recovered dispatch belongs to that remembered substate too.
        bound_substate = None
        if recovered_dispatch and self.max_dispatches is not None:
            bound_substate = (pending or {}).get("params", {}).get("substate")
        while True:
            state = self.store.state(self.run_id)
            cmd = decide(state)
            if cmd is None:
                return state
            if cmd.kind == "dispatch_agent" and self.max_dispatches is not None:
                stop, bound_substate = self._dispatch_gate(
                    cmd, dispatches, bound_substate)
                if stop:
                    return state
                dispatches += 1
            self.issue(cmd)

    def _dispatch_gate(self, cmd, dispatches: int,
                        bound_substate: str | None) -> tuple[bool, str | None]:
        """Bounded-mode gate: stop (True) at budget exhaustion or before a
        dispatch for a different substate; remember the first dispatch's
        substate. The assignment overlay is per-invocation, so a dispatch for
        another substate would run under a stale overlay — hand control back
        instead. Retries within the remembered substate pass."""
        if dispatches >= self.max_dispatches:
            return True, bound_substate
        substate = cmd.params.get("substate")
        if bound_substate is None:
            return False, substate
        return substate != bound_substate, bound_substate

    def issue(self, cmd: Command) -> None:
        """Write-ahead log `cmd` (FR-30), then execute it; the per-kind handler
        logs the result event that closes it. command_id is assigned here so the
        result pairs with the issued record. Used by run_loop and by one-shot
        setup commands (create_branch in `trac start`)."""
        state = self.store.state(self.run_id)
        cid = new_ulid()
        params = dict(cmd.params)
        if cmd.kind == "dispatch_agent" and self.assignment_overlay is not None:
            assignment = dict(params.get("assignment") or {})
            assignment["scenario_context"] = deepcopy(self.assignment_overlay)
            params["assignment"] = assignment
        # D-28: Runtime enriches the Shield WRITE assignment with structured
        # test_tasks parsed from test-plan §8 (AC layer + IF green conditions)
        # before write-ahead logging so the persisted command.issued evidence
        # carries the complete input (architecture.md §1.2 DISPATCH).
        if (cmd.kind == "dispatch_agent"
                and params.get("role") == "shield"
                and params.get("substate") == "WRITE"
                and state.stage == "M-TEST"
                and "test_tasks" not in (params.get("assignment") or {})):
            vdir = self._vdir()
            tasks = parse_test_tasks(vdir / "acceptance.md",
                                     vdir / "test-plan.md")
            assignment = dict(params.get("assignment") or {})
            # Preserve an empty/malformed parse result as evidence-bearing input;
            # FakeBackend must turn it into a failed outcome instead of allowing
            # Shield to fall back to rereading the design documents.
            assignment["test_tasks"] = tasks
            params["assignment"] = assignment
        issued = Command(kind=cmd.kind, params=params, command_id=cid)
        task_id = None
        if cmd.kind == "dispatch_agent":
            task_id = (f"{self.run_id}:{cmd.params.get('substate')}"
                       f":{state.review_round}:{state.current_attempt}")
        self._emit(
            "command.issued",
            {"command": {"kind": issued.kind, "params": issued.params,
                         "command_id": cid}},
            command_id=cid, task_id=task_id,
        )
        self._execute(issued, self.store.state(self.run_id), task_id)

    def _recover(self) -> bool:
        """Hanging command (issued, no result): reconcile first (D-13), reissue
        the same assignment without consuming an attempt (D-11)."""
        state = self.store.state(self.run_id)
        if state.pending:
            cmd = Command(kind=state.pending["kind"],
                          params=state.pending.get("params", {}),
                          command_id=state.pending.get("command_id"))
            self._execute(cmd, state, None, reconcile=True)
            return cmd.kind == "dispatch_agent"
        return False

    def _execute(self, cmd: Command, state: State,
                 task_id: str | None, reconcile: bool = False) -> None:
        getattr(self, "_do_" + cmd.kind)(cmd, state, task_id, reconcile)

    # -- per-kind handlers ---------------------------------------------------

    def _do_dispatch_agent(self, cmd, state, task_id, reconcile):
        p = cmd.params
        role, substate, doc = p["role"], p["substate"], p.get("doc")
        doc_path = self._doc_path(doc) if doc else None
        assignment = p.get("assignment")
        if p.get("evidence") is not None:
            # FR-11: failed-outcome evidence rides params["evidence"]; merge it
            # into the assignment so the backend renders it into the prompt
            # (opencode._assignment_context serializes the whole assignment).
            assignment = dict(assignment or {})
            assignment["evidence"] = p["evidence"]
        result = self.backend.act(
            role, substate, doc, doc_path, assignment=assignment
        )
        self._emit("outcome.received", _dispatch_payload(self.store, p, result),
                   command_id=cmd.command_id, task_id=task_id)
        verdict = result.get("verdict")
        # D-29 anti-self-report triple ③: M-TEST PRISM_REVIEW criteria-pack
        # mismatch -> verdict.failed, re-dispatch Prism (no prism.verdict).
        if self._criteria_pack_mismatch(role, substate, state, verdict,
                                        assignment, result, cmd):
            return
        if verdict:
            self._emit_verdict(role, verdict, result, state, p, cmd, task_id)

    def _criteria_pack_mismatch(self, role, substate, state, verdict,
                                assignment, result, cmd) -> bool:
        """Return True when the criteria-pack identity mismatch was emitted
        (caller should skip the normal verdict emission)."""
        if not (verdict and role == "prism" and substate == "PRISM_REVIEW"
                and state.stage == "M-TEST"):
            return False
        assigned_pack = (assignment or {}).get("criteria_pack")
        outcome_pack = result.get("criteria_pack")
        if assigned_pack and outcome_pack != assigned_pack:
            self._emit("verdict.failed",
                       {"check": "criteria_pack_mismatch",
                        "reason": f"expected {assigned_pack}, got {outcome_pack}",
                        "attempt": state.current_attempt + 1,
                        "evidence": str(outcome_pack)},
                       command_id=cmd.command_id)
            return True
        return False

    def _emit_verdict(self, role, verdict, result, state, p, cmd, task_id):
        """Emit the verdict event (+ review.round_started for Prism revise)."""
        ev = _VERDICT_EVENT[role]
        payload = {"verdict": verdict, "diff_ref": result.get("diff_ref")}
        if role == "prism" and state.stage == "M-TEST":
            payload["criteria_pack"] = result.get("criteria_pack")
        self._emit(ev, payload, command_id=cmd.command_id, task_id=task_id)
        if ev == "prism.verdict" and verdict != "pass":
            # flow.md §8.2: a revise verdict opens the next review round
            # (reducer bumps review_round and resets the reviewer flags).
            self._emit("review.round_started",
                       {"stage": p.get("stage"),
                        "round": state.review_round + 1},
                       command_id=cmd.command_id, task_id=task_id)

    def _do_validate_document(self, cmd, state, task_id, reconcile):
        doc = cmd.params["doc"]
        path = self._doc_path(doc)
        checks = cmd.params.get("checks") or []
        # token() is FakeBackend's optional TRAC_FAKE_SIMULATE hook; production
        # backends (OpencodeBackend) implement only act() -> "ok" (no simulation).
        token = getattr(self.backend, "token", lambda *_: "ok")("validator", doc, "ok")
        failure = validate_document(path, doc, checks)
        if failure is None and token != "ok":
            failure = ("schema", f"simulated failure: {token}")
        if failure:
            check, reason = failure
            payload = {"check": check, "reason": reason, "evidence": str(path)}
            if check != "scope_overflow":  # FR-20 rolls back, never escalates
                payload["attempt"] = state.current_attempt + 1
            self._emit("verdict.failed", payload, command_id=cmd.command_id)
        else:
            self._emit("verdict.passed",
                       {"check": ",".join(checks) or "schema", "detail": "format checks"},
                       command_id=cmd.command_id)

    def _do_commit_document(self, cmd, state, task_id, reconcile):
        doc, message = cmd.params["doc"], cmd.params["message"]
        marker = f"command_id: {cmd.command_id}"
        if reconcile:
            probe = git(self.repo, "log", "--grep", marker,
                        "--format=%H", check=False)
            if probe.stdout.split():
                self._emit_committed(doc, probe.stdout.split()[0], cmd.command_id)
                return
        doc_path = self._doc_path(doc)
        stage_paths = [doc_path]
        if state.stage == "M-DESIGN" and doc == "architecture.md":
            scaffold_paths, issue = self._design_scaffold_paths(doc_path)
            if issue is not None:
                self._emit_commit_failure_evidence(
                    state, cmd.command_id, "declared scaffold path rejected", issue,
                )
                return
            stage_paths.extend(scaffold_paths)
        git(self.repo, "add", *(str(path) for path in stage_paths))
        proc = _commit_if_staged(self.repo, f"{message}\n\n{marker}")
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit_committed(doc, commit_sha, cmd.command_id)

    def _design_scaffold_paths(self, architecture: Path) -> tuple[list[Path], str | None]:
        try:
            text = architecture.read_text(encoding="utf-8")
        except OSError as exc:
            return [], f"scaffold manifest unreadable: {exc}"
        paths = []
        for raw in sorted(_scaffold_declared_paths(text)):
            path, issue = self._stageable_scaffold_path(raw)
            if issue is not None:
                return [], issue
            assert path is not None
            paths.append(path)
        return paths, None

    def _stageable_scaffold_path(self, raw: str) -> tuple[Path | None, str | None]:
        raw_path = Path(raw)
        candidate = raw_path if raw_path.is_absolute() else self.repo / raw_path
        try:
            resolved = candidate.resolve(strict=False)
            relative = resolved.relative_to(self.repo.resolve())
        except (OSError, RuntimeError, ValueError):
            return None, f"scaffold path escapes repository: {raw}"
        if raw_path.is_absolute():
            return None, f"scaffold path is not allowed (must be repo-relative): {raw}"
        if not relative.parts or relative.parts[0] in _SCAFFOLD_RESERVED_ROOTS:
            return None, f"scaffold path is not allowed: {raw}"
        if candidate.is_symlink():
            return None, f"scaffold path is not allowed (symlink): {raw}"
        if not candidate.exists():
            return None, f"scaffold path is missing: {raw}"
        if candidate.is_dir():
            return None, f"scaffold path is a directory: {raw}"
        if not candidate.is_file():
            return None, f"scaffold path is not allowed: {raw}"
        relative_text = str(relative)
        tracked = git(self.repo, "ls-files", "--error-unmatch", "--",
                      relative_text, check=False).returncode == 0
        ignored = git(self.repo, "check-ignore", "--quiet", "--no-index", "--",
                      relative_text, check=False).returncode == 0
        if ignored and not tracked:
            return None, f"scaffold path is not allowed (ignored): {raw}"
        return self.repo / relative, None

    def _emit_committed(self, doc, commit_sha, command_id, final=False):
        ev_type, sha_key = _COMMITTED_EVENT[doc]
        payload = {"commit_sha": commit_sha,
                   sha_key: doc_body_sha(self._doc_path(doc)),
                   "final": final}
        if ev_type == "design.committed":
            payload["doc"] = doc  # one event type serves all three design docs
        self._emit(ev_type, payload, command_id=command_id)

    def _do_write_frontmatter(self, cmd, state, task_id, reconcile):
        """EXIT seal (FR-17/FR-23): body sha256 -> frontmatter `sha` -> commit ->
        stage.exited (+ next stage.entered / run.completed). Without a `doc`
        (M-REQ-APPROVAL boundary SM-05.6; M-DESIGN EXIT, which writes nothing
        extra per flow.md §8) there is nothing to seal."""
        doc, stage = cmd.params.get("doc"), cmd.params["stage"]
        if reconcile and state.stage_exited:
            return
        if doc:
            path = self._doc_path(doc)
            set_frontmatter_field(path, "sha", doc_body_sha(path))
            git(self.repo, "add", str(path))
            proc = _commit_if_staged(
                self.repo,
                f"{stage}: seal {doc} sha\n\ncommand_id: {cmd.command_id}")
            if proc is not None and proc.returncode != 0:
                self._emit_commit_failure(proc, state, cmd.command_id)
                return
            # R4-02: the sealed commit gets its own final committed event so ACs
            # match `final=true` and never the DRAFT-stage commit.
            commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            self._emit_committed(doc, commit_sha, cmd.command_id, final=True)
        self._emit("stage.exited", {"stage": stage}, command_id=cmd.command_id)
        nxt = _NEXT_STAGE.get(stage)
        if nxt:
            self._emit("stage.entered", {"stage": nxt},
                       command_id=cmd.command_id)
        else:
            # SM-05.6 / IF-003 §10f: no successor -> stop at the next-stage
            # boundary (M-DESIGN boundary pre-v0.3; M-IMPL boundary since).
            self._emit("run.completed", {"terminal_state": "boundary"},
                       command_id=cmd.command_id)

    def _do_record_backlog(self, cmd, state, task_id, reconcile):
        if reconcile and state.backlog_recorded:
            return
        self._emit("backlog.recorded", dict(cmd.params),
                   command_id=cmd.command_id)

    def _do_complete_run(self, cmd, state, task_id, reconcile):
        # Branch deletion is a separate delete_branch command (FR-09), not here.
        self._emit("run.completed", {"terminal_state": cmd.params["terminal_state"]},
                   command_id=cmd.command_id)

    def _do_create_branch(self, cmd, state, task_id, reconcile):
        """Create/switch the branch, then log branch.created. Reconcile (R3-03):
        the git work is skipped iff the branch exists AND HEAD is already on it;
        branch.created is always logged so the command closes."""
        branch = cmd.params["branch_name"]
        base = cmd.params.get("base", "main")
        exists = git(self.repo, "rev-parse", "--verify", branch,
                     check=False).returncode == 0
        if not (exists and self._head() == branch):
            if exists:
                git(self.repo, "checkout", branch)
            else:
                git(self.repo, "checkout", "-b", branch, base)
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit("branch.created",
                   {"branch_name": branch, "base": base, "commit_sha": commit_sha},
                   command_id=cmd.command_id)

    def _do_delete_branch(self, cmd, state, task_id, reconcile):
        """Tear down the branch, then log branch.deleted. Reconcile (R3-03):
        _teardown_branch is idempotent (done iff HEAD==main AND branch absent)."""
        branch = cmd.params["branch_name"]
        self._teardown_branch(branch)
        self._emit("branch.deleted", {"branch_name": branch},
                   command_id=cmd.command_id)

    def _do_rollback_stage(self, cmd, state, task_id, reconcile):
        self._emit("stage.rolled_back",
                   {"from_stage": state.stage,
                    "to_stage": cmd.params["to_stage"],
                    "reason": cmd.params.get("reason", "")},
                   command_id=cmd.command_id)

    # -- M-TEST handlers (flow.md §9, FR-0030/0050/0070) -----------------------

    def _do_collect_tests(self, cmd, state, task_id, reconcile):
        """SM-01.5: Runtime independently collects tests/integration + tests/e2e
        via pytest --collect-only (architecture.md §3.2). Never trusts the
        Shield self-report."""
        if reconcile and state.test_collected:
            return
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "tests/integration/", "tests/e2e/"],
            cwd=self.repo, capture_output=True, text=True,
        )
        if proc.returncode == 0:
            count = _parse_collected_count(proc.stdout)
            self._emit("test.collected",
                       {"status": "passed", "collected_count": count, "errors": []},
                       command_id=cmd.command_id)
        else:
            self._emit("test.collected",
                       {"status": "failed", "collected_count": 0,
                        "errors": [proc.stderr.strip() or proc.stdout.strip()]},
                       command_id=cmd.command_id)

    def _do_run_tests(self, cmd, state, task_id, reconcile):
        """SM-01.9: Runtime independently re-runs integration/e2e and classifies
        each failure (FR-0050). All-legit -> red.validated(valid) -> EXIT; any
        illegit or unexpected pass -> red.validated(invalid) -> DIAGNOSE."""
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/integration/", "tests/e2e/",
             "--tb=short", "-q"],
            cwd=self.repo, capture_output=True, text=True,
        )
        output = f"{proc.stdout}\n{proc.stderr}"
        red_class = classify_red("*", proc.returncode, proc.stdout, proc.stderr)
        legit = red_class in _LEGIT_RED
        findings = [{"test_id": "*", "classification": red_class,
                     "detail": _short_detail(output)}]
        if legit:
            self._emit("red.validated",
                       {"status": "valid", "findings": findings},
                       command_id=cmd.command_id)
            return
        # Invalid Red -> DIAGNOSE: classify the gap and emit verdict.failed.
        self._emit("red.validated",
                   {"status": "invalid", "findings": findings},
                   command_id=cmd.command_id)
        classification = self._diagnose_classification()
        target = {"test_defect": "M-TEST", "stub_gap": "M-DESIGN",
                  "ac_gap": "M-ACC", "spec_gap": "M-SPEC"}.get(classification, "M-TEST")
        self._emit("verdict.failed",
                   {"check": classification, "target_stage": target,
                    "artifact_disposition": "rewrite" if classification == "test_defect"
                    else "rollback", "reason": red_class,
                    "attempt": state.current_attempt + 1},
                   command_id=cmd.command_id)

    def _do_check_trace(self, cmd, state, task_id, reconcile):
        """SM-01.14 EXIT gate: trac check trace closure, filtered to required
        ACs (integration|e2e layer, FR-0070 decision A / architecture.md §5.1)."""
        if reconcile and state.trace_passed:
            return
        from tracks.checks.trace import check_trace_full_file  # lazy: avoid circular import
        vdir = self._vdir()
        tests_dir = self.repo / "tests"
        report = check_trace_full_file(vdir, tests_dir)
        required = required_ac_ids(vdir / "acceptance.md", vdir / "test-plan.md")
        blocking = [e for e in report.hard_errors
                    if any(rid in e for rid in required)] if required else list(
                        report.hard_errors)
        if not blocking:
            self._emit("verdict.passed",
                       {"check": "trace", "detail": "trace closure verified"},
                       command_id=cmd.command_id)
        else:
            self._emit("verdict.failed",
                       {"check": "trace",
                        "reason": "; ".join(blocking),
                        "evidence": "trac check trace",
                        "attempt": state.current_attempt + 1},
                       command_id=cmd.command_id)

    def _do_commit_tests(self, cmd, state, task_id, reconcile):
        """SM-01.14: freeze the test asset via a controlled git commit of tests/
        -> test.committed + stage.exited(M-TEST) + run.completed(boundary)."""
        if reconcile and state.test_committed:
            return
        marker = f"command_id: {cmd.command_id}"
        tests_dir = self.repo / "tests"
        if tests_dir.exists():
            git(self.repo, "add", "tests")
        proc = _commit_if_staged(
            self.repo, f"M-TEST: freeze test assets\n\n{marker}")
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit("test.committed",
                   {"commit_sha": commit_sha, "test_count": test_count},
                   command_id=cmd.command_id)
        self._emit("stage.exited", {"stage": "M-TEST"}, command_id=cmd.command_id)
        # M-TEST has no successor (M-IMPL not registered) -> boundary.
        self._emit("run.completed", {"terminal_state": "boundary"},
                   command_id=cmd.command_id)

    def _diagnose_classification(self) -> str:
        """Read the DIAGNOSE classification from the fake backend's simulate
        token (default ``test_defect``). The real channel dispatches Prism for
        diagnostic review; the fake channel keeps it deterministic."""
        token = getattr(self.backend, "token",
                        lambda *_: "test_defect")("diagnose", "classification",
                                                  "test_defect")
        return token if token in ("test_defect", "stub_gap", "ac_gap",
                                  "spec_gap") else "test_defect"

    # -- M-REQ-APPROVAL handlers (FR-0180/0190/0200) ---------------------------

    def _vdir(self) -> Path:
        return paths.version_dir(self.store.home, self.version)

    def _do_generate_preview(self, cmd, state, task_id, reconcile):
        if reconcile and state.preview_ready:
            return
        vdir = self._vdir()
        self._emit("preview.generated",
                   {"digest": revision_digest(vdir),
                    "summary": baseline_summary(vdir)},
                   command_id=cmd.command_id)

    def _stale_regenerate(self, cmd, approved_digest: str) -> bool:
        """FR-0190 entry gate (D-02/D-03): post-approval commands recompute the
        trio digest; a mismatch means the approval is stale — regenerate the
        preview (back to the human gate) instead of proceeding downstream."""
        vdir = self._vdir()
        current = revision_digest(vdir)
        if current == approved_digest:
            return False
        self._emit("preview.generated",
                   {"digest": current, "summary": baseline_summary(vdir)},
                   command_id=cmd.command_id)
        return True

    def _do_record_approval(self, cmd, state, task_id, reconcile):
        if reconcile and state.substate == "ISSUES":
            return
        if self._stale_regenerate(cmd, cmd.params["digest"]):
            return
        self._emit("approval.recorded",
                   {"actor": cmd.params["actor"], "digest": cmd.params["digest"],
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "readonly": True},
                   command_id=cmd.command_id)

    def _do_create_issues(self, cmd, state, task_id, reconcile):
        digest = cmd.params["digest"]
        if reconcile and state.issues_created:
            return
        if self._stale_regenerate(cmd, digest):
            return
        if self._issue_backend is None:
            self._issue_backend = select_issue_backend(self.repo, self.version)
        backend = self._issue_backend
        # D-06 breakpoint resume: item_ids already logged are never rebuilt.
        done = {e.payload["item_id"]: e.payload["issue_id"]
                for e in self.store.events(self.run_id)
                if e.type == "issue.created"}
        try:
            for item_id, title, body in issue_items(self._vdir(), digest):
                if item_id in done:
                    continue
                issue_id = backend.create_issue(title, body, [self.version])
                backend.add_to_project(issue_id, backend.project)
                done[item_id] = issue_id
                self._emit("issue.created",
                           {"item_id": item_id, "issue_id": issue_id,
                            "digest": digest},
                           command_id=cmd.command_id)
        except GithubIssuesError as e:
            # NFR-0030 style: no half-written summary; the reducer counts the
            # failed outcome as an attempt (3rd escalates to Human).
            self._emit("outcome.received",
                       {"role": "github", "status": "failed",
                        "failure_class": e.classification,
                        "self_report": str(e)},
                       command_id=cmd.command_id)
            return
        self._emit("issues.created",
                   {"digest": digest, "mapping": done,
                    "project": backend.project},
                   command_id=cmd.command_id)

    # -- git helpers -----------------------------------------------------------

    def _head(self) -> str:
        return git(self.repo, "symbolic-ref", "--short", "HEAD",
                   check=False).stdout.strip()

    def _teardown_branch(self, branch: str) -> None:
        """FR-09 / reconcile-safe: end with HEAD==main and branch absent."""
        if self._head() != "main":
            git(self.repo, "checkout", "main")
        if git(self.repo, "rev-parse", "--verify", branch,
               check=False).returncode == 0:
            git(self.repo, "branch", "-D", branch)
