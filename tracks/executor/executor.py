"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""

from __future__ import annotations

import subprocess  # noqa: F401  (test seam: subprocess.run monkeypatching)
import sys
from copy import deepcopy
from pathlib import Path

from tracks.effects import oob, select_backend

# T-015 v0.7 runtime assembly (architecture §1.0.9/§1.1): the Executor is the
# composition root. Importing this module re-registers the v0.7 extension
# delivered by ``v07_runtime`` (T-013) with the Phase 0 entry-guard
# capability added on top -- the T-013 file itself is consumed by import
# only and never modified. Re-registering the same version replaces its
# binding idempotently (version_extensions contract), so the trace /
# island_gate_2 / render_closure capabilities are inherited unchanged and
# kernel.machine's generic seam call point can resolve ``before_mtest``.
from tracks.executor import v07_runtime as _v07_runtime  # noqa: E402
from tracks.executor import version_extensions as _version_extensions  # noqa: E402
from tracks.executor.breaker import RunBreaker
from tracks.executor.code_stamp import code_stamp
from tracks.executor.dispatch import (
    ExecDispatchMixin,
    _assignment_budget,  # noqa: F401
    parse_agent_output,  # noqa: F401
    record_failure,  # noqa: F401
    review_failure_chain,  # noqa: F401
    select_failure,  # noqa: F401
    static_parity_check,  # noqa: F401
)
from tracks.executor.doc_face import (
    _NEXT_STAGE,  # noqa: F401
    ExecDocMixin,
)
from tracks.executor.doc_gap_face import ExecDocGapMixin
from tracks.executor.doc_gap_runtime import (
    DocGapCapabilities,
    DocGapRuntime,
)
from tracks.executor.escape_face import ExecEscapeMixin
from tracks.executor.helpers import (  # noqa: F401  (m_impl_runtime lazy-import seam)
    _scoped_commit_if_staged,
    git,
)
from tracks.executor.hotfix_face import (
    ExecHotfixMixin,
    _hotfix_state_line,
    _resolve_run_version,
    hotfix_entry_output,  # noqa: F401
    hotfix_feature_route,  # noqa: F401
    precheck_hotfix_report,
)
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.milestone_chain import ExecMilestoneMixin
from tracks.executor.observe import ExecObserveMixin
from tracks.executor.phase0_face import ExecPhase0Mixin
from tracks.executor.publish_face import ExecPublishMixin
from tracks.executor.release_tail import ExecReleaseTailMixin
from tracks.executor.result_checkpoint import ResultCheckpointMixin
from tracks.executor.run_loop import ExecRunLoopMixin
from tracks.executor.stall import CommandStallTracker
from tracks.executor.test_collect import (
    _R2_BASIS,  # noqa: F401
    _R2_SCOPE,  # noqa: F401
    ExecTestCollectMixin,
    _resolve_contract_argv0,  # noqa: F401
)
from tracks.executor.test_execute import ExecTestRunMixin
from tracks.executor.verdict_face import ExecVerdictMixin
from tracks.executor.verify_ci import ExecVerifyCiMixin
from tracks.executor.verify_gates import (
    _DEFAULT_HOST_CONTRACT_TOML,  # noqa: F401
    ExecVerifyGatesMixin,
)
from tracks.executor.verify_park import ExecVerifyParkMixin
from tracks.executor.verify_version import ExecVerifyVersionMixin
from tracks.executor.worktree_face import ExecWorktreeMixin

# Compatibility re-exports: the dispatch mixin owns the behavior bodies, but
# executor.py remains the composition surface that external code and the
# T-001 face (E) wiring pin import these envelope/failure-chain seam names
# from (architecture §1.1).
from tracks.project import layout_paths as _layout_paths
from tracks.store import Store, new_ulid


class _V07RuntimeExtension(_v07_runtime.V07Extension):
    """v0.7 extension assembled with the Phase 0 entry guard (T-015).

    ``before_mtest`` delegates to the T-004 ``kernel.phase0.decide_phase0``
    entry guard (pure, no I/O): any non-SEALED Phase 0 projection routes to
    ``Command(phase0_validate)``; SEALED/BLOCKED park (§1c).

    ``after_m_impl`` is declared explicitly as the below-threshold no-route:
    v0.7 predates the RELEASE pipeline, so the M-IMPL boundary completes the
    run instead of re-routing to M-VERIFY (FR-0267 keeps below-threshold
    versions byte-identical). Without the explicit callback the required
    capability resolution fail-closes v0.7 runs at M-IMPL/EXIT with
    CapabilityBlockedError — a stall, not the contracted boundary completion.
    """

    @staticmethod
    def before_mtest(state):
        from tracks.kernel.phase0 import decide_phase0

        return decide_phase0(state)

    @staticmethod
    def after_m_impl(state):
        return None


_version_extensions.register_extension(
    _v07_runtime.EXTENSION_VERSION, _V07RuntimeExtension()
)


def island_gate_2_dispatch(version, arguments=(), **kwargs):
    """ISLAND_GATE_2 capability call point (IF-FAILCLOSED-001).

    Resolves the target version's ``island_gate_2`` callback on the generic
    capability seam (architecture 1.0.9 / interfaces 1b kind 4) and invokes
    it with *arguments* plus keywords -- through the registered v0.7
    extension (T-013) this lazily dispatches ``demonstrate_failclosed``
    (T-016).  Early versions select no callback and return None, keeping
    classic behaviour byte-identical (FR-0264-02); a registered extension
    that lacks the callback fails closed via ``CapabilityBlockedError``.
    """
    callback = _version_extensions.resolve_capability(version or "", "island_gate_2")
    if callback is None:
        return None
    return callback(arguments, **kwargs)


def hotfix_entry_run(repo, store, issue_number: int, scenario: str) -> str:
    """v0.6 hotfix entry (IF-HOTFIX-001, interfaces §2a #1/#2): establish the
    hotfix run with its resolved identity version (``{target}-hotfix-{issue}``,
    ARCH-006 §1.0.3) and drive the synchronous triage run_loop. A REJECTED
    precheck still establishes the run for audit (SM-01.1/.4) — the loop
    completes it immediately. Returns the run_id."""
    report, _issue = precheck_hotfix_report(repo, store, issue_number, scenario)
    run_id = new_ulid()
    version = (
        f"{report.target_version}-hotfix-{issue_number}"
        if report.status == "pass"
        else f"hotfix-{issue_number}"
    )
    store.append(run_id, version, "hotfix.requested", {"issue": issue_number, "scenario": scenario})
    store.append(run_id, version, "stage.entered", {"stage": "M-HOTFIX-TRIAGE"})
    ex = Executor(store, repo, run_id)
    ex.version = version  # pre-PRECHECK state cannot re-derive the target
    ex.run_loop()
    return run_id


def hotfix_human_anchor(repo, store, run_id: str, refs: list[str]) -> int:
    """Form #4 (interfaces §2a): human manual anchor of cross-version AC refs
    on the active hotfix run at awaiting=hotfix_triage. human.anchor(manual) ->
    validate_anchor -> ANCHORED completion (SM-01.8). Untruthful refs keep the
    run at AWAIT_HUMAN (retryable) and exit 1."""
    state = store.state(run_id)
    version = _resolve_run_version(state)
    actor = git(repo, "config", "user.name", check=False).stdout.strip() or "Human"
    store.append(
        run_id, version, "human.anchor",
        {
            "mode": "manual",
            "acs": list(refs),
            "issue": state.hotfix_issue,
            "actor": actor,
        },
    )
    state = Executor(store, repo, run_id).run_loop()
    if state.awaiting == "hotfix_triage":
        print(
            "anchor invalid: refs do not resolve to existing ACs in the "
            "referenced versions; retry with valid refs",
            file=sys.stderr,
        )
        return 1
    print(f"anchor recorded: {', '.join(refs)}")
    print(f"run {run_id}: {_hotfix_state_line(state)}")
    return 0


class Executor(
    MImplRuntimeMixin,
    ExecObserveMixin,
    ExecDocMixin,
    ExecHotfixMixin,
    ExecRunLoopMixin,
    ExecTestCollectMixin,
    ExecTestRunMixin,
    ExecPhase0Mixin,
    ExecDispatchMixin,
    ExecWorktreeMixin,
    ExecVerdictMixin,
    ExecDocGapMixin,
    ExecEscapeMixin,
    ExecPublishMixin,
    ExecMilestoneMixin,
    ExecReleaseTailMixin,
    ExecVerifyParkMixin,
    ExecVerifyGatesMixin,
    ExecVerifyVersionMixin,
    ExecVerifyCiMixin,
    ResultCheckpointMixin,
):
    """Drives one run: project -> decide -> issue -> execute -> observe."""

    def __init__(
        self,
        store: Store,
        repo: Path,
        run_id: str,
        assignment_overlay: dict | None = None,
        max_dispatches: int | None = None,
    ):
        if assignment_overlay is not None and not isinstance(assignment_overlay, dict):
            raise TypeError("assignment_overlay must be a dict or None")
        if max_dispatches is not None and (
            isinstance(max_dispatches, bool)
            or not isinstance(max_dispatches, int)
            or max_dispatches < 1
        ):
            raise ValueError("max_dispatches must be a positive integer or None")
        self.store = store
        self.repo = repo
        self.run_id = run_id
        self.version = _resolve_run_version(store.state(run_id))
        # run_id 传入 opencode backend 启用 D-39 用户简化版（#44）session
        # 复用（fake backend 忽略该参数）。
        self.backend = select_backend(repo, self.version, run_id=run_id)
        self.assignment_overlay = deepcopy(assignment_overlay)
        self.max_dispatches = max_dispatches
        self._issue_backend = None  # lazy: created on first create_issues
        # D-36 浅版（#43）：OOB 观察点。初始化为当前 HEAD——run 停止期间
        # （静默期）的操作者提交按既有语义由下一次 baseline 冻结吸收，
        # 不发 oob.accepted；只有本进程存活期间（派发窗口内/外）观察到
        # 的前移才入账。
        self._oob_head = oob.head_sha(Path(self.repo))
        # B43（#45）：进程代码版本戳。宿主项目（无 tracks/ 包）为 None，
        # 漂移检查跳过。
        self._code_stamp = code_stamp(Path(self.repo))
        # B44（#46）：run 级熔断器计数（窗口：上次 human.retry/recover 之后；
        # 进程重启后从事件流重播种子，计数不丢）。
        self._breaker = RunBreaker.from_events(store.events(run_id))
        # B86/B88（#77）：非派发命令紧循环熔断（STALL）。循环内内存计数，
        # 无需持久化（崩溃重启清零可接受——紧循环 20 次在数秒内达成）。
        self._stall = CommandStallTracker()
        # SM-02 doc-gap lifecycle runtime (physical slice 1, DESIGN.md
        # appendix 2026-09-09): the capture -> classify -> quarantine ->
        # nested design revision -> adjudication-ingestion lifecycle lives in
        # tracks/executor/doc_gap_runtime.py behind this explicit narrow
        # capability facade. Only genuinely external capabilities are
        # injected (repo/run_id/store/backend/emit/issue/doc_path/
        # path_identity/dirty_snapshot/dispatch_issued/layout_paths) — no
        # complete Executor, no mixin, no reverse import. Executor keeps
        # thin same-name forwarders for dynamic dispatch and API stability.
        self._doc_gap = DocGapRuntime(
            DocGapCapabilities(
                repo=repo,
                run_id=run_id,
                store=store,
                backend=self.backend,
                emit=self._emit,
                issue=self.issue,
                doc_path=self._doc_path,
                path_identity=self._path_identity,
                dirty_snapshot=self._dirty_snapshot,
                dispatch_issued=self._dispatch_issued,
                layout_paths=_layout_paths,
            )
        )
