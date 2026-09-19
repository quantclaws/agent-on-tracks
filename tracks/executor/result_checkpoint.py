"""ResultCheckpoint pipeline: validate, checkpoint, publish.

Extracted from ``executor.py`` for module-size compliance (C0302).
The ``ResultCheckpointMixin`` is inherited by ``Executor``; all methods
access shared infrastructure (``_emit``, ``_doc_path``, ``_artifact_path``,
``_dirty_files``, ``_design_scaffold_paths``, ``_project_contract_paths``,
``_emit_committed``, ``_emit_commit_failure*``) via ``self``.

Composition (C0302 split): payload construction lives in
:mod:`tracks.executor.result_payload` and the validation/audit face in
:mod:`tracks.executor.result_audit`; both mixins are composed into
``ResultCheckpointMixin`` below, so the pre-split import surface of this
module is unchanged (``ResultCheckpointMixin`` still carries every method).
"""

from __future__ import annotations

import subprocess as subprocess  # noqa: F401  (explicit re-export: test seam)

from tracks.executor.file_identity import (  # noqa: F401  (re-export: test import seam)
    is_regular_file_identity as _is_regular_file_identity,
)
from tracks.executor.helpers import _scoped_commit_if_staged, git
from tracks.executor.host_contract import CANONICAL_CONTRACT_RELPATH
from tracks.executor.result_audit import ResultAuditMixin
from tracks.executor.result_payload import ResultPayloadMixin
from tracks.executor.validate import has_staged_changes, verify_digests
from tracks.kernel.events import Command

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


class ResultCheckpointMixin(ResultPayloadMixin, ResultAuditMixin):
    """ResultCheckpoint pipeline methods.  Requires the host class to provide:
    ``store``, ``repo``, ``run_id``, ``version``, ``backend``,
    ``_emit``, ``_emit_commit_failure``, ``_emit_commit_failure_evidence``,
    ``_doc_path``, ``_doc_paths``, ``_artifact_path``, ``_artifact_paths``,
    ``_dirty_files``, ``_dirty_snapshot``, ``_design_scaffold_paths``,
    ``_project_contract_paths``, ``_run_contract_sections``,
    ``_emit_committed``.
    """

    # -- checkpoint -----------------------------------------------------------

    def _recover_checkpoint(self, command_id, base_sha, result_id):
        """Crash-safe recovery: if a commit with this command_id marker
        already exists, emit the missing result.checkpointed and return True."""
        marker = f"command_id: {command_id}"
        log_proc = git(self.repo, "log", "--grep", marker, "--format=%H", "-1", check=False)
        found_sha = log_proc.stdout.strip()
        if found_sha:
            self._emit(
                "result.checkpointed",
                {
                    "created_commit": True,
                    "commit_sha": found_sha,
                    "base_sha": base_sha,
                    "result_id": result_id,
                },
                command_id=command_id,
            )
            return True
        return False

    def _check_checkpoint_preconditions(self, cmd, allowed_paths, source, attempt):
        """Check digests and diff policy. Returns 'abort', 'no_change', or
        'proceed'. Emits verdict.failed on abort."""
        digests = cmd.params.get("digests") or {}
        drifted_doc, drifted_path = verify_digests(self._artifact_paths(list(digests)), digests)
        if drifted_doc:
            self._emit(
                "verdict.failed",
                {
                    "check": "digest_drift",
                    "reason": f"{drifted_doc} changed between capture and checkpoint",
                    "evidence": str(drifted_path),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
            )
            return "abort"
        forbid_diff = cmd.params.get("forbid_diff", False)
        requires_diff = cmd.params.get("requires_diff", False)
        changes = has_staged_changes(self.repo, self._artifact_paths(allowed_paths))
        if forbid_diff and changes:
            self._emit(
                "verdict.failed",
                {
                    "check": "forbidden_diff",
                    "reason": f"{source} forbid_diff but changes present",
                    "evidence": str(allowed_paths),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
            )
            return "abort"
        if requires_diff and not changes and not cmd.params.get("no_diff_approved", False):
            self._emit(
                "verdict.failed",
                {
                    "check": "no_diff",
                    "reason": f"{source} verdict requires a diff but no changes were produced",
                    "evidence": str(allowed_paths),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
            )
            return "abort"
        return "no_change" if not changes else "proceed"

    def _do_checkpoint_result(self, cmd, state, task_id, reconcile):
        """Independent checkpoint: stage allowed_paths, commit with a
        command_id marker. Re-checks diff against base_sha. Crash-safe: if a
        commit with this marker already exists, emits without re-committing.
        Verifies digests before committing (v0.5 review-D)."""
        if reconcile and state.active_result and state.active_result.get("checkpointed"):
            return
        if self._recover_checkpoint(
            cmd.command_id, cmd.params.get("base_sha"), cmd.params.get("result_id")
        ):
            return
        current_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        base_sha = cmd.params.get("base_sha")
        if base_sha and current_head != base_sha:
            self._emit_commit_failure_evidence(
                state,
                cmd.command_id,
                "base_sha mismatch: checkpoint aborted",
                f"expected {base_sha}, got {current_head}",
            )
            return
        status = self._check_checkpoint_preconditions(
            cmd,
            cmd.params.get("allowed_paths", []),
            cmd.params.get("source", "?"),
            state.current_attempt + 1,
        )
        if status == "abort":
            return
        if status == "no_change":
            self._emit(
                "result.checkpointed",
                {
                    "created_commit": False,
                    "commit_sha": current_head,
                    "base_sha": base_sha,
                    "result_id": cmd.params.get("result_id"),
                },
                command_id=cmd.command_id,
            )
            return
        self._stage_and_commit(cmd, state, base_sha, current_head)

    def _stage_and_commit(self, cmd, state, base_sha, current_head):
        """Stage allowed_paths (+ scaffold for M-DESIGN), commit, emit result."""
        allowed_paths = cmd.params.get("allowed_paths", [])
        source = cmd.params.get("source", "unknown source")
        commit_label = cmd.params.get("commit_label") or f"{source} checkpoint"
        stage_paths = [self._artifact_path(doc) for doc in allowed_paths]
        if self._maybe_add_scaffold(
            state, allowed_paths, stage_paths, cmd, state.current_attempt + 1
        ):
            return
        git(self.repo, "add", *(str(path) for path in stage_paths))
        # 2026-09-19 (run 01M2QTJB): scoped checkpoint commit, the
        # ResultCheckpoint twin of the helpers.py single-writer fix. The
        # unscoped _commit_if_staged swept EVERYTHING staged -- an
        # operator-staged file would land inside Devon's GREEN commit under
        # the wrong command_id (AC-FR0236-01 attribution) -- and ran with
        # repo hygiene hooks that evaluate the whole tree, so an
        # out-of-scope agent's WIP vetoed the checkpoint (same R0801 class
        # that mis-routed the 04:51 shield fix into a spurious replan).
        # The runtime stages exactly stage_paths above; commit exactly
        # those, --no-verify: the dispatch contract's gate suite is the
        # agent-facing lint authority, and operator commits keep the hooks.
        proc = _scoped_commit_if_staged(
            self.repo,
            f"{commit_label}\n\ncommand_id: {cmd.command_id}",
            paths=stage_paths,
        )
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        checkpoint_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit(
            "result.checkpointed",
            {
                "created_commit": True,
                "commit_sha": checkpoint_sha,
                "base_sha": base_sha,
                "result_id": cmd.params.get("result_id"),
            },
            command_id=cmd.command_id,
        )

    def _maybe_add_scaffold(self, state, allowed_paths, stage_paths, cmd, attempt):
        """For M-DESIGN DRAFT/RESPOND, add scaffold + contract paths to
        stage_paths. Returns True if a failure was emitted (abort)."""
        if not (
            state.stage == "M-DESIGN"
            and state.substate in ("DRAFT", "RESPOND")
            and "architecture.md" in allowed_paths
        ):
            return False
        arch_path = self._artifact_path("architecture.md")
        scaffold_paths, issue = self._design_scaffold_paths(arch_path)
        if issue is not None:
            self._emit_commit_failure_evidence(
                state, cmd.command_id, "declared scaffold path rejected", issue
            )
            return True
        already_staged = {str(p) for p in stage_paths}
        for sp in scaffold_paths:
            if str(sp) not in already_staged:
                stage_paths.append(sp)
                already_staged.add(str(sp))
        for cp in self._project_contract_paths():
            if str(cp) not in already_staged:
                stage_paths.append(cp)
                already_staged.add(str(cp))
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
            self._publish_committed(ev_type, commit_sha, created_commit, state, cmd)
        elif handler == "design":
            self._publish_design_committed(commit_sha, state, cmd)
        elif handler == "test_written":
            self._publish_test_written(commit_sha, state, cmd)
        elif handler == "review_verdict":
            payload["verdict"] = verdict
            payload["diff_ref"] = commit_sha if created_commit and commit_sha else None
            self._emit(ev_type, payload, command_id=cmd.command_id)
        elif handler == "prism":
            self._publish_prism_verdict(
                verdict, commit_sha, created_commit, result_id, state, cmd, task_id
            )
        elif handler == "human":
            self._publish_human(ev_type, payload, verdict, commit_sha, created_commit, cmd)
        else:
            self._emit(
                "verdict.failed",
                {
                    "check": "publish_error",
                    "reason": f"unknown domain_event: {ev_type}",
                    "evidence": ev_type,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )

    def _publish_human(self, ev_type, payload, verdict, commit_sha, created_commit, cmd):
        """Publish human.triage or human.review event."""
        if ev_type == "human.triage":
            payload["decision"] = verdict
        else:
            payload["action"] = verdict
            payload["diff_ref"] = commit_sha if created_commit and commit_sha else None
        self._emit(ev_type, payload, command_id=cmd.command_id)

    def _publish_committed(self, ev_type, commit_sha, created_commit, state, cmd):
        """Author publish: resolve doc from event type and emit committed.
        v0.5 review-D: result_id propagated as audit correlation."""
        doc = None
        for d in ("story.md", "spec.md", "acceptance.md"):
            if _COMMITTED_EVENT.get(d, (None,))[0] == ev_type:
                doc = d
                break
        if doc is None:
            self._emit(
                "verdict.failed",
                {
                    "check": "publish_error",
                    "reason": f"unknown committed event: {ev_type}",
                    "evidence": ev_type,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        checkpoint_sha = commit_sha or git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit_committed(
            doc, checkpoint_sha, cmd.command_id, result_id=cmd.params.get("result_id")
        )

    def _publish_design_committed(self, commit_sha, state, cmd):
        """M-DESIGN author publish: emit one design.committed per doc (all
        three share the same checkpoint commit_sha), then materialize the
        versioned host contract (IF-HOSTCONTRACT-002 / AC-FR0281-01: Archer's
        M-DESIGN completion is the materialization point the M-VERIFY chain
        consumes). The materialize command is idempotent per contract digest,
        so a RESPOND re-publish never duplicates the record."""
        checkpoint_sha = commit_sha or git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result_id = cmd.params.get("result_id")
        for doc in cmd.params.get("artifacts", []):
            self._emit_committed(doc, checkpoint_sha, cmd.command_id, result_id=result_id)
        self.issue(
            Command(
                kind="materialize_host_contract",
                params={
                    "contract_path": str(
                        self.repo.joinpath(*CANONICAL_CONTRACT_RELPATH)
                    )
                },
            )
        )

    def _publish_test_written(self, commit_sha, state, cmd):
        """M-TEST Shield WRITE publish: emit test.written with the checkpoint
        commit_sha. The reducer transitions WRITE -> COLLECT."""
        self._emit(
            "test.written",
            {"commit_sha": commit_sha, "result_id": cmd.params.get("result_id")},
            command_id=cmd.command_id,
        )

    def _publish_prism_verdict(
        self, verdict, commit_sha, created_commit, result_id, state, cmd, task_id
    ):
        """Prism verdict publish (M-DESIGN and M-TEST). Emits prism.verdict
        with the verdict, diff_ref, and criteria_pack (M-TEST only). For
        non-pass verdicts, also emits review.round_started (M-DESIGN only;
        M-TEST revise goes straight to WRITE via the reducer)."""
        payload = {
            "verdict": verdict,
            "diff_ref": commit_sha if created_commit and commit_sha else None,
            "result_id": result_id,
        }
        if state.stage == "M-TEST":
            domain_payload = cmd.params.get("domain_event", {}).get("payload", {})
            payload["criteria_pack"] = domain_payload.get("criteria_pack")
            defect_classification = domain_payload.get("defect_classification")
            if defect_classification is not None:
                # Never publish defect_classification=null (the reducer would
                # see a present null instead of a missing key).
                payload["defect_classification"] = defect_classification
            # D-35 (SC-D35 §2.3, B23): thread the structured review fields
            # from the result pipeline's domain payload into the published
            # event — without this the verdict event carries no findings and
            # the revise re-dispatch evidence stays empty (live run 01M0AMKV
            # PRISM rounds 1-3, 2026-08-18).
            for key in ("review_summary", "findings", "review_ref", "discussion_refs"):
                val = domain_payload.get(key)
                if val:
                    payload[key] = val
        elif state.stage == "M-DESIGN":
            # D-35 / PRISM-D35-R1-ADV1: M-DESIGN passthrough (AC-FR0240-04) —
            # optional fields ride along when the reviewer supplied them.
            domain_payload = cmd.params.get("domain_event", {}).get("payload", {})
            for key in ("review_summary", "findings", "review_ref", "discussion_refs"):
                val = domain_payload.get(key)
                if val:
                    payload[key] = val
        self._emit("prism.verdict", payload, command_id=cmd.command_id, task_id=task_id)
        if verdict != "pass" and state.stage == "M-DESIGN":
            self._emit(
                "review.round_started",
                {"stage": "M-DESIGN", "round": state.review_round + 1},
                command_id=cmd.command_id,
                task_id=task_id,
            )
