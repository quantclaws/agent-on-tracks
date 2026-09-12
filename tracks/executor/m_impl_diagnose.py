"""M-IMPL RED diagnosis (mixin ``MImplDiagnoseMixin``): classification and
attribution helpers plus the runtime RED checkpoint and anchor/walk RED
paths.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tracks.executor.helpers import git
from tracks.executor.m_impl_anchor import _combined_provenance
from tracks.executor.quality_gate import failed_summary_lines
from tracks.executor.rgr import adopt_red_ref, create_red_ref
from tracks.executor.test_select import TestSelectError, resolve_selected_command
from tracks.project import ContractError, load_contract


def _red_classification_label(value: object) -> str:
    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    return value or "missing"


def _red_inference_from_command(command: object) -> list[str]:
    """Classifications inferable from one command entry (token first,
    then keyword inference on fail-only natural-language summaries)."""
    if not isinstance(command, dict):
        return []
    summary = command.get("output_summary")
    if not isinstance(summary, str):
        return []
    tokens = _RED_CLASSIFY_PATTERN.findall(summary)
    if tokens:
        return tokens
    if command.get("result") != "fail":
        return []
    if _RED_ASSEMBLY_ERROR_PATTERN.search(summary):
        return []
    if _RED_SYMBOL_PATTERN.search(summary):
        return ["symbol_missing"]
    if _RED_ASSERTION_PATTERN.search(summary):
        return ["assertion_failure"]
    return []


def _m_impl_red_classifications(outcome: dict) -> tuple[list[str], bool]:
    results = outcome.get("results")
    if isinstance(results, list) and results:
        classifications = [
            _red_classification_label(
                result.get("classification") if isinstance(result, dict) else None
            )
            for result in results
        ]
        return classifications, True
    # Fail-closed fallback: Devon RED outcomes sometimes omit `results`.
    # Infer classifications from `commands[*].output_summary`, which always
    # carries a `classify_red -> <legal|illegal>` token. Only the two legal
    # classifications are matched - stub/illegal tokens never infer.
    commands = outcome.get("commands")
    if not isinstance(commands, list):
        return ["missing"], False
    inferred: list[str] = []
    for command in commands:
        inferred.extend(_red_inference_from_command(command))
    if not inferred:
        return ["missing"], False
    return inferred, True


def _m_impl_red_classification_error(outcome: dict) -> tuple[str, str] | None:
    """Return deterministic failure evidence for an invalid M-IMPL Red."""
    classifications, has_results = _m_impl_red_classifications(outcome)
    verdict_present = "verdict" in outcome
    verdict = _red_classification_label(outcome.get("verdict")) if verdict_present else "missing"
    evidence = json.dumps(
        {"classifications": sorted(classifications), "verdict": verdict},
        sort_keys=True,
        separators=(",", ":"),
    )
    if not has_results:
        return "M-IMPL RED classifications are missing", evidence
    illegal = sorted(set(classifications) - _M_IMPL_RED_CLASSIFICATIONS)
    if illegal:
        return "M-IMPL RED contains illegal classification: " + ",".join(illegal), evidence
    if len(set(classifications)) != 1:
        return "M-IMPL RED classifications are mixed", evidence
    if verdict_present and verdict not in _M_IMPL_RED_CLASSIFICATIONS:
        return "M-IMPL RED contains an illegal verdict: " + verdict, evidence
    if verdict_present and verdict != classifications[0]:
        return "M-IMPL RED verdict does not match classification", evidence
    return None


_M_IMPL_RED_CLASSIFICATIONS = frozenset({"assertion_failure", "symbol_missing"})


_RED_CLASSIFY_PATTERN = re.compile(r"classify_red\s*->\s*(assertion_failure|symbol_missing)")


# Natural-language RED summaries (T-016 attempt 2, 2026-08-16): Devon
# describes failing assertions without the classify_red token. Infer the
# two legal classifications from failure keywords; only `result: "fail"`
# commands are considered (passing guards are not RED evidence). Keep it
# conservative: assembly errors (collection/import/fixture) never infer.
_RED_ASSERTION_PATTERN = re.compile(
    r"\bassert\b|AssertionError|assertion failure|assertion_failure", re.IGNORECASE
)


_RED_SYMBOL_PATTERN = re.compile(
    r"ModuleNotFoundError|ImportError|NameError|AttributeError|"
    r"symbol_missing|cannot import|has no attribute",
)


_RED_ASSEMBLY_ERROR_PATTERN = re.compile(
    r"collection error|ERROR collecting|FixtureLookupError|"
    r"SyntaxError|ModuleNotFoundError|ImportError",
    re.IGNORECASE,
)


class MImplDiagnoseMixin:
    """M-IMPL RED diagnosis mixin."""

    def _retry_cutoff_seq(self) -> int:
        # FR-11: `trac retry` resets the attempt budget, so a fresh attempt
        # number N after a retry is a *different* attempt from an attempt N
        # recorded before it. Guards that scan prior verdict.failed /
        # committed events must only consider events strictly after the last
        # human.retry, otherwise the stale pre-retry verdict (same task,
        # same attempt number) false-positives the fresh attempt forever
        # (run 01KZTHE7 T-013: seq 937/942 blocked post-retry attempt 1).
        cutoff = 0
        for ev in self.store.events(self.run_id):
            if ev.type == "human.retry":
                cutoff = ev.seq
        return cutoff

    def _m_impl_event_recorded(
        self,
        event_type: str,
        task_id: str,
        attempt: int,
        min_seq: int | None = None,
    ) -> bool:
        cutoff = self._retry_cutoff_seq()
        if min_seq is not None:
            # B90 (#91): an additional task-scoped staleness floor on top of
            # the retry cutoff (see _last_task_outcome_seq).
            cutoff = max(cutoff, min_seq)
        return any(
            ev.type == event_type
            and ev.seq > cutoff
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("attempt") == attempt
            for ev in self.store.events(self.run_id)
        )

    def _last_task_outcome_seq(self, task_id: str) -> int:
        """B90 (#91): seq of the task's latest dispatch outcome (Devon or
        Prism), 0 when none. A red.checkpointed recorded BEFORE the current
        round's outcome belongs to an earlier review round -- it cannot
        satisfy the live RED_CHECKPOINT wait, so it must not feed the
        idempotency guard. Operator finding (2026-08-27, run 01M0S0FQ
        T-013): the guard keyed on (task_id, attempt) alone livelocked
        after a Prism revise round because _red_ref_free_attempt (B54/#70)
        had inflated the earlier checkpoint's recorded attempt past the
        machine's counter (orphan pre-replan ref held slot 2, so the event
        logged attempt=3 while current_attempt+1 later equalled 3 again);
        the guard silently returned, decide() re-issued checkpoint_red 20x,
        and only the B86/B88 stall breaker stopped the loop. Mirrors the
        #86 state-aware discriminator already present in commit_green's
        guard (recorded + green_committed)."""
        seq = 0
        for ev in self.store.events(self.run_id):
            if ev.type == "outcome.received" and ev.task_id == task_id:
                seq = max(seq, ev.seq)
        return seq

    def _do_checkpoint_red(self, cmd, state, task_id, reconcile):
        task_id = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        attempt = state.current_attempt + 1
        if self._m_impl_event_recorded(
            "red.checkpointed",
            task_id,
            attempt,
            # B90 (#91): only an event recorded after this round's dispatch
            # outcome is a true duplicate (crash-replay double-fire); an
            # earlier-round checkpoint is stale and must be re-issued on a
            # fresh ref slot.
            min_seq=self._last_task_outcome_seq(task_id),
        ):
            self._rebuild_task_log_projection()
            return
        reason, diff = self._validated_diff("red", state)
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check="red_invalid",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
            self._rebuild_task_log_projection()
            return
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        attempt = self._red_ref_free_attempt(task_id, attempt)
        r = create_red_ref(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=attempt,
            test_diff=diff,
            base_sha=base_sha,
        )
        self._emit(
            "red.checkpointed",
            self._red_checkpoint_payload(task_id, attempt, r),
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _red_ref_free_attempt(self, task_id: str, attempt: int) -> int:
        """Allocate the first free immutable-ref attempt slot >= attempt.

        Operator finding (2026-08-24, run 01M0S0FQ): a rollback through
        M-DESIGN re-approval abandons an M-IMPL cycle whose RGR refs
        (refs/trac/rgr/{run}/{task}/{N}/red) outlive the cycle -- the fresh
        residency restarts task attempts at 1 and would collide with the
        orphaned ref (create_red_ref raises; the checkpoint crashed).

        B54 (#70): ANY taken slot is skipped -- orphan (abandoned residency)
        or live (checkpointed earlier in THIS residency). The old walk raised
        on a live slot, crashing the framework's own same-residency
        re-checkpoint flows: a Prism red_defect retry re-runs Devon and
        re-checkpoints the same task (run 01M0S0FQ T-001: slot 4 live ->
        second checkpoint crashed trac run), and a human.retry round moves
        the idempotency guard's cutoff past the prior checkpoint the same
        way. R immutability demands a fresh slot for a fresh checkpoint;
        the exact-duplicate double-fire (no retry in between) is already
        caught by the guard in _do_checkpoint_red, so the raise protected
        no real scenario. The event records the allocated attempt, keeping
        history and refs consistent."""
        slot = attempt
        for _ in range(50):
            ref = f"refs/trac/rgr/{self.run_id}/{task_id}/{slot}/red"
            if git(self.repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode != 0:
                return slot
            slot += 1
        raise TestSelectError(
            f"no free R ref attempt slot for task {task_id} within 50 of {attempt}"
        )

    def _red_checkpoint_payload(self, task_id: str, attempt: int, r) -> dict:
        """``red.checkpointed`` payload (interfaces §4a, AC-FR0245-01): the
        RGR trailer block rides the R checkpoint alongside the green commit
        trailers, so the hotfix issue provenance is auditable on fix/{issue}.
        The checkpoint itself stays valid when the task identity cannot be
        resolved; only the trailer block is then omitted (audit enrichment,
        never a new fail path for R creation)."""
        payload = {"ref": r.ref, "r_sha": r.sha, "task_id": task_id, "attempt": attempt}
        task = self._lookup_task(task_id)
        issue_number = self.store.state(self.run_id).hotfix_issue if task is not None else None
        issue_number = issue_number or (task.issue_number if task is not None else None)
        if task is not None and issue_number:
            payload["trailers"] = {
                "Tracks-Task": task_id,
                "Tracks-Attempt": str(attempt),
                "Tracks-R": r.sha,
                "Tracks-Issue": str(issue_number),
                "Tracks-AC": ",".join(_combined_provenance(task)),
            }
        return payload

    def _rebaseline_red_family(self, task_id: str, commit_sha: str, command_id: str) -> None:
        """B91 follow-up (re-baseline): freeze a sanctioned mid-M-IMPL Shield
        test-fix commit as a NEW immutable R slot in the task's red.checkpointed
        family (2026-08-27, run 01M0S0FQ T-013 post-mortem).

        ``test_defect`` rounds are the system's designed channel for catching
        M-TEST-stage test defects during Devon's cycle (four-way DIAGNOSE ->
        Shield SHIELD_FIX -> ``test.committed``, SM-01.14 re-points the
        regression baseline). Before this, the fix commit never entered the R
        family, so the G lineage anchor and the regression baseline diverged:
        pre-B91 the G bound the fix commit (never provable); post-B91 the G
        bound the ORIGINAL slot -- honest only while the fix leaves the frozen
        test bodies untouched, an over-certification the moment it edits one.
        Adopting the fix commit as a fresh slot (``red.checkpointed`` with
        ``sanction: shield_fix``) reunifies both semantics: the trailer names
        exactly the frozen tree that gated the G, and B91's exact-match
        resolution picks the new slot up automatically. The kernel projection
        treats a sanctioned checkpoint as a pure re-anchor: it must NOT
        re-enter the RED review substate mid-cycle.

        No-op (fail-closed by omission) when the task has no checkpointed RED
        at all: a family that never opened is not ours to open from a fix
        commit; B91's no-checkpoint lineage guard still governs.
        """
        has_family = any(
            ev.type == "red.checkpointed" and ev.payload.get("task_id") == task_id
            for ev in self.store.events(self.run_id)
        )
        if not has_family:
            return
        # Idempotent replay: the fix commit already sits in the family (a
        # crashed/retried shield round must not fork duplicate slots).
        already_adopted = any(
            ev.type == "red.checkpointed"
            and ev.payload.get("task_id") == task_id
            and ev.payload.get("r_sha") == commit_sha
            for ev in self.store.events(self.run_id)
        )
        if already_adopted:
            return
        attempt = self._red_ref_free_attempt(task_id, 1)
        r = adopt_red_ref(
            repo=str(self.repo),
            run_id=self.run_id,
            task_id=task_id,
            attempt=attempt,
            sha=commit_sha,
        )
        payload = self._red_checkpoint_payload(task_id, attempt, r)
        payload["sanction"] = "shield_fix"
        self._emit(
            "red.checkpointed",
            payload,
            command_id=command_id,
            task_id=task_id,
        )
        self._rebuild_task_log_projection()

    def _selected_test_argv(self, test_refs: list, command_id: str, layer: str):
        """Contract ``run_selected`` argv for engine-executed test refs (T-015).

        The executed argv is the verbatim host-contract expansion (the
        anchor_probe/test_select ``load_contract`` pattern): the engine
        injects neither a runner module nor flags. Returns
        ``(argv, result_path, cwd)``; raises ``ContractError`` /
        ``TestSelectError`` which callers route to their fail-closed
        channels. The staged result file is consumed evidence -- callers
        unlink it after the run (FA-4)."""
        contract = load_contract(Path(self.repo))
        section = getattr(contract, layer)
        cwd = Path(self.repo) if section.cwd == "." else Path(self.repo) / section.cwd
        result_path = self._result_staging_path(command_id, f"{layer}_selected")
        argv = list(
            resolve_selected_command(section.run_selected, list(test_refs), str(result_path), cwd)
        )
        return argv, result_path, cwd

    def _run_anchor_suite(self, cmd_argv, run_cwd, result_path) -> tuple[int, str, str]:
        """Run one engine-selected anchor suite; always consume the staging result."""
        try:
            proc = subprocess.run(
                cmd_argv, cwd=run_cwd, capture_output=True, text=True, timeout=1800
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            return 124, str(exc), "anchor run timed out after 1800s"
        finally:
            result_path.unlink(missing_ok=True)

    def _pin_r_ref(self, tid: str, attempt: int) -> tuple[str, str]:
        """Pin the R ref straight to HEAD; return (ref, base_sha)."""
        base_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        ref = f"refs/trac/rgr/{self.run_id}/{tid}/{attempt}/red"
        git(self.repo, "update-ref", ref, base_sha)
        return ref, base_sha

    def _fail_anchor_contract(self, cmd, tid: str, attempt: int, layer: str, exc) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "contract_error",
                "reason": (
                    f"anchor RED run needs the host contract [{layer}].run_selected: {exc}"
                ),
                "task_id": tid,
                "attempt": attempt,
            },
            command_id=cmd.command_id,
            task_id=tid,
        )

    def _do_anchor_red(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        """Runtime-executed RED anchor confirmation for preset-anchor tasks.

        The frozen failing tests ARE the red anchor (no new unit test to
        write): run them expecting red, then pin the R ref straight to the
        base sha - the base tree already contains the anchors - and emit
        red.checkpointed so the normal PRISM_RED -> GREEN flow continues
        (run 01KZTHE7 T-013, 2026-08-15: a Devon RED dispatch here can only
        produce an empty changed_paths evidence the gate rejects).
        """
        tid = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        meta = state.current_task_metadata or {}
        if meta.get("integration"):
            self._do_walk_red(cmd, state, tid, meta)
            return
        test_refs = meta.get("test_refs") or []
        attempt = state.current_attempt + 1
        if not isinstance(test_refs, list) or not test_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": "preset-anchor task declares no test_refs",
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                test_refs, cmd.command_id or "", "integration"
            )
        except (ContractError, TestSelectError) as exc:
            self._fail_anchor_contract(cmd, tid, attempt, "integration", exc)
            return
        rc, out, err = self._run_anchor_suite(cmd_argv, run_cwd, result_path)
        summary = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0:
            # Anchors already green: either the implementation already
            # exists (task-graph drift) or an anchor broke - a human must
            # decide which.
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": (
                        "preset RED anchors unexpectedly pass - anchors must "
                        f"be red before GREEN. {summary}"
                    ),
                    "evidence": out[-2000:],
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._rebuild_task_log_projection()
            return
        ref, base_sha = self._pin_r_ref(tid, attempt)
        ref_payload = {
            "failed": failed_summary_lines(out),
            "tail": out[-400:],
        }
        blob = self.store.write_audit_blob(
            {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
        )
        if blob:
            ref_payload["log_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit(
            "red.checkpointed",
            {
                "ref": ref,
                "r_sha": base_sha,
                "task_id": tid,
                "attempt": attempt,
                "anchor": True,
                "evidence": json.dumps(ref_payload, ensure_ascii=False),
            },
            command_id=cmd.command_id,
            task_id=tid,
        )
        self._rebuild_task_log_projection()

    def _do_walk_red(self, cmd, state, tid: str, meta: dict) -> None:
        """Runtime-executed RED for integration tasks (walk the R-tree).

        An integration task carries no new RED unit test to write; its
        unit pins are already delivered. Seal the unit R-tree on site:
        run the unit_refs expecting green, then pin the R ref straight
        to the base sha and emit red.checkpointed so the normal
        PRISM_RED -> GREEN flow continues with a resolvable
        r_tree_identity (run 01M19FJVES7G113RD8QXXY3PQZ T-042:
        GREEN start with no RED checkpoint left GREEN structurally
        unpassable - Devon assignment without r_tree_identity plus
        green commit lineage without R ref).
        """
        attempt = state.current_attempt + 1
        unit_refs = [r for r in (meta.get("unit_refs") or []) if isinstance(r, str)]
        if not unit_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": "walk_red integration task declares no unit_refs",
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                unit_refs, cmd.command_id or "", "unit"
            )
        except (ContractError, TestSelectError) as exc:
            self._fail_anchor_contract(cmd, tid, attempt, "unit", exc)
            return
        rc, out, err = self._run_anchor_suite(cmd_argv, run_cwd, result_path)
        if rc != 0:
            summary = out.strip().splitlines()[-1] if out.strip() else ""
            self._emit(
                "verdict.failed",
                {
                    "check": "red_invalid",
                    "reason": (
                        "walk_red unit pins are not green - regression "
                        f"baseline cannot be sealed. {summary}"
                    ),
                    "evidence": out[-2000:],
                    "task_id": tid,
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._rebuild_task_log_projection()
            return
        ref, base_sha = self._pin_r_ref(tid, attempt)
        ref_payload: dict = {"passed": True, "tail": out[-400:], "walk_red": True}
        blob = self.store.write_audit_blob(
            {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
        )
        if blob:
            ref_payload["log_ref"] = f".tracks/runtime/blobs/{blob}"
        self._emit(
            "red.checkpointed",
            {
                "ref": ref,
                "r_sha": base_sha,
                "task_id": tid,
                "attempt": attempt,
                "anchor": True,
                "walk_red": True,
                "evidence": json.dumps(ref_payload, ensure_ascii=False),
            },
            command_id=cmd.command_id,
            task_id=tid,
        )
        self._rebuild_task_log_projection()

    def _do_verify_task(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        """Runtime-executed acceptance for verification-only tasks (§1.0.3).

        User ruling 2026-08-15: acceptance is Runtime work, not agent work. A
        verification-only task has frozen test assets and no
        RED-implementation, so the RGR chain (evidence diff -> red ref ->
        green) structurally cannot apply - run 01KZTHE7 T-001 burned three
        Devon attempts at "RED evidence has no changed paths" before this
        path. The Runtime runs the task's declared test_refs directly; a full
        pass completes the task, a failure re-enters the gate-failure budget
        (3 attempts -> escalation for a human, matching flaky-retry then
        stop semantics).
        """
        tid = state.current_task_id or cmd.params.get("task_id") or task_id or ""
        meta = state.current_task_metadata or {}
        test_refs = meta.get("test_refs") or []
        if not isinstance(test_refs, list) or not test_refs:
            self._emit(
                "verdict.failed",
                {
                    "check": "verification_failed",
                    "reason": "verification-only task declares no test_refs",
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        try:
            cmd_argv, result_path, run_cwd = self._selected_test_argv(
                test_refs, cmd.command_id or "", "integration"
            )
        except (ContractError, TestSelectError) as exc:
            self._emit(
                "verdict.failed",
                {
                    "check": "contract_error",
                    "reason": (
                        "verification run needs the host contract "
                        f"[integration].run_selected: {exc}"
                    ),
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            return
        err = ""
        try:
            proc = subprocess.run(
                cmd_argv,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            rc, out, err = 124, str(exc), "verification timed out after 1800s"
        finally:
            result_path.unlink(missing_ok=True)
        summary = out.strip().splitlines()[-1] if out.strip() else ""
        if rc == 0:
            self._emit(
                "task.completed",
                {
                    "task_id": tid,
                    "verification": True,
                    "commands": [
                        {
                            "cmd": " ".join(cmd_argv),
                            "result": "pass",
                            "output_summary": summary,
                        }
                    ],
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
            self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
        else:
            # Failure evidence for the fixer relay (user directive
            # 2026-08-15): fixers cannot re-run these suites, so hand them
            # the whole picture - FAILED summary lines, the full-output blob
            # path (--tb=long stacks included), and a short tail inline.
            ref = self.store.write_audit_blob(
                {"cmd": cmd_argv, "rc": rc, "stdout": out, "stderr": err}
            )
            evidence_payload = {
                "failed": failed_summary_lines(out),
                "tail": out[-400:],
            }
            if ref:
                evidence_payload["log_ref"] = f".tracks/runtime/blobs/{ref}"
            self._emit(
                "verdict.failed",
                {
                    "check": "verification_failed",
                    "reason": f"verification test_refs failed rc={rc}: {summary}",
                    "evidence": json.dumps(evidence_payload, ensure_ascii=False),
                    "task_id": tid,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=tid,
            )
        self._rebuild_task_log_projection()
