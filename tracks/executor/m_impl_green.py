"""M-IMPL GREEN chain (mixin ``MImplGreenMixin``): validated diffs, green
commit lineage and reuse/review evidence.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tracks.executor.helpers import git
from tracks.executor.m_impl_anchor import (
    _combined_provenance,
    _manifest_path_matches,
    emit_devon_outcome_failure,
)
from tracks.executor.rgr import create_green_commit, red_base_sha, verify_lineage
from tracks.executor.test_select import EvidenceIdentity, TestSelectError, reuse_allowed
from tracks.kernel.machine import State
from tracks.project import load_contract


def _green_diff_checks(
    repo, run_id, task_id, attempt, g_sha, base_sha, events, allowed, trailers, task
) -> dict | None:
    if g_sha is None or base_sha is None:
        return None
    diff_proc = git(Path(repo), "diff", "--name-only", base_sha, g_sha, check=False)
    if diff_proc.returncode == 0:
        outside = sorted(
            path
            for path in diff_proc.stdout.splitlines()
            if path.strip()
            and not any(_manifest_path_matches(path.strip(), rule) for rule in allowed)
        )
        if outside:
            return {
                "check": "scope",
                "reason": "G changes outside manifest allowed paths: " + ", ".join(outside),
                "evidence": json.dumps(outside),
            }
    proof = verify_lineage(
        repo,
        run_id,
        task_id,
        attempt,
        g_sha,
        events,
        issue_number=task.issue_number,
        ac_refs=_combined_provenance(task),
    )
    if not proof.g_trailers_valid:
        return {"check": "lineage", "reason": "G trailers do not match the immutable R lineage"}
    if diff_proc.returncode == 0:
        full = git(Path(repo), "diff", base_sha, g_sha, check=False)
        if full.returncode == 0 and any(
            ln.startswith("+") and not ln.startswith("+++") and _SECRET_PATTERN.search(ln)
            for ln in full.stdout.splitlines()
        ):
            return {"check": "secret", "reason": "G adds a secret-shaped added line"}
    if not (trailers.get("Tracks-AC") or "").strip():
        return {"check": "provenance", "reason": "G has no Tracks-AC provenance trailer"}
    return None


def _task_review_failure(
    repo, run_id, events, task, g_sha, base_sha, trailers, allowed, task_id, attempt
) -> dict | None:
    # B84 (#84): g_sha=None means no-change review (green.no_change) — no new G
    # exists, so scope/lineage/secret/provenance checks are vacuous. Only the
    # budget check below runs.
    failure = _green_diff_checks(
        repo, run_id, task_id, attempt, g_sha, base_sha, events, allowed, trailers, task
    )
    if failure is not None:
        return failure
    # Fix F (run 01KZTHE7 T-013, 2026-08-16): FR-11 `trac retry` resets the
    # attempt budget, so only verdict.failed events recorded after the last
    # human.retry count against the task budget. Pre-retry failures are
    # superseded - this run's history (56 retries, 31 verdict.failed) would
    # otherwise permanently fail-closed every TASK_REVIEW.
    cutoff = max(
        (ev["seq"] for ev in events if ev["type"] == "human.retry"),
        default=0,
    )
    # Per-TASK budget (live defect, run 01M2QTJB T-008 2026-09-20): the count
    # had no task filter, so every verdict.failed in the run -- other tasks'
    # semantic rounds included -- consumed THIS task's budget; once tripped,
    # each budget rejection itself emitted another verdict.failed and the
    # counter snowballed, fail-closing every subsequent task after any busy
    # stretch. B84's intent is the task's own post-retry failures: the event
    # column when the projection carries it, the payload as fallback.
    def _owns(ev: dict) -> bool:
        owner = ev.get("task_id") or (ev.get("payload") or {}).get("task_id")
        return owner == task_id

    if (
        sum(
            ev["type"] == "verdict.failed" and ev["seq"] > cutoff and _owns(ev)
            for ev in events
        )
        > task.budget
    ):
        return {
            "check": "budget",
            "reason": f"verdict.failed count exceeds task budget {task.budget}",
        }
    return None


_SECRET_PATTERN = re.compile(r"(sk-|ghp_|gho_|AKIA)[A-Za-z0-9]{16,}")


class MImplGreenMixin:
    """M-IMPL GREEN chain mixin."""

    def _validated_diff(self, phase: str, state: State) -> tuple[str | None, str | None]:
        reason = self._devon_evidence_error(phase, state)
        outcome = self._last_devon_outcome()
        diff = outcome.get("diff_ref") if outcome else None
        if reason is None and not isinstance(diff, str):
            # Fail-closed fallback: Devon outcomes sometimes omit `diff_ref`.
            # Reconstruct it from the working tree using `changed_paths`.
            # B58 (#74): GREEN must reconstruct from the union of every green
            # outcome of the current RGR cycle -- impl_defect re-dispatches
            # report only their own delta, so the last outcome alone
            # under-captures the cycle's impl diff (run 01M0S0FQ T-001:
            # dispatch 1 changed tracks/project.py, dispatch 3 changed
            # tracks/adapters/base.py, G captured only base.py and the loader
            # impl never reached any commit).
            if phase == "green":
                union = self._green_cycle_changed_paths(state.current_task_id)
                if union:
                    outcome = {**(outcome or {}), "changed_paths": union}
            generated = self._generate_diff_from_changed_paths(outcome)
            if generated is None:
                return f"Devon {phase.upper()} outcome has no captured diff_ref", diff
            diff = generated
        if reason is None and not diff.strip():
            return f"Devon {phase.upper()} captured diff is empty", diff
        return reason, diff

    def _green_cycle_changed_paths(self, task_id: str | None = None) -> list[str] | None:
        """B58 (#74): changed_paths union across every devon GREEN outcome of
        the current RGR cycle (events after the last red.checkpointed; that
        ref bounds the lineage the eventual G binds -- B56). The union only
        widens the ``git diff -- <paths>`` filter of the working-tree
        reconstruction: content still comes from the real tree, so a path a
        later dispatch reverted contributes no diff lines. A sanctioned
        Shield-fix re-baseline (sanction=shield_fix, B91 follow-up) does NOT
        bound the union -- the impl cycle continues through it; only a fresh
        task-starting checkpoint does.

        Prism OOB A02: task_id filtering makes the task boundary explicit
        (task.started/red.checkpointed already bound it implicitly); outcomes
        without a task_id are legacy-shape and stay included."""
        paths: set[str] = set()
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "red.checkpointed" and ev.payload.get("sanction") != "shield_fix":
                break
            if ev.type == "task.started":
                break
            if (
                ev.type == "outcome.received"
                and ev.payload.get("role") == "devon"
                and ev.payload.get("phase") in (None, "green")
                and ev.payload.get("task_id") in (None, task_id)
            ):
                changed = ev.payload.get("changed_paths")
                if isinstance(changed, list):
                    paths.update(p for p in changed if isinstance(p, str) and p)
        return sorted(paths) or None

    def _generate_diff_from_changed_paths(self, outcome: dict | None) -> str | None:
        if not outcome:
            return None
        changed = outcome.get("changed_paths")
        if not isinstance(changed, list) or not changed:
            return None
        existing = [
            str(self.repo / path)
            for path in changed
            if isinstance(path, str) and path and (self.repo / path).exists()
        ]
        if not existing:
            return None
        # `git add -N` registers intent-to-add without staging content, so
        # untracked new files surface in `git diff` as new-file diffs (which
        # is exactly what `diff_ref` should be). Partial failure is fine: the
        # command still succeeds for tracked modified files.
        git(self.repo, "add", "-N", "--", *existing, check=False)
        diff = git(self.repo, "diff", "--", *existing).stdout
        return diff or None

    def _emit_green_no_change(self, cmd, state, task_id, diff, reconcile) -> bool:
        """B38 (#39): if the GREEN evidence is structurally valid but the captured
        worktree diff is empty and Devon declared an explicit no_change_reason,
        emit green.no_change (idempotent on reconcile). Returns True when the
        no-change path was taken (skipping the commit)."""
        outcome = self._last_devon_outcome() or {}
        if (
            diff
            or not outcome.get("no_change_reason")
            or self._devon_evidence_error("green", state) is not None
        ):
            return False
        if reconcile and any(
            e.type == "green.no_change" and e.command_id == cmd.command_id
            for e in self.store.events(self.run_id)
        ):
            self._rebuild_task_log_projection()
            return True
        self._emit(
            "green.no_change",
            {"task_id": task_id, "reason": outcome["no_change_reason"]},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()
        return True

    def _attempt_impl_defect_recorded(self, task_id: str, attempt: int, cutoff: int) -> bool:
        """True when the attempt's LATEST gate word is impl_defect.

        Live defect (run 01M2QTJB T-019, 2026-09-22): a GREEN gate failed
        impl_defect, Devon fixed it inside the same attempt flow, the gate
        RE-RAN AND PASSED -- and commit_green still refused ("Runtime gate
        already failed for this attempt"), sending the run into a
        self-referential DIAGNOSE loop (every diagnosis honestly reported
        "no reproducible failure"; the only failure markers were the guard's
        own echo and prior DIAGNOSE outputs). A verdict.passed(check=green)
        recorded for the task AFTER an impl_defect supersedes it: the gate's
        final word is green (the green verdict carries no attempt field, so
        the supersede is task-scoped and ordered by seq)."""
        superseded = max(
            (
                ev.seq
                for ev in self.store.events(self.run_id)
                if ev.type == "verdict.passed"
                and ev.payload.get("check") == "green"
                and ev.payload.get("task_id") == task_id
            ),
            default=0,
        )
        return any(
            ev.type == "verdict.failed"
            and ev.payload.get("check") == "impl_defect"
            # Exact task match only: the old `in (None, task_id)` amnesty
            # let task-less legacy events (DIAGNOSE verdicts before 2026-08-15
            # carried no task_id) poison every task's commit after an attempt
            # reset - run 01KZTHE7 T-013's green commit was blocked by T-008's
            # stale diagnosis verdict (seq 890) hours later.
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("attempt") == attempt
            # Retry cutoff: pre-retry verdicts for the same attempt number
            # describe a superseded attempt (FR-11 budget reset) and must
            # not block the post-retry fresh attempt.
            and ev.seq > cutoff
            and ev.seq > superseded
            for ev in self.store.events(self.run_id)
        )

    def _green_commit_lineage(self, state, task_id: str, attempt: int) -> tuple:
        """Resolve the G-commit lineage inputs for this attempt.

        Returns ``(failure_payload, task, r_sha, b_sha, green_base)``; when
        ``failure_payload`` is not None the commit is blocked and only the
        payload fields up to the failure are meaningful.
        """
        task = self._lookup_task(task_id)
        if task is None or not state.r_tree_identity:
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green commit lacks task_id or R lineage identity "
                        "(check Devon pre/post identity trailers)"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                },
                None,
                "",
                None,
                None,
            )
        r_sha = self._r_lineage_r_sha(task_id, state.r_tree_identity)
        if not r_sha:
            # B91 (#92): no checkpoint family exists at all -- the original
            # fail-closed block below still fires via the empty-identity
            # path only when r_tree_identity is also empty; a non-empty
            # identity with NO recorded checkpoints is an inconsistent
            # lineage, fail closed the same way.
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green commit R lineage unresolvable: r_tree_identity "
                        "matches no red.checkpointed for the task"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                },
                None,
                "",
                None,
                None,
            )
        # FR-0120 replay-safe base: B is derived from the immutable R commit's
        # parent (never blindly the current HEAD), so a crash after the branch
        # update but before green.committed reconciles to the same G.
        b_sha = red_base_sha(str(self.repo), r_sha)
        if b_sha is None:
            return (
                {
                    "check": "impl_defect",
                    "reason": "green lineage base B unresolvable from R",
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": f"r_sha={r_sha}",
                },
                task,
                r_sha,
                None,
                None,
            )
        green_base = self._green_commit_base(b_sha)
        if green_base is None:
            return (
                {
                    "check": "impl_defect",
                    "reason": (
                        "green lineage violation: branch HEAD diverged from "
                        "base B (not a descendant; unknown work on HEAD)"
                    ),
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": (
                        f"b_sha={b_sha} head={git(self.repo, 'rev-parse', 'HEAD').stdout.strip()}"
                    ),
                },
                task,
                r_sha,
                b_sha,
                None,
            )
        return None, task, r_sha, b_sha, green_base

    def _green_gate_evidence(self, task_id: str) -> dict:
        return next(
            (
                ev.payload
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "verdict.passed"
                and ev.payload.get("check") == "green"
                and ev.payload.get("task_id") == task_id
            ),
            {},
        )

    def _r_lineage_r_sha(self, task_id: str, identity: str | None) -> str | None:
        """B91 (#92): resolve the TRUE R ref sha for the G lineage.

        ``state.r_tree_identity`` carries two semantics: the GREEN regression
        gate's diff baseline and the G commit's R lineage trailer source.
        SM-01.14 (_on_test_committed) deliberately re-points it at the
        runtime-committed Shield fix so sanctioned test fixes do not read as
        drift -- after a test_defect round the identity is the fix COMMIT,
        not an R ref. Binding Tracks-R to it produces a G that no
        verify_lineage can ever validate (run 01M0S0FQ T-013, 2026-08-27:
        Tracks-R=5b72700 shield commit vs slot-4 ref d826d34 -> TASK_REVIEW
        lineage rollback). When the identity matches no recorded checkpoint
        for the task, fall back to the LATEST red.checkpointed r_sha; the
        immutable R family is the only valid lineage anchor."""
        if not identity:
            return None
        latest = None
        for ev in self.store.events(self.run_id):
            if (
                ev.type == "red.checkpointed"
                and ev.payload.get("task_id") == task_id
                and isinstance(ev.payload.get("r_sha"), str)
            ):
                if ev.payload.get("r_sha") == identity:
                    return identity
                latest = ev.payload["r_sha"]
        return latest

    def _r_lineage_attempt(self, task_id: str, r_sha: str | None, attempt: int) -> int:
        """B56 (#72): the G lineage attempt is the R ref slot allocated at
        checkpoint time, not the logical attempt counter.

        B54's first-free-slot walk lets the immutable R ref live at a slot
        HIGHER than the logical attempt (run 01M0S0FQ T-001: logical attempt
        3 checkpointed into slot 5 after the abandoned cycle and the Prism
        red_defect retry occupied 3-4). verify_lineage resolves
        refs/trac/rgr/{run}/{task}/{attempt}/red by the attempt recorded in
        green.committed, so G's Tracks-Attempt must be that slot; a G built
        on the logical attempt binds a slot it does not live in and parks
        TASK_REVIEW lineage. The latest red.checkpointed matching
        (task, r_sha) is authoritative; the logical attempt is only the
        fallback when no checkpoint matches."""
        if not r_sha:
            return attempt
        for ev in reversed(list(self.store.events(self.run_id))):
            if (
                ev.type == "red.checkpointed"
                and ev.payload.get("task_id") == task_id
                and ev.payload.get("r_sha") == r_sha
            ):
                recorded = ev.payload.get("attempt")
                if isinstance(recorded, int):
                    return recorded
        return attempt

    def _do_commit_green(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        cutoff = self._retry_cutoff_seq()
        if self._attempt_impl_defect_recorded(task_id, attempt, cutoff):
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason="Runtime gate already failed for this attempt",
                evidence="prior verdict.failed(impl_defect)",
                task_id=task_id,
                attempt=attempt,
            )
            self._rebuild_task_log_projection()
            return
        # B56 (#72): the green.committed idempotency guard keys on the
        # lineage attempt (the R ref slot) -- the same identity the payload
        # and G trailers record -- so a crash-replay reconciles.
        # #86: legal same-R re-commit (PRISM_FINAL revise or DIAGNOSE routed
        # back to GREEN) clears state.green_committed so the guard below
        # distinguishes "already done, reconcile" (recorded + True) from
        # "revised, re-issue" (recorded + False).  A recorded event with
        # green_committed=False lets the same-R-slot green.committed be
        # re-emitted; TASK_REVIEW reads by task_id (most recent seq) per #84.
        lineage_attempt = self._r_lineage_attempt(
            task_id,
            # B91 (#92): resolve through the checkpoint family so a Shield
            # fix commit re-pointed r_tree_identity (SM-01.14) cannot leak
            # the fix sha into the dedup key; matches the r_sha the G will
            # actually carry.
            self._r_lineage_r_sha(task_id, state.r_tree_identity),
            attempt,
        )
        if (
            self._m_impl_event_recorded("green.committed", task_id, lineage_attempt)
            and state.green_committed
        ):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("green", state)
        if reason is not None:
            if self._emit_green_no_change(cmd, state, task_id, diff, reconcile):
                return
            emit_devon_outcome_failure(self, cmd, task_id, attempt, reason)
            self._rebuild_task_log_projection()
            return
        blocker, task, r_sha, b_sha, green_base = self._green_commit_lineage(
            state, task_id, attempt
        )
        if blocker is not None:
            self._emit(
                "verdict.failed",
                blocker,
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        g = create_green_commit(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=lineage_attempt,
            impl_diff=diff,
            base_sha=green_base,
            r_sha=r_sha,
            issue_number=state.hotfix_issue or task.issue_number,
            ac_refs=_combined_provenance(task),
        )
        materialized, materialize_reason = self._materialize_green_commit(
            g.sha,
            green_base,
        )
        if not materialized:
            self._emit(
                "verdict.failed",
                {
                    "check": "impl_defect",
                    "reason": materialize_reason,
                    "task_id": task_id,
                    "attempt": attempt,
                    "evidence": f"g_sha={g.sha} b_sha={b_sha}",
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            self._rebuild_task_log_projection()
            return
        # green.committed is emitted only after the checked-out release branch
        # and worktree actually contain G (idempotent replay emits no duplicate).
        green_evidence = self._green_gate_evidence(task_id)
        green_payload = {
            "g_sha": g.sha,
            "task_id": task_id,
            "attempt": lineage_attempt,
            "r_sha": r_sha,
            "base_sha": g.parent,
            "trailers": g.trailers,
            "evidence_ids": list(green_evidence.get("evidence_ids") or []),
            "identity_basis": dict(green_evidence.get("identity_basis") or {}),
        }
        if green_evidence.get("snapshot_ref"):
            green_payload["snapshot_ref"] = str(green_evidence["snapshot_ref"])
        self._emit(
            "green.committed",
            green_payload,
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _green_commit_base(self, b_sha: str) -> str | None:
        """Resolve the base for the formal G commit (run 01KZTHE7 T-017).

        Returns the sha G should be parented on:
        - HEAD == B: the fast-forward case, G.parent = B.
        - HEAD is a DESCENDANT of B: legitimate post-B commits landed on the
          branch (the runtime's own SHIELD_FIX result_checkpoint commit, or
          operator runtime-fix commits). G is then re-based onto HEAD — the
          impl diff replays on top; RGR semantics stay intact (R is recorded
          as a trailer, B lineage is preserved through HEAD).
        - otherwise (diverged / unrelated work): None -> fail closed.
        """
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if head == b_sha:
            return b_sha
        # --is-ancestor signals its verdict via exit code (0=yes, 1=no), so
        # check must be off; any other failure also lands on the fail-closed
        # path below.
        anc = git(self.repo, "merge-base", "--is-ancestor", b_sha, "HEAD", check=False)
        if anc.returncode == 0:
            return head
        return None

    def _materialize_green_commit(
        self,
        g_sha: str,
        b_sha: str,
    ) -> tuple[bool, str | None]:
        """Materialize the formal G commit onto the checked-out release branch.

        Safe compare-and-set / fast-forward semantics only — unrelated work is
        never reset or overwritten:

        - ``HEAD == G``: already materialized -> idempotent success (replay
          after a crash between branch update and green.committed).
        - ``HEAD == B``: fast-forward the branch and working tree to G (G's
          parent is exactly B). Uncommitted dirty paths outside G's diff
          survive the fast-forward byte-for-byte (B47).
        - any other ``HEAD``: fail closed with a lineage reason.

        Returns ``(ok, reason)``; ``ok=False`` carries the fail-closed reason.
        """
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if head == g_sha:
            return True, None
        if head != b_sha:
            return False, (
                "green lineage violation: branch HEAD is neither B nor G "
                f"(HEAD={head[:12]}, B={b_sha[:12]}, G={g_sha[:12]})"
            )
        # B47 (run 01M0AMKV r2): the fast-forward reset must not destroy
        # uncommitted runtime-owned artifacts. tasks.json/tasks.md are
        # written by PLANNING and are never part of G, so a bare
        # ``reset --hard`` reverted them to the stale version committed by
        # a previous cycle (the r1 taskgraph resurrected over the in-flight
        # r2 graph). Preserve every dirty path G does not touch.
        changed_by_g = set(git(self.repo, "diff", "--name-only", b_sha, g_sha).stdout.splitlines())
        dirty: set[str] = set()
        for line in git(self.repo, "status", "--porcelain").stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:  # rename entry: keep both ends
                dirty.update(p for p in path.split(" -> ") if p)
            else:
                dirty.add(path)
        preserved: dict[str, bytes] = {}
        for rel in sorted(dirty - changed_by_g):
            p = self.repo / rel
            if p.is_file():
                preserved[rel] = p.read_bytes()
        git(self.repo, "reset", "--hard", g_sha)
        for rel, data in preserved.items():
            (self.repo / rel).write_bytes(data)
        return True, None

    def _validated_green_snapshot(self, green):
        """Load and shape-check the GREEN content identity snapshot blob."""
        snapshot = self._read_runtime_blob(green.payload.get("snapshot_ref"))
        if not isinstance(snapshot, dict):
            raise TestSelectError("GREEN content identity snapshot is malformed")
        rules = snapshot.get("rules")
        previous = snapshot.get("entries")
        templates = snapshot.get("templates")
        if (
            not isinstance(rules, list)
            or not isinstance(previous, dict)
            or not isinstance(templates, dict)
        ):
            raise TestSelectError("GREEN content identity snapshot lacks rules/entries/templates")
        return rules, previous, templates

    def _restore_r_owned_unit_tests(self, current, previous, r_sha: str) -> None:
        for path, identity in previous.items():
            if (
                path.startswith("tests/unit/")
                and current.get(path) == "missing"
                and r_sha
                and self._path_in_tree(r_sha, path)
            ):
                # Formal G intentionally excludes the frozen R commit. The
                # REFACTOR gate injects R separately, so an R-owned unit node
                # absent from main is unchanged, not candidate drift.
                current[path] = identity

    def _green_command_identities(self, basis: dict, templates) -> tuple:
        """Validate the recorded command identity and rebuild it for now.

        Returns (green_command, current_command): the former as recorded on
        the green evidence, the latter extended when contract templates
        drifted since GREEN.
        """
        command = basis.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise TestSelectError("GREEN evidence command identity is malformed")
        contract = load_contract(self.repo)
        sections = {"unit": contract.unit, "integration": contract.integration}
        current_templates = {
            layer: {
                "run_selected": sections[layer].run_selected,
                "cwd": sections[layer].cwd,
            }
            for layer in templates
            if layer in sections
        }
        current_command = tuple(command)
        if current_templates != templates:
            current_command += (
                json.dumps(current_templates, sort_keys=True, separators=(",", ":")),
            )
        return tuple(command), current_command

    def _stale_target_refs(self) -> set[str]:
        return {
            str(target.get("ref"))
            for ev in self.store.events(self.run_id)
            if ev.type == "evidence.staled"
            for target in (ev.payload.get("targets") or [])
            if isinstance(target, dict) and target.get("ref")
        }

    def _green_reuse_state(self, task_id: str, cwd: str):  # pylint: disable=too-many-locals
        """Return (allowed, changed_paths, green_event) from Runtime snapshots."""
        green = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "green.committed" and ev.payload.get("task_id") == task_id
            ),
            None,
        )
        if green is None:
            raise TestSelectError("REFACTOR_GATE lacks green.committed evidence")
        basis = dict(green.payload.get("identity_basis") or {})
        rules, previous, templates = self._validated_green_snapshot(green)
        current = self._task_content_snapshot(Path(cwd), rules)
        r_sha = self.store.state(self.run_id).r_tree_identity or ""
        self._restore_r_owned_unit_tests(current, previous, r_sha)
        current_digest = self._snapshot_digest(current)
        green_command, current_command = self._green_command_identities(basis, templates)
        green_identity = EvidenceIdentity(
            tree=str(basis.get("tree") or ""),
            command=green_command,
            env=str(basis.get("env") or ""),
            selection_id=str(basis.get("selection_id") or ""),
        )
        current_identity = EvidenceIdentity(
            tree=current_digest,
            command=current_command,
            env=self._gate_environment_identity(),
            selection_id=green_identity.selection_id,
        )
        stale_refs = self._stale_target_refs()
        related_refs = {
            green_identity.selection_id,
            *(str(ref) for ref in (green.payload.get("evidence_ids") or []) if ref),
        }
        changed = sorted(
            path for path in set(previous) | set(current) if previous.get(path) != current.get(path)
        )
        return (
            reuse_allowed(green_identity, current_identity, stale_refs & related_refs),
            changed,
            green,
        )

    def _reuse_green_evidence(self, cmd, task_id: str, outcome: dict, green) -> None:
        self._emit(
            "evidence.reused",
            {
                "kind": "green",
                "task_id": task_id,
                "reused_evidence_ids": list(green.payload.get("evidence_ids") or []),
                "identity_basis": dict(green.payload.get("identity_basis") or {}),
                "consumer_gate": "REFACTOR_GATE",
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._emit(
            "refactor.no_change",
            {"task_id": task_id, "reason": outcome.get("no_change_reason")},
            command_id=cmd.command_id,
        )

    def _flag_stale_green_evidence(self, cmd, task_id: str, green, observed_changed) -> None:
        green_basis = dict(green.payload.get("identity_basis") or {})
        self._emit_stale_refs(
            cmd,
            task_id,
            [str(green_basis.get("selection_id") or "")],
            list(green.payload.get("evidence_ids") or []),
            "green_content_identity_changed" if observed_changed else "green_evidence_stale",
        )
