"""ResultCheckpoint pipeline: payload construction, validate, checkpoint,
publish. Extracted from ``executor.py`` for module-size compliance (C0302).
The ``ResultCheckpointMixin`` is inherited by ``Executor``; all methods
access shared infrastructure (``_emit``, ``_doc_path``, ``_artifact_path``,
``_dirty_files``, ``_design_scaffold_paths``, ``_project_contract_paths``,
``_emit_committed``, ``_emit_commit_failure*``) via ``self``.
"""
from __future__ import annotations

import subprocess
import sys

from tracks.executor.helpers import _commit_if_staged, git
from tracks.executor.validate import (
    capture_digests,
    has_diff,
    has_staged_changes,
    is_discussion_diff,
    validate_document,
    verify_digests,
)
from tracks.kernel.machine import _STAGES, DESIGN_DOCS
from tracks.store import new_ulid

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


class ResultCheckpointMixin:
    """ResultCheckpoint pipeline methods.  Requires the host class to provide:
    ``store``, ``repo``, ``run_id``, ``version``, ``backend``,
    ``_emit``, ``_emit_commit_failure``, ``_emit_commit_failure_evidence``,
    ``_doc_path``, ``_doc_paths``, ``_artifact_path``, ``_artifact_paths``,
    ``_dirty_files``, ``_design_scaffold_paths``,
    ``_project_contract_paths``, ``_emit_committed``.
    """

    # -- payload construction -------------------------------------------------

    def _result_checkpoint_payload(self, cmd, state, result, params,
                                   substate, role, doc, pre_dirty=None):
        """Build the ResultCheckpoint pipeline payload for a successful agent
        dispatch. Delegates to per-stage helpers."""
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = cmd.command_id
        if state.stage == "M-DESIGN":
            return self._design_payload(
                substate, role, result, base_sha, result_id)
        if state.stage == "M-TEST":
            return self._m_test_payload(
                substate, role, result, base_sha, result_id, pre_dirty)
        return self._requirement_payload(
            state, substate, role, result, base_sha, result_id)

    def _design_payload(self, substate, role, result, base_sha, result_id):
        """M-DESIGN: multi-doc pipeline (3 design docs + scaffold)."""
        docs = list(DESIGN_DOCS)
        doc_paths = {doc: self._doc_path(doc) for doc in docs}
        digests = capture_digests(doc_paths)
        if substate in ("DRAFT", "RESPOND"):
            return self._design_draft_payload(
                substate, role, docs, doc_paths, digests,
                base_sha, result_id)
        verdict = result.get("verdict")
        return {
            "source": "prism", "stage": "M-DESIGN",
            "substate": "PRISM_REVIEW", "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": docs, "allowed_paths": docs,
            "base_sha": base_sha, "checks": ["template"],
            "requires_diff": verdict != "pass", "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"M-DESIGN: prism ({verdict}) checkpoint",
            "result_id": result_id, "digests": digests,
            "domain_event": {"type": "prism.verdict",
                              "payload": {"verdict": verdict}},
        }

    def _design_draft_payload(self, substate, role, docs, doc_paths,
                              digests, base_sha, result_id):
        """M-DESIGN DRAFT/RESPOND: author commits design docs + scaffold."""
        allowed = list(docs)
        scaffold_paths_str = []
        arch_path = doc_paths.get("architecture.md")
        if arch_path and arch_path.exists():
            scaffold_paths, issue = self._design_scaffold_paths(arch_path)
            if issue is None:
                for sp in scaffold_paths:
                    rel = str(sp.relative_to(self.repo))
                    allowed.append(rel)
                    scaffold_paths_str.append(rel)
        return {
            "source": role, "stage": "M-DESIGN", "substate": substate,
            "actor_kind": "agent",
            "artifacts": docs, "allowed_paths": allowed,
            "base_sha": base_sha, "checks": ["template"],
            "requires_diff": False, "forbid_diff": False,
            "discussion_only": False,
            "commit_label": f"M-DESIGN: {role} commit",
            "result_id": result_id,
            "digests": {**digests, **capture_digests(
                {str(p.relative_to(self.repo)): p
                 for p in (self.repo / sp for sp in scaffold_paths_str)
                 if (self.repo / sp).exists()})},
            "domain_event": {"type": "design.committed", "payload": {}},
        }

    def _m_test_payload(self, substate, role, result, base_sha,
                        result_id, pre_dirty):
        """M-TEST: Shield WRITE (tests/) or Prism PRISM_REVIEW."""
        if substate == "WRITE":
            return self._m_test_write_payload(base_sha, result_id, pre_dirty)
        return self._m_test_prism_payload(result, base_sha, result_id)

    def _m_test_write_payload(self, base_sha, result_id, pre_dirty):
        """M-TEST Shield WRITE: commit test files in tests/."""
        post_dirty = self._dirty_files()
        changed = (post_dirty - pre_dirty) if pre_dirty is not None \
            else post_dirty
        test_files = sorted(
            f for f in changed if f.startswith("tests/")
            and f.endswith(".py"))
        artifacts = list(test_files)
        digests = capture_digests({f: self.repo / f for f in test_files})
        return {
            "source": "shield", "stage": "M-TEST", "substate": "WRITE",
            "actor_kind": "agent",
            "artifacts": artifacts, "allowed_paths": test_files,
            "base_sha": base_sha,
            "checks": ["write_scope", "collection"],
            "requires_diff": False, "forbid_diff": False,
            "discussion_only": False,
            "commit_label": "M-TEST: shield commit",
            "result_id": result_id, "digests": digests,
            "domain_event": {"type": "test.written", "payload": {}},
        }

    def _m_test_prism_payload(self, result, base_sha, result_id):
        """M-TEST Prism PRISM_REVIEW: checkpoint discussion diff on design docs.
        acceptance.md is excluded - it may carry pre-existing uncommitted
        diffs from stale-approval modifications."""
        prism_docs = ["test-plan.md", "interfaces.md"]
        digests = capture_digests(
            {doc: self._doc_path(doc) for doc in prism_docs})
        verdict = result.get("verdict")
        return {
            "source": "prism", "stage": "M-TEST",
            "substate": "PRISM_REVIEW", "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": prism_docs, "allowed_paths": prism_docs,
            "base_sha": base_sha, "checks": ["template"],
            "requires_diff": verdict != "pass", "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"M-TEST: prism ({verdict}) checkpoint",
            "result_id": result_id, "digests": digests,
            "domain_event": {"type": "prism.verdict",
                              "payload": {"verdict": verdict,
                                          "criteria_pack": result.get("criteria_pack")}},
        }

    def _requirement_payload(self, state, substate, role, result,
                             base_sha, result_id):
        """M-STORY/M-SPEC/M-ACC: single-doc pipeline."""
        if substate in ("DRAFT", "RESPOND"):
            return self._requirement_draft_payload(
                state, substate, role, base_sha, result_id)
        return self._requirement_review_payload(
            state, substate, role, result, base_sha, result_id)

    def _requirement_draft_payload(self, state, substate, role,
                                   base_sha, result_id):
        """Requirement stage DRAFT/RESPOND: author commits single doc."""
        sd = _STAGES[state.stage]
        doc_name = sd.doc
        digests = capture_digests({doc_name: self._doc_path(doc_name)}) if doc_name else {}
        return {
            "source": role,
            "stage": state.stage,
            "substate": substate,
            "actor_kind": "agent",
            "artifacts": [doc_name],
            "allowed_paths": [doc_name],
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": False,
            "forbid_diff": False,
            "discussion_only": False,
            "commit_label": f"{state.stage}: {role} commit",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {
                "type": sd.committed_event,
                "payload": {},
            },
        }

    def _requirement_review_payload(self, state, substate, role, result,
                                    base_sha, result_id):
        """Requirement stage reviewer: checkpoint discussion diff, publish verdict."""
        sd = _STAGES[state.stage]
        verdict = result.get("verdict")
        doc_name = sd.doc
        digests = capture_digests({doc_name: self._doc_path(doc_name)}) if doc_name else {}
        return {
            "source": role,
            "stage": state.stage,
            "substate": substate,
            "actor_kind": "agent",
            "verdict": verdict,
            "artifacts": [doc_name] if doc_name else [],
            "allowed_paths": [doc_name] if doc_name else [],
            "base_sha": base_sha,
            "checks": ["template"],
            "requires_diff": verdict != "pass",
            "forbid_diff": False,
            "discussion_only": True,
            "commit_label": f"{state.stage}: {role} ({verdict}) checkpoint",
            "result_id": result_id,
            "digests": digests,
            "domain_event": {
                "type": sd.verdict_event,
                "payload": {"verdict": verdict},
            },
        }

    def submit_human_result(self, state, domain_event_type, payload,
                            verdict, artifacts, allowed_paths, checks,
                            requires_diff, commit_label,
                            forbid_diff=False, discussion_only=False):
        """v0.5: append result.submitted for a human gate (triage/review).
        The CLI calls this under writer_lock, then calls run_pipeline().

        v0.5 review-A:
        - result_id: stable ULID for audit/reconcile.
        - digests: artifact sha256 at capture, re-verified at checkpoint.
        - forbid_diff: no-comment gate rejects any doc diff.
        - discussion_only: human revise allows body edits (not discussion-only).
        """
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = new_ulid()
        digests = capture_digests(self._doc_paths(artifacts))
        self._emit("result.submitted", {
            "source": "human",
            "actor_kind": "human",
            "stage": state.stage,
            "substate": state.substate,
            "verdict": verdict,
            "artifacts": artifacts,
            "allowed_paths": allowed_paths,
            "base_sha": base_sha,
            "checks": checks,
            "requires_diff": requires_diff,
            "forbid_diff": forbid_diff,
            "discussion_only": discussion_only,
            "commit_label": commit_label,
            "result_id": result_id,
            "digests": digests,
            "domain_event": {
                "type": domain_event_type,
                "payload": payload,
            },
        })

    # -- validate -------------------------------------------------------------

    def _check_diff_policy(self, doc, base_sha, requires_diff, forbid_diff,
                           discussion_only, command_id, attempt):
        """Enforce diff policy for one artifact. Returns True if a failure
        was emitted (caller should abort)."""
        doc_path = self._artifact_path(doc)
        diff = has_diff(self.repo, doc_path, base_sha)
        if forbid_diff and diff:
            self._emit("verdict.failed",
                       {"check": "forbidden_diff",
                        "reason": f"{doc} has a diff but forbid_diff is set",
                        "evidence": str(doc_path),
                        "attempt": attempt},
                       command_id=command_id)
            return True
        if requires_diff and not diff:
            self._emit("verdict.failed",
                       {"check": "no_diff",
                        "reason": f"{doc} requires a diff but none was produced",
                        "evidence": str(doc_path),
                        "attempt": attempt},
                       command_id=command_id)
            return True
        if discussion_only and diff and \
                not is_discussion_diff(self.repo, doc_path, base_sha):
            self._emit("verdict.failed",
                       {"check": "discussion_diff",
                        "reason": f"diff in {doc} is not a canonical "
                                  f"discussion change",
                        "evidence": str(self._artifact_path(doc)),
                        "attempt": attempt},
                       command_id=command_id)
            return True
        return False

    def _do_validate_result(self, cmd, state, task_id, reconcile):
        """Validate artifacts captured by result.submitted. Runs the declared
        checks (template, etc.) on each artifact. Then enforces diff policy:

        - forbid_diff: target doc must have NO diff vs base_sha (no-comment gate).
        - discussion_only: if a diff exists, it must be canonical discussion
          change (blockquote additions). Applies to reviewer results regardless
          of pass/comment. Independent of requires_diff.
        - requires_diff: a diff MUST exist (reviewer comment/revise, human revise).

        On failure emits verdict.failed (which clears active_result); on success
        emits result.validated."""
        if reconcile and state.active_result and state.active_result.get("validated"):
            return
        artifacts = cmd.params.get("artifacts", [])
        checks = cmd.params.get("checks", [])
        base_sha = cmd.params.get("base_sha")
        attempt = state.current_attempt + 1
        if self._validate_artifacts(artifacts, checks, attempt, cmd.command_id):
            return
        if base_sha and self._validate_diff_policy(
                cmd, artifacts, base_sha, attempt):
            return
        self._emit("result.validated",
                   {"artifacts": artifacts, "base_sha": base_sha,
                    "result_id": cmd.params.get("result_id")},
                   command_id=cmd.command_id)

    def _validate_artifacts(self, artifacts, checks, attempt, command_id):
        """Run template/schema checks on each artifact. Returns True if a
        failure was emitted (caller should abort)."""
        if "write_scope" in checks and self._check_write_scope(
                artifacts, attempt, command_id):
            return True
        if "collection" in checks and self._check_collection(
                artifacts, attempt, command_id):
            return True
        if not ("write_scope" in checks or "collection" in checks):
            return self._check_templates(
                artifacts, checks, attempt, command_id)
        return False

    def _check_write_scope(self, artifacts, attempt, command_id):
        """Verify all artifacts are under tests/. Returns True on failure."""
        for artifact in artifacts:
            if not artifact.startswith("tests/"):
                self._emit("verdict.failed",
                           {"check": "write_scope",
                            "reason": f"file outside tests/: {artifact}",
                            "evidence": artifact, "attempt": attempt},
                           command_id=command_id)
                return True
        return False

    def _check_collection(self, artifacts, attempt, command_id):
        """Run pytest --collect-only on each test file. Returns True on failure."""
        for artifact in artifacts:
            path = self._artifact_path(artifact)
            if not path.exists():
                self._emit("verdict.failed",
                           {"check": "collection",
                            "reason": f"test file missing: {artifact}",
                            "evidence": str(path), "attempt": attempt},
                           command_id=command_id)
                return True
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q",
                 str(path)],
                cwd=self.repo, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                self._emit("verdict.failed",
                           {"check": "collection",
                            "reason": f"pytest collection failed: {artifact}",
                            "evidence": proc.stderr.strip() or
                            proc.stdout.strip(),
                            "attempt": attempt},
                           command_id=command_id)
                return True
        return False

    def _check_templates(self, artifacts, checks, attempt, command_id):
        """Run document template validation. Returns True on failure."""
        for doc in artifacts:
            path = self._doc_path(doc)
            failure = validate_document(path, doc, checks)
            token = getattr(self.backend, "token",
                            lambda *_: "ok")("validator", doc, "ok")
            if failure is None and token != "ok":
                failure = ("schema", f"simulated failure: {token}")
            if failure is not None:
                check, reason = failure
                self._emit("verdict.failed",
                           {"check": check, "reason": reason,
                            "evidence": str(path), "attempt": attempt},
                           command_id=command_id)
                return True
        return False

    def _validate_diff_policy(self, cmd, artifacts, base_sha, attempt):
        """Enforce requires_diff (batch-level) and per-doc diff policy.
        Returns True if a failure was emitted (caller should abort)."""
        requires_diff = cmd.params.get("requires_diff", False)
        if requires_diff:
            any_diff = any(
                has_diff(self.repo, self._artifact_path(doc), base_sha)
                for doc in artifacts)
            if not any_diff:
                self._emit("verdict.failed",
                           {"check": "no_diff",
                            "reason": "verdict requires a diff but "
                                      "none was produced in any artifact",
                            "evidence": str(artifacts),
                            "attempt": attempt},
                           command_id=cmd.command_id)
                return True
        for doc in artifacts:
            if self._check_diff_policy(
                    doc, base_sha,
                    False,  # requires_diff already checked at batch level
                    cmd.params.get("forbid_diff", False),
                    cmd.params.get("discussion_only", False),
                    cmd.command_id, attempt):
                return True
        return False

    # -- checkpoint -----------------------------------------------------------

    def _recover_checkpoint(self, command_id, base_sha, result_id):
        """Crash-safe recovery: if a commit with this command_id marker
        already exists, emit the missing result.checkpointed and return True."""
        marker = f"command_id: {command_id}"
        log_proc = git(self.repo, "log", "--grep", marker,
                       "--format=%H", "-1", check=False)
        found_sha = log_proc.stdout.strip()
        if found_sha:
            self._emit("result.checkpointed",
                       {"created_commit": True, "commit_sha": found_sha,
                        "base_sha": base_sha, "result_id": result_id},
                       command_id=command_id)
            return True
        return False

    def _check_checkpoint_preconditions(self, cmd, allowed_paths, source,
                                        attempt):
        """Check digests and diff policy. Returns 'abort', 'no_change', or
        'proceed'. Emits verdict.failed on abort."""
        digests = cmd.params.get("digests") or {}
        drifted_doc, drifted_path = verify_digests(
            self._artifact_paths(list(digests)), digests)
        if drifted_doc:
            self._emit("verdict.failed",
                       {"check": "digest_drift",
                        "reason": f"{drifted_doc} changed between capture "
                                  f"and checkpoint",
                        "evidence": str(drifted_path),
                        "attempt": attempt},
                       command_id=cmd.command_id)
            return "abort"
        forbid_diff = cmd.params.get("forbid_diff", False)
        requires_diff = cmd.params.get("requires_diff", False)
        changes = has_staged_changes(self.repo,
                                     self._artifact_paths(allowed_paths))
        if forbid_diff and changes:
            self._emit("verdict.failed",
                       {"check": "forbidden_diff",
                        "reason": f"{source} forbid_diff but changes present",
                        "evidence": str(allowed_paths),
                        "attempt": attempt},
                       command_id=cmd.command_id)
            return "abort"
        if requires_diff and not changes:
            self._emit("verdict.failed",
                       {"check": "no_diff",
                        "reason": f"{source} verdict requires a diff "
                                  f"but no changes were produced",
                        "evidence": str(allowed_paths),
                        "attempt": attempt},
                       command_id=cmd.command_id)
            return "abort"
        return "no_change" if not changes else "proceed"

    def _do_checkpoint_result(self, cmd, state, task_id, reconcile):
        """Independent checkpoint: stage allowed_paths, commit with a
        command_id marker. Re-checks diff against base_sha. Crash-safe: if a
        commit with this marker already exists, emits without re-committing.
        Verifies digests before committing (v0.5 review-D)."""
        if reconcile and state.active_result and state.active_result.get("checkpointed"):
            return
        if self._recover_checkpoint(cmd.command_id,
                                    cmd.params.get("base_sha"),
                                    cmd.params.get("result_id")):
            return
        current_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        base_sha = cmd.params.get("base_sha")
        if base_sha and current_head != base_sha:
            self._emit_commit_failure_evidence(
                state, cmd.command_id,
                "base_sha mismatch: checkpoint aborted",
                f"expected {base_sha}, got {current_head}",
            )
            return
        status = self._check_checkpoint_preconditions(
            cmd, cmd.params.get("allowed_paths", []),
            cmd.params.get("source", "?"), state.current_attempt + 1)
        if status == "abort":
            return
        if status == "no_change":
            self._emit("result.checkpointed",
                       {"created_commit": False, "commit_sha": current_head,
                        "base_sha": base_sha,
                        "result_id": cmd.params.get("result_id")},
                       command_id=cmd.command_id)
            return
        self._stage_and_commit(cmd, state, base_sha, current_head)

    def _stage_and_commit(self, cmd, state, base_sha, current_head):
        """Stage allowed_paths (+ scaffold for M-DESIGN), commit, emit result."""
        allowed_paths = cmd.params.get("allowed_paths", [])
        source = cmd.params.get("source", "?")
        commit_label = cmd.params.get("commit_label") or f"{source} checkpoint"
        stage_paths = [self._artifact_path(doc) for doc in allowed_paths]
        if self._maybe_add_scaffold(state, allowed_paths, stage_paths,
                                    cmd, state.current_attempt + 1):
            return
        git(self.repo, "add", *(str(path) for path in stage_paths))
        proc = _commit_if_staged(
            self.repo, f"{commit_label}\n\ncommand_id: {cmd.command_id}")
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        checkpoint_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit("result.checkpointed",
                   {"created_commit": True, "commit_sha": checkpoint_sha,
                    "base_sha": base_sha,
                    "result_id": cmd.params.get("result_id")},
                   command_id=cmd.command_id)

    def _maybe_add_scaffold(self, state, allowed_paths, stage_paths,
                            cmd, attempt):
        """For M-DESIGN DRAFT/RESPOND, add scaffold + contract paths to
        stage_paths. Returns True if a failure was emitted (abort)."""
        if not (state.stage == "M-DESIGN"
                and state.substate in ("DRAFT", "RESPOND")
                and "architecture.md" in allowed_paths):
            return False
        arch_path = self._artifact_path("architecture.md")
        scaffold_paths, issue = self._design_scaffold_paths(arch_path)
        if issue is not None:
            self._emit_commit_failure_evidence(
                state, cmd.command_id,
                "declared scaffold path rejected", issue)
            return True
        already_staged = {str(p) for p in stage_paths}
        for sp in scaffold_paths:
            if str(sp) not in already_staged:
                stage_paths.append(sp)
        for cp in self._project_contract_paths():
            if str(cp) not in already_staged:
                stage_paths.append(cp)
        return False

    # -- publish --------------------------------------------------------------

    def _do_publish_result(self, cmd, state, task_id, reconcile):
        """Publish the domain event that the pipeline captured. The domain
        event's reducer clears active_result (or verdict.failed on failure).
        v0.5 review-D: result_id propagated as audit correlation."""
        if reconcile and state.active_result is None:
            return
        domain_event = cmd.params.get("domain_event", {})
        ev_type = domain_event.get("type")
        payload = dict(domain_event.get("payload", {}))
        commit_sha = cmd.params.get("commit_sha")
        created_commit = cmd.params.get("created_commit", False)
        verdict = cmd.params.get("verdict")
        result_id = cmd.params.get("result_id")
        payload["result_id"] = result_id
        _PUBLISH_DISPATCH = {
            "story.committed": "committed",
            "spec.committed": "committed",
            "acceptance.committed": "committed",
            "design.committed": "design",
            "test.written": "test_written",
            "sage.verdict": "review_verdict",
            "lex.verdict": "review_verdict",
            "prism.verdict": "prism",
            "human.triage": "human",
            "human.review": "human",
        }
        handler = _PUBLISH_DISPATCH.get(ev_type, "unknown")
        if handler == "committed":
            self._publish_committed(ev_type, commit_sha, created_commit,
                                    state, cmd)
        elif handler == "design":
            self._publish_design_committed(commit_sha, state, cmd)
        elif handler == "test_written":
            self._publish_test_written(commit_sha, state, cmd)
        elif handler == "review_verdict":
            payload["verdict"] = verdict
            payload["diff_ref"] = commit_sha if created_commit and commit_sha else None
            self._emit(ev_type, payload, command_id=cmd.command_id)
        elif handler == "prism":
            self._publish_prism_verdict(verdict, commit_sha, created_commit,
                                        result_id, state, cmd, task_id)
        elif handler == "human":
            self._publish_human(ev_type, payload, verdict, commit_sha,
                                created_commit, cmd)
        else:
            self._emit("verdict.failed",
                       {"check": "publish_error",
                        "reason": f"unknown domain_event: {ev_type}",
                        "attempt": state.current_attempt + 1},
                       command_id=cmd.command_id)

    def _publish_human(self, ev_type, payload, verdict, commit_sha,
                       created_commit, cmd):
        """Publish human.triage or human.review event."""
        if ev_type == "human.triage":
            payload["decision"] = verdict
        else:
            payload["action"] = verdict
            payload["diff_ref"] = commit_sha if created_commit and commit_sha else None
        self._emit(ev_type, payload, command_id=cmd.command_id)

    def _publish_committed(self, ev_type, commit_sha, created_commit,
                           state, cmd):
        """Author publish: resolve doc from event type and emit committed.
        v0.5 review-D: result_id propagated as audit correlation."""
        doc = None
        for d in ("story.md", "spec.md", "acceptance.md"):
            if _COMMITTED_EVENT.get(d, (None,))[0] == ev_type:
                doc = d
                break
        if doc is None:
            self._emit("verdict.failed",
                       {"check": "publish_error",
                        "reason": f"unknown committed event: {ev_type}",
                        "attempt": state.current_attempt + 1},
                       command_id=cmd.command_id)
            return
        checkpoint_sha = commit_sha or git(
            self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit_committed(doc, checkpoint_sha, cmd.command_id,
                             result_id=cmd.params.get("result_id"))

    def _publish_design_committed(self, commit_sha, state, cmd):
        """M-DESIGN author publish: emit one design.committed per doc (all
        three share the same checkpoint commit_sha)."""
        checkpoint_sha = commit_sha or git(
            self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = cmd.params.get("result_id")
        for doc in cmd.params.get("artifacts", []):
            self._emit_committed(doc, checkpoint_sha, cmd.command_id,
                                 result_id=result_id)

    def _publish_test_written(self, commit_sha, state, cmd):
        """M-TEST Shield WRITE publish: emit test.written with the checkpoint
        commit_sha. The reducer transitions WRITE -> COLLECT."""
        self._emit("test.written",
                   {"commit_sha": commit_sha,
                    "result_id": cmd.params.get("result_id")},
                   command_id=cmd.command_id)

    def _publish_prism_verdict(self, verdict, commit_sha, created_commit,
                               result_id, state, cmd, task_id):
        """Prism verdict publish (M-DESIGN and M-TEST). Emits prism.verdict
        with the verdict, diff_ref, and criteria_pack (M-TEST only). For
        non-pass verdicts, also emits review.round_started (M-DESIGN only;
        M-TEST revise goes straight to WRITE via the reducer)."""
        payload = {"verdict": verdict,
                   "diff_ref": commit_sha if created_commit and commit_sha else None,
                   "result_id": result_id}
        if state.stage == "M-TEST":
            payload["criteria_pack"] = cmd.params.get("domain_event", {}).get(
                "payload", {}).get("criteria_pack")
        self._emit("prism.verdict", payload,
                   command_id=cmd.command_id, task_id=task_id)
        if verdict != "pass" and state.stage == "M-DESIGN":
            self._emit("review.round_started",
                       {"stage": "M-DESIGN",
                        "round": state.review_round + 1},
                       command_id=cmd.command_id, task_id=task_id)
