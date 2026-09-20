"""Run-loop, recovery and boundary routing extracted from the Executor
(mixin ``ExecRunLoopMixin``)."""

from __future__ import annotations

import os
import sys
import time
from copy import deepcopy
from pathlib import Path

from tracks.capabilities import base_version, supports_m_impl
from tracks.executor import version_extensions as _version_extensions
from tracks.executor.code_stamp import DRIFT_MESSAGE, RuntimeCodeDriftError, code_stamp
from tracks.executor.doc_face import _CANONICAL_CONTRACT_PATH
from tracks.executor.failure_review import restore_from_events
from tracks.executor.helpers import git
from tracks.executor.host_contract import load_host_contract
from tracks.executor.stall import STALL_COMMAND_LIMIT, CommandStallError, CommandStallTracker
from tracks.executor.validate import parse_test_tasks, resolve_inherited_baseline_docs
from tracks.kernel.contracts import result_file_contract
from tracks.kernel.envelope import (
    ENVELOPE_VERSION,
    build_assignment_envelope,
    envelope_declared_kind,
)
from tracks.kernel.events import Command
from tracks.kernel.m_test import pipeline_incomplete_links
from tracks.kernel.machine import State, decide
from tracks.project import lint_check_command
from tracks.store import new_ulid


class ExecRunLoopMixin:
    """Project -> decide -> issue -> execute loop and its recovery/boundary helpers;
the host Executor keeps construction and the command handlers."""

    def _fail_fast_on_code_drift(self) -> None:
        """B43（#45）：派发前复核 tracks/** 指纹；漂移即 fail-fast。

        r1 实证：运行中进程外的代码修改不热加载，旧逻辑继续派发导致
        误判/escalation。这里宁可停车提示重启，绝不带旧逻辑继续。
        #85：fail-fast 退出前先落 loop.aborted 审计事件——screen 无重定向
        时 stdout 证据会丢，事件流是 append-only 审计流，事后可区分
        abort/crash/kill。store.append 在 CLI 的 writer_lock 内执行，与
        既有 append 点一致；_emit 走统一熔断漏斗，loop.aborted 在 breaker
        是 no-op。
        """
        if self._code_stamp is None:
            return
        stamp = code_stamp(Path(self.repo))
        if stamp != self._code_stamp:
            # M7 (convergence plan 2026-09-05): graceful handover. The
            # loop runs from the installed distribution (bootstrap
            # isolation), so tree drift no longer means this process
            # executes stale
            # logic mid-pipeline -- and this check only runs at dispatch
            # boundaries, so the in-flight dispatch has already fully
            # concluded (129 loop.aborted events and their 55-event
            # evidence.staled cascade in tracks.db were mid-pipeline
            # interruptions of exactly this shape). Record code.drift
            # for the audit trail; the restart watcher rebuilds the
            # installed distribution and the next process takes over
            # from the event
            # store. The run state stays active -- no abort, no staling.
            self._emit(
                "code.drift",
                {
                    "reason": "handover",
                    "stamp_at_start": self._code_stamp,
                    "stamp_now": stamp,
                },
            )
            print(
                "run handover: tracks/** code drift -- dispatch boundary "
                "reached, nothing aborted (M7); the restart watcher "
                "rebuilds and the next process takes over",
                file=sys.stderr,
                flush=True,
            )
            raise RuntimeCodeDriftError(DRIFT_MESSAGE)

    def _emit(
        self, type: str, payload: dict, command_id: str | None = None, task_id: str | None = None
    ):
        ev = self.store.append(
            self.run_id, self.version, type, payload, command_id=command_id, task_id=task_id
        )
        # B44（#46）：统一漏斗——熔断计数与本进程内发出的事件保持同步。
        self._breaker.note(type, payload)
        # B86/B88（#77）：非派发命令紧循环计数（进展事件清零；dispatch_agent
        # 不计入）。loop.aborted 自身也走这里（触发后按重置处理，不影响判定）。
        self._stall.observe(type, payload)
        return ev

    def _check_breaker(self, state: State) -> State | None:
        """B44（#46）：run 级熔断。仅在 active 且 WAL 窗口之外判定；越限
        即发 run.breaker_tripped（machine 置 awaiting_human/escalation，
        损耗报告走 last_failure），本次 run_loop 停车返回。"""
        if state.status != "active":
            return None
        trip = self._breaker.evaluate()
        if trip is None:
            return None
        self._emit("run.breaker_tripped", trip)
        print(f"  [{state.stage}] {trip['report']}", file=sys.stderr, flush=True)
        return self.store.state(self.run_id)

    def run_loop(self) -> State:
        # IF-FAILURE-001 replay face: the failure-review store is process-
        # local; under the M7 handover regime the loop is replaced at every
        # drift boundary, so the stream must be replayed into the selection
        # rule or every post-restart DIAGNOSE runs evidenceless (live
        # 2026-09-06: two consecutive unknown escalations while the round's
        # records sat on the stream).
        restored = restore_from_events(self.run_id, self.store.events(self.run_id))
        if restored:
            print(
                f"failure-review store: {restored} record(s) replayed from the event stream",
                file=sys.stderr,
                flush=True,
            )
        stop, recovered_kind, pending = self._recover_for_run()
        if stop:
            return self.store.state(self.run_id)
        dispatches, bound_substate = self._recovery_dispatch_state(recovered_kind, pending)
        # Bounded verify-chain re-drive (once per `trac run` invocation): an
        # unfinished M-VERIFY chain (a link landed an attention -- dirty_tree
        # at freeze, missing CI binding) has no decide-level command (a
        # decide re-issue would spin against the unresolved attention, each
        # non-dispatch command resetting the stall observer -- live hang:
        # walker tests looping freeze/attention forever). The chain head is
        # idempotent (freeze skips a frozen candidate; every link resumes by
        # its own dedup), so ONE re-drive here resumes exactly the missing
        # links; a still-blocked chain parks and the operator resolves.
        state_after_recover = self.store.state(self.run_id)
        if (
            state_after_recover.stage == "M-VERIFY"
            and state_after_recover.substate == "VERIFYING"
            and not state_after_recover.stage_exited
        ):
            self.issue(
                Command(kind="freeze_candidate", params={"stage": "M-VERIFY"})
            )
        elif (
            state_after_recover.stage == "M-SECURITY"
            and state_after_recover.substate == "ASSESSING"
            and not state_after_recover.stage_exited
        ):
            # Same bounded one-shot resume for the security link: a blocked
            # assessment (attention/repair disposition) parks; a stale
            # assessment for an older candidate re-assesses idempotently.
            self.issue(Command(kind="assess_security", params={}))
        try:
            return self._run_loop_body(dispatches, bound_substate)
        finally:
            # D-36 浅版（#43）：末次派发窗口内的 OOB 提交也要入账——
            # run_loop 可能在一次派发后就返回（phase boundary / gate
            # stop），进程重启后的观察点会重置为新 HEAD，事件就丢了。
            # Prism review #43 B1：异常路径（pending 未关闭的 WAL 窗口，
            # 典型为 backend 抛错）绝不观察——非 command.issued 事件会清
            # pending（B40 挂死条件）；指针不前移，待恢复关闭窗口后的
            # 下一次 loop-top 观察补账。
            if sys.exc_info()[0] is None:
                self._observe_oob()

    def _run_loop_preflight(self) -> tuple[str, State | None]:
        """One loop-top iteration's checks (B43 drift fail-fast, D-36 OOB
        observe, B44 breaker, SM-02 doc-gap lifecycle). Returns
        ``("return", state)`` to stop the loop, ``("continue", None)`` to
        restart the iteration, or ``("proceed", state)`` to fall through to
        ``decide()`` with the freshly-read state."""
        self._fail_fast_on_code_drift()
        self._observe_oob()
        state = self.store.state(self.run_id)
        tripped = self._check_breaker(state)
        if tripped is not None:
            return "return", tripped
        if self._sm02_pre_decide(state):
            return "continue", None
        return "proceed", state

    def _post_issue_checks(self, cmd) -> bool:
        """B86/B88 (#77) stall trip + phase-boundary stop after one issue().
        Returns True when the loop must return at this durable boundary."""
        if self._stall.should_trip():
            self._abort_command_stall()
        return self._is_phase_boundary(cmd)

    def _run_loop_body(self, dispatches: int, bound_substate: str | None) -> State:
        # B86/B88（#77）：本次 run_loop 的紧循环计数从零开始（每次 run_loop
        # 等同进程内一次运行窗口；崩溃/重启清零可接受）。limit 引用模块常量，
        # 便于测试/调参在调用期覆盖。
        self._stall = CommandStallTracker(limit=STALL_COMMAND_LIMIT)
        # r10/SM-01.3: Phase 0 BLOCKED resume preflight -- the kernel parks at
        # BLOCKED (decide_phase0 returns no commands), so the executor effects
        # layer is the only legal IO edge that can issue a fresh
        # phase0_validate after Human repairs the repo facts (AC-FR0257-05
        # repair -> revalidate cycle). This method runs once per run_loop, so
        # the resume fires at most once per drive invocation -- no tick-level
        # hot loop -- and re-validation keeps the full hard checks.
        self._phase0_blocked_resume(self.store.state(self.run_id))
        while True:
            action, state = self._run_loop_preflight()
            if action == "return":
                return state
            if action == "continue":
                continue
            cmd = decide(state)
            if cmd is None:
                if self._park_and_maybe_rearm(state):
                    continue
                return state
            stop, dispatches, bound_substate = self._gate_loop_dispatch(
                cmd, dispatches, bound_substate
            )
            if stop:
                return state
            self._progress(cmd, state)
            self.issue(cmd)
            # B86/B88（#77）：handlers 已在本轮 issue() 内跑完——若期间出现
            # 任何进展事件计数已被清零。连续 N 轮同 kind/同标识的 command.issued
            # 之间零进展事件才是紧循环签名，_post_issue_checks 判定并停车
            # （与 B43 #45 一致：先落 loop.aborted 审计事件再 raise）。
            if self._post_issue_checks(cmd):
                return self.store.state(self.run_id)

    def _park_and_maybe_rearm(self, state) -> bool:
        """Run the park evidence chain at a halted decide() and tell the
        loop whether to keep driving: §1.0.14 B -- the repair disposition
        re-armed the parked walk (an opened round lifted the escalation
        gate) -- ``continue``; the repair budget bounds the loop.

        An ordinary decide() halt on an already-active run was NEVER parked:
        nothing re-arms it, and the loop must exit (the pre-fix form kept
        ``continue``-ing on every active state -- an infinite decide->None
        loop, caught by test_run_loop_rotating_commands_no_trip)."""
        if self._judge_pipeline_incomplete(state):
            return False
        was_parked = state.status == "awaiting_human" or bool(state.awaiting)
        if not was_parked:
            return False
        self._verify_chain_park_evidence(state)
        state = self.store.state(self.run_id)
        return state.status == "active" and not state.awaiting

    def _judge_pipeline_incomplete(self, state) -> bool:
        """AC-FR0285-01: judge an M-TEST decide() halt against the frozen
        chain. A hole before the furthest observed link (WRITE -> COLLECT ->
        red.validated -> prism.verdict) is ``pipeline incomplete``: the run
        stops fail-closed here and an audit ``attention.required`` names the
        missing link, so ``trac status`` reports it (never a silent halt).
        The judgement is idempotent per missing-link detail."""
        if state.stage != "M-TEST":
            return False
        missing = pipeline_incomplete_links(list(self.store.events(self.run_id)))
        if not missing:
            return False
        detail = "missing " + ", ".join(missing)
        already = any(
            event.type == "attention.required"
            and (event.payload or {}).get("area") == "pipeline"
            and (event.payload or {}).get("detail") == detail
            for event in self.store.events(self.run_id)
        )
        if not already:
            self._emit(
                "attention.required",
                {
                    "area": "pipeline",
                    "reason": "pipeline_incomplete",
                    "stage": "M-TEST",
                    "detail": detail,
                    "next": (
                        "restore the WRITE -> COLLECT -> red.validated -> "
                        "prism.verdict chain before continuing"
                    ),
                },
            )
        return True

    def _abort_command_stall(self) -> None:
        """B86/B88（#77）：非派发命令紧循环停车。

        先 append loop.aborted（append-only 审计流，screen 无重定向时
        stdout 证据会丢，同 B85/#85 模式），再打印处置指引 banner，最后
        raise CommandStallError——由 CLI 捕获转为非零退出码。
        """
        detail = self._stall.summary()
        self._emit(
            "loop.aborted",
            {"reason": "command_stall", "detail": detail},
        )
        print(
            f"command stall detected: {detail}\n"
            "This is a runtime defect's tight-loop signature (a non-dispatch "
            "command re-issued with zero progress events between iterations). "
            "Kill this process and open an issue carrying the loop.aborted "
            "evidence above.",
            file=sys.stderr,
            flush=True,
        )
        raise CommandStallError(detail)

    def _gate_loop_dispatch(
        self, cmd: Command, dispatches: int, bound_substate: str | None
    ) -> tuple[bool, int, str | None]:
        """Bounded-dispatch gate for the run loop (max_dispatches budget)."""
        if cmd.kind != "dispatch_agent" or self.max_dispatches is None:
            return False, dispatches, bound_substate
        stop, bound_substate = self._dispatch_gate(cmd, dispatches, bound_substate)
        if stop:
            return True, dispatches, bound_substate
        return False, dispatches + 1, bound_substate

    def _recover_for_run(self) -> tuple[bool, str | None, dict | None]:
        """(phase_boundary_stop, recovered_kind, pending) after D-13 recovery."""
        pending = self.store.state(self.run_id).pending
        recovered_kind = self._recover()
        if recovered_kind == "rollback_stage":
            recovered = Command(
                kind=recovered_kind,
                params=pending.get("params", {}),
                command_id=pending.get("command_id"),
            )
            return self._is_phase_boundary(recovered), recovered_kind, pending
        return False, recovered_kind, pending

    def _contract_guard_commands(self) -> list[str]:
        """(attempt-4 language neutrality) IF-HOSTCONTRACT-001: guard
        commands are constructed from the host contract, never hardcoded.

        A quality gate with an inline command renders verbatim from the
        contract; a source=guard_registry quality gate (and a missing or
        malformed [host-contract] table) resolves through the single-truth
        registry loader (lint_check_command, None -> skip): gates skip,
        never guess a host toolchain invocation.
        """
        commands: list[str] = []
        try:
            contract = load_host_contract(self.repo.joinpath(*_CANONICAL_CONTRACT_PATH))
        except (OSError, ValueError):
            contract = None
        for gate in contract.local_gates if contract else ():
            if gate.kind == "quality" and gate.command:
                commands.append(gate.command)
        lint_cmd = lint_check_command(self.repo)
        if lint_cmd and lint_cmd not in commands:
            commands.append(lint_cmd)
        return commands

    def _enrich_shield_write_params(self, params: dict, state: State) -> None:
        """D-28: enrich Shield WRITE assignments with structured test_tasks
        parsed from test-plan §8 and the pre-dirty content snapshot.

        B28/#30 slim (PRISM-B28-R1-01): also materialize ``commands.guard``
        (ruff + the project contract's [lint] check) so the writer-manifest
        contract's self-check references a real command for M-TEST WRITE and
        M-IMPL SHIELD_FIX alike — commands were previously only materialized
        on the M-IMPL Devon path."""
        if not (
            params.get("role") == "shield"
            and params.get("substate") == "WRITE"
            and state.stage in ("M-TEST", "M-IMPL")
        ):
            return
        assignment = dict(params.get("assignment") or {})
        if not assignment.get("test_tasks"):
            vdir = self._vdir()
            # IF-HOTFIX-010: hotfix version dirs carry no acceptance.md
            # (FR-0241-02); resolve the inherited baseline doc read-only.
            acc_path, _ = resolve_inherited_baseline_docs(vdir / "test-plan.md")
            assignment["test_tasks"] = parse_test_tasks(
                acc_path, vdir / "test-plan.md"
            )
        if not assignment.get("commands"):
            assignment["commands"] = {"guard": self._contract_guard_commands()}
        if state.stage == "M-IMPL":
            # B64 (#82): deterministic ownership wall — Shield's audit domain
            # excludes every dispatched task's red_test_paths (Archer manifest
            # data, no hardcoding). A Shield write inside Devon's RED unit
            # tests is over-reach and rolls back regardless of what the
            # diagnosis text names; attribution must route red_defect ->
            # Devon RED re-pin instead.
            red_scope = self._red_test_scope()
            if red_scope:
                assignment["forbidden_paths"] = red_scope
        params["assignment"] = assignment
        params["pre_dirty"] = sorted(self._dirty_files())
        params["pre_dirty_snapshot"] = self._dirty_snapshot()

    def _recovery_dispatch_state(self, recovered_kind, pending):
        """Compute (dispatches, bound_substate) after recovery."""
        dispatches = 1 if recovered_kind == "dispatch_agent" else 0
        bound_substate = None
        if recovered_kind == "dispatch_agent" and self.max_dispatches is not None:
            bound_substate = (pending or {}).get("params", {}).get("substate")
        return dispatches, bound_substate

    @staticmethod
    def _is_phase_boundary(cmd: Command) -> bool:
        """True when issuing ``cmd`` must end the current ``run_loop``
        invocation (a durable stop boundary).

        - ``rollback_stage`` with reason ``stub_gap``: after an M-TEST/M-IMPL
          -> M-DESIGN stub_gap rollback the same run would immediately
          re-dispatch Archer against the identical invalid test-task contract
          (auto-reenter the defective downstream cycle), so we return and
          require a later explicit ``trac run``.
        - ``complete_hotfix_entry`` (v0.6 ANCHORED terminal, SM-01.10): the
          entry drive stops once stage.entered(M-DESIGN) lands — the canonical
          M-DESIGN delta work continues on a later ``trac run`` (FR-0242).

        Every other rollback reason (scope_overflow, human_return,
        diagnose_rollback) needs no external correction, so run_loop continues
        in the same invocation to dispatch the upstream agent with evidence."""
        return cmd.kind == "rollback_stage" and cmd.params.get("reason") == "stub_gap" or (
            cmd.kind == "complete_hotfix_entry"
        )

    def run_pipeline(self) -> State:
        """Drive only the ResultCheckpoint pipeline (validate->checkpoint->
        publish). No agent dispatch, no recovery, no cross-substate flow.
        Returns when active_result is cleared (published or failed)."""
        while True:
            state = self.store.state(self.run_id)
            if state.active_result is None:
                return state
            cmd = decide(state)
            if cmd is None:
                return state
            self._progress(cmd, state)
            self.issue(cmd)

    def _progress(self, cmd: Command, state: State) -> None:
        """Concise non-agent progress to stderr (validate/commit/seal). Agent
        dispatch progress is emitted in ``_do_dispatch_agent`` around
        ``backend.act()`` so both normal and recovered paths share it."""
        if cmd.kind == "validate_document":
            doc = cmd.params.get("doc", "?")
            print(f"  [{state.stage}] validate {doc}", file=sys.stderr, flush=True)
        elif cmd.kind == "commit_document":
            doc = cmd.params.get("doc", "?")
            print(f"  [{state.stage}] commit {doc}", file=sys.stderr, flush=True)
        elif cmd.kind == "write_frontmatter":
            stage = cmd.params.get("stage", state.stage or "?")
            print(f"  [{state.stage}] seal frontmatter ({stage})", file=sys.stderr, flush=True)
        elif cmd.kind == "validate_result":
            source = cmd.params.get("source", "?")
            print(f"  [{state.stage}] validate result ({source})", file=sys.stderr, flush=True)
        elif cmd.kind == "checkpoint_result":
            source = cmd.params.get("source", "?")
            print(f"  [{state.stage}] checkpoint ({source})", file=sys.stderr, flush=True)
        elif cmd.kind == "publish_result":
            ev = cmd.params.get("domain_event", {}).get("type", "?")
            print(f"  [{state.stage}] publish {ev}", file=sys.stderr, flush=True)
        elif cmd.kind == "register_known_issue":
            print(f"  [{state.stage}] register known issue", file=sys.stderr, flush=True)
        elif cmd.kind == "assess_security":
            print(f"  [{state.stage}] assess security", file=sys.stderr, flush=True)
        elif cmd.kind == "generate_preview":
            print(f"  [{state.stage}] generate preview", file=sys.stderr, flush=True)
        elif cmd.kind == "execute_publish":
            print(f"  [{state.stage}] execute publish", file=sys.stderr, flush=True)
        elif cmd.kind == "close_milestone":
            print(f"  [{state.stage}] close milestone", file=sys.stderr, flush=True)
        elif cmd.kind == "materialize_host_contract":
            print(
                f"  [{state.stage}] materialize host contract",
                file=sys.stderr,
                flush=True,
            )

    def _dispatch_gate(
        self, cmd, dispatches: int, bound_substate: str | None
    ) -> tuple[bool, str | None]:
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

    def _enrich_dispatch_params(
        self, params: dict, state: State, cid: str
    ) -> None:
        """Apply assignment overlay, M-IMPL materialization, hotfix, and
        Shield WRITE enrichment to dispatch params (issue() pre-WAL).

        Doc-gap nested dispatches (doc_gap marker) skip all enrichment so
        the nested Archer/Prism agents don't get M-IMPL/hotfix/Shield
        assignment injection."""
        if self.assignment_overlay is not None:
            assignment = dict(params.get("assignment") or {})
            assignment["scenario_context"] = deepcopy(self.assignment_overlay)
            params["assignment"] = assignment
        if state.stage == "M-IMPL":
            params["assignment"] = self._materialize_m_impl_assignment(
                state, params, cid,
            )
        if state.hotfix_issue is not None:
            # The no-op hotfix path returns ``params`` itself. Copy before
            # replacing in place or clear() would erase the source mapping.
            materialized = dict(self._materialize_hotfix_assignment(state, params))
            params.clear()
            params.update(materialized)
        self._enrich_shield_write_params(params, state)
        self._enrich_m_test_prism_assignment(params, state)
        self._enrich_envelope_params(params, cid)

    def _enrich_envelope_params(self, params: dict, cid: str) -> None:
        """(E) IF-ENVELOPE-001 injection face (operator OOB 2026-09-06,
        user-authorized): declared dispatches carry the authoritative reply
        envelope — kind + inline payload schema + schema digest — built by
        kernel.envelope from the single registry, so the agent .md contract
        ("schema 以 assignment 注入为权威") finally has an injector. Before
        this the assignment declared nothing while the module was a stub,
        and reviewers learned the payload shape from rejection errors after
        burning a full reasoning hop. Runs LAST so overlay/hotfix/shield
        enrichment (which may replace params["assignment"]) cannot wipe it.
        TRAC_ENVELOPE_DECLARE=0 reverts to undeclared (legacy) dispatches.

        #172/#174 wiring (OOB 2026-09-20): every declared dispatch also
        carries a result_file block — the agent's machine-serialized reply
        channel (json.dump to the inbox file, validate-reply before
        replying). The path is ABSOLUTE, anchored at the main repo: writer
        dispatches run inside an isolated worktree, and a repo-relative
        path would die with the worktree at replay cleanup. cid (the
        per-dispatch command id) names the file — unique per dispatch, so a
        reused session can only ever write its own dispatch's result.
        TRAC_RESULT_FILE=0 reverts to fence-only delivery.
        """
        if os.environ.get("TRAC_ENVELOPE_DECLARE", "").strip() == "0":
            return
        kind = envelope_declared_kind(params.get("role"), params.get("substate"))
        if kind is None:
            return
        assignment = dict(params.get("assignment") or {})
        task = assignment.get("task") if isinstance(assignment.get("task"), dict) else None
        assignment["envelope"] = build_assignment_envelope(kind, task)
        assignment["envelope_version"] = ENVELOPE_VERSION
        if os.environ.get("TRAC_RESULT_FILE", "").strip() != "0":
            inbox = self.repo / ".tracks" / "runtime" / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            result_path = str((inbox / f"{cid}.json").resolve())
            assignment["result_file"] = result_file_contract(result_path, kind)
        params["assignment"] = assignment

    def _enrich_m_test_prism_assignment(self, params: dict, state: State) -> None:
        """D-41 AC-FR0250-03 (v3 timing): PRISM_REVIEW consumes the Runtime's
        CURRENT-TREE red evidence. The assignment exposes the latest
        ``red.validated`` identity (selection binding + per-node legal-Red
        outcomes) as the approved factual basis for the review; Prism runs
        only its isolated counterexample kill on top -- never a suite rerun."""
        if not (state.stage == "M-TEST" and params.get("substate") == "PRISM_REVIEW"):
            return
        selection = self._latest_event("test.selected")
        red = self._latest_event("red.validated")
        if red is None:
            return
        assignment = dict(params.get("assignment") or {})
        assignment["red_evidence"] = {
            "status": red.payload.get("status"),
            "selection_id": red.payload.get("selection_id")
            or (selection.payload if selection else {}).get("selection_id"),
            "scope": (selection.payload if selection else {}).get("scope"),
            "baseline": (selection.payload if selection else {}).get("baseline"),
            "commit": (selection.payload if selection else {}).get("commit"),
            "nodes_count": (selection.payload if selection else {}).get("nodes_count"),
            "nodes_blob": red.payload.get("nodes_blob"),
            "outcomes_ref": red.payload.get("outcomes_ref"),
            # M5 card diet (2026-09-18, run 01M2QTJB): per-node detail
            # (traceback text) is zero-reader history riding the card --
            # the red.validated event keeps the full record; the card
            # carries only the classification summary the review reads.
            "findings": [
                {k: v for k, v in f.items() if k != "detail"}
                for f in (red.payload.get("findings") or [])
                if isinstance(f, dict)
            ],
        }
        params["assignment"] = assignment

    def _infra_backoff_delay(self, streak: int) -> int:
        """Exponential infra backoff: 30s base, x2 per streak, capped.

        OOB 2026-09-20 (user decision: the model channel never changes; a
        quota-dead gateway waits under high load): the cap is
        max(300, TRAC_INFRA_BACKOFF_MAX_SECONDS, default 900s=15min) so
        sustained 429 storms back off to quarter-hour spacing instead of
        hammering every 5 minutes. Streak-at-limit behavior is unchanged
        (the kernel escalates; see machine_outcomes._handle_infra_failure).
        """
        try:
            configured = int(os.environ.get("TRAC_INFRA_BACKOFF_MAX_SECONDS", "").strip() or 900)
        except ValueError:
            configured = 900
        cap = max(300, configured)
        return min(30 * 2 ** (max(streak, 1) - 1), cap)

    def issue(self, cmd: Command, command_id: str | None = None) -> None:
        """Write-ahead log `cmd` (FR-30), then execute it; the per-kind handler
        logs the result event that closes it. command_id is assigned here so the
        result pairs with the issued record; an explicit ``command_id``
        (doc-gap resume, SM-02.9) pre-binds the recorded identity. Used by
        run_loop and by one-shot setup commands (create_branch in `trac start`)."""
        state = self.store.state(self.run_id)
        if cmd.kind == "dispatch_agent" and state.infra_failure_streak > 0:
            # Infra-failure backoff (kernel never sleeps): consecutive
            # infra failures are re-dispatched with exponential backoff so a
            # degraded gateway is not stormed with full prompts (run 01KZTHE7
            # T-017, 2026-08-16: SIGKILL -> immediate retry -> SIGKILL).
            delay = self._infra_backoff_delay(state.infra_failure_streak)
            print(
                f"  [{state.stage}] infra failure streak "
                f"{state.infra_failure_streak}: backoff {delay}s before re-dispatch",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
        cid = command_id or new_ulid()
        params = dict(cmd.params)
        _is_doc_gap = bool(params.get("doc_gap"))
        if cmd.kind == "dispatch_agent" and not _is_doc_gap:
            self._enrich_dispatch_params(params, state, cid)
        if cmd.kind == "close_milestone" and not params.get("tracker"):
            # FR-0284: the kernel's close_milestone carries no tracker (pure
            # decider); the executor resolves the host declaration into the
            # issued command so the WAL and the closer see one target.
            tracker = self._close_milestone_tracker(state)
            if tracker:
                params["tracker"] = tracker
        issued = Command(kind=cmd.kind, params=params, command_id=cid)
        task_id = None
        if cmd.kind == "dispatch_agent":
            task_id = (
                state.current_task_id
                if state.stage == "M-IMPL" and state.current_task_id
                else f"{self.run_id}:{cmd.params.get('substate')}"
                f":{state.review_round}:{state.current_attempt}"
            )
        self._emit(
            "command.issued",
            {"command": {"kind": issued.kind, "params": issued.params, "command_id": cid}},
            command_id=cid,
            task_id=task_id,
        )
        self._execute(issued, self.store.state(self.run_id), task_id)

    def _recover(self) -> str | None:
        """Hanging command (issued, no result): reconcile first (D-13), reissue
        the same assignment without consuming an attempt (D-11). Returns the
        reconciled command's kind (or None when no pending command exists) so
        callers can react to phase-boundary commands like rollback_stage."""
        state = self.store.state(self.run_id)
        if state.pending:
            cmd = Command(
                kind=state.pending["kind"],
                params=state.pending.get("params", {}),
                command_id=state.pending.get("command_id"),
            )
            self._execute(cmd, state, None, reconcile=True)
            return cmd.kind
        return None

    def _execute(
        self, cmd: Command, state: State, task_id: str | None, reconcile: bool = False
    ) -> None:
        getattr(self, "_do_" + cmd.kind)(cmd, state, task_id, reconcile)

    def _is_boundary_transition(self, stage: str, nxt: str) -> bool:
        """True when the declared stage transition must stop at a boundary at
        execution time instead of entering ``nxt``.

        ``_NEXT_STAGE`` stays declarative (single source of truth); the v0.5
        feature gate lives here, at transition execution. ``M-TEST ->
        M-IMPL`` applies only to runs on v0.5+ (``supports_m_impl`` from the
        neutral ``tracks.capabilities``); historical v0.1/v0.4 runs and
        malformed versions complete at the M-TEST boundary.
        """
        return stage == "M-TEST" and nxt == "M-IMPL" and not supports_m_impl(self.version)

    def _release_boundary_route(self, state):
        """M-IMPL boundary guard (architecture §1.1 / IF-VERIFY-001): a
        RELEASE-capable run re-routes at the boundary through the version
        capability seam — ``after_m_impl`` returns the M-VERIFY chain-head
        command (Command(freeze_candidate)); below-threshold versions select
        no extension and get ``None`` back, keeping their boundary
        completion. The gate runs only here at execution time, keeping
        ``_NEXT_STAGE`` declarative (single source of truth).

        A hotfix run inherits its TARGET version's capability extension
        (FR-0277 post-release/dev journeys; ``capabilities.base_version``
        strips the ``-hotfix-{issue}`` identity), so a v0.8 hotfix re-routes
        into the release pipeline while sub-threshold hotfixes keep the v0.6
        boundary completion byte-identical."""
        callback = _version_extensions.resolve_capability(
            base_version(self.version or ""), "after_m_impl"
        )
        return callback(state) if callback is not None else None

    def _do_record_backlog(self, cmd, state, task_id, reconcile):
        if reconcile and state.backlog_recorded:
            return
        self._emit("backlog.recorded", dict(cmd.params), command_id=cmd.command_id)

    def _do_complete_run(self, cmd, state, task_id, reconcile):
        # Branch deletion is a separate delete_branch command (FR-09), not here.
        self._emit(
            "run.completed",
            {"terminal_state": cmd.params["terminal_state"]},
            command_id=cmd.command_id,
        )

    def _do_create_branch(self, cmd, state, task_id, reconcile):
        """Create/switch the branch, then log branch.created. Reconcile (R3-03):
        the git work is skipped iff the branch exists AND HEAD is already on it;
        branch.created is always logged so the command closes."""
        branch = cmd.params["branch_name"]
        base = cmd.params.get("base", "main")
        exists = git(self.repo, "rev-parse", "--verify", branch, check=False).returncode == 0
        if not (exists and self._head() == branch):
            if exists:
                git(self.repo, "checkout", branch)
            else:
                git(self.repo, "checkout", "-b", branch, base)
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self._emit(
            "branch.created",
            {"branch_name": branch, "base": base, "commit_sha": commit_sha},
            command_id=cmd.command_id,
        )

    def _do_delete_branch(self, cmd, state, task_id, reconcile):
        """Tear down the branch, then log branch.deleted. Reconcile (R3-03):
        _teardown_branch is idempotent (done iff HEAD==main AND branch absent)."""
        branch = cmd.params["branch_name"]
        self._teardown_branch(branch)
        self._emit("branch.deleted", {"branch_name": branch}, command_id=cmd.command_id)

    def _next_event_seq(self) -> int:
        """Seq the next appended event of this run will carry (the store
        assigns MAX(seq)+1); 1 for an empty log."""
        return max(
            (getattr(e, "seq", 0) for e in self.store.events(self.run_id)),
            default=0,
        ) + 1

    def _do_recover_stage(self, cmd, state, task_id, reconcile):
        # B32 (#32): forward recovery. Reconcile idempotency mirrors
        # _do_rollback_stage: if stage.recovered was already persisted for this
        # command (crash between event commit and return), do not re-enter.
        if reconcile:
            already = any(
                e.type == "stage.recovered" and e.command_id == cmd.command_id
                for e in self.store.events(self.run_id)
            )
            if already:
                return
        # Fail-closed gate (defense in depth; the CLI/decide path already
        # constrains these, so a violation means a malformed command or an
        # out-of-band event log). v0.6 whitelists ONLY M-IMPL as a forward
        # recovery target -- every other target must go through the normal
        # human.return/rollback path instead.
        if state.substate != "RECOVER_PENDING":
            self.store.write_audit_blob(
                {
                    "event": "stage.recovered",
                    "command_id": cmd.command_id,
                    "rejected": True,
                    "reason": (
                        f"recover_stage issued in substate {state.substate} "
                        "(expected RECOVER_PENDING)"
                    ),
                }
            )
            return
        if cmd.params.get("to_stage") != "M-IMPL":
            self.store.write_audit_blob(
                {
                    "event": "stage.recovered",
                    "command_id": cmd.command_id,
                    "rejected": True,
                    "reason": (
                        f"recover_stage target {cmd.params.get('to_stage')} "
                        "not in v0.6 whitelist (M-IMPL)"
                    ),
                }
            )
            return
        self._emit(
            "stage.recovered",
            {
                "stage": "M-IMPL",
                "from_stage": state.stage,
                "reason": cmd.params.get("reason", ""),
            },
            command_id=cmd.command_id,
        )

    def _latest_event(self, ev_type: str, *, status: str | None = None):
        """Latest event of ``ev_type`` (optionally with payload.status), or None."""
        found = None
        for ev in self.store.events(self.run_id):
            if ev.type != ev_type:
                continue
            if status is not None and ev.payload.get("status") != status:
                continue
            found = ev
        return found

    def _head(self) -> str:
        return git(self.repo, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()

    def _teardown_branch(self, branch: str) -> None:
        """FR-09 / reconcile-safe: end with HEAD==main and branch absent."""
        if self._head() != "main":
            git(self.repo, "checkout", "main")
        if git(self.repo, "rev-parse", "--verify", branch, check=False).returncode == 0:
            git(self.repo, "branch", "-D", branch)
