"""Universal escape/rollback command handlers extracted from the Executor
(mixin ``ExecEscapeMixin``)."""

from __future__ import annotations

import fnmatch

from tracks.executor.escape import quarantine_late_outcome
from tracks.executor.helpers import git
from tracks.kernel.machine import canonical_stage_order


class ExecEscapeMixin:
    """Rollback/late-outcome escape handlers; pure barrier helpers stay in escape.py."""

    def _release_empty_hotfix_shield(
        self, cmd, state, task_id, role, substate, assignment
    ) -> bool:
        """Declare a unit-only hotfix M-TEST increment without Shield I/O."""
        if not (
            state.stage == "M-TEST"
            and state.hotfix_issue is not None
            and role == "shield"
            and substate == "WRITE"
            and isinstance(assignment, dict)
            and not assignment.get("test_tasks")
        ):
            return False
        from tracks.executor.test_tasks import parse_hotfix_unit_rows

        plan = self._vdir() / "test-plan.md"
        unit_rows = parse_hotfix_unit_rows(
            plan.read_text(encoding="utf-8", errors="replace") if plan.exists() else ""
        )
        anchors = set(state.hotfix_anchor_acs or [])
        if (
            not unit_rows
            or {row.get("ac") for row in unit_rows} != anchors
            or not self._valid_hotfix_unit_rows(unit_rows)
        ):
            return False
        self._emit(
            "red.validated",
            {"status": "valid", "findings": [], "basis": "unit-only hotfix increment"},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _rollback_write_scope(self) -> list[str]:
        """B59 (#75): write-scope patterns of every dispatched task in this
        run (manifest allowed_paths + red_test_paths). The union bounds what
        the discarded M-IMPL cycles could legitimately have touched in the
        MAIN tree — dirty files inside it at rollback are residue of the
        discarded work."""
        patterns: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type != "task.started":
                continue
            manifest = ev.payload.get("manifest")
            if not isinstance(manifest, dict):
                continue
            for key in ("allowed_paths", "red_test_paths"):
                value = manifest.get(key)
                if isinstance(value, list):
                    patterns.update(
                        p for p in value if isinstance(p, str) and p.strip()
                    )
        return sorted(patterns)

    def _red_test_scope(self) -> list[str]:
        """B64 (#82): red_test_paths of every dispatched task in this run —
        Archer-authored manifest data (language-neutral; nothing hardcoded).
        These are Devon's RED unit artifacts: only Devon may rewrite them
        (red_defect -> RED re-pin). Shield's SHIELD_FIX write domain excludes
        them deterministically — the ownership wall is data, not prompt
        discipline (user ruling 2026-08-25; run 01M0S0FQ T-002: a test_defect
        misroute sent Shield into Devon's RED unit test)."""
        patterns: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type != "task.started":
                continue
            manifest = ev.payload.get("manifest")
            if not isinstance(manifest, dict):
                continue
            value = manifest.get("red_test_paths")
            if isinstance(value, list):
                patterns.update(
                    p for p in value if isinstance(p, str) and p.strip()
                )
        return sorted(patterns)

    @staticmethod
    def _path_in_write_scope(path: str, patterns: list[str]) -> bool:
        """Exact file, directory-prefix, or glob match against scope patterns
        (manifest patterns mix all three: ``tracks/project.py``,
        ``tests/unit``, ``.tracks/projects/**``)."""
        for pat in patterns:
            if path == pat:
                return True
            if path.startswith(pat.rstrip("/") + "/"):
                return True
            if fnmatch.fnmatch(path, pat):
                return True
        return False

    def _quarantine_rollback_residue(self) -> list[str]:
        """B59 (#75): stash main-tree residue intersecting the discarded
        stage's write scope. Run 01M0S0FQ: the previous T-001 cycle's
        implementation residue (tracks/project.py) survived every rollback
        and retry --clear-evidence in the main tree, then leaked into the new
        baseline via the M-TEST freeze and into Devon's environment checks.
        Untracked files count as residue too (Prism OOB R1 Blocker 01): the
        designed flow commits agent output during the pipeline, so anything
        dirty in scope at rollback is the discarded cycle's leftover.
        Stash (never destroy): the content stays recoverable and the
        discarded-scope tree returns to the committed baseline state.
        Best-effort: git failures leave the tree untouched and are reported
        by simply not listing the file as quarantined."""
        status = git(self.repo, "status", "--porcelain", check=False)
        dirty: list[str] = []
        for line in status.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename: keep both ends in scope checks
                path = path.split(" -> ")[-1]
            if path:
                dirty.append(path)
        scope = self._rollback_write_scope()
        matched = [p for p in dirty if self._path_in_write_scope(p, scope)]
        if not matched:
            return []
        proc = git(
            self.repo,
            "stash",
            "push",
            "-u",
            "-m",
            f"trac B59 rollback residue quarantine ({self.run_id})",
            "--",
            *matched,
            check=False,
        )
        return matched if proc.returncode == 0 else []

    def _do_rollback_stage(self, cmd, state, task_id, reconcile):
        # Reconcile idempotency: if stage.rolled_back was already persisted for
        # this command (crash between event commit and return), do not emit a
        # duplicate. Under normal recovery the pending command means the event
        # has not been logged yet, so this guard only fires on the edge case.
        if reconcile:
            already = any(
                e.type == "stage.rolled_back" and e.command_id == cmd.command_id
                for e in self.store.events(self.run_id)
            )
            if already:
                return
        # B57 re-fix defense in depth (review 2026-08-27): the CLI validates a
        # closed target set and the kernel adopts the approval payload's
        # to_stage -- but a hand-crafted out-of-band human.approval could
        # smuggle an arbitrary stage in. Mirror _do_recover_stage: reject
        # out-of-set targets with an audit blob, no event, no state change.
        # T-001 face (B) / IF-RELEASE-003: the closed set is derived from the
        # canonical stage order (machine table + the five release stages), so
        # the impl-cycle targets M-TEST/M-IMPL and the release stages are
        # legal return destinations; M-REQ-APPROVAL is never a target.
        allowed_targets = canonical_stage_order()
        if cmd.params.get("to_stage") not in allowed_targets:
            self.store.write_audit_blob(
                {
                    "event": "stage.rolled_back",
                    "command_id": cmd.command_id,
                    "rejected": True,
                    "reason": (
                        "rollback_stage target not in canonical closed set "
                        f"({'|'.join(allowed_targets)}): "
                        f"{cmd.params.get('to_stage')!r}"
                    ),
                }
            )
            return
        # B59 (#75): discarding M-IMPL progress must also discard its main-tree
        # residue, or the "discarded" work leaks into the next cycle's
        # environment (baseline freeze + agent verification).
        quarantined = (
            self._quarantine_rollback_residue() if state.stage == "M-IMPL" else []
        )
        self._emit(
            "stage.rolled_back",
            {
                "from_stage": state.stage,
                "to_stage": cmd.params["to_stage"],
                "reason": cmd.params.get("reason", ""),
                "quarantined": quarantined,
            },
            command_id=cmd.command_id,
        )

    def _consume_escape_cutover(self, outcome: dict) -> bool:
        """T-001 face (B) / AC-FR0287-02: dispatch-loop cutover consumption.

        With an established escape barrier (escape.barrier_established), an
        outcome whose originating seq <= cutover_seq was dispatched before
        the barrier: quarantine it (escape.late_outcome, status=quarantined;
        never checkpointed, never published, never overwriting State) and
        return True so the caller drops the outcome. Outcomes originating
        after the barrier (and barrier-less runs) pass through untouched.
        """
        barrier = None
        for ev in self.store.events(self.run_id):
            if ev.type == "escape.barrier_established":
                barrier = ev.payload
        if not barrier:
            return False
        seq = outcome.get("seq")
        if seq is None:
            # Resolve the originating dispatch's seq from its command.issued
            # event (the loop guard passes only the dispatch id).
            dispatch_id = outcome.get("dispatch_id") or ""
            for ev in self.store.events(self.run_id):
                if ev.type == "command.issued" and ev.command_id == dispatch_id:
                    seq = ev.seq
                    break
            outcome = {**outcome, "seq": seq}
        verdict = quarantine_late_outcome(barrier, outcome)
        if not verdict.get("quarantined"):
            return False
        self._emit(
            "escape.late_outcome",
            {
                "dispatch_id": verdict.get("dispatch_id", ""),
                "status": "quarantined",
                "outcome_seq": seq,
                "cutover_seq": verdict.get("cutover_seq"),
            },
        )
        return True

    def _assert_agent_forbidden(self) -> bool:
        """(G) True when the selected backend is an Agent backend — the
        publish execution path must run on non-Agent infrastructure only."""
        backend_kind = type(self.backend).__name__.lower()
        return "opencode" in backend_kind or "agent" in backend_kind
