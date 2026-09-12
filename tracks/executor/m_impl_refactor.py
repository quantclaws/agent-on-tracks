"""M-IMPL RGR phase dispatch (mixin ``MImplDispatchMixin``): task gates,

task review, selected-layer execution and the REFACTOR gate.



Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size

compliance (C0302); code moved verbatim.

"""



from __future__ import annotations

from tracks.executor.helpers import git
from tracks.executor.m_impl_anchor import (
    _is_evidence_shape_error,
    _manifest_path_matches,
)
from tracks.executor.m_impl_dispatch import TaskSelectionFailure
from tracks.executor.test_select import (
    TestResultError,
    TestSelectError,
)
from tracks.executor.worktree import cleanup_worktree
from tracks.project import ContractError


class MImplRefactorMixin:
    def _do_run_refactor_gate(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        gate_handle = None
        try:
            reason = self._devon_evidence_error("refactor", state)
            if reason is not None:
                self._emit_refactor_evidence_error(cmd, reason, state)
                return
            cwd, gate_handle = self._ensure_gate_worktree(state, phase="refactor")
            outcome = self._last_devon_outcome() or {}
            task_id = state.current_task_id or task_id or ""
            reusable, observed_changed, green = self._green_reuse_state(task_id, str(self.repo))
            if reusable:
                self._reuse_green_evidence(cmd, task_id, outcome, green)
                return
            self._flag_stale_green_evidence(cmd, task_id, green, observed_changed)
            task = self._lookup_task(task_id)
            if task is None:
                raise TestSelectError(f"REFACTOR_GATE task not found: {task_id}")
            if self._refactor_diff_missing(
                outcome, observed_changed
            ) and not self._refactor_diff_reconstructable(outcome, observed_changed):
                raise TestSelectError("refactor content identity changed without a captured diff")
            selection_evidence = self._execute_task_selection(cmd, state, cwd, task)
            changed = sorted(set(outcome.get("changed_paths", [])) | set(observed_changed))
            allowed = set((state.current_manifest or {}).get("allowed_paths", []))
            outside = self._refactor_outside_paths(changed, allowed)
            if outside:
                self._emit(
                    "verdict.failed",
                    {
                        "check": "scope",
                        "reason": "refactor changed forbidden paths: " + ", ".join(outside),
                        "evidence": str(outside),
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                )
                return
            if not changed:
                self._emit(
                    "refactor.no_change",
                    {
                        "task_id": task_id,
                        "reason": outcome.get("no_change_reason"),
                        **selection_evidence,
                    },
                    command_id=cmd.command_id,
                )
                return
            committed = self._commit_refactor_changes(
                cmd, state, task_id, changed, selection_evidence
            )
            if not committed:
                return
        except TaskSelectionFailure as exc:
            self._emit_gate_failure(
                cmd,
                check="regression",
                reason=str(exc),
                evidence=exc.evidence,
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"REFACTOR SELECT_TASK failed closed: {type(exc).__name__}: {exc}",
                evidence="task_if refactor selection/identity",
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)
            self._rebuild_task_log_projection()

    @staticmethod
    def _refactor_diff_missing(outcome: dict, observed_changed) -> bool:
        diff_ref = outcome.get("diff_ref")
        return bool(observed_changed) and (
            not isinstance(diff_ref, str) or not diff_ref.strip() or diff_ref.strip() == "no-change"
        )

    def _refactor_diff_reconstructable(self, outcome: dict, observed_changed) -> bool:
        """B55 (#71): the OpenCode backend never captures diff_ref for Devon
        RGR worktree phases (the artifact is changed_paths + pre/post
        identities; GREEN outcomes carry diff_ref=None too). RED/GREEN
        already reconstruct the diff from the working tree
        (_validated_diff's fallback); REFACTOR must accept the same
        reconstruction -- the refactor changes sit uncommitted in the main
        tree at gate time -- instead of parking every real refactor with a
        contract_error."""
        union = {
            "changed_paths": sorted(
                set(outcome.get("changed_paths") or []) | set(observed_changed or [])
            )
        }
        return self._generate_diff_from_changed_paths(union) is not None

    @staticmethod
    def _refactor_outside_paths(changed, allowed) -> list[str]:
        return sorted(
            path
            for path in changed
            if not any(_manifest_path_matches(path, rule) for rule in allowed)
        )

    def _commit_refactor_changes(
        self, cmd, state, task_id: str, changed, selection_evidence
    ) -> bool:
        git(self.repo, "add", "--", *changed)
        # Lazy import: executor.py imports this module's mixin at module
        # scope, so the scoped commit helper resolves at call time.
        from tracks.executor.executor import _scoped_commit_if_staged

        proc = _scoped_commit_if_staged(
            self.repo,
            f"M-IMPL: refactor\n\ncommand_id: {cmd.command_id}",
            paths=sorted(changed),
        )
        if proc is None:
            self._emit(
                "verdict.failed",
                {
                    "check": "regression",
                    "reason": "refactor evidence has no captured diff",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return False
        if proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return False
        self._emit(
            "refactor.committed",
            {"task_id": task_id, **selection_evidence},
            command_id=cmd.command_id,
        )
        return True

    def _emit_refactor_evidence_error(self, cmd, reason: str, state) -> None:
        if _is_evidence_shape_error(reason):
            self._emit(
                "verdict.failed",
                {
                    "check": "evidence_malformed",
                    "failure_class": "evidence_malformed",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
        else:
            self._emit(
                "verdict.failed",
                {
                    "check": "regression",
                    "reason": reason,
                    "evidence": "backend Devon outcome",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
