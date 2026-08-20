"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from tracks import paths
from tracks.baseline import baseline_summary, revision_digest
from tracks.capabilities import supports_m_impl
from tracks.discuss.parser import parse_threads
from tracks.effects import oob, select_backend
from tracks.effects.backend import valid_test_tasks
from tracks.effects.github import GithubIssuesError, issue_items, select_issue_backend
from tracks.executor.breaker import RunBreaker
from tracks.executor.code_stamp import (
    DRIFT_MESSAGE,
    RuntimeCodeDriftError,
    code_stamp,
)
from tracks.executor.doc_comment import (
    ROLE_ALLOWED_DOCS,
    DocCommentOrigin,
    classify_design_document_deltas,
    create_doc_gap_record,
    quarantine_authorized_changes,
)
from tracks.executor.file_identity import path_identity
from tracks.executor.helpers import (
    _DIAGNOSE_TARGET,
    _LEGIT_RED,
    _commit_if_staged,
    _dispatch_payload,
    _hook_output,
    _parse_collected_count,
    _short_detail,
    classify_red,
    git,
)
from tracks.executor.hotfix import (
    complete_hotfix_entry,
    parse_anchor_refs,
    precheck_hotfix,
    validate_anchor_refs,
)
from tracks.executor.m_impl_runtime import MImplRuntimeMixin
from tracks.executor.result_checkpoint import (
    _COMMITTED_EVENT,
    ResultCheckpointMixin,
)
from tracks.executor.validate import (
    parse_test_tasks,
    required_ac_ids,
    resolve_inherited_baseline_docs,
    validate_document,
)
from tracks.executor.worktree import (
    WorktreeHandle,
    _writer_worktree_path,
    cleanup_worktree,
    create_devon_worktree,
    create_test_authority_worktree,
    ensure_runtime_assets,
)
from tracks.frontmatter import doc_body_sha, set_frontmatter_field
from tracks.kernel.events import Command
from tracks.kernel.machine import _REVIEW_SUBSTATE, State, decide
from tracks.project import ContractError, lint_check_command, load_contract, validate_layout
from tracks.scaffold import _scaffold_declared_paths
from tracks.store import Store, new_ulid

_SCAFFOLD_RESERVED_ROOTS = frozenset({".git", ".opencode", ".tracks"})
# D-XX scaffold safety: the canonical host test-execution contract location is
# the ONLY .tracks/** path a Scaffold 宣言 may declare (it is where the fake /
# production backend writes the collect/run contract consumed by M-TEST). Every
# other .tracks/** stays rejected (the tracked project documents live under
# .tracks/projects/ and are staged via _emit_committed, never via the manifest).
# The M-IMPL reach entrypoint manifest (`.tracks/reach-entries.txt`, declared
# only by the Fake fixture) is the single additional canonical config artifact
# the design ResultCheckpoint stages alongside the design trio.
_CANONICAL_CONTRACT_PATH = (".tracks", "projects", "project.toml")
_CANONICAL_REACH_ENTRIES_PATH = (".tracks", "reach-entries.txt")

# Relative project-venv interpreter paths a contract may declare as argv[0].
# Used by _resolve_contract_argv0 to decide whether to substitute the Runtime's
# own interpreter when the project worktree lacks a .venv (live replay fix).
_VENV_PYTHON_RELS = frozenset({".venv/bin/python", ".venv/bin/python3"})


def _resolve_contract_argv0(argv: list[str], cwd: Path) -> list[str]:
    """Resolve a contract command's argv[0] against the project cwd.

    Live replay fix: an external worktree's project.toml contract may declare
    ``.venv/bin/python -m pytest ...`` while the worktree itself has no
    ``.venv`` (it was created from a host that does). When argv[0] is the
    relative project venv interpreter and it does not exist under cwd,
    substitute the Runtime's own ``sys.executable`` — but ONLY when that
    executable is itself running inside a venv (so we never fall back to a
    system Python). If the project carries its own ``.venv/bin/python`` it is
    used as-is (project interpreter is authoritative); non-Python commands,
    absolute paths, and other missing executables keep their original
    subprocess error / contract-failure semantics.
    """
    if not argv or argv[0] not in _VENV_PYTHON_RELS:
        return argv
    if (cwd / argv[0]).exists():
        return argv
    if sys.prefix != sys.base_prefix and os.access(sys.executable, os.X_OK):
        return [sys.executable, *argv[1:]]
    return argv


# Stage-transition table (design §1, single source of truth): EXIT seal ->
# next stage.entered. v0.5: M-TEST -> M-IMPL (previously a boundary exit).
_NEXT_STAGE = {
    "M-STORY": "M-SPEC",
    "M-SPEC": "M-ACC",
    "M-ACC": "M-REQ-APPROVAL",
    "M-REQ-APPROVAL": "M-DESIGN",
    "M-DESIGN": "M-TEST",
    "M-TEST": "M-IMPL",
}

# reviewer role -> its verdict event type
_VERDICT_EVENT = {"sage": "sage.verdict", "lex": "lex.verdict", "prism": "prism.verdict"}

# D-29 criteria pack identity (architecture.md §3.4): echoed by Prism and
# read back by the executor to enforce the anti-self-report triple.
_CRITERIA_PACK = {"name": "tracks-prism-test", "version": "0.1"}

# D-35 / PRISM-D35-R1-ADV1 review fields threaded into the M-DESIGN
# prism.verdict publish (kept in parity with the ResultCheckpointMixin
# publish branches; the hotfix override below prepends anchor_verdict,
# FR-0243 / IF-HOTFIX-009, interfaces §1a).
_DESIGN_PRISM_THREAD_KEYS = ("review_summary", "findings", "review_ref", "discussion_refs")


def _hotfix_approved_versions(store) -> set[str]:
    """The set of versions with an ``approval.recorded`` event across all runs
    (IF-HOTFIX-003 P-4 baseline-approval input)."""
    rows = store.conn.execute(
        "SELECT DISTINCT version FROM events WHERE type='approval.recorded'"
    ).fetchall()
    return {row[0] for row in rows if row[0]}


def _hotfix_run_branch(store, run_id: str) -> str | None:
    """The git branch a run lives on (IF-HOTFIX-006): ``fix/{issue}`` for a
    hotfix run, ``releases/{version}`` for a feature run, else the recorded
    ``branch.created`` target."""
    st = store.state(run_id)
    if st.hotfix_issue is not None:
        return f"fix/{st.hotfix_issue}"
    if st.version and st.version.startswith("v"):
        return f"releases/{st.version}"
    for ev in store.events(run_id):
        if ev.type == "branch.created":
            return ev.payload.get("branch_name") or None
    return None


def _resolve_run_version(state) -> str:
    """The run's version identity (ARCH-006 §1.0.3).

    The kernel projects ``State.version`` only from ``story.requested``
    (machine.py _on_story_requested); a hotfix run never emits that event, so
    its identity ``{target_version}-hotfix-{issue}`` is re-derived from the
    projected hotfix fields. Pre-PRECHECK (target unknown yet) falls back to
    the stable ``hotfix-{issue}`` identity; a non-hotfix run keeps its
    projected version.
    """
    if state.version:
        return state.version
    if state.hotfix_issue is not None:
        if state.hotfix_target_version:
            return f"{state.hotfix_target_version}-hotfix-{state.hotfix_issue}"
        return f"hotfix-{state.hotfix_issue}"
    return ""


_SAGE_OUTCOME_FIELDS = (
    "acs",
    "outcome",
    "searched_versions",
    "corpus_digests",
    "rationale_refs",
)


def _sage_anchor_outcome_fields(result: dict) -> dict:
    """The anchor-search fields a SAGE_TRIAGE outcome carries into the
    persisted outcome.received (IF-HOTFIX-004)."""
    return {key: result[key] for key in _SAGE_OUTCOME_FIELDS if result.get(key) is not None}


def _hotfix_corpus_paths(home: Path) -> list[str]:
    """The absolute spec.md / acceptance.md path list across all version dirs
    (sage anchor-search corpus, ARCH-006 §3.3)."""
    projects = paths.projects_dir(home)
    corpus: list[str] = []
    for d in sorted(projects.iterdir()):
        if not (d.is_dir() and d.name.startswith("v")):
            continue
        for doc in ("spec.md", "acceptance.md"):
            pth = d / doc
            if pth.exists():
                corpus.append(str(pth))
    return corpus


def precheck_hotfix_report(repo, store, issue_number: int, scenario: str):
    """Run the deterministic PRECHECK (IF-HOTFIX-003) with inputs collected
    from the repo + event store (issue fetch, branch probe, active-run branch,
    approved baselines). Returns ``(report, issue)``. Shared by ``cmd_hotfix``
    (run-version resolution) and ``_do_precheck_hotfix`` (event emission)."""
    try:
        issue = select_issue_backend(repo, "").fetch_issue(issue_number)
    except GithubIssuesError:
        issue = None  # fetch failure: pure rules map it to issue_fetch_failed
    branches = git(repo, "branch", "--list", check=False).stdout.splitlines()
    active = store.active_run()
    active_branch = _hotfix_run_branch(store, active) if active else None
    projects = paths.projects_dir(paths.tracks_home(repo))
    approved = _hotfix_approved_versions(store)
    report = precheck_hotfix(issue, scenario, branches, active_branch, projects, approved)
    return report, issue


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


def hotfix_entry_output(store, run_id: str) -> int:
    """Print the hotfix entry journey line-by-line + the terminal state line
    (interfaces §2a #1/#2). Returns the CLI exit code: 1 on REJECTED (the
    reason goes to stderr), otherwise 0."""
    for ev in store.events(run_id):
        detail = _hotfix_event_detail(ev)
        if detail:
            print(detail)
    state = store.state(run_id)
    if state.status == "completed" and state.terminal_state == "rejected":
        reason = _last_triage_reason(store, run_id)
        print(f"run {run_id}: REJECTED ({reason})", file=sys.stderr)
        return 1
    if state.awaiting == "hotfix_triage":
        print(
            f"run {run_id}: awaiting=awaiting_human origin=hotfix-triage "
            f"issue={state.hotfix_issue}"
        )
        return 0
    print(f"run {run_id}: {_hotfix_state_line(state)}")
    return 0


def _last_triage_reason(store, run_id: str) -> str:
    for ev in reversed(list(store.events(run_id))):
        if ev.type == "triage.prechecked" and ev.payload.get("status") == "rejected":
            return str(ev.payload.get("reason") or "")
    return ""


def _hotfix_event_detail(ev) -> str | None:
    """One readable line per hotfix triage event (spec E-01 style)."""
    tag = f"run {ev.run_id}"
    p = ev.payload
    if ev.type == "hotfix.requested":
        return (
            f"{tag} hotfix.requested "
            f"(issue={p.get('issue')}, scenario={p.get('scenario')})"
        )
    if ev.type == "triage.prechecked":
        if p.get("status") == "rejected":
            line = f"{tag} triage.prechecked REJECTED ({p.get('reason')})"
            nxt = p.get("next")
            return f"{line}\n  next: {nxt}" if nxt else line
        return f"{tag} triage.prechecked (issue={p.get('issue')} type={p.get('issue_type')})"
    if ev.type == "anchor.validated":
        return f"{tag} anchor.validated ({', '.join(p.get('acs') or [])})"
    if ev.type == "stage.entered":
        return f"{tag} stage.entered({p.get('stage')})"
    if ev.type == "backlog.recorded":
        return (
            f"{tag} backlog.recorded "
            f"(issue={p.get('issue')} decision={p.get('decision')})"
        )
    return None


def _hotfix_state_line(state) -> str:
    """One-line hotfix state (interfaces §2b): the shared state format plus the
    branch/scenario/issue fields for hotfix runs."""
    line = (
        f"stage={state.stage} substate={state.substate or '-'} "
        f"status={state.status} awaiting={state.awaiting or '-'}"
    )
    if state.hotfix_issue is not None:
        line += (
            f" branch=fix/{state.hotfix_issue} "
            f"scenario={state.hotfix_scenario or '-'} issue={state.hotfix_issue}"
        )
    return line


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


def hotfix_feature_route(repo, store, run_id: str) -> int:
    """Form #5 (interfaces §2a): human confirms the feature route on the active
    hotfix run at awaiting=hotfix_triage. human.anchor(feature_route) ->
    backlog.recorded -> run.completed(feature_route) (SM-01.9/.11), with no
    fix branch and no dangling run."""
    state = store.state(run_id)
    version = _resolve_run_version(state)
    actor = git(repo, "config", "user.name", check=False).stdout.strip() or "Human"
    store.append(
        run_id, version, "human.anchor",
        {"mode": "feature_route", "acs": None, "issue": state.hotfix_issue, "actor": actor},
    )
    store.append(
        run_id, version, "backlog.recorded",
        {"issue": state.hotfix_issue, "decision": "feature_route", "version": version},
    )
    store.append(
        run_id, version, "run.completed", {"terminal_state": "feature_route"}
    )
    print(f"feature route recorded; issue {state.hotfix_issue} -> backlog")
    return 0


class Executor(MImplRuntimeMixin, ResultCheckpointMixin):
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

    def _fail_fast_on_code_drift(self) -> None:
        """B43（#45）：派发前复核 tracks/** 指纹；漂移即 fail-fast。

        r1 实证：运行中进程外的代码修改不热加载，旧逻辑继续派发导致
        误判/escalation。这里宁可停车提示重启，绝不带旧逻辑继续。
        """
        if self._code_stamp is None:
            return
        if code_stamp(Path(self.repo)) != self._code_stamp:
            print(DRIFT_MESSAGE, file=sys.stderr, flush=True)
            raise RuntimeCodeDriftError(DRIFT_MESSAGE)

    def _emit(
        self, type: str, payload: dict, command_id: str | None = None, task_id: str | None = None
    ):
        ev = self.store.append(
            self.run_id, self.version, type, payload, command_id=command_id, task_id=task_id
        )
        # B44（#46）：统一漏斗——熔断计数与本进程内发出的事件保持同步。
        self._breaker.note(type, payload)
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

    def _observe_oob(self) -> None:
        """D-36 浅版（#43）：观察派发窗口外的操作者提交并入账。

        HEAD 相对上次观察点前移时，``since..HEAD`` 中携带 ``Tracks-OOB``
        trailer 的提交逐个发出 ``oob.accepted``（sha/reason/files）；未
        声明的提交仅 stderr 提示后随静默期语义吸收（观察点前移，不发
        事件）。观察点单调前移保证幂等。

        Prism review #43 B1：``pending`` 未关闭（WAL 窗口内——典型为
        异常路径的 finally 调用）时绝不发射事件（B40 挂死条件：非
        command.issued 事件清 pending → pending=None + doc_dispatched=
        True → run 挂死）。此时观察整体推迟（不前移指针），待 pending
        关闭后的下一次观察补账。
        """
        current = oob.head_sha(Path(self.repo))
        if current is None or current == self._oob_head:
            return
        if self.store.state(self.run_id).pending is not None:
            return  # WAL 窗口内：推迟到 pending 关闭后再观察
        commits = oob.commits_since(Path(self.repo), self._oob_head)
        for c in commits:
            if c["oob"]:
                self._emit(
                    "oob.accepted",
                    {
                        "sha": c["sha"],
                        "reason": c["reason"],
                        "files": sorted(oob.changed_paths(Path(self.repo), c["sha"])),
                    },
                )
            else:
                print(
                    f"  [oob] undeclared operator commit {c['sha'][:12]} "
                    f"'{c['subject']}' — declare with a 'Tracks-OOB: <reason>' "
                    f"trailer to have it accepted/audited",
                    file=sys.stderr,
                    flush=True,
                )
        # 历史改写（range 不可解析 → commits 为空）时同样前移观察点。
        self._oob_head = current

    def _emit_commit_failure(
        self, proc: subprocess.CompletedProcess, state: State, command_id: str
    ) -> None:
        """D-30/F-1: pre-commit hook rejected the commit -> emit the established
        failure-evidence event (verdict.failed -> s.last_failure via
        _on_verdict_failed) carrying the hook's combined output, so decide()
        re-dispatches the agent to fix the deliverable (FR-11)."""
        self._emit_commit_failure_evidence(
            state,
            command_id,
            "pre-commit hook rejected the commit",
            _hook_output(proc),
        )

    def _emit_commit_failure_evidence(
        self, state: State, command_id: str, reason: str, evidence: str
    ) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "commit",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
            },
            command_id=command_id,
        )

    def _doc_path(self, doc: str) -> Path:
        return paths.version_dir(self.store.home, self.version) / doc

    def _doc_paths(self, docs: list[str]) -> dict:
        """Map doc names to their filesystem paths."""
        return {doc: self._doc_path(doc) for doc in docs}

    def _artifact_path(self, name: str) -> Path:
        """Resolve an artifact name to its filesystem path for checkpoint
        operations. Known doc names (in ``_COMMITTED_EVENT``) resolve to the
        version dir; other names (e.g. ``tests/``) are repo-relative."""
        if name in _COMMITTED_EVENT:
            return self._doc_path(name)
        return self.repo / name

    def _artifact_paths(self, names: list[str]) -> dict:
        """Map artifact names to filesystem paths (version-dir docs or
        repo-relative paths)."""
        return {name: self._artifact_path(name) for name in names}

    def _dirty_files(self) -> set[str]:
        """Return repo-relative paths of all dirty (modified, staged, or
        untracked) files, excluding ignored files. Used to capture the
        exact file set written by Shield during M-TEST WRITE."""
        proc = git(self.repo, "status", "--porcelain", "-uall", check=False)
        files: set[str] = set()
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.add(path.strip().strip('"'))
        return files

    def _dirty_snapshot(self) -> dict[str, str]:
        """Content-identity snapshot of all dirty files: {path: identity}.

        identity is sha256(content) for regular files, ``symlink:{target}``
        for symlinks, ``missing`` for deleted entries, ``unreadable`` for
        non-regular/permission-denied files. No mtime — deterministic and
        JSON-serializable so it can be persisted in ``command.issued`` and
        compared after the Agent returns (or after crash recovery)."""
        proc = git(self.repo, "status", "--porcelain", "-uall", check=False)
        snapshot: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip().strip('"')
            snapshot[path] = self._path_identity(self.repo / path)
        return snapshot

    @staticmethod
    def _path_identity(path: Path) -> str:
        return path_identity(path)

    def _resolve_pre_dirty(self, state, substate, params):
        """Resolve the pre-dispatch dirty baseline for M-TEST Shield WRITE.

        Prefers the persisted content-identity snapshot (new commands); falls
        back to the legacy path-set for backward compat with old WALs; finally
        falls back to a fresh snapshot. Returns ``None`` outside M-TEST/WRITE
        (including v0.5 no_diff peer-review substates — no file attribution)."""
        if not (state.stage == "M-TEST" and substate == "WRITE"):
            return None
        if substate in ("NO_DIFF_EXPLAIN", "NO_DIFF_REVIEW"):
            return None
        if "pre_dirty_snapshot" in params:
            return params["pre_dirty_snapshot"]
        if "pre_dirty" in params:
            return set(params["pre_dirty"])
        return self._dirty_snapshot()

    # -- main loop (FR-29/FR-30) -------------------------------------------

    def run_loop(self) -> State:
        stop, recovered_kind, pending = self._recover_for_run()
        if stop:
            return self.store.state(self.run_id)
        dispatches, bound_substate = self._recovery_dispatch_state(recovered_kind, pending)
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

    def _run_loop_body(self, dispatches: int, bound_substate: str | None) -> State:
        while True:
            # B43（#45）：派发前 fail-fast——进程存活期间 tracks/** 漂移
            # （含 OOB 修复提交）即停车提示重启，绝不带旧逻辑继续。
            self._fail_fast_on_code_drift()
            # D-36 浅版（#43）：WAL 窗口之外观察操作者 OOB 提交。任何
            # command.issued 与其 outcome 之间的事件都会清 machine 的
            # pending（B40 语境：pending=None + doc_dispatched=True 会
            # 挂死 run），因此观察点必须在 issue() 之外。
            self._observe_oob()
            state = self.store.state(self.run_id)
            # B44（#46）：派发前熔断判定（active 状态；WAL 窗口之外）。
            tripped = self._check_breaker(state)
            if tripped is not None:
                return tripped
            # SM-02.9 (AC-FR0235-03): a paused doc-gap whose threads are all
            # resolved resumes with a NEW dispatch/attempt before decide() —
            # doc_dispatched stays True at the pause so decide() would halt.
            if self._resume_doc_gap_if_ready(state):
                continue
            cmd = decide(state)
            if cmd is None:
                return state
            if cmd.kind == "dispatch_agent" and self.max_dispatches is not None:
                stop, bound_substate = self._dispatch_gate(cmd, dispatches, bound_substate)
                if stop:
                    return state
                dispatches += 1
            self._progress(cmd, state)
            self.issue(cmd)
            if self._is_phase_boundary(cmd):
                return self.store.state(self.run_id)

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
            guard = [".venv/bin/ruff check"]
            lint_cmd = lint_check_command(self.repo)
            if lint_cmd and lint_cmd not in guard:
                guard.append(lint_cmd)
            assignment["commands"] = {"guard": guard}
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

    # NOTE (B32/#32): recover_stage deliberately gets NO similar boundary
    # special case. Unlike the stub_gap rollback -- which would auto-reenter a
    # defective downstream cycle -- recover_stage is the HUMAN-CHOSEN forward
    # path back into M-IMPL (stage.recovered resets to BASELINE and Archer
    # re-decomposes the task graph). run_loop may continue in the same
    # invocation; nothing requires an external correction first.

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
            delay = min(30 * 2 ** (state.infra_failure_streak - 1), 300)
            print(
                f"  [{state.stage}] infra failure streak "
                f"{state.infra_failure_streak}: backoff {delay}s before re-dispatch",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
        cid = command_id or new_ulid()
        params = dict(cmd.params)
        if cmd.kind == "dispatch_agent" and self.assignment_overlay is not None:
            assignment = dict(params.get("assignment") or {})
            assignment["scenario_context"] = deepcopy(self.assignment_overlay)
            params["assignment"] = assignment
        if cmd.kind == "dispatch_agent" and state.stage == "M-IMPL":
            params["assignment"] = self._materialize_m_impl_assignment(
                state,
                params,
                cid,
            )
        if cmd.kind == "dispatch_agent" and state.hotfix_issue is not None:
            params = self._materialize_hotfix_assignment(state, params)
        # D-28: enrich Shield WRITE assignments with structured test_tasks and
        # the pre-dirty snapshot before write-ahead logging (architecture.md
        # §1.2 DISPATCH). The persisted command.issued carries the full input.
        if cmd.kind == "dispatch_agent":
            self._enrich_shield_write_params(params, state)
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

    # -- per-kind handlers ---------------------------------------------------

    def _handle_no_diff_outcome(self, substate, result, cmd, task_id, state):
        """v0.5 no_diff peer review: emit explain/review events without
        entering the ResultCheckpoint pipeline. Returns True if handled."""
        if substate not in ("NO_DIFF_EXPLAIN", "NO_DIFF_REVIEW"):
            return False
        result_id = (state.active_result or {}).get("result_id")
        if substate == "NO_DIFF_EXPLAIN":
            if result.get("status") == "done":
                self._emit(
                    "no_diff.explained",
                    {"explanation": result.get("self_report", ""), "result_id": result_id},
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
            else:
                # Failed explanation: treat as a rejected review (revise).
                self._emit(
                    "no_diff.reviewed",
                    {"verdict": "revise", "result_id": result_id},
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
        else:  # NO_DIFF_REVIEW
            verdict = (
                result.get("verdict", "revise") if result.get("status") == "done" else "revise"
            )
            self._emit(
                "no_diff.reviewed",
                {"verdict": verdict, "result_id": result_id},
                command_id=cmd.command_id,
                task_id=task_id,
            )
        return True

    def _do_dispatch_agent(self, cmd, state, task_id, reconcile):
        p = cmd.params
        role, substate, doc = p["role"], p["substate"], p.get("doc")
        doc_path = self._doc_path(doc) if doc else None
        assignment = p.get("assignment")
        assignment = self._assignment_with_evidence(assignment, p)
        materialization_error = (
            self._invalid_m_impl_assignment(
                role,
                substate,
                assignment,
            )
            if state.stage == "M-IMPL"
            else None
        )
        if materialization_error is not None:
            self._emit_stale_assignment(cmd, task_id, role, materialization_error)
            return
        # D-32: fail-closed M-DESIGN→M-TEST gate. After the persisted
        # assignment.test_tasks enrichment (issue()), an invalid test-task
        # contract must NOT reach the (production or fake) backend: emit a
        # stub_gap failed outcome instead, which the reducer routes straight
        # to DIAGNOSE/stub_gap → rollback M-DESIGN (no attempt, no Human).
        if self._reject_invalid_test_tasks(state, role, substate, assignment, cmd, task_id):
            return
        self._dispatch_agent_backend(cmd, state, task_id, role, substate, doc, doc_path, assignment)

    @staticmethod
    def _assignment_with_evidence(assignment: dict | None, params: dict) -> dict | None:
        # D-35: carry the dispatch's stage into the assignment context so the
        # backend can apply stage-scoped review contracts (M-TEST/M-IMPL
        # structured findings vs M-DESIGN doc-anchored threads, SC-D35 §2.1).
        enriched_stage = params.get("stage")
        if params.get("evidence") is None and enriched_stage is None:
            return assignment
        enriched = dict(assignment or {})
        if params.get("evidence") is not None:
            enriched["evidence"] = params["evidence"]
        if enriched_stage is not None:
            enriched["stage"] = enriched_stage
        return enriched

    def _emit_stale_assignment(
        self,
        cmd: Command,
        task_id: str | None,
        role: str,
        reason: str,
    ) -> None:
        self._emit(
            "outcome.received",
            {"role": role, "status": "failed", "failure_class": "stale", "self_report": reason},
            command_id=cmd.command_id,
            task_id=task_id,
        )

    # -- SM-02 doc-comment-first (IF-DOCGAP-001 / IF-QUARANTINE-001) ---------

    def _snapshot_design_docs(self, role: str) -> dict[str, bytes]:
        """Read the role's allowed design docs before/after a dispatch."""
        allowed = ROLE_ALLOWED_DOCS.get(role, frozenset())
        snapshot: dict[str, bytes] = {}
        for name in allowed:
            path = self._doc_path(name)
            snapshot[name] = path.read_bytes() if path.exists() else b""
        return snapshot

    def _capture_doc_gap_context(
        self, state: State, role: str
    ) -> tuple[dict[str, bytes], dict[str, str]] | None:
        """Capture baseline design docs + dirty snapshot before act().

        Returns None when the role is not doc-gap-checked (IF-DOCGAP-001).
        The §1k contract (and architecture.md §3.8 entry path) covers every
        Devon or Shield outcome with NO stage qualifier: Shield WRITE fires
        in M-TEST, Devon RGR in M-IMPL. A `state.stage != "M-IMPL"` guard here
        would silently skip Shield WRITE deltas, leaving hook-injected legal
        discussions / illegal body edits unclassified before ordinary
        validation (AC-FR0234-01/04, AC-FR0237-02). Only the role allow-list
        gates which outcomes are doc-comment-checked.
        """
        if role not in ("devon", "shield"):
            return None
        return self._snapshot_design_docs(role), self._dirty_snapshot()

    def _doc_gap_origin(
        self, state: State, role: str, substate: str, cmd: Command
    ) -> DocCommentOrigin:
        """Build the origin identity bound to the dispatch (§1m)."""
        return DocCommentOrigin(
            run_id=self.run_id,
            role=role,
            task_id=state.current_task_id,
            phase=substate,
            dispatch_id=cmd.command_id,
            attempt=cmd.params.get("attempt", state.current_attempt + 1),
        )

    def _handle_doc_gap_outcome(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        substate: str,
        result: dict,
        doc_gap: tuple[dict[str, bytes], dict[str, str]] | None,
    ) -> bool:
        """Classify design-doc deltas BEFORE ordinary validation (§1k).

        Returns True when the outcome is routed to doc-gap adjudication
        (rejected or paused) instead of the ordinary validate/checkpoint path.
        Precedence: illegal_body_edit > legal_discussion > ordinary.
        ``discussion_reply`` deltas (replies to pre-existing threads, no new
        roots) pass through to the ordinary path — no SM-02 pause (B26a/#28).
        """
        if doc_gap is None:
            return False
        baseline_documents, pre_dirty = doc_gap
        current_documents = self._snapshot_design_docs(role)
        deltas = classify_design_document_deltas(
            role=role,
            baseline_documents=baseline_documents,
            current_documents=current_documents,
        )
        if any(d.classification == "illegal_body_edit" for d in deltas):
            self._reject_over_reach(
                cmd, state, task_id, role, deltas, baseline_documents, pre_dirty
            )
            return True
        legal = [d for d in deltas if d.classification == "legal_discussion"]
        if legal:
            self._pause_for_legal_discussion(
                cmd, state, task_id, role, substate, deltas, legal, pre_dirty
            )
            return True
        return False

    def _rollback_doc_gap_round(
        self,
        baseline_documents: dict[str, bytes],
        pre_dirty: dict[str, str] | None,
    ) -> list[str]:
        """Atomic rollback of all agent-attributable changes (§1k).

        Restores design docs to pre-dispatch bytes and reverts non-doc repo
        changes attributable to this outcome. Human/pre-dirty content survives.
        """
        rejected: list[str] = []
        for name, data in baseline_documents.items():
            path = self._doc_path(name)
            path.write_bytes(data)
            rejected.append(name)
        pre = pre_dirty or {}
        for rel in sorted(self._dirty_snapshot()):
            if rel in pre:
                continue  # Human/pre-dirty content survives (AC-FR0237-02).
            git(self.repo, "checkout", "--", rel, check=False)
            if (self.repo / rel).exists():
                (self.repo / rel).unlink()
            rejected.append(rel)
        return rejected

    def _reject_over_reach(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        deltas: tuple,
        baseline_documents: dict[str, bytes],
        pre_dirty: dict[str, str] | None,
    ) -> None:
        """illegal_body_edit: atomic rollback + outcome.rejected (AC-FR0237-02).

        Emits outcome.received(status='rejected') so the machine's
        failed-outcome path drives the retry re-dispatch (reset_doc +
        consume_attempt). status != 'failed' avoids the DIAGNOSE mis-route
        for devon RED/GREEN (machine.py _handle_failed_outcome).
        """
        rejected_paths = self._rollback_doc_gap_round(baseline_documents, pre_dirty)
        origin = self._doc_gap_origin(state, role, state.substate, cmd)
        self._emit(
            "outcome.received",
            {
                "role": role,
                "status": "rejected",
                "failure_class": "over_reach",
                "self_report": "illegal body edit: non-discussion design-doc content changed",
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._emit(
            "outcome.rejected",
            {
                "failure_class": "over_reach",
                "rollback": "atomic",
                "rejected_paths": sorted(set(rejected_paths)),
                "origin": asdict(origin),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _pause_for_legal_discussion(
        self,
        cmd: Command,
        state: State,
        task_id: str | None,
        role: str,
        substate: str,
        deltas: tuple,
        legal_deltas: list,
        pre_dirty: dict[str, str] | None,
    ) -> None:
        """legal_discussion: visible pause before validation (FR-0234-01/02).

        Emits doc_comment.detected + outcome.quarantined and does NOT emit
        outcome.received: the ordinary validate/checkpoint path never runs
        (§1k). doc_dispatched stays True so decide() halts at the pause.
        """
        origin = self._doc_gap_origin(state, role, substate, cmd)
        record = create_doc_gap_record(
            origin=origin,
            deltas=deltas,
            quarantine_id=None,
        )
        descriptor, manifest_ref = self._quarantine_legal_changes(
            state, origin, record, pre_dirty
        )
        self._emit_doc_gap_pause(
            cmd, task_id, origin, legal_deltas, record, descriptor, manifest_ref
        )

    def _quarantine_legal_changes(
        self,
        state: State,
        origin: DocCommentOrigin,
        record,
        pre_dirty: dict[str, str] | None,
    ) -> tuple:
        """Quarantine authorized changes and persist the manifest blob."""
        allowed_paths: tuple = ()
        manifest_meta = (state.current_task_metadata or {}).get(
            "manifest",
        )
        if isinstance(manifest_meta, dict):
            allowed_paths = tuple(manifest_meta.get("allowed_paths", []))
        descriptor = quarantine_authorized_changes(
            origin=origin,
            allowed_paths=allowed_paths,
            pre_dirty_identities=pre_dirty or {},
            agent_changes=(),
            design_identity=record.document_deltas[0].baseline_identity
            if record.document_deltas
            else "",
            run_identity=self.run_id,
        )
        manifest_ref = self.store.write_audit_blob(
            {
                "quarantine_id": descriptor.quarantine_id,
                "origin": asdict(origin),
                "changes": [asdict(c) for c in descriptor.changes],
                "status": descriptor.status,
            }
        )
        return descriptor, manifest_ref

    def _legal_delta_targets(self, legal_deltas: list) -> tuple[list[str], list[str]]:
        """Collect (document_paths, thread_ids) touched by legal deltas."""
        document_paths: list[str] = []
        thread_ids: list[str] = []
        for d in legal_deltas:
            document_paths.append(d.path)
            thread_ids.extend(d.new_thread_ids)
        return document_paths, thread_ids

    def _emit_doc_gap_pause(
        self,
        cmd: Command,
        task_id: str | None,
        origin: DocCommentOrigin,
        legal_deltas: list,
        record,
        descriptor,
        manifest_ref: str | None,
    ) -> None:
        """Emit doc_comment.detected + outcome.quarantined (§1m closed set)."""
        document_paths, thread_ids = self._legal_delta_targets(legal_deltas)
        self._emit(
            "doc_comment.detected",
            {
                "record_id": record.record_id,
                "origin": asdict(origin),
                "document_paths": document_paths,
                "thread_ids": thread_ids,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        self._emit(
            "outcome.quarantined",
            {
                "record_id": record.record_id,
                "quarantine_id": descriptor.quarantine_id,
                "status": descriptor.status,
                "manifest_ref": manifest_ref or descriptor.manifest_ref,
                "manifest_sha256": descriptor.manifest_sha256,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _resume_doc_gap_if_ready(self, state: State) -> bool:
        """SM-02.9: resume a paused doc-gap whose threads are all resolved.

        Scans waiting doc-gap records (state AGENT_CORRECTION). If every
        thread_id in the record is resolved in the current doc text, emits
        outcome.restored|discarded + outcome.resumed and issues a NEW
        dispatch for the same logical role/task/phase. Open threads stay
        paused (NFR-0090-02 fail-closed).
        """
        if state.stage != "M-IMPL" or state.status != "active":
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") != "AGENT_CORRECTION":
                continue
            if not self._threads_resolved_in_docs(
                rec.get("document_paths") or [], rec.get("thread_ids") or []
            ):
                continue  # open thread — fail-closed: stay paused (NFR-0090-02).
            self._resume_doc_gap_record(record_id, rec)
            return True
        return False

    def _resume_doc_gap_record(self, record_id: str, rec: dict) -> None:
        """Emit the resume decision pair and issue the NEW dispatch."""
        origin = rec.get("origin") or {}
        next_dispatch_id = new_ulid()
        next_attempt = int(origin.get("attempt", 1)) + 1
        restore = rec.get("quarantine_status") == "held"
        self._emit(
            "outcome.restored" if restore else "outcome.discarded",
            {
                "record_id": record_id,
                "quarantine_id": rec.get("quarantine_id") or "",
                "reason": "identity_current" if restore else "empty",
                "next_dispatch_id": next_dispatch_id,
                "next_attempt": next_attempt,
            },
        )
        self._emit(
            "outcome.resumed",
            {
                "record_id": record_id,
                "origin_dispatch_id": origin.get("dispatch_id", ""),
                "next_dispatch_id": next_dispatch_id,
                "next_attempt": next_attempt,
            },
        )
        self._issue_resume_dispatch(origin, next_dispatch_id, next_attempt)

    def _threads_resolved_in_docs(
        self, document_paths: list, thread_ids: list
    ) -> bool:
        """Instance helper: parse docs and check all thread_ids resolved."""
        if not thread_ids:
            return False
        found: dict[str, str] = {}
        for name in document_paths:
            path = self._doc_path(name)
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for t in parse_threads(text):
                found[t.thread_id] = t.status
        return all(found.get(tid) == "resolved" for tid in thread_ids)

    def _issue_resume_dispatch(
        self, origin: dict, command_id: str, attempt: int
    ) -> None:
        """Issue the resume dispatch for the same logical role/task/phase."""
        role = origin.get("role", "devon")
        substate = origin.get("phase", "RED")
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": role,
                "substate": substate,
                "stage": "M-IMPL",
                "attempt": attempt,
                "assignment": {"phase": substate.lower()},
            },
        )
        self.issue(cmd, command_id=command_id)

    def _dispatch_agent_backend(
        self,
        cmd,
        state,
        task_id,
        role,
        substate,
        doc,
        doc_path,
        assignment,
    ) -> None:
        p = cmd.params
        self._dispatch_log_start(p, state)
        t0 = time.monotonic()
        pre_dirty = self._resolve_pre_dirty(state, substate, p)
        # SM-02 doc-comment-first (IF-DOCGAP-001): capture design-doc baseline
        # and dirty snapshot BEFORE act() so deltas can be classified after.
        doc_gap = self._capture_doc_gap_context(state, role)
        # B1 (issue #2, user ruling 2026-08-18): writer agents (Devon RGR in
        # M-IMPL, Shield WRITE in M-TEST) run in a spatially isolated
        # worktree created from the current main HEAD; the work is replayed
        # onto the main tree afterwards, so every existing pipeline
        # (pre_dirty, manifest audit, R/G refs, atomic rollback) keeps
        # operating on the main tree unchanged. Per-dispatch lifecycle:
        # each phase re-creates from HEAD, so REFACTOR and PRISM-revise
        # re-dispatches naturally see committed work; a crash leaks only a
        # worktree that the B2 start-time sweep reclaims. Audited events.
        handle, wt_task_id = self._open_writer_worktree(state, role, substate, cmd)
        replay_error: str | None = None
        replayed = False
        try:
            result = self.backend.act(
                role,
                substate,
                doc,
                doc_path,
                assignment=assignment,
                worktree=Path(handle.path) if handle is not None else None,
            )
            if handle is not None:
                replay_error, replayed = self._replay_worktree_to_main(handle)
        finally:
            if handle is not None:
                cleanup_worktree(handle)
                self._emit(
                    "worktree.closed",
                    {
                        "kind": handle.kind,
                        "path": handle.path,
                        "task_id": wt_task_id,
                        "replayed": replayed,
                    },
                    command_id=cmd.command_id,
                    task_id=wt_task_id,
                )
        if replay_error is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "worktree",
                    "reason": f"worktree replay failed: {replay_error}",
                    "evidence": replay_error,
                    "task_id": task_id,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        self._dispatch_log_end(p, result, time.monotonic() - t0)
        # v0.5 no_diff peer review: NO_DIFF_EXPLAIN and NO_DIFF_REVIEW outcomes
        # do NOT enter the ResultCheckpoint pipeline. The explanation/review
        # events drive the state machine directly.
        if self._handle_no_diff_outcome(substate, result, cmd, task_id, state):
            return
        # D-29 anti-self-report triple ③: M-TEST PRISM_REVIEW criteria-pack
        # mismatch -> verdict.failed, re-dispatch Prism (no prism.verdict).
        # Must be checked before emitting outcome.received so a mismatch
        # short-circuits without entering the pipeline.
        if self._criteria_pack_mismatch(
            role, substate, state, result.get("verdict"), assignment, result, cmd
        ):
            self._emit_criteria_pack_failure(
                cmd,
                task_id,
                p,
                assignment,
                result,
                state,
            )
            return
        # SM-02 doc-comment-first pre-check (§1k): classify design-doc deltas
        # BEFORE ordinary validation. illegal_body_edit > legal_discussion.
        if self._handle_doc_gap_outcome(
            cmd, state, task_id, role, substate, result, doc_gap,
        ):
            return
        # v0.5 ResultCheckpoint pipeline (batch 1: M-STORY/M-SPEC/M-ACC):
        # embed the full pipeline capture in outcome.received so the reducer
        # atomically sets active_result in the same event — no crash window
        # between outcome.received and a separate result.submitted event.
        # Failed outcomes (status != "done") are handled by _on_outcome_received
        # (attempt consumed, substate reset) — no pipeline payload.
        result.setdefault("self_report", "")
        payload = _dispatch_payload(self.store, p, result)
        # v0.6 hotfix SAGE_TRIAGE (IF-HOTFIX-004): carry the Sage anchor-search
        # outcome fields (acs / no_anchor / searched_versions / corpus_digests /
        # rationale_refs) into the persisted outcome so _do_validate_anchor can
        # programmatically validate them.
        if role == "sage" and substate == "SAGE_TRIAGE":
            payload.update(_sage_anchor_outcome_fields(result))
        # Expose the agent I/O blob ref on the result so downstream verdict
        # emissions can point the next agent at the full transcript (critic
        # reviews and diagnoses live in blobs - long-form content must be
        # passed by reference, never re-derived or inlined).
        agent_io = payload.get("agent_io") or {}
        if agent_io.get("output_ref"):
            result.setdefault("output_ref", agent_io["output_ref"])
        if (
            state.stage in ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN", "M-TEST")
            and result.get("status") == "done"
            and (substate in ("DRAFT", "RESPOND", "WRITE") or substate in _REVIEW_SUBSTATE)
        ):
            payload["result_checkpoint"] = self._result_checkpoint_payload(
                cmd, state, result, p, substate, role, doc, pre_dirty
            )
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        if "result_checkpoint" in payload:
            return  # pipeline drives the domain event
        self._emit_dispatch_verdict(result, role, state, p, cmd, task_id)
        self._emit_shield_commit(result, role, state, cmd, task_id)

    def _open_writer_worktree(self, state, role: str, substate: str, cmd):
        """B1 (issue #2): open the per-dispatch worktree for writer agents.

        Devon RED/GREEN/REFACTOR (M-IMPL) get a devon_candidate worktree
        keyed by the task lease; Shield WRITE (M-TEST) gets a
        test_authority worktree. Both are created from the main HEAD —
        NOT a stale C_design — so re-dispatches after G/R commits see
        the committed work. Returns (handle, task_id) or (None, None)
        for every other dispatch.
        """
        kind: str | None = None
        task_id: str | None = None
        if (
            state.stage == "M-IMPL"
            and role == "devon"
            and substate in ("RED", "GREEN", "REFACTOR")
        ):
            kind, task_id = "devon", state.current_task_id or ""
        elif state.stage == "M-TEST" and role == "shield" and substate == "WRITE":
            kind = "test_authority"
        if kind is None or (kind == "devon" and not task_id):
            return None, None
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if kind == "devon":
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, task_id, "devon"),
                "devon_candidate",
            )
            handle = create_devon_worktree(str(self.repo), head, self.run_id, task_id)
        else:
            self._clear_stale_worktree_path(
                _writer_worktree_path(str(self.repo), self.run_id, None, "test_authority"),
                "test_authority",
            )
            handle = create_test_authority_worktree(str(self.repo), head, self.run_id)
        ensure_runtime_assets(str(self.repo), handle.path)
        self._emit(
            "worktree.opened",
            {
                "kind": handle.kind,
                "path": handle.path,
                "task_id": task_id,
                "base_sha": head,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return handle, task_id

    def _clear_stale_worktree_path(self, path: str, kind: str) -> None:
        """Reclaim this exact path if a crashed dispatch left it behind.

        Deliberately NOT the B2 sweep (user ruling: full sweeps run only at
        ``trac start``) — this reclaims only the one path this dispatch is
        about to occupy, so a crash between two runs cannot brick the
        loop's next writer dispatch.
        """
        if not os.path.exists(path):
            return
        cleanup_worktree(WorktreeHandle(path=path, base_sha="", kind=kind))

    def _replay_worktree_to_main(self, handle: WorktreeHandle) -> tuple[str | None, bool]:
        """Apply the worktree's working-tree delta onto the main tree.

        ``git add -A`` + ``git diff --cached --binary`` captures tracked
        edits plus new files; ``git apply`` (bytes mode — binary patches
        must never be decoded as text) writes them into the main tree
        WITHOUT staging, so downstream pipelines see exactly the state the
        agent would have left had it worked in the main tree.
        Returns (error, had_delta); error None means clean or applied ok.

        D-36 浅版（#43/B36 反碾压守卫）：主树在派发窗口内被操作者提交
        改动过的文件，mirror 回退不再覆盖——同名冲突 fail-closed 报错
        （主树侧内容保留，人工裁决后重试），杜绝 concurrent-collision
        replay 静默吞掉操作者已提交的工作。
        """
        wt = handle.path
        subprocess.run(
            ["git", "-C", wt, "add", "-A"],
            capture_output=True,
            check=False,
        )
        diff = subprocess.run(
            ["git", "-C", wt, "diff", "--cached", "--binary"],
            capture_output=True,
            check=False,
        )
        if not diff.stdout.strip():
            # No file delta, but the agent may still have created empty
            # directories (git diffs cannot carry them; e.g. Shield's
            # tests/assets scaffold). Sync those so the replayed state
            # matches what working directly in the main tree would leave.
            return self._sync_worktree_dirs(handle), False
        applied = subprocess.run(
            ["git", "-C", str(self.repo), "apply", "--whitespace=nowarn", "-"],
            input=diff.stdout,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            # Fast path refused (e.g. the main tree holds an uncommitted
            # file the diff creates). Mirror the changed files instead:
            # per-file copy/delete from the worktree's final state, which
            # is exactly the state the agent would have left had it
            # worked directly in the main tree (a direct agent edit also
            # overwrites a pre-existing dirty file) — EXCEPT paths the
            # operator committed on main during the dispatch window
            # (#43 anti-clobber guard: those conflict fail-closed).
            return self._mirror_worktree_changes(handle), True
        return self._sync_worktree_dirs(handle), True

    def _main_committed_since(self, base_sha: str) -> set[str]:
        """派发窗口内主树侧被提交改动过的路径（base_sha..HEAD，B36 守卫用）。

        Runtime 是单写者且在派发期间阻塞，窗口内主树 HEAD 前移只能是
        操作者提交（声明与否在守卫处不区分——碾压数据是更重的伤害）。
        base_sha 不可解析（历史改写）时返回空集（守卫退化为旧行为）。
        """
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(self.repo),
                "diff",
                "--name-only",
                "-z",
                f"{base_sha}..HEAD",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return set()
        return {p for p in proc.stdout.split("\0") if p}

    def _sync_worktree_dirs(self, handle: WorktreeHandle) -> str | None:
        """mkdir -p every directory present in the worktree but missing in
        the main tree (agent-created scaffolding; empty dirs are invisible
        to git diffs). Never deletes anything; .git and symlinks are not
        traversed. Returns an error string or None."""
        wt = Path(handle.path)
        for dirpath, dirnames, _filenames in os.walk(wt, followlinks=False):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in dirnames:
                src = Path(dirpath) / name
                rel = src.relative_to(wt)
                dst = self.repo / rel
                if not dst.exists():
                    try:
                        dst.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        return f"mkdir {rel}: {exc}"
        return None

    def _mirror_worktree_changes(self, handle: WorktreeHandle) -> str | None:
        """Per-file sync of the worktree's staged changes onto the main
        tree (replay fallback). Returns an error string or None.

        D-36 浅版（#43）反碾压守卫：与派发窗口内主树侧提交重叠的路径
        fail-closed——不镜像、保留主树内容、报冲突清单（all-or-nothing：
        发现任一冲突即整个 mirror 不执行，避免留下半镜像的混合状态）。
        """
        names = subprocess.run(
            ["git", "-C", handle.path, "diff", "--cached", "--name-only", "-z"],
            capture_output=True,
            text=True,
            check=False,
        )
        changed = [n for n in names.stdout.split("\0") if n]
        guarded = self._main_committed_since(handle.base_sha)
        conflicts = sorted(set(changed) & guarded)
        if conflicts:
            return (
                "main tree moved during dispatch (operator commits) conflict "
                f"with agent worktree delta: {', '.join(conflicts)} — "
                "operator content preserved on main; resolve manually "
                "(re-dispatch will re-run the agent on the new HEAD)"
            )
        for name in changed:
            src = Path(handle.path) / name
            dst = self.repo / name
            try:
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                else:
                    dst.unlink(missing_ok=True)
            except OSError as exc:
                return f"mirror {name}: {exc}"
        return None

    def _emit_criteria_pack_failure(
        self,
        cmd,
        task_id,
        params,
        assignment,
        result,
        state,
    ) -> None:
        payload = _dispatch_payload(self.store, params, result)
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        assigned = (assignment or {}).get("criteria_pack")
        self._emit(
            "verdict.failed",
            {
                "check": "criteria_pack_mismatch",
                "reason": f"expected {assigned}, got {result.get('criteria_pack')}",
                "attempt": state.current_attempt + 1,
                "evidence": str(result.get("criteria_pack")),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _emit_dispatch_verdict(self, result, role, state, params, cmd, task_id):
        if role not in _VERDICT_EVENT:
            return
        if state.stage == "M-IMPL" and role == "prism" and state.substate == "DIAGNOSE":
            self._emit_diagnose_verdict(result, state, cmd, task_id)
            return
        if not result.get("verdict"):
            return
        self._emit_verdict(role, result["verdict"], result, state, params, cmd, task_id)

    def _emit_diagnose_verdict(self, result, state, cmd, task_id):
        # Real channel (opencode): the Prism DIAGNOSE reply ends with a
        # {"classification", "reason", "evidence"} JSON (skill contract)
        # extracted into result["verdict"] / result["diagnosis"]. The fixer
        # dispatch receives the diagnostic's actual reason/evidence via FR-11
        # last_failure - without them it only sees the classification label
        # and has to re-derive the whole analysis (run 01KZTHE7 T-008: Shield
        # burned 52 minutes re-archaeologying what Prism had already found).
        verdict = result.get("verdict")
        if verdict not in ("test_defect", "stub_gap", "ac_gap", "spec_gap", "impl_defect"):
            # Prism DIAGNOSE contract violation: no valid classification JSON
            # in final reply. Fail-closed (consume attempt, redispatch Prism;
            # budget exhaustion escalates) — do NOT fallback-derive a
            # classification from prose (user stance: agents honor contracts).
            self._emit(
                "verdict.failed",
                {
                    "check": "diagnose_contract_violation",
                    "target_stage": "M-IMPL",
                    "task_id": task_id or state.current_task_id or "",
                    "reason": (
                        "Prism DIAGNOSE returned no "
                        "{classification,reason,evidence} JSON; "
                        "contract violation"
                    ),
                    "evidence": (
                        "final reply missing bare JSON object per "
                        "Prism.md DIAGNOSE contract"
                    ),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        classification = verdict
        diagnosis = result.get("diagnosis") if isinstance(result.get("diagnosis"), dict) else {}
        prior = state.last_failure or {}
        fallback = "Prism diagnosis: " + classification
        reason = diagnosis.get("reason") or prior.get("reason") or fallback
        evidence = diagnosis.get("evidence") or prior.get("evidence") or fallback
        # Point the fixer at the full transcript blob: the verdict's
        # reason/evidence are a summary; Prism's complete step-by-step
        # analysis lives in the session blob (user stance: long-form critic
        # output is passed by blob reference, not re-derived downstream).
        ref = result.get("output_ref")
        if ref:
            evidence += f"; full diagnosis transcript: .tracks/runtime/blobs/{ref}"
        target = (
            "M-IMPL"
            if classification in ("test_defect", "impl_defect")
            else _DIAGNOSE_TARGET.get(classification, "M-IMPL")
        )
        self._emit(
            "verdict.failed",
            {
                "check": classification,
                "target_stage": target,
                "task_id": task_id or state.current_task_id or "",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _shield_fix_manifest_mismatch(self, result: dict, changed: list) -> str | None:
        """PRISM-B28-R2-02: SHIELD_FIX include vs observed tests/ files,
        with the result_checkpoint dirty-retry tolerance (a retry may verify
        pre-existing files instead of creating new ones). Returns the
        evidence string on mismatch, None to pass."""
        manifest = result.get("artifact_manifest") or {}
        include = [e.get("path") for e in (manifest.get("include") or []) if e.get("path")]
        if not include:
            return None
        code_include = [p for p in include if p.startswith("tests/")]
        if set(code_include) == set(changed):
            return None
        dirty = self._dirty_files()
        # PRISM-B28-R3-01: an include that declares no tests/ path at all is
        # the extreme under-report — the grace branch must not pass vacuously
        # on all([]).
        if code_include and all(
            p in dirty and (self.repo / p).is_file() for p in code_include
        ):
            return None
        return (
            "artifact_manifest include paths do not match observed tests/ "
            f"files: include={sorted(code_include)}, observed={changed}"
        )

    def _emit_shield_commit(self, result, role, state, cmd, task_id):
        if not (
            state.stage == "M-IMPL"
            and role == "shield"
            and state.substate == "SHIELD_FIX"
            and result.get("status") == "done"
        ):
            return
        # Stage only tests/ files
        tests_dir = self.repo / "tests"
        # Get list of changed files under tests/
        proc = git(self.repo, "status", "--porcelain", "--", "tests/")
        changed = (
            [line[3:] for line in proc.stdout.splitlines() if line.strip()]
            if proc.stdout.strip()
            else []
        )
        if not changed:
            # No tests/ changes - fail closed
            self._emit(
                "verdict.failed",
                {
                    "check": "scope",
                    "reason": "shield_fix_no_diff",
                    "evidence": "Shield SHIELD_FIX produced no tests/ diff",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        # PRISM-B28-R2-02: the SHIELD_FIX manifest contract promises an
        # include==observed comparison — enforce it here (M-IMPL shield
        # WRITE bypasses the ResultCheckpoint pipeline).
        if self._shield_fix_manifest_mismatch(result, changed):
            self._emit(
                "verdict.failed",
                {
                    "check": "manifest",
                    "reason": "include_mismatch",
                    "evidence": self._shield_fix_manifest_mismatch(result, changed),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        # Stage all changed tests/ files
        git(self.repo, "add", "--", "tests/")
        # Create commit with trailers
        task_id_val = state.current_task_id or ""
        attempt_val = str(state.current_attempt + 1)
        message = (
            f"Shield fix: {task_id_val} attempt {attempt_val}\n\n"
            f"Tracks-Task: {task_id_val}\n"
            f"Tracks-Attempt: {attempt_val}\n"
            f"command_id: {cmd.command_id}"
        )
        proc = _commit_if_staged(self.repo, message)
        if proc is not None and proc.returncode != 0:
            self._emit(
                "verdict.failed",
                {
                    "check": "scope",
                    "reason": "shield_fix_commit_failed",
                    "evidence": proc.stderr or proc.stdout,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit(
            "test.committed",
            {"commit_sha": commit_sha, "test_count": test_count},
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _reject_invalid_test_tasks(self, state, role, substate, assignment, cmd, task_id) -> bool:
        """D-32 fail-closed gate: return True (and emit a stub_gap failed
        outcome) when an M-TEST Shield WRITE assignment carries an invalid
        test-task contract, so the backend is never called."""
        if not (
            state.stage in ("M-TEST", "M-IMPL")
            and substate == "WRITE"
            and role == "shield"
            and not valid_test_tasks((assignment or {}).get("test_tasks"))
        ):
            return False
        self._emit(
            "outcome.received",
            {
                "role": role,
                "status": "failed",
                "failure_class": "stub_gap",
                "self_report": "test-task contract invalid",
                "audit_evidence": (
                    "assignment.test_tasks must be a non-empty list of "
                    "{ac_id, layers, if_ids} with non-empty layers "
                    "(integration/e2e) and registered IF- ids; got "
                    f"{assignment.get('test_tasks') if assignment else None}"
                ),
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    @staticmethod
    def _dispatch_log_start(p: dict, state: State) -> None:
        """Fix 5: emit a start line to stderr before the backend call so the
        operator sees what is being dispatched during a long ``trac run``.
        Flushed immediately; does not print agent stdout."""
        role = p.get("role", "?")
        substate = p.get("substate", "?")
        attempt = p.get("attempt", "?")
        objective = p.get("objective") or ""
        if not objective:
            assignment = p.get("assignment") or {}
            objective = assignment.get("kind") or ""
            if not objective:
                docs = p.get("docs")
                doc = p.get("doc")
                if docs:
                    objective = ", ".join(docs)
                elif doc:
                    objective = doc
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        print(
            f"  {ts} [{state.stage}] dispatch {role} ({substate}, attempt {attempt})"
            f" {objective}".rstrip(),
            file=sys.stderr,
            flush=True,
        )

    @staticmethod
    def _dispatch_log_end(p: dict, result: dict, elapsed: float) -> None:
        """Fix 5: emit a completion line to stderr after the backend returns.
        For failures, includes failure_class and a one-line self_report."""
        role = p.get("role", "?")
        status = result.get("status", "?")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
        line = f"  {ts} {role} {status} ({elapsed:.1f}s)"
        if status != "done":
            fc = result.get("failure_class") or "?"
            report = (result.get("self_report") or "").strip()
            report = report.splitlines()[0] if report else ""
            line += f" [{fc}]"
            if report:
                line += f" {report}"
        print(line, file=sys.stderr, flush=True)

    def _criteria_pack_mismatch(
        self, role, substate, state, verdict, assignment, result, cmd
    ) -> bool:
        """Return True when the criteria-pack identity mismatch was emitted
        (caller should skip the normal verdict emission).

        Only DONE outcomes carry the echo — a failed result (manifest
        malformed, provider error) legitimately lacks criteria_pack, and
        re-classifying it as a pack mismatch double-punishes the same
        attempt (live T-002 PRISM_FINAL: three 140-cap rejects each also
        burned as criteria_pack_mismatch, 2026-08-19)."""
        if result.get("status") != "done":
            return False
        if not (
            role == "prism"
            and (
                (verdict and substate == "PRISM_REVIEW" and state.stage == "M-TEST")
                or (
                    state.stage == "M-IMPL"
                    and substate in ("PRISM_PLAN", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE")
                )
            )
        ):
            return False
        assigned_pack = (assignment or {}).get("criteria_pack")
        outcome_pack = result.get("criteria_pack")
        return bool(assigned_pack and outcome_pack != assigned_pack)

    def _apply_prism_review_fields(self, payload: dict, verdict: str, result: dict) -> None:
        """D-35 (SC-D35 §2.2/§2.3): prism verdict events carry the criteria
        pack plus, on revise, the structured review fields — including the
        routing key defect_classification (PRISM-D35-R2-01: without it the
        M-IMPL routers see None and every revise falls to the default route,
        red_defect/design_gap/ac_gap/spec_gap unreachable — fail-wrong) —
        and the blobs ref for the full body. M-IMPL reviews
        (PRISM_PLAN/RED/FINAL) emit through this legacy path rather than the
        ResultCheckpoint pipeline."""
        payload["criteria_pack"] = result.get("criteria_pack")
        # FR-0243 / IF-HOTFIX-009 (interfaces §1a): the M-DESIGN Prism hotfix
        # review carries the anchor_verdict routing field — 缺省 "upheld",
        # preserved "overturned" when Prism overturns the hotfix anchor so the
        # kernel routes M-DESIGN -> M-HOTFIX-TRIAGE/SAGE_TRIAGE without
        # consuming the M-DESIGN redispatch budget (kernel/machine.py L840).
        # Set before the pass early-return so a pass verdict still exposes the
        # default "upheld" for AC-FR0243-02 review observability.
        payload["anchor_verdict"] = result.get("anchor_verdict") or "upheld"
        if verdict == "pass":
            return
        for key in ("review_summary", "findings", "discussion_refs"):
            val = result.get(key)
            if val:
                payload[key] = val
        # Non-None guard mirrors _m_test_prism_payload: a present null would
        # defeat the reducers' absent-key defaults.
        if result.get("defect_classification") is not None:
            payload["defect_classification"] = result["defect_classification"]
        review_body = result.get("review_body")
        if isinstance(review_body, str) and review_body.strip():
            review_ref = self.store.write_audit_blob(review_body)
            if review_ref:
                payload["review_ref"] = review_ref

    def _emit_verdict(self, role, verdict, result, state, p, cmd, task_id):
        """Emit the verdict event (+ review.round_started for Prism revise)."""
        ev = _VERDICT_EVENT[role]
        payload = {"verdict": verdict, "diff_ref": result.get("diff_ref")}
        if role == "prism" and state.stage in ("M-TEST", "M-IMPL"):
            self._apply_prism_review_fields(payload, verdict, result)
        self._emit(ev, payload, command_id=cmd.command_id, task_id=task_id)
        if ev == "prism.verdict" and verdict != "pass":
            # flow.md §8.2: a revise verdict opens the next review round
            # (reducer bumps review_round and resets the reviewer flags).
            self._emit(
                "review.round_started",
                {"stage": p.get("stage"), "round": state.review_round + 1},
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _publish_prism_verdict(
        self, verdict, commit_sha, created_commit, result_id, state, cmd, task_id
    ):
        """Hotfix extension of the ResultCheckpoint publish (FR-0243 /
        IF-HOTFIX-009, interfaces §1a): every M-DESIGN prism.verdict event
        carries the anchor verdict — 缺省 "upheld", preserved "overturned" —
        so Prism's hotfix-anchor review is observable (AC-FR0243-02) and the
        kernel routes an overturned anchor back to M-HOTFIX-TRIAGE/SAGE_TRIAGE
        without consuming the M-DESIGN redispatch budget (AC-FR0243-03). The
        mixin branch threads only the D-35 review fields, and its checkpoint
        domain payload skips _apply_prism_review_fields on pass
        (PRISM-V06-R1-T003-01), so a pass checkpoint has no explicit
        anchor_verdict key and defaults to upheld here. Non-M-DESIGN publish
        keeps the mixin semantics via delegation."""
        if state.stage != "M-DESIGN":
            ResultCheckpointMixin._publish_prism_verdict(
                self, verdict, commit_sha, created_commit, result_id, state, cmd, task_id
            )
            return
        domain_payload = cmd.params.get("domain_event", {}).get("payload", {})
        payload = {
            "verdict": verdict,
            "diff_ref": commit_sha if created_commit and commit_sha else None,
            "result_id": result_id,
            "anchor_verdict": domain_payload.get("anchor_verdict") or "upheld",
        }
        for key in _DESIGN_PRISM_THREAD_KEYS:
            val = domain_payload.get(key)
            if val:
                payload[key] = val
        self._emit("prism.verdict", payload, command_id=cmd.command_id, task_id=task_id)
        if verdict != "pass":
            # flow.md §8.2: a revise verdict opens the next review round.
            round_payload = {"stage": "M-DESIGN", "round": state.review_round + 1}
            self._emit(
                "review.round_started",
                round_payload,
                command_id=cmd.command_id,
                task_id=task_id,
            )

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
        # FR-0120: Archer must declare [layout.devon]/[layout.shield] in
        # project.toml; gate at M-DESIGN EXIT when architecture.md is validated.
        if failure is None and doc == "architecture.md" and state.stage == "M-DESIGN":
            layout_err = validate_layout(self.repo)
            if layout_err is not None:
                failure = ("layout", layout_err)
        if failure:
            check, reason = failure
            payload = {"check": check, "reason": reason, "evidence": str(path)}
            if check != "scope_overflow":  # FR-20 rolls back, never escalates
                payload["attempt"] = state.current_attempt + 1
            self._emit("verdict.failed", payload, command_id=cmd.command_id)
        else:
            self._emit(
                "verdict.passed",
                {"check": ",".join(checks) or "schema", "detail": "format checks"},
                command_id=cmd.command_id,
            )

    def _do_commit_document(self, cmd, state, task_id, reconcile):
        doc, message = cmd.params["doc"], cmd.params["message"]
        marker = f"command_id: {cmd.command_id}"
        if reconcile:
            probe = git(self.repo, "log", "--grep", marker, "--format=%H", check=False)
            if probe.stdout.split():
                self._emit_committed(doc, probe.stdout.split()[0], cmd.command_id)
                return
        doc_path = self._doc_path(doc)
        stage_paths = [doc_path]
        if state.stage == "M-DESIGN" and doc == "architecture.md":
            scaffold_paths, issue = self._design_scaffold_paths(doc_path)
            if issue is not None:
                self._emit_commit_failure_evidence(
                    state,
                    cmd.command_id,
                    "declared scaffold path rejected",
                    issue,
                )
                return
            # Dedup: the canonical project.toml may be both declared in the
            # manifest and returned by _project_contract_paths(); stage once.
            stage_paths.extend(dict.fromkeys([*scaffold_paths, *self._project_contract_paths()]))
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

    def _project_contract_paths(self) -> list[Path]:
        """Return the project.toml path if it exists, for staging alongside
        architecture.md during M-DESIGN."""
        toml_path = paths.project_toml_path(paths.tracks_home(self.repo))
        return [toml_path] if toml_path.exists() else []

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
        if (
            not relative.parts
            or relative.parts[0] in _SCAFFOLD_RESERVED_ROOTS
            and tuple(relative.parts)
            not in (_CANONICAL_CONTRACT_PATH, _CANONICAL_REACH_ENTRIES_PATH)
        ):
            # exactly the canonical .tracks/projects/project.toml and
            # .tracks/reach-entries.txt are allowed; every other .tracks/**
            # (and .git/.opencode/**) is rejected.
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
        tracked = (
            git(
                self.repo, "ls-files", "--error-unmatch", "--", relative_text, check=False
            ).returncode
            == 0
        )
        ignored = (
            git(
                self.repo, "check-ignore", "--quiet", "--no-index", "--", relative_text, check=False
            ).returncode
            == 0
        )
        if ignored and not tracked:
            return None, f"scaffold path is not allowed (ignored): {raw}"
        return self.repo / relative, None

    def _emit_committed(self, doc, commit_sha, command_id, final=False, result_id=None):
        ev_type, sha_key = _COMMITTED_EVENT[doc]
        payload = {
            "commit_sha": commit_sha,
            sha_key: doc_body_sha(self._doc_path(doc)),
            "final": final,
        }
        if result_id is not None:
            payload["result_id"] = result_id
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
                self.repo, f"{stage}: seal {doc} sha\n\ncommand_id: {cmd.command_id}"
            )
            if proc is not None and proc.returncode != 0:
                self._emit_commit_failure(proc, state, cmd.command_id)
                return
            # R4-02: the sealed commit gets its own final committed event so ACs
            # match `final=true` and never the DRAFT-stage commit.
            commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            self._emit_committed(doc, commit_sha, cmd.command_id, final=True)
        self._emit("stage.exited", {"stage": stage}, command_id=cmd.command_id)
        nxt = _NEXT_STAGE.get(stage)
        if nxt and not self._is_boundary_transition(stage, nxt):
            self._emit("stage.entered", {"stage": nxt}, command_id=cmd.command_id)
        else:
            # SM-05.6 / IF-003 §10f: no successor (or a version-gated
            # successor) -> stop at the next-stage boundary. M-DESIGN boundary
            # pre-v0.3; M-TEST boundary for pre-v0.5 runs; M-IMPL boundary
            # since. v0.6 hotfix boundary restores the suspended run's branch.
            self._emit(
                "run.completed",
                self._hotfix_boundary_completion(state),
                command_id=cmd.command_id,
            )

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

    # -- v0.6 hotfix handlers (IF-HOTFIX-002/003/004/005/007) -----------------

    def _materialize_hotfix_assignment(
        self, state, params: dict
    ) -> dict:
        """v0.6 hotfix dispatch materialization (interfaces §1h / ARCH-006
        §3.3), routed by stage:

        - M-HOTFIX-TRIAGE (SAGE_TRIAGE): enrich the Sage assignment with the
          anchor-search corpus — issue corpus, scenario, target_version,
          anchor_hints (parse_issue_hints) and the corpus path list.
        - M-DESIGN: the Archer delta-design assignment carries the inherited
          baseline contract — anchor_acs, target_version, hotfix_issue and
          the read-only baseline doc paths (target trio + design trio).
        Other dispatches pass through unchanged."""
        if state.stage == "M-DESIGN":
            return self._materialize_hotfix_mdesign_assignment(state, params)
        if not (
            params.get("substate") == "SAGE_TRIAGE" and params.get("role") == "sage"
        ):
            return params
        p = dict(params)
        assignment = dict(p.get("assignment") or {})
        issue = self._hotfix_issue_corpus(state.hotfix_issue)
        if issue is not None:
            assignment["issue"] = {
                "title": issue.title, "body": issue.body, "labels": list(issue.labels)
            }
            from tracks.executor.hotfix import parse_issue_hints
            assignment["anchor_hints"] = parse_issue_hints(issue.body)
        else:
            assignment["issue"] = None
            assignment["anchor_hints"] = {}
        assignment["scenario"] = state.hotfix_scenario or "post-release"
        assignment["target_version"] = state.hotfix_target_version or ""
        assignment["corpus"] = _hotfix_corpus_paths(self.store.home)
        p["assignment"] = assignment
        return p

    def _materialize_hotfix_mdesign_assignment(self, state, params: dict) -> dict:
        """v0.6 hotfix M-DESIGN dispatch materialization (interfaces §1h /
        ARCH-006 §3.3): the Archer delta-design assignment carries the
        inherited baseline contract — anchor_acs, target_version and the
        read-only baseline doc paths (target trio + design trio) — so the
        delta three docs can be produced against the inherited baseline
        (FR-0243-01, IF-HOTFIX-005). No-op for non-hotfix M-DESIGN runs."""
        p = dict(params)
        assignment = dict(p.get("assignment") or {})
        assignment["anchor_acs"] = list(state.hotfix_anchor_acs or [])
        assignment["target_version"] = state.hotfix_target_version or ""
        assignment["hotfix_issue"] = state.hotfix_issue
        target_version = state.hotfix_target_version
        if target_version:
            vdir = paths.projects_dir(self.store.home) / target_version
            assignment["baseline_doc_paths"] = [
                str(vdir / name)
                for name in ("story.md", "spec.md", "acceptance.md",
                             "architecture.md", "interfaces.md", "test-plan.md")
            ]
        p["assignment"] = assignment
        return p

    def _hotfix_issue_corpus(self, issue_number):
        """The host issue corpus for the Sage assignment (deterministic read
        channel). A fetch failure yields None (PRECHECK already reported it)."""
        try:
            return select_issue_backend(self.repo, self.version).fetch_issue(issue_number)
        except GithubIssuesError:
            return None

    def _do_precheck_hotfix(self, cmd, state, task_id, reconcile):
        """precheck_hotfix (SM-01.1-.4, IF-HOTFIX-003): deterministic issue
        fetch + scenario / active-branch / target-version location. Emits
        triage.prechecked(pass) -> SAGE_TRIAGE or
        triage.prechecked(rejected) + run.completed(rejected) (SM-01.4)."""
        if reconcile and any(
            e.type == "triage.prechecked" for e in self.store.events(self.run_id)
        ):
            return
        issue_number = state.hotfix_issue
        scenario = state.hotfix_scenario or "post-release"
        report, issue = precheck_hotfix_report(
            self.repo, self.store, issue_number, scenario
        )
        issue_type = None
        if issue is not None:
            issue_type = "bug" if issue.is_bug else (
                issue.labels[0] if issue.labels else None
            )
        payload = {
            "issue": issue_number,
            "issue_type": issue_type,
            "scenario": scenario,
            "status": report.status,
            "reason": report.reason,
            "next": report.next,
            "target_version": report.target_version,
            "active_branch": report.active_branch,
        }
        self._emit("triage.prechecked", payload, command_id=cmd.command_id, task_id=task_id)
        if report.status == "rejected":
            self._emit(
                "run.completed",
                {"terminal_state": "rejected", "reason": report.reason},
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _do_validate_anchor(self, cmd, state, task_id, reconcile):
        """validate_anchor (SM-01.5/.6/.8, IF-HOTFIX-004): programmatically
        validate the anchor AC set (from Sage outcome or human manual anchor)
        against the referenced versions' acceptance.md. Valid ->
        anchor.validated (ANCHORED, SM-01.5/.8); invalid / NO_ANCHOR ->
        verdict.failed(check="anchor_invalid") (Sage redispatch <=3, SM-01.6;
        NO_ANCHOR parks directly at AWAIT_HUMAN, SM-01.7)."""
        if reconcile and state.hotfix_anchor_validated:
            return
        acs, source, rationale = self._anchor_inputs(state)
        if not acs or source == "no_anchor":
            # NO_ANCHOR: park directly at AWAIT_HUMAN (SM-01.7) — never
            # auto-feature-route (NFR-0100-03). Emit with the budget ceiling
            # so kernel's hotfix failure routing parks immediately.
            self._emit(
                "verdict.failed",
                {
                    "check": "anchor_invalid",
                    "reason": "NO_ANCHOR: Sage found no correlatable AC",
                    "evidence": "no_anchor outcome",
                    "attempt": 3,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
            return
        try:
            refs = parse_anchor_refs(acs)
            ok, missing = validate_anchor_refs(
                refs, paths.projects_dir(self.store.home)
            )
        except ValueError as exc:
            ok, missing = False, [str(exc)]
        if ok:
            self._emit(
                "anchor.validated",
                {
                    "acs": list(acs),
                    "source": source,
                    "attempt": state.current_attempt,
                    "rationale_refs": list(rationale) if rationale else [],
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )
        else:
            # Invalid refs: for manual anchor, park at AWAIT_HUMAN (retryable);
            # for Sage, consume attempt budget (<=3 redispatches).
            attempt = 3 if source == "human" else state.current_attempt + 1
            self._emit(
                "verdict.failed",
                {
                    "check": "anchor_invalid",
                    "reason": "; ".join(missing),
                    "evidence": "; ".join(missing),
                    "attempt": attempt,
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _anchor_inputs(self, state):
        """The anchor set under validation + its provenance: (acs, source,
        rationale_refs). Human manual anchors come from the last
        ``human.anchor`` event; Sage anchors from the last SAGE_TRIAGE
        ``outcome.received``."""
        for ev in reversed(list(self.store.events(self.run_id))):
            inputs = self._anchor_input_from_event(ev)
            if inputs is not None:
                return inputs
        return list(state.hotfix_anchor_acs or []), "sage", []

    def _anchor_input_from_event(self, ev):
        """(acs, source, rationale_refs) for one candidate anchor event, or
        None when the event is not the anchor provenance we seek."""
        if ev.type == "human.anchor":
            if ev.payload.get("mode") == "feature_route":
                return None
            return list(ev.payload.get("acs") or []), "human", []
        if ev.type == "outcome.received" and ev.payload.get("role") == "sage":
            p = ev.payload
            if p.get("outcome") == "no_anchor":
                return [], "no_anchor", []
            return list(p.get("acs") or []), "sage", list(p.get("rationale_refs") or [])
        return None

    def _do_complete_hotfix_entry(self, cmd, state, task_id, reconcile):
        """complete_hotfix_entry (SM-01.10, IF-HOTFIX-005): ANCHORED atomic
        entry completion. Creates fix/{issue} from main (post-release) or the
        active release branch (dev), records baseline.inherited (source
        approval), then stage.entered(M-DESIGN). Fails closed: branch creation
        failure completes the run as rejected (no half-built run)."""
        done = any(
            e.type == "baseline.inherited" for e in self.store.events(self.run_id)
        )
        if reconcile and done:
            return
        issue = state.hotfix_issue
        scenario = state.hotfix_scenario or "post-release"
        target_version = state.hotfix_target_version or ""
        acs = state.hotfix_anchor_acs or []
        try:
            result = complete_hotfix_entry(
                self.repo, self.run_id, issue, scenario,
                target_version, acs,
            )
        except (RuntimeError, ValueError):
            self._emit(
                "run.completed", {"terminal_state": "rejected", "reason": "hotfix entry failed"},
                command_id=cmd.command_id, task_id=task_id,
            )
            return
        self._emit(
            "branch.created",
            {
                "branch_name": result["branch_name"],
                "base": result["base"],
                "commit_sha": result["commit_sha"],
            },
            command_id=cmd.command_id, task_id=task_id,
        )
        self._emit(
            "baseline.inherited",
            {
                "target_version": result["target_version"],
                "baseline_digest": result["baseline_digest"],
                "anchor_acs": result["anchor_acs"],
                "baseline_doc_paths": result["baseline_doc_paths"],
            },
            command_id=cmd.command_id, task_id=task_id,
        )
        self._emit(
            "stage.entered", {"stage": "M-DESIGN"},
            command_id=cmd.command_id, task_id=task_id,
        )

    def _hotfix_boundary_completion(self, state):
        """v0.6 hotfix boundary (FR-0246 / IF-HOTFIX-006): when a hotfix run
        completes at the boundary, restore the suspended run as active by
        checking out its branch (Runtime is the only branch/worktree
        authority). Payload carries restored_active_run / restored_branch."""
        if state.hotfix_issue is None:
            return {"terminal_state": "boundary"}
        payload = {"terminal_state": "boundary"}
        suspended = self._next_suspended_run()
        if suspended is None:
            return payload
        run_id, branch = suspended
        if branch and branch != self._head():
            git(self.repo, "checkout", branch, check=False)
        payload["restored_active_run"] = run_id
        payload["restored_branch"] = branch
        return payload

    def _next_suspended_run(self):
        """The latest non-completed run after this hotfix run (the suspended
        run that resumes active under the single-active-run semantics).
        Returns (run_id, branch) or None."""
        rows = self.store.conn.execute(
            "SELECT run_id FROM runs WHERE status NOT IN ('completed','backlog') "
            "AND stage IS NOT NULL AND run_id != ? ORDER BY updated_ts DESC LIMIT 1",
            (self.run_id,),
        ).fetchall()
        if not rows:
            return None
        rid = rows[0][0]
        branch = _hotfix_run_branch(self.store, rid)
        return rid, branch

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
        self._emit(
            "stage.rolled_back",
            {
                "from_stage": state.stage,
                "to_stage": cmd.params["to_stage"],
                "reason": cmd.params.get("reason", ""),
            },
            command_id=cmd.command_id,
        )

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

    # -- M-TEST handlers (flow.md §9, FR-0030/0050/0070) -----------------------

    def _run_contract_sections(
        self, cmd, state, field: str
    ) -> tuple[list[tuple[str, int, str, str]], str | None]:
        """Execute the contract's ``collect`` or ``run`` command across all
        declared sections. Returns ``(results, error_msg)`` where ``results``
        is a list of ``(section_name, rc, stdout, stderr)`` per section.
        On contract/shlex error ``error_msg`` is set and ``results`` is empty."""
        try:
            contract = load_contract(self.repo)
        except ContractError as exc:
            return [], f"contract error: {exc.reason}"
        sections = [("integration", contract.integration)]
        if contract.e2e is not None:
            sections.append(("e2e", contract.e2e))
        results: list[tuple[str, int, str, str]] = []
        for name, section in sections:
            try:
                argv = shlex.split(getattr(section, field))
            except ValueError as exc:
                return [], f"contract {field} command invalid: {exc}"
            cwd = self.repo / section.cwd if section.cwd != "." else self.repo
            argv = _resolve_contract_argv0(argv, cwd)
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            rc = proc.returncode
            # M-TEST collection: pytest exit_code=5 ("no tests collected")
            # on the e2e section is not a collection failure — a hotfix run
            # with an integration-only delta has no e2e tests. Normalize to
            # 0 so _do_collect_tests does not hard-fail an empty e2e suite
            # (PRISM-V06-R7-01, T-004). Only the collect field is relaxed;
            # the run field still surfaces real e2e failures.
            if (
                field == "collect"
                and name == "e2e"
                and rc == 5
            ):
                rc = 0
            results.append((name, rc, proc.stdout, proc.stderr))
        return results, None

    def _do_collect_tests(self, cmd, state, task_id, reconcile):
        """SM-01.5: Runtime independently collects integration + e2e tests via
        the host project contract's ``collect`` command (architecture.md §3.2).
        Never trusts the Shield self-report."""
        if reconcile and state.test_collected:
            return
        results, error = self._run_contract_sections(cmd, state, "collect")
        if error is not None:
            self._emit(
                "test.collected",
                {"status": "failed", "collected_count": 0, "errors": [error]},
                command_id=cmd.command_id,
            )
            return
        all_stdout = "\n".join(r[2] for r in results)
        any_failed = any(r[1] != 0 for r in results)
        if not any_failed:
            count = _parse_collected_count(all_stdout)
            self._emit(
                "test.collected",
                {"status": "passed", "collected_count": count, "errors": []},
                command_id=cmd.command_id,
            )
        else:
            errors = [r[3].strip() or r[2].strip() for r in results if r[1] != 0]
            self._emit(
                "test.collected",
                {"status": "failed", "collected_count": 0, "errors": errors},
                command_id=cmd.command_id,
            )

    def _red_log_blob_ref(self, payload: dict) -> str | None:
        """B21/#23 (PRISM-B21-R1-01/R1-03): persist the full RED logs and
        return a fixer-usable blobs PATH (the m_impl_runtime convention), or
        None when the best-effort write fails — callers pass it through."""
        ref = self.store.write_audit_blob(payload)
        if not ref:
            return None
        return f".tracks/runtime/blobs/{ref}"

    def _do_run_tests(self, cmd, state, task_id, reconcile):
        """SM-01.9: Runtime independently re-runs integration/e2e via the host
        project contract's ``run`` command and classifies each failure
        (FR-0050). All-legit -> red.validated(valid) -> EXIT; any illegit or
        unexpected pass -> red.validated(invalid) -> DIAGNOSE.

        B21/#23: on failure the full per-section logs are persisted to a
        blob and the verdict carries findings + a blobs PATH (the
        m_impl_runtime fixer-facing convention) — the DIAGNOSE fixer gets
        the actual output, not just classification labels. The all-legit
        path emits without a blob (no orphan writes; DIAGNOSE never runs)."""
        results, error = self._run_contract_sections(cmd, state, "run")
        if error is not None:
            log_ref = self._red_log_blob_ref({"error": error})
            findings = [{"test_id": "*", "classification": "collection_error", "detail": error}]
            self._emit(
                "red.validated",
                {"status": "invalid", "findings": findings, "log_ref": log_ref},
                command_id=cmd.command_id,
            )
            self._emit(
                "verdict.failed",
                {
                    "check": "contract_error",
                    "target_stage": "M-DESIGN",
                    "artifact_disposition": "rollback",
                    "reason": error,
                    "evidence": findings,
                    "log_ref": log_ref,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        findings: list[dict] = []
        all_legit = True
        for name, rc, stdout, stderr in results:
            red_class = classify_red(name, rc, stdout, stderr)
            if red_class not in _LEGIT_RED:
                all_legit = False
            findings.append(
                {
                    "test_id": name,
                    "classification": red_class,
                    "detail": _short_detail(f"{stdout}\n{stderr}"),
                }
            )
        if all_legit:
            self._emit(
                "red.validated",
                {"status": "valid", "findings": findings},
                command_id=cmd.command_id,
            )
            return
        # Invalid Red -> DIAGNOSE: classify the gap and emit verdict.failed.
        log_ref = self._red_log_blob_ref(
            {
                "sections": [
                    {"name": n, "returncode": rc, "stdout": out, "stderr": err}
                    for n, rc, out, err in results
                ]
            }
        )
        self._emit(
            "red.validated",
            {"status": "invalid", "findings": findings, "log_ref": log_ref},
            command_id=cmd.command_id,
        )
        classification = self._diagnose_classification()
        self._emit(
            "verdict.failed",
            {
                "check": classification,
                "target_stage": _DIAGNOSE_TARGET.get(classification, "M-TEST"),
                "artifact_disposition": "rewrite"
                if classification == "test_defect"
                else "rollback",
                "reason": next(
                    (
                        f["classification"]
                        for f in findings
                        if f["classification"] not in _LEGIT_RED
                    ),
                    "invalid",
                ),
                "evidence": findings,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_check_trace(self, cmd, state, task_id, reconcile):
        """SM-01.14 EXIT gate: trac check trace closure, filtered to required
        ACs (integration|e2e layer, FR-0070 decision A / architecture.md §5.1).

        v0.6 hotfix empty-Shield increment (FR-0244-04, IF-HOTFIX-007): when
        the EXIT route carries ``hotfix=True``, emit the increment.declared
        release evidence (shield=empty + delta §8 unit rows) and run the
        plan-level closure (check_hotfix_plan_closure) on the anchor AC set."""
        if reconcile and state.trace_passed:
            return
        from tracks.checks.trace import check_trace_full_file  # lazy: avoid circular import
        from tracks.executor.test_tasks import parse_hotfix_unit_rows

        vdir = self._vdir()
        tests_dir = self.repo / "tests"
        if cmd.params.get("hotfix"):
            plan_path = vdir / "test-plan.md"
            plan_text = (
                plan_path.read_text(encoding="utf-8", errors="replace")
                if plan_path.exists()
                else ""
            )
            unit_rows = parse_hotfix_unit_rows(plan_text)
            report = check_trace_full_file(
                vdir,
                tests_dir,
                hotfix_ctx={
                    "projects_dir": str(paths.projects_dir(self.store.home)),
                    "run_id": self.run_id,
                    "anchor_acs": list(state.hotfix_anchor_acs or []),
                    "declared_unit_rows": unit_rows,
                },
            )
            blocking = list(report.hard_errors)
            self._emit(
                "increment.declared",
                {
                    "shield": "empty",
                    "unit_rows": unit_rows,
                    "trace_status": "fail" if report.status == "fail" else "pass",
                },
                command_id=cmd.command_id,
            )
        else:
            report = check_trace_full_file(vdir, tests_dir)
            required = required_ac_ids(vdir / "acceptance.md", vdir / "test-plan.md")
            blocking = (
                [e for e in report.hard_errors if any(rid in e for rid in required)]
                if required
                else list(report.hard_errors)
            )
        if not blocking:
            self._emit(
                "verdict.passed",
                {"check": "trace", "detail": "trace closure verified"},
                command_id=cmd.command_id,
            )
        else:
            self._emit(
                "verdict.failed",
                {
                    "check": "trace",
                    "reason": "; ".join(blocking),
                    "evidence": "trac check trace",
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )

    def _do_commit_tests(self, cmd, state, task_id, reconcile):
        """SM-01.14: freeze the test asset via a controlled git commit of tests/
        -> test.committed. stage.exited + run.completed are handled by the
        subsequent write_frontmatter command (v0.5 batch 2)."""
        if reconcile and state.test_committed:
            return
        marker = f"command_id: {cmd.command_id}"
        tests_dir = self.repo / "tests"
        if tests_dir.exists():
            git(self.repo, "add", "tests")
        proc = _commit_if_staged(self.repo, f"M-TEST: freeze test assets\n\n{marker}")
        if proc is not None and proc.returncode != 0:
            self._emit_commit_failure(proc, state, cmd.command_id)
            return
        # proc is None when there's nothing new to stage — tests were already
        # committed during the WRITE pipeline. That's OK: emit test.committed
        # pointing at the current HEAD (the existing freeze commit).
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit(
            "test.committed",
            {"commit_sha": commit_sha, "test_count": test_count},
            command_id=cmd.command_id,
        )

    def _diagnose_classification(self) -> str:
        """Read the DIAGNOSE classification from the fake backend's simulate
        token (default ``test_defect``). The real channel dispatches Prism for
        diagnostic review; the fake channel keeps it deterministic."""
        token = getattr(self.backend, "token", lambda *_: "test_defect")(
            "diagnose", "classification", "test_defect"
        )
        return (
            token
            if token in ("test_defect", "stub_gap", "ac_gap", "spec_gap", "impl_defect")
            else "test_defect"
        )

    # -- M-REQ-APPROVAL handlers (FR-0180/0190/0200) ---------------------------

    def _vdir(self) -> Path:
        return paths.version_dir(self.store.home, self.version)

    def _do_generate_preview(self, cmd, state, task_id, reconcile):
        if reconcile and state.preview_ready:
            return
        vdir = self._vdir()
        self._emit(
            "preview.generated",
            {"digest": revision_digest(vdir), "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )

    def _stale_regenerate(self, cmd, approved_digest: str) -> bool:
        """FR-0190 entry gate (D-02/D-03): post-approval commands recompute the
        trio digest; a mismatch means the approval is stale — regenerate the
        preview (back to the human gate) instead of proceeding downstream."""
        vdir = self._vdir()
        current = revision_digest(vdir)
        if current == approved_digest:
            return False
        self._emit(
            "preview.generated",
            {"digest": current, "summary": baseline_summary(vdir)},
            command_id=cmd.command_id,
        )
        return True

    def _do_record_approval(self, cmd, state, task_id, reconcile):
        if reconcile and state.substate == "ISSUES":
            return
        if self._stale_regenerate(cmd, cmd.params["digest"]):
            return
        self._emit(
            "approval.recorded",
            {
                "actor": cmd.params["actor"],
                "digest": cmd.params["digest"],
                "ts": datetime.now(timezone.utc).isoformat(),
                "readonly": True,
            },
            command_id=cmd.command_id,
        )

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
        done = {
            e.payload["item_id"]: e.payload["issue_id"]
            for e in self.store.events(self.run_id)
            if e.type == "issue.created"
        }
        try:
            for item_id, title, body in issue_items(self._vdir(), digest):
                if item_id in done:
                    continue
                issue_id = backend.create_issue(title, body, [self.version])
                backend.add_to_project(issue_id, backend.project)
                done[item_id] = issue_id
                self._emit(
                    "issue.created",
                    {"item_id": item_id, "issue_id": issue_id, "digest": digest},
                    command_id=cmd.command_id,
                )
        except GithubIssuesError as e:
            # NFR-0030 style: no half-written summary; the reducer counts the
            # failed outcome as an attempt (3rd escalates to Human).
            self._emit(
                "outcome.received",
                {
                    "role": "github",
                    "status": "failed",
                    "failure_class": e.classification,
                    "self_report": str(e),
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "issues.created",
            {"digest": digest, "mapping": done, "project": backend.project},
            command_id=cmd.command_id,
        )

    # -- git helpers -----------------------------------------------------------

    def _head(self) -> str:
        return git(self.repo, "symbolic-ref", "--short", "HEAD", check=False).stdout.strip()

    def _teardown_branch(self, branch: str) -> None:
        """FR-09 / reconcile-safe: end with HEAD==main and branch absent."""
        if self._head() != "main":
            git(self.repo, "checkout", "main")
        if git(self.repo, "rev-parse", "--verify", branch, check=False).returncode == 0:
            git(self.repo, "branch", "-D", branch)
