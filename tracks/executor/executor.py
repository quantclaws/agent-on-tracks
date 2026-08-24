"""Command executor: write-ahead `command.issued` (FR-30), per-kind
execute + reconcile (D-13), agent dispatch via the effects backend seam
(NFR-01; ARCH-003 §4), validate pass-through (D-16).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
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
    AdjudicationMarker,
    DocCommentOrigin,
    QuarantinedChange,
    QuarantineDescriptor,
    ResumeDecision,
    classify_design_document_deltas,
    combined_design_identity,
    create_doc_gap_record,
    decide_quarantine_resume,
    legal_anchor_pairs,
    match_adjudication_marker,
    quarantine_authorized_changes,
    scan_adjudication_markers,
)
from tracks.executor.file_identity import path_identity
from tracks.executor.helpers import (
    _DIAGNOSE_TARGET,
    _LEGIT_RED,
    _commit_if_staged,
    _dispatch_payload,
    _hook_output,
    _short_detail,
    classify_red_detail,
    git,
    parse_collected_nodes,
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
from tracks.executor.test_select import (
    BaselineAssets,
    EmptyR2SelectionError,
    JUnitResultError,
    TestSelectError,
    capture_test_baseline,
    classify_nodes,
    collect_node_source_digests,
    make_selection_id,
    parse_junit_result,
    require_exact_node_coverage,
    require_nonempty_r2_selection,
    resolve_selected_command,
)
from tracks.executor.test_select import (
    audit as audit_selection_argv,
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
from tracks.kernel.machine import _REVIEW_SUBSTATE, DESIGN_DOCS, State, decide
from tracks.project import (
    ContractError,
    lint_check_command,
    load_contract,
    validate_layout,
)
from tracks.project import layout_paths as _layout_paths
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

# D-41 (v6 review pin): pytest exit code that means an EMPTY layer at collect
# time is ONLY 5 ("no tests collected"). rc=4 is a usage/path error -- a
# broken declaration that must fail closed naming the layer, never silently
# contribute zero nodes (a fresh feature project legitimately has zero unit/
# e2e nodes via rc=5; an un-collectable layer never does).
_EMPTY_LAYER_COLLECT_RC = frozenset({5})

# D-41 selection scope/basis for M-TEST RED_CHECK (interfaces §1a/§1j).
_R2_SCOPE = "r2_delta"
_R2_BASIS = "delta-declaration"

# Paths excluded from the dirty-aware tree stamp: runtime state and build
# artifacts are not source/test/config content and must never destabilize a
# selection identity across WAL/replay of the same command.
_TREE_STAMP_SKIP_PREFIXES = (
    ".git/",
    ".opencode/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".tracks/",
    ".venv/",
    "build/",
    "dist/",
    "logs/",
)


def _contract_sections(contract):
    """The required flat test layers in canonical order (unit, integration,
    e2e); the activated atomic schema declares all three -- the loader fails
    closed on any missing layer section."""
    return [
        ("unit", contract.unit),
        ("integration", contract.integration),
        ("e2e", contract.e2e),
    ]


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
            # SM-02 doc-gap lifecycle: adjudication ingestion, nested
            # design-revision workflow, thread-resolution resume, and
            # crash-window recovery all run before decide().
            if self._sm02_pre_decide(state):
                continue
            cmd = decide(state)
            if cmd is None:
                return state
            stop, dispatches, bound_substate = self._gate_loop_dispatch(
                cmd, dispatches, bound_substate
            )
            if stop:
                return state
            self._progress(cmd, state)
            self.issue(cmd)
            if self._is_phase_boundary(cmd):
                return self.store.state(self.run_id)

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
            "findings": red.payload.get("findings") or [],
        }
        params["assignment"] = assignment

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
        _is_doc_gap = bool(params.get("doc_gap"))
        if cmd.kind == "dispatch_agent" and not _is_doc_gap:
            self._enrich_dispatch_params(params, state, cid)
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
        # SM-02 design_gap nested workflow (#62 finding 1): doc_gap dispatches
        # (Archer revision, Prism review) route to a dedicated handler that
        # checkpoints the outcome without emitting design.committed/prism.verdict
        # (those clobber origin stage state) and without a Human gate.
        if p.get("doc_gap"):
            self._do_doc_gap_dispatch(cmd, state, task_id, p)
            return
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
        if self._release_empty_hotfix_shield(cmd, state, task_id, role, substate, assignment):
            return
        # D-32: fail-closed M-DESIGN→M-TEST gate. After the persisted
        # assignment.test_tasks enrichment (issue()), an invalid test-task
        # contract must NOT reach the (production or fake) backend: emit a
        # stub_gap failed outcome instead, which the reducer routes straight
        # to DIAGNOSE/stub_gap → rollback M-DESIGN (no attempt, no Human).
        if self._reject_invalid_test_tasks(state, role, substate, assignment, cmd, task_id):
            return
        self._dispatch_agent_backend(cmd, state, task_id, role, substate, doc, doc_path, assignment)

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

    def _valid_hotfix_unit_rows(self, unit_rows: list[dict]) -> bool:
        """Require each empty-Shield row to carry a registered interface."""
        from tracks.executor.test_tasks import _extract_if_registry, resolve_inherited_baseline_docs

        _acc, interfaces = resolve_inherited_baseline_docs(self._vdir() / "test-plan.md")
        if not interfaces.is_file():
            return False
        registry = _extract_if_registry(interfaces.read_text(encoding="utf-8"))
        return bool(registry) and all(
            row.get("if_ids") and set(row["if_ids"]).issubset(registry) for row in unit_rows
        )

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
        # SM-02 (#62 finding 1): when the manifest is absent (M-TEST Shield
        # WRITE has no task-level manifest), fall back to the project
        # contract's [layout.<role>] writable paths so authorized non-doc
        # changes are quarantined and the resume decision can detect
        # design-stale discard after a design revision.
        if not allowed_paths:
            allowed_paths = tuple(_layout_paths(self.repo, origin.role))
        # Collect agent-attributable non-document changes from the dirty
        # snapshot: every file dirty after the dispatch that is not a
        # protected design doc. quarantine_authorized_changes filters by
        # allowed_paths and pre_dirty internally.
        design_docs = set(ROLE_ALLOWED_DOCS.get(origin.role, frozenset()))
        agent_changes: list[QuarantinedChange] = []
        for rel, identity in self._dirty_snapshot().items():
            if rel in design_docs:
                continue
            agent_changes.append(
                QuarantinedChange(
                    path=rel,
                    operation="modify",
                    baseline_identity=None,
                    content_identity=identity,
                )
            )
        descriptor = quarantine_authorized_changes(
            origin=origin,
            allowed_paths=allowed_paths,
            pre_dirty_identities=pre_dirty or {},
            agent_changes=tuple(agent_changes),
            # Design anchor: combined AT-PAUSE identity of the LEGAL
            # discussion documents — the same set the resume decision
            # re-identifies from live bytes (never document_deltas[0],
            # the alphabetically-first protected doc, and never the
            # pre-dispatch baseline, which would count the discussion
            # itself as permanent drift).
            design_identity=combined_design_identity(
                list(legal_anchor_pairs(record.document_deltas))
            ),
            run_identity=self.run_id,
        )
        manifest_ref = self.store.write_audit_blob(
            {
                "quarantine_id": descriptor.quarantine_id,
                "origin": asdict(origin),
                # Identity anchors persisted for the resume decision
                # (decide_quarantine_resume rebuilds from this blob).
                "design_identity": descriptor.design_identity,
                "run_identity": descriptor.run_identity,
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

    # -- SM-02 design_gap nested workflow (#62 finding 1) --------------------

    def _sm02_pre_decide(self, state: State) -> bool:
        """Run the SM-02 doc-gap lifecycle checks before decide().

        Returns True when any check handled the iteration (continue the run
        loop).  Order: adjudication ingestion -> nested design-revision ->
        thread-resolution resume -> crash-window recovery.
        """
        if self._ingest_doc_gap_adjudications(state):
            return True
        if self._advance_doc_gap_design_revision(state):
            return True
        if self._resume_doc_gap_if_ready(state):
            return True
        return bool(self._resume_interrupted_dispatch(state))

    def _advance_doc_gap_design_revision(self, state: State) -> bool:
        """Drive the nested Archer design-revision + Prism review workflow.

        A DESIGN_GAP record whose revision sub-progress is None (just
        adjudicated) or ``design_revised`` (Archer done, Prism pending) gets
        its nested dispatch issued here via WAL ``issue()``.  The dispatch's
        outcome is routed to ``_do_doc_gap_dispatch`` (not the ordinary
        M-DESIGN pipeline) so no ``design.committed`` / ``prism.verdict``
        event clobbers the paused origin stage.  Returns True (continue the
        run loop) when a dispatch was issued.

        WAL-safe recovery (#62 finding 1) is delegated to
        ``_recover_doc_gap_dispatch`` — see its docstring for the crash
        window and the idempotent re-issue contract.
        """
        if state.status != "active" or not state.doc_gaps:
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") != "DESIGN_GAP":
                continue
            revision = rec.get("revision")
            if revision is None:
                self._dispatch_doc_gap_agent(
                    record_id, rec, state, phase="design_revision"
                )
                return True
            if revision == "design_revised":
                self._dispatch_doc_gap_agent(
                    record_id, rec, state, phase="design_review"
                )
                return True
            if revision in ("archer_dispatched", "prism_dispatched"):
                if self._recover_doc_gap_dispatch(record_id, rec, state, revision):
                    return True
                break  # genuinely waiting for the nested outcome
            # prism_reviewed: fall through to the resume check.
            break
        return False

    def _recover_doc_gap_dispatch(
        self, record_id: str, rec: dict, state: State, revision: str
    ) -> bool:
        """WAL-safe recovery for a crash between ``doc_gap.design_dispatched``
        and ``command.issued`` (#62 finding 1).

        ``_dispatch_doc_gap_agent`` persists the audit event BEFORE
        ``issue()`` writes ``command.issued``.  A crash in between leaves
        the record at ``archer_dispatched``/``prism_dispatched`` with a
        persisted ``design_dispatch_id`` but NO WAL command - decide()
        cannot advance (revision != prism_reviewed) and no outcome ever
        arrives, so the record parks permanently.  When the recorded
        ``design_dispatch_id`` was never issued, re-issue the nested
        dispatch with the SAME id (idempotent: an already-issued id is
        skipped by the ``_dispatch_issued`` guard).  No duplicate audit
        event is emitted (the original is already in the WAL).  When the
        persisted id is missing (corrupted WAL), emit a fresh audit event
        with a new id instead.  Returns True when a re-issue was issued;
        False when the dispatch was already issued (wait for the outcome).
        """
        dispatch_id = rec.get("design_dispatch_id")
        if dispatch_id and self._dispatch_issued(dispatch_id):
            return False
        phase = (
            "design_revision"
            if revision == "archer_dispatched"
            else "design_review"
        )
        self._dispatch_doc_gap_agent(
            record_id, rec, state, phase=phase,
            dispatch_id=dispatch_id,
            emit_audit=not bool(dispatch_id),
        )
        return True

    def _dispatch_doc_gap_agent(
        self, record_id: str, rec: dict, state: State, *, phase: str,
        dispatch_id: str | None = None, emit_audit: bool = True,
    ) -> None:
        """Emit the SM-02 dispatch audit event (when ``emit_audit``) and
        issue the nested agent.

        The audit event ``doc_gap.design_dispatched`` is bound to the
        dispatch id via its envelope ``command_id`` so the WAL trail
        explicitly links the audit record to the ``command.issued`` it
        announces (the reducer also stores ``design_dispatch_id`` on the
        record for replay-side verification).

        Crash-window recovery (``emit_audit=False``): re-issue the nested
        dispatch with the SAME persisted ``design_dispatch_id`` after a
        crash between ``doc_gap.design_dispatched`` and ``command.issued``
        left the record parked at ``archer_dispatched``/
        ``prism_dispatched`` with no WAL command.  No duplicate audit
        event is emitted (the original is already in the WAL).
        """
        origin = rec.get("origin") or {}
        document_paths = rec.get("document_paths") or []
        role = "archer" if phase == "design_revision" else "prism"
        substate = "RESPOND" if phase == "design_revision" else "PRISM_REVIEW"
        cid = dispatch_id or new_ulid()
        audit_payload = {
            "record_id": record_id,
            "origin_dispatch_id": origin.get("dispatch_id", ""),
            "dispatch_id": cid,
            "phase": phase,
            "role": role,
            "document_paths": list(document_paths),
        }
        if emit_audit:
            # SM-02 (#62 finding 2): persist a nested Archer pre-dispatch
            # identity snapshot of the design trio so the checkpoint can
            # stage ONLY the design docs whose identity drifted during this
            # dispatch.  The snapshot covers the full DESIGN_DOCS trio (the
            # Archer revises the whole set, not just the adjudicated
            # document_paths); identity comparison attributes changes to the
            # Archer while Human / pre-dirty / unattributed content (unchanged
            # from the snapshot) stays out of the commit (AC-FR0236-01).
            if phase == "design_revision":
                audit_payload["pre_dispatch_identities"] = {
                    name: self._path_identity(self._doc_path(name))
                    for name in DESIGN_DOCS
                }
            self._emit(
                "doc_gap.design_dispatched",
                audit_payload,
                command_id=cid,
            )
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": role,
                "substate": substate,
                "stage": state.stage,
                "doc_gap": {"record_id": record_id, "phase": phase},
                "assignment": {"kind": substate, "doc_gap_revision": True},
            },
        )
        self.issue(cmd, command_id=cid)

    def _do_doc_gap_dispatch(
        self, cmd: Command, state: State, task_id: str | None, p: dict
    ) -> None:
        """Execute a doc-gap nested dispatch and checkpoint its outcome.

        Archer (design_revision): the backend revises the design docs; the
        executor commits the revised trio to git (advancing HEAD so the
        subsequent origin-stage Prism review sees a clean baseline) and
        checkpoints the revised combined design identity
        (``doc_gap.design_revised``) so the resume decision can detect
        design-stale discard.  No ``design.committed`` event is emitted
        (that clobbers origin stage state).  Prism (design_review): the
        backend reviews and the executor emits ``doc_gap.design_reviewed``
        (pass/revise).  Neither path emits ``design.committed`` /
        ``prism.verdict``.

        Fail-closed (#62 finding 1): ``doc_gap.design_revised`` is emitted
        ONLY when (a) the nested Archer outcome succeeded, (b) at least one
        authorized adjudicated design doc in ``document_paths`` changed
        identity during this dispatch, and (c) the git checkpoint commit
        succeeded.  Otherwise no event is emitted and the record stays at
        ``archer_dispatched`` so the resume gate never proceeds (the held
        origin outcome is never resumed on a failed/stale revision).
        """
        gap = p["doc_gap"]
        record_id = gap["record_id"]
        phase = gap["phase"]
        role = p["role"]
        substate = p["substate"]
        assignment = p.get("assignment") or {}
        print(
            f"  [{state.stage}] doc-gap {phase} dispatch {role}/{substate}",
            file=sys.stderr,
            flush=True,
        )
        result = self.backend.act(
            role, substate, None, None, assignment=assignment
        )
        rec = state.doc_gaps.get(record_id) or {}
        document_paths = rec.get("document_paths") or []
        if phase == "design_revision":
            self._checkpoint_doc_gap_revision(
                cmd, task_id, record_id, rec, document_paths, result
            )
        elif phase == "design_review":
            if result.get("status") != "done":
                self._fail_doc_gap_revision(
                    cmd, task_id, record_id, phase,
                    f"nested design review outcome status={result.get('status')!r}",
                )
                return
            verdict = result.get("verdict")
            if verdict not in ("pass", "revise"):
                self._fail_doc_gap_revision(
                    cmd, task_id, record_id, phase,
                    f"invalid nested design review verdict={verdict!r}",
                )
                return
            self._emit(
                "doc_gap.design_reviewed",
                {
                    "record_id": record_id,
                    "verdict": verdict,
                    "review_summary": result.get("review_summary", ""),
                },
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _checkpoint_doc_gap_revision(
        self,
        cmd: Command,
        task_id: str | None,
        record_id: str,
        rec: dict,
        document_paths: list[str],
        result: dict,
    ) -> None:
        """Fail-closed checkpoint of Archer's design revision (#62 finding 1).

        Emits ``doc_gap.design_revised`` ONLY when the nested Archer outcome
        succeeded, at least one adjudicated design doc in ``document_paths``
        changed identity during the dispatch, and the git checkpoint commit
        succeeded.  Otherwise no event is emitted (fail-closed): the record
        parks at ``archer_dispatched`` and the resume gate never proceeds,
        so a failed / no-op / un-attributable revision never resumes the
        held origin outcome.
        """
        phase = (cmd.params.get("doc_gap") or {}).get("phase", "design_revision")
        # (a) nested Archer outcome must be successful.
        if result.get("status") != "done":
            self._fail_doc_gap_revision(
                cmd, task_id, record_id, phase,
                f"nested {phase} outcome status={result.get('status')!r}",
            )
            return
        pre_identities = rec.get("pre_dispatch_identities") or {}
        # (b) at least one authorized adjudicated design doc (a record
        # document_path) changed identity during the dispatch.
        changed_document_paths = [
            name for name in document_paths
            if self._path_identity(self._doc_path(name))
            != pre_identities.get(name)
        ]
        if not changed_document_paths:
            self._fail_doc_gap_revision(
                cmd, task_id, record_id, phase,
                "no adjudicated design document changed",
            )
            return
        # Stage EVERY design doc whose identity drifted from the pre-dispatch
        # snapshot (the Archer revises the design trio; only identity-changed
        # docs are staged so Human / pre-dirty / unattributed content is
        # never committed as Archer output — #62 finding 2, AC-FR0236-01).
        changed_design_docs = [
            name for name in DESIGN_DOCS
            if self._path_identity(self._doc_path(name))
            != pre_identities.get(name)
        ]
        # (c) git checkpoint commit must succeed (raises on failure — never
        # swallowed, never emits success).
        try:
            head = self._commit_doc_gap_revision(changed_design_docs)
        except RuntimeError as exc:
            self._fail_doc_gap_revision(cmd, task_id, record_id, phase, str(exc))
            return
        pairs = [
            (name, self._path_identity(self._doc_path(name)))
            for name in document_paths
        ]
        revised_identity = combined_design_identity(pairs)
        self._emit(
            "doc_gap.design_revised",
            {
                "record_id": record_id,
                "revised_design_identity": revised_identity,
                "changed_paths": changed_design_docs,
                "commit_sha": head,
            },
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _fail_doc_gap_revision(
        self, cmd, task_id, record_id: str, phase: str, reason: str
    ) -> None:
        """Close the nested WAL command without fabricating a revision."""
        self._emit(
            "doc_gap.design_failed",
            {"record_id": record_id, "phase": phase, "reason": reason},
            command_id=cmd.command_id,
            task_id=task_id,
        )

    def _commit_doc_gap_revision(self, changed_paths: list[str]) -> str:
        """Commit ONLY the identity-changed design docs to git.

        SM-02 (#62 finding 2): stages only the ``changed_paths`` (design docs
        whose identity drifted from the nested Archer pre-dispatch snapshot)
        so Human / pre-dirty / unattributed design changes are never swept
        into the Archer commit (AC-FR0236-01 attribution).  Untouched docs
        and non-document dirty content stay uncommitted.

        #62 finding 3: a git commit failure is NOT swallowed (``check=True``
        raises) and the caller never emits ``doc_gap.design_revised`` on
        failure — no success is fabricated from a stale HEAD.
        """
        staged_any = False
        for name in changed_paths:
            path = self._doc_path(name)
            if path.exists():
                git(self.repo, "add", "--", str(path))
                staged_any = True
        if not staged_any:
            raise RuntimeError(
                "doc-gap design revision commit attempted with no staged "
                "design docs (changed_paths resolved to nothing on disk)"
            )
        proc = git(self.repo, "diff", "--cached", "--name-only", check=False)
        if not proc.stdout.strip():
            raise RuntimeError(
                "doc-gap design revision commit attempted with an empty index"
            )
        commit_paths = [
            str(self._doc_path(name).relative_to(self.repo))
            for name in changed_paths
            if self._doc_path(name).exists()
        ]
        git(
            self.repo,
            "commit",
            "--only",
            "-m",
            "doc-gap design revision (SM-02 design_gap nested workflow)",
            "--",
            *commit_paths,
        )
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def _ingest_doc_gap_adjudications(self, state: State) -> bool:
        """SM-02 adjudication ingestion (IF-DOCGAP-001, AC-FR0235-01/02).

        Scans every DETECTED doc-gap record's touched documents for Prism's
        structured SM-02-ADJUDICATION marker reply.  Exactly one valid,
        record-bound marker emits ``doc_comment.adjudicated`` and moves the
        record to DESIGN_GAP/AGENT_CORRECTION.  Anything malformed, ambiguous
        or mismatched stays fail-closed waiting (no event, no state change).
        Only waiting records are consulted, so replays and re-reads of the
        same comment can never re-emit the adjudication.
        """
        if state.status != "active" or not state.doc_gaps:
            return False
        return any(
            self._ingest_record_adjudication(record_id)
            for record_id in sorted(state.doc_gaps)
        )

    def _ingest_record_adjudication(self, record_id: str) -> bool:
        """Ingest one waiting doc-gap record's marker; True when adjudicated."""
        state = self.store.state(self.run_id)
        rec = state.doc_gaps.get(record_id)
        if rec is None or rec.get("state") != "DETECTED":
            return False
        candidates: list[AdjudicationMarker] = []
        errors: list[tuple[str, str]] = []
        for name in rec.get("document_paths") or []:
            path = self._doc_path(name)
            if not path.exists():
                continue
            markers, doc_errors = scan_adjudication_markers(
                path.read_text(encoding="utf-8", errors="replace")
            )
            candidates.extend(markers)
            errors.extend(doc_errors)
        # Relevance filtering (history vs this record) happens inside the
        # matcher, BEFORE any ambiguity decision.
        marker, error = match_adjudication_marker(
            candidates,
            errors,
            quarantine_id=rec.get("quarantine_id"),
            thread_ids=rec.get("thread_ids"),
            origin_role=(rec.get("origin") or {}).get("role"),
        )
        # Fail-closed: a malformed/mismatched marker never drives SM-02; the
        # outcome keeps waiting for a legal adjudication.
        if error is not None or marker is None:
            return False
        origin = rec.get("origin") or {}
        self._emit(
            "doc_comment.adjudicated",
            {
                "record_id": record_id,
                "quarantine_id": rec.get("quarantine_id"),
                "origin_dispatch_id": origin.get("dispatch_id", ""),
                "route": marker.route,
                "responsible_role": marker.responsible_role,
                "thread_ids": list(marker.thread_ids),
                "decision_ref": marker.decision_ref,
            },
        )
        return True

    def _resume_doc_gap_if_ready(self, state: State) -> bool:
        """SM-02.9: resume an adjudicated doc-gap whose threads are resolved.

        Scans adjudicated doc-gap records (DESIGN_GAP / AGENT_CORRECTION —
        both routes converge on thread closure). If every thread_id in the
        record is resolved in the current doc text, emits the
        decide_quarantine_resume decision pair and issues a NEW dispatch for
        the same logical role/task/phase. Open threads stay paused
        (NFR-0090-02 fail-closed). Shield pauses fire in M-TEST WRITE, Devon
        RGR pauses in M-IMPL — both stages resume here.
        """
        if state.stage not in ("M-IMPL", "M-TEST") or state.status != "active":
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") not in ("DESIGN_GAP", "AGENT_CORRECTION"):
                continue
            # SM-02 design_gap nested workflow (#62 finding 1): a DESIGN_GAP
            # record must complete the nested Archer revision + Prism review
            # (revision == "prism_reviewed") before thread resolution may
            # drive the resume - otherwise an Archer revision that resolves
            # threads early would short-circuit past Prism's review.
            if (
                rec.get("state") == "DESIGN_GAP"
                and rec.get("revision") != "prism_reviewed"
            ):
                continue
            if not self._threads_resolved_in_docs(
                rec.get("document_paths") or [], rec.get("thread_ids") or []
            ):
                continue  # open thread — fail-closed: stay paused (NFR-0090-02).
            self._resume_doc_gap_record(record_id, rec)
            return True
        return False

    def _resume_interrupted_dispatch(self, state: State) -> bool:
        """Re-issue a resume decision whose dispatch never got issued.

        The resume sequence (restored/discarded -> resumed -> issue()) spans
        three store writes; a crash in between leaves the record terminal
        (RESTORED/DISCARDED/RESUMED) with the decision identity persisted but
        NO command.issued — decide() would park forever.  When no open WAL
        pending exists and the recorded next_dispatch_id was never issued,
        emit the missing outcome.resumed (audit closure) and issue the
        dispatch with the SAME id (idempotent: an already-issued id is
        skipped, so a completed resume is never duplicated).
        """
        if state.pending is not None or state.status != "active":
            return False
        if state.stage not in ("M-IMPL", "M-TEST"):
            return False
        for record_id, rec in state.doc_gaps.items():
            if rec.get("state") not in ("RESTORED", "DISCARDED", "RESUMED"):
                continue
            next_dispatch_id = rec.get("next_dispatch_id")
            next_attempt = int(rec.get("next_attempt") or 1)
            if not next_dispatch_id or self._dispatch_issued(next_dispatch_id):
                continue
            origin = rec.get("origin") or {}
            if rec.get("state") != "RESUMED":
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
            return True
        return False

    def _dispatch_issued(self, command_id: str) -> bool:
        """True when a ``command.issued`` with this id was already written
        (WAL).

        Checks the ``command.issued`` event type specifically so audit
        events that carry the same command_id (e.g.
        ``doc_gap.design_dispatched`` bound to the dispatch id) do not
        falsely report an issued command — the crash-window recovery
        relies on this to detect a missing WAL command after the audit
        event was persisted.
        """
        return any(
            event.type == "command.issued" and event.command_id == command_id
            for event in self.store.events(self.run_id)
        )

    def _rebuild_quarantine_descriptor(
        self, rec: dict
    ) -> QuarantineDescriptor | None:
        """Rebuild the persisted quarantine manifest blob for the resume call.

        Returns None when no reference is recorded or the blob is unreadable
        (legacy records, evidence filesystem loss) — the caller then decides
        fail-closed from the projected quarantine status alone.
        """
        ref = rec.get("manifest_ref")
        if not ref:
            return None
        try:
            manifest = self.store.load_payload({"$ref": ref})
        except (OSError, ValueError):
            return None
        if not isinstance(manifest, dict):
            return None
        changes = tuple(
            QuarantinedChange(
                path=change.get("path", ""),
                operation=change.get("operation", "modify"),
                baseline_identity=change.get("baseline_identity"),
                content_identity=change.get("content_identity"),
            )
            for change in manifest.get("changes") or []
        )
        status = manifest.get("status")
        if status not in ("empty", "held"):
            status = "held"
        return QuarantineDescriptor(
            quarantine_id=manifest.get("quarantine_id", ""),
            origin=rec.get("origin") or {},
            design_identity=manifest.get("design_identity", ""),
            run_identity=manifest.get("run_identity", ""),
            changes=changes,
            manifest_ref=ref,
            manifest_sha256=ref,
            status=status,
        )

    def _quarantine_resume_decision(
        self, rec: dict, next_dispatch_id: str, next_attempt: int
    ) -> ResumeDecision:
        """decide_quarantine_resume over rebuilt evidence, fail-closed.

        An empty quarantine restores trivially even without readable blob
        evidence; a held quarantine whose manifest cannot be rebuilt discards
        as content_conflict (AC-FR0236-04 fail-closed default).

        #62 finding 4: the ``design_stale`` discard fires ONLY after an actual
        nested Archer design revision (``revised_design_identity`` set on the
        record).  For an AGENT_CORRECTION pause (no Archer revision) the
        adjudication marker Prism appends to the design doc post-pause is
        not a substantive revision - the held agent output is still current
        relative to its task, so the design-drift check is short-circuited
        (the marker's drift must not falsely discard a held quarantine).
        """
        descriptor = self._rebuild_quarantine_descriptor(rec)
        if descriptor is None:
            if rec.get("quarantine_status") == "empty":
                return ResumeDecision(
                    "restore", "empty", next_dispatch_id, next_attempt
                )
            return ResumeDecision(
                "discard", "content_conflict", next_dispatch_id, next_attempt
            )
        document_paths = rec.get("document_paths") or []
        current_pairs = [
            (name, self._path_identity(self._doc_path(name)))
            for name in document_paths
        ]
        current_paths = {
            change.path: self._path_identity(self.repo / change.path)
            for change in descriptor.changes
        }
        # Only an actual nested Archer design revision (revised_design_identity)
        # may discard a held quarantine as design_stale; otherwise the pause-time
        # anchor is the comparison baseline (marker-only drift is ignored).
        if rec.get("revised_design_identity"):
            current_design_identity = combined_design_identity(current_pairs)
        else:
            current_design_identity = descriptor.design_identity
        return decide_quarantine_resume(
            descriptor,
            current_design_identity=current_design_identity,
            current_run_identity=self.run_id,
            current_path_identities=current_paths,
            next_dispatch_id=next_dispatch_id,
            next_attempt=next_attempt,
        )

    def _resume_doc_gap_record(self, record_id: str, rec: dict) -> None:
        """Emit the resume decision pair and issue the NEW dispatch."""
        origin = rec.get("origin") or {}
        next_dispatch_id = new_ulid()
        next_attempt = int(origin.get("attempt", 1)) + 1
        decision = self._quarantine_resume_decision(
            rec, next_dispatch_id, next_attempt
        )
        self._emit(
            "outcome.restored" if decision.action == "restore" else "outcome.discarded",
            {
                "record_id": record_id,
                "quarantine_id": rec.get("quarantine_id") or "",
                "reason": decision.reason,
                "next_dispatch_id": decision.next_dispatch_id,
                "next_attempt": decision.next_attempt,
            },
        )
        self._emit(
            "outcome.resumed",
            {
                "record_id": record_id,
                "origin_dispatch_id": origin.get("dispatch_id", ""),
                "next_dispatch_id": decision.next_dispatch_id,
                "next_attempt": decision.next_attempt,
            },
        )
        self._issue_resume_dispatch(
            origin, decision.next_dispatch_id, decision.next_attempt
        )

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
        # Shield WRITE pauses fire inside M-TEST; Devon RGR phases in M-IMPL.
        # Resuming into the wrong stage would misroute the outcome pipeline.
        stage = "M-TEST" if role == "shield" else "M-IMPL"
        cmd = Command(
            kind="dispatch_agent",
            params={
                "role": role,
                "substate": substate,
                "stage": stage,
                "attempt": attempt,
                "assignment": {"kind": substate, "phase": substate.lower()},
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
        result, replay_error = self._act_in_worktree(
            cmd, role, substate, doc, doc_path, assignment, handle, wt_task_id
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
        if self._checkpoint_eligible(state, result, substate):
            payload["result_checkpoint"] = self._result_checkpoint_payload(
                cmd, state, result, p, substate, role, doc, pre_dirty
            )
        self._emit("outcome.received", payload, command_id=cmd.command_id, task_id=task_id)
        if "result_checkpoint" in payload:
            return  # pipeline drives the domain event
        self._emit_dispatch_verdict(result, role, state, p, cmd, task_id)
        shield_committed = self._emit_shield_commit(result, role, state, cmd, task_id)
        self._maybe_transition_full_ledger(cmd, state, role, result, shield_committed)

    def _act_in_worktree(
        self,
        cmd,
        role,
        substate,
        doc,
        doc_path,
        assignment,
        handle,
        wt_task_id,
    ):
        """Run backend.act() (inside the writer worktree when one is open),
        replay the worktree delta onto the main tree, and always close the
        worktree (audited via ``worktree.closed``). Returns
        ``(result, replay_error)``; replay_error None means clean/applied."""
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
        return result, replay_error

    @staticmethod
    def _checkpoint_eligible(state, result, substate) -> bool:
        """v0.5 ResultCheckpoint pipeline eligibility: batch-1 stages on a
        done outcome for writer/review substates (payload embeds the capture)."""
        return (
            state.stage in ("M-STORY", "M-SPEC", "M-ACC", "M-DESIGN", "M-TEST")
            and result.get("status") == "done"
            and (substate in ("DRAFT", "RESPOND", "WRITE") or substate in _REVIEW_SUBSTATE)
        )

    def _maybe_transition_full_ledger(
        self, cmd, state, role, result, shield_committed
    ) -> None:
        """Full-chain ledger: a done Shield repair under an active full-chain
        round advances CLASSIFIED -> FIXED."""
        if (
            state.stage == "M-IMPL"
            and state.full_chain_round is not None
            and state.substate == "SHIELD_FIX"
            and role == "shield"
            and result.get("status") == "done"
            and shield_committed
        ):
            self._transition_full_ledger(
                cmd,
                "CLASSIFIED",
                "FIXED",
                "Shield repair outcome completed",
                "shield",
            )

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
        if state.full_chain_round is not None:
            self._transition_full_ledger(
                cmd,
                "OPEN",
                "CLASSIFIED",
                f"Prism attributed FULL failure as {classification}",
                "prism",
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
            return False
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
            return False
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
            return False
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
            return False
        commit_sha = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        test_count = sum(1 for _ in tests_dir.rglob("test_*.py")) if tests_dir.exists() else 0
        self._emit(
            "test.committed",
            {"commit_sha": commit_sha, "test_count": test_count},
            command_id=cmd.command_id,
            task_id=task_id,
        )
        return True

    def _reject_invalid_test_tasks(self, state, role, substate, assignment, cmd, task_id) -> bool:
        """D-32 fail-closed gate: return True (and emit a stub_gap failed
        outcome) when an M-TEST Shield WRITE assignment carries an invalid
        test-task contract, so the backend is never called."""
        applies = (
            state.stage in ("M-TEST", "M-IMPL")
            and substate == "WRITE"
            and role == "shield"
        )
        if not applies:
            return False
        valid = valid_test_tasks((assignment or {}).get("test_tasks"))
        if state.hotfix_issue is not None:
            from tracks.executor.test_tasks import _coverage_rows_with_test

            plan = self._vdir() / "test-plan.md"
            rows = _coverage_rows_with_test(
                plan.read_text(encoding="utf-8", errors="replace") if plan.exists() else ""
            )
            valid = valid and {row[0] for row in rows} == set(state.hotfix_anchor_acs or [])
        if valid:
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
            branch = self._hotfix_entry_base()
            if self._restore_hotfix_entry_branch(branch):
                payload["restored_branch"] = branch
            return payload
        run_id, branch = suspended
        if branch and branch != self._head():
            git(self.repo, "checkout", branch, check=False)
        payload["restored_active_run"] = run_id
        payload["restored_branch"] = branch
        return payload

    def _restore_hotfix_entry_branch(self, branch: str | None) -> bool:
        """Restore the entry branch without losing committed delta artifacts."""
        if not branch or branch == self._head():
            return True
        artifacts = {
            path: path.read_bytes()
            for path in self._vdir().rglob("*")
            if path.is_file()
        }
        if git(self.repo, "checkout", branch, check=False).returncode != 0:
            return False
        for path, content in artifacts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return True

    def _hotfix_entry_base(self) -> str | None:
        """Recover the branch active before this hotfix created fix/<issue>."""
        for event in self.store.events(self.run_id):
            if event.type == "branch.created" and event.payload.get("base"):
                return event.payload["base"]
        return None

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
        """Execute the contract's ``collect``/``run`` command across the
        declared Shield layers ([integration] + optional [e2e]) -- the legacy
        M-TEST/M-IMPL section set. Returns ``(results, error_msg)`` where
        ``results`` is a list of ``(section_name, rc, stdout, stderr)`` per
        section. On contract/shlex error ``error_msg`` is set and ``results``
        is empty."""
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
            # 0 for collect only so _do_collect_tests does not hard-fail.
            # (D-41 full-inventory paths use _collect_all_declared_layers,
            # where ONLY rc=5 is a legal empty layer and rc=4 fails closed.)
            if field == "collect" and name == "e2e" and rc == 5:
                rc = 0
            results.append((name, rc, proc.stdout, proc.stderr))
        return results, None

    def _collect_all_declared_layers(
        self, *, capture_absent_ok: bool = False
    ) -> tuple[dict[str, str] | None, str | None]:
        """D-41 FR-0250-01: run EVERY declared layer's ``collect`` command
        ([unit]/[integration]/[e2e]) and map each collected node id to its
        declaring layer. Only rc=5 ("no tests collected") is a legal empty
        declared layer; any other failure (incl. rc=4 usage/path errors) is
        returned as an error string naming the layer (fail-closed). A nodeid
        collected by MULTIPLE layers is a contract defect and fails the scan
        closed -- first-layer-wins masking would silently hide it.

        ``capture_absent_ok=True`` (pre-WRITE capture only, FRB-D): when ALL
        of a section's declared ``paths`` (resolved under the section cwd)
        are absent from disk, the declared layer normalizes to EMPTY without
        executing its collect command -- a valid contract may legitimately
        declare paths a fresh tree does not have yet. The cwd itself must
        exist and be a directory for that normalization: a missing/non-
        directory section cwd is malformed infrastructure and routes a layer
        collection error (baseline_defect at capture) WITHOUT subprocess. If
        ANY declared path exists the command executes and rc=4 stays a
        failure. Post-WRITE COLLECT never normalizes (default False)."""
        try:
            contract = load_contract(self.repo)
            sections = _contract_sections(contract)
        except ContractError as exc:
            return None, f"contract error: {exc.reason}"
        node_layer: dict[str, str] = {}
        errors: list[str] = []
        for name, section in sections:
            fatal_error = self._collect_declared_layer(
                name, section, capture_absent_ok, node_layer, errors
            )
            if fatal_error is not None:
                return None, fatal_error
        if errors:
            return None, "; ".join(errors)
        return node_layer, None

    def _collect_declared_layer(
        self,
        name: str,
        section,
        capture_absent_ok: bool,
        node_layer: dict[str, str],
        errors: list[str],
    ) -> str | None:
        """Collect ONE declared layer into ``node_layer`` (D-41 FR-0250-01).

        Per-layer collection failures accumulate into ``errors`` (fail-closed
        at the caller); a returned string is a FATAL scan error (the layer's
        ``collect`` command itself is unparseable) that aborts immediately."""
        try:
            argv = shlex.split(section.collect)
        except ValueError as exc:
            return f"[{name}].collect command invalid: {exc}"
        cwd = self.repo / section.cwd if section.cwd != "." else self.repo
        if capture_absent_ok and self._capture_layer_absent(name, section, cwd, errors):
            return None
        argv = _resolve_contract_argv0(argv, cwd)
        try:
            proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        except (OSError, UnicodeError) as exc:
            # FRB-G: a missing executable / undecodable stream is a layer
            # collection failure routed through the event channel, never
            # a raw crash out of the handler.
            errors.append(
                f"{name} layer collection failed ({type(exc).__name__}): {exc}"
            )
            return None
        self._absorb_layer_nodes(name, proc, node_layer, errors)
        return None

    def _capture_layer_absent(self, name, section, cwd: Path, errors: list[str]) -> bool:
        """FRB-D pre-WRITE normalization: True when the declared layer must be
        treated as EMPTY without executing its collect command. A missing/non-
        directory section cwd is malformed infrastructure (every declared path
        reads absent as a side effect) -- routes a layer collection error
        WITHOUT any subprocess. If ANY declared path exists the command must
        execute (rc=4 stays a failure)."""
        if not cwd.is_dir():
            errors.append(
                f"{name} layer collection failed: section cwd is "
                f"missing or not a directory: {section.cwd}"
            )
            return True
        return bool(section.paths) and not any((cwd / rel).exists() for rel in section.paths)

    @staticmethod
    def _absorb_layer_nodes(
        name: str, proc: subprocess.CompletedProcess, node_layer: dict[str, str],
        errors: list[str],
    ) -> None:
        """Fold one collect result into ``node_layer``: rc=0 maps each
        collected node to this layer (a nodeid claimed by MULTIPLE layers is a
        contract defect recorded in ``errors``), rc in _EMPTY_LAYER_COLLECT_RC
        is a legal empty declared layer, anything else fails the layer closed."""
        if proc.returncode == 0:
            for node in parse_collected_nodes(proc.stdout):
                previous = node_layer.get(node)
                if previous is not None and previous != name:
                    errors.append(
                        f"{node} collected by multiple layers ({previous}, {name}): "
                        "layer ownership must be unique"
                    )
                    continue
                node_layer[node] = name
        elif proc.returncode not in _EMPTY_LAYER_COLLECT_RC:
            detail = (proc.stderr or proc.stdout).strip()
            errors.append(
                f"{name} layer collection failed (rc={proc.returncode}): {detail[:400]}"
            )

    def _read_runtime_blob(self, ref: str | None):
        """Read a repo-relative ``.tracks/runtime/blobs/...`` reference."""
        if not ref:
            raise TestSelectError("event payload lacks its runtime blob reference")
        try:
            return json.loads((Path(self.repo) / ref).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TestSelectError(f"runtime blob unreadable: {ref}: {exc}") from exc

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

    def _emit_baseline_failed(self, cmd, state, errors: list[str]) -> None:
        """test.baseline_captured(failed) + fail-closed upstream routing
        (interfaces §1j v5: never vacate R1, never re-dispatch Shield)."""
        self._emit(
            "test.baseline_captured",
            {
                "status": "failed",
                "baseline_id": "",
                "baseline_tree": "",
                "layers": ["unit", "integration", "e2e"],
                "nodes_count": 0,
                "empty_baseline": False,
                "node_digest_blob": None,
                "errors": errors,
            },
            command_id=cmd.command_id,
        )
        log_ref = self._red_log_blob_ref({"errors": errors})
        self._emit(
            "verdict.failed",
            {
                "check": "baseline_defect",
                "target_stage": "M-DESIGN",
                "artifact_disposition": "rollback",
                "reason": (
                    "pre-WRITE R1 baseline capture failed: the inherited tree "
                    "is un-collectable/un-importable before Shield writes"
                ),
                "evidence": errors,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_capture_baseline(self, cmd, state, task_id, reconcile):
        """D-41/IF-SELECT-001 (v5): M-TEST entry pre-WRITE R1 snapshot.

        Runs BEFORE this run's first Shield WRITE dispatch: full collect of
        every DECLARED layer ([unit]/[integration]/[e2e]), per-node source
        digests over physical function segments (never whole files), stamped
        baseline/tree identity persisted as ``test.baseline_captured`` plus a
        node-digest blob. Any collection/infrastructure failure fails closed
        before WRITE (baseline_defect -> upstream/design route). WAL/replay
        reuse the same stamped capture: a crashed capture re-runs only while
        no passed capture is persisted, and recomputes the identical identity.
        """
        if reconcile and state.baseline_captured:
            return
        node_layer, collect_error = self._collect_all_declared_layers(
            capture_absent_ok=True
        )
        if collect_error is not None:
            self._emit_baseline_failed(cmd, state, [collect_error])
            return
        all_nodes = sorted(node_layer)
        try:
            digests = collect_node_source_digests(Path(self.repo), all_nodes)
        except TestSelectError as exc:
            self._emit_baseline_failed(cmd, state, [str(exc)])
            return
        # FRB-E: the baseline tree identity is dirty-aware -- a clean relevant
        # tree stamps HEAD, an uncommitted source/test tree stamps head+dirty
        # content so two distinct uncommitted trees never share one snapshot.
        tree_identity = self._dirty_tree_stamp()
        snapshot = capture_test_baseline(
            lambda layer: sorted(n for n in all_nodes if node_layer[n] == layer),
            lambda node: digests[node],
            tree_identity,
        )
        # Blob entries carry BOTH identities: `digest` stamps the node's
        # physical file content (externally verifiable), `node_digest` is the
        # per-node AST segment digest classification consumes.
        file_digests: dict[str, str] = {}
        entries = []
        for node in all_nodes:
            physical = node.partition("::")[0]
            if physical not in file_digests:
                try:
                    file_digests[physical] = hashlib.sha256(
                        (Path(self.repo) / physical).read_bytes()
                    ).hexdigest()
                except OSError:
                    file_digests[physical] = "unreadable"
            entries.append(
                {
                    "node": node,
                    "layer": node_layer[node],
                    "digest": file_digests[physical],
                    "node_digest": digests[node],
                }
            )
        blob_ref = self.store.write_audit_blob(entries)
        if blob_ref is None:
            self._emit_baseline_failed(cmd, state, ["node digest blob write failed"])
            return
        self._emit(
            "test.baseline_captured",
            {
                "status": "passed",
                "baseline_id": snapshot.baseline_id,
                "baseline_tree": snapshot.baseline_tree,
                "layers": list(snapshot.layers),
                "nodes_count": len(snapshot.node_digests),
                "empty_baseline": snapshot.empty_baseline,
                "node_digest_blob": f".tracks/runtime/blobs/{blob_ref}",
                "errors": [],
            },
            command_id=cmd.command_id,
        )

    def _do_collect_tests(self, cmd, state, task_id, reconcile):
        """SM-01.5 / D-41 FR-0250-01: Runtime independently collects ALL
        declared layers via the host project contract's ``collect`` command
        (architecture.md §3.2; never trusts the Shield self-report), then
        classifies the current three-layer inventory against THIS run's
        persisted pre-WRITE ``test.baseline_captured`` snapshot.

        REMOVED (a baseline node absent from full collect) is fail-closed test-
        asset deletion: test.collected(failed, error_class=asset_deleted);
        it never silently de-registers nor continues gating. The per-node
        {node, layer, class} table persists to a blob referenced by
        ``per_node_blob`` for RED_CHECK's SELECT_R2 binding."""
        if reconcile and state.test_collected:
            return
        node_layer, collect_error = self._collect_all_declared_layers()
        if collect_error is not None:
            self._emit(
                "test.collected",
                {"status": "failed", "collected_count": 0, "errors": [collect_error]},
                command_id=cmd.command_id,
            )
            return
        all_nodes = sorted(node_layer)
        baseline_ev = self._latest_event("test.baseline_captured", status="passed")
        if baseline_ev is None:
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": [
                        "missing test.baseline_captured(passed): classification has "
                        "no pre-WRITE R1 snapshot (MissingBaselineCaptureError)"
                    ],
                },
                command_id=cmd.command_id,
            )
            return
        try:
            baseline_entries = self._read_runtime_blob(
                baseline_ev.payload.get("node_digest_blob")
            )
            baseline_assets = BaselineAssets(
                nodes=frozenset(entry["node"] for entry in baseline_entries),
                node_digests={entry["node"]: entry["node_digest"] for entry in baseline_entries},
            )
            digests = collect_node_source_digests(Path(self.repo), all_nodes)
        except (TestSelectError, OSError, ValueError, KeyError, TypeError) as exc:
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": [str(exc)],
                },
                command_id=cmd.command_id,
            )
            return
        classes = classify_nodes(baseline_assets, all_nodes, digests)
        removed = sorted(node for node, klass in classes.items() if klass == "removed")
        if removed:
            failures = [{"node": node, "error_class": "asset_deleted"} for node in removed]
            errors = [
                f"test asset deleted since baseline snapshot: {node}" for node in removed
            ]
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "removed": len(removed),
                    "failures": failures,
                    "errors": errors,
                    # FRB-K2 pairing marker: the companion verdict.failed
                    # (test_defect) emitted below by THIS command is the
                    # pair's single attempt charge; the kernel skips the
                    # collect-side consume for marked payloads only.
                    "companion": "test_defect",
                },
                command_id=cmd.command_id,
            )
            # FA-3 (final review pin): the failed collect alone leaves the
            # router without actionable evidence (blind re-dispatch); emit
            # the test_defect verdict naming every deleted asset BEFORE any
            # Shield rewrite so upstream routes a pinpointed rewrite.
            log_ref = self._red_log_blob_ref({"errors": errors, "failures": failures})
            self._emit(
                "verdict.failed",
                {
                    "check": "test_defect",
                    "target_stage": "M-TEST",
                    "artifact_disposition": "rewrite",
                    "reason": (
                        "REMOVED test assets deleted since the pre-WRITE baseline "
                        f"snapshot: {', '.join(removed)}"
                    ),
                    "evidence": failures,
                    "log_ref": log_ref,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        inherited_r1 = sum(1 for klass in classes.values() if klass == "r1")
        delta_r2 = sum(1 for klass in classes.values() if klass == "r2")
        per_node = [
            {"node": node, "layer": node_layer.get(node, ""), "class": classes[node]}
            for node in all_nodes
        ]
        per_ref = self.store.write_audit_blob(per_node)
        if per_ref is None:
            # Fail closed: a passed classification whose per_node_blob is null
            # has no replayable evidence and RED_CHECK would have no
            # authoritative input (review pin: never passed + null ref).
            self._emit(
                "test.collected",
                {
                    "status": "failed",
                    "collected_count": len(all_nodes),
                    "failures": [],
                    "errors": ["per-node classification blob write failed"],
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "test.collected",
            {
                "status": "passed",
                "collected_count": len(all_nodes),
                "inherited_r1": inherited_r1,
                "delta_r2": delta_r2,
                "removed": 0,
                "failures": [],
                "per_node_blob": f".tracks/runtime/blobs/{per_ref}",
                "errors": [],
            },
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

    def _selection_context(self):
        """Load RED_CHECK's selection inputs from persisted events (D-41).

        Returns ``(baseline_id, selected_by_layer, selected_all)`` where the
        R2 nodes come from COLLECT's persisted per-node classification blob
        keyed by their declared layer. Raises TestSelectError when this run
        has no persisted passed baseline capture / classification (fail-closed;
        never re-guesses from the mutable tree)."""
        collected_ev = self._latest_event("test.collected", status="passed")
        baseline_ev = self._latest_event("test.baseline_captured", status="passed")
        if collected_ev is None or baseline_ev is None:
            raise TestSelectError(
                "RED_CHECK lacks a persisted COLLECT classification / pre-WRITE "
                "test.baseline_captured snapshot"
            )
        per_node = self._read_runtime_blob(collected_ev.payload.get("per_node_blob"))
        selected_by_layer: dict[str, list[str]] = {}
        for entry in per_node:
            if entry.get("class") == "r2":
                selected_by_layer.setdefault(entry.get("layer", ""), []).append(entry["node"])
        for nodes in selected_by_layer.values():
            nodes.sort()
        selected_all = sorted(
            node for nodes in selected_by_layer.values() for node in nodes
        )
        return baseline_ev.payload.get("baseline_id"), selected_by_layer, selected_all

    def _result_staging_path(self, command_id: str, section: str) -> Path:
        """Runtime-provided unique writable ``{result}`` JUnit XML path.

        Lives in system temp staging (per run/command/layer), never inside the
        repo tree, so it enters no tree identity and no write attribution."""
        base = Path(tempfile.gettempdir()) / "tracks-results" / self.run_id
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{command_id}-{section}.xml"

    def _has_persisted_unit_only_increment(self) -> bool:
        """Review-6: the empty-R2 hotfix bypass requires an explicit PERSISTED
        ``increment.declared(shield=empty, unit_rows nonempty)`` fact in this
        run's event stream -- never merely ``hotfix_issue is not None`` (a
        bare hotfix issue carries no increment to release)."""
        for ev in self.store.events(self.run_id):
            if ev.type != "increment.declared":
                continue
            payload = ev.payload or {}
            if payload.get("shield") == "empty" and payload.get("unit_rows"):
                return True
        return False

    def _dirty_tree_stamp(self, root: Path | None = None) -> str:
        """Dirty-aware worktree content stamp over relevant source/test/config
        files (review pin: selection identity must move when dirty R2 content
        changes under an identical node set and HEAD).

        Clean relevant tree -> the HEAD commit identity; dirty -> a sha256
        over HEAD plus the changed paths' current content. Porcelain rename
        pairs (``R  old -> new``, FRB-E) are keyed by their DESTINATION and
        hash the destination file's live content, so mutating a renamed file
        moves the stamp. Runtime state (.tracks/, caches, venvs, build
        output) never enters the stamp: it must stay deterministic across
        WAL/replay of the same command."""
        repo = Path(root) if root is not None else Path(self.repo)
        head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
        status = git(repo, "status", "--porcelain", "-uall", check=False).stdout
        dirty: dict[str, str] = {}
        for line in status.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]  # FRB-E: hash the rename dest
            path = path.strip().strip('"')
            if (
                not path
                or path.startswith(_TREE_STAMP_SKIP_PREFIXES)
                or "__pycache__/" in path
                or Path(path).name.startswith(".coverage")
            ):
                continue
            try:
                digest = hashlib.sha256(
                    (repo / path).read_bytes()
                ).hexdigest()
            except OSError:
                digest = "unreadable"
            dirty[path] = digest
        if not dirty:
            return head  # clean relevant tree -> plain HEAD identity
        canonical = json.dumps(
            {"head": head, "dirty": dirty},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _emit_contract_error_red(self, cmd, state, reason: str) -> None:
        """Fail-closed contract_error channel for unusable results/inputs."""
        findings = [{"test_id": "*", "classification": "collection_error", "detail": reason}]
        log_ref = self._red_log_blob_ref({"error": reason})
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
                "reason": reason,
                "evidence": findings,
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_run_tests(self, cmd, state, task_id, reconcile):
        """SM-01.9 / D-41 FR-0250-02 RED_CHECK (v3 timing + v6 result channel).

        SELECT_R2 against THIS run's persisted pre-WRITE snapshot -> emit
        ``test.selected(scope=r2_delta)`` BEFORE executing -> execute ONLY the
        selected nodes through each layer's contract ``run_selected`` template
        ({nodes}/{result} substituted; concurrency/junit flags are
        contract-owned, Runtime injects nothing) -> parse the strict JUnit XML
        result and require exact node coverage -> classify legal Red per node.
        Never executes a full ``run`` nor any historical/R1 node. Feature
        empty-R2 fails closed (check=empty_r2); the hotfix explicit unit-only
        increment bypass is preserved (FR-0244). All-legit -> red.validated(valid)
        -> PRISM_REVIEW; illegit/unexpected pass -> red.validated(invalid) ->
        DIAGNOSE. stdout/stderr are logs only -- the {result} JUnit record is
        the sole per-node authority."""
        if reconcile and state.red_validated:
            return
        try:
            contract = load_contract(self.repo)
            sections = _contract_sections(contract)
            baseline_id, selected_by_layer, selected_all = self._selection_context()
            require_nonempty_r2_selection(
                _R2_SCOPE,
                selected_all,
                allow_explicit_unit_increment=self._has_persisted_unit_only_increment(),
            )
        except EmptyR2SelectionError:
            self._emit_empty_r2_failure(cmd, state)
            return
        except (ContractError, TestSelectError) as exc:
            reason = f"contract error: {exc.reason}" if isinstance(exc, ContractError) else str(exc)
            self._emit_contract_error_red(cmd, state, reason)
            return

        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        # Review-5: dirty worktree content is stamped separately from commit
        # so two selections over the same node set + HEAD with different
        # uncommitted R2 content never share a selection identity.
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=selected_all,
            scope=_R2_SCOPE,
            basis=_R2_BASIS,
            baseline=str(baseline_id or ""),
            commit=commit,
            tree_stamp=tree_stamp,
        )
        # FA-5 (final review pin): a crash/reconcile replay of a command whose
        # test.selected is already persisted must compare the CURRENT recomputed
        # identity against the persisted record. A mismatch means the worktree/
        # baseline moved under the old selection identity -- fail closed WITHOUT
        # executing (evidence-integrity hole); an exact match resumes normally.
        persisted_selection = next(
            (
                ev.payload
                for ev in self.store.events(self.run_id)
                if ev.type == "test.selected" and ev.command_id == cmd.command_id
            ),
            None,
        )
        stale_keys = self._stale_selection_fields(
            persisted_selection, selection_id, tree_stamp, baseline_id, selected_all
        )
        if stale_keys:
            self._emit_stale_selection_failure(
                cmd,
                state,
                persisted_selection,
                stale_keys,
                selection_id,
                tree_stamp,
                baseline_id,
                selected_all,
            )
            return
        nodes_blob = self._persist_selection_nodes(
            cmd, state, selected_by_layer, selected_all
        )
        if nodes_blob is None:
            return
        # WAL/reconcile reuse the SAME stamped selection: re-issue only when
        # this command has not already emitted it (deterministic identity).
        if persisted_selection is None:
            self._emit_test_selected(
                cmd, baseline_id, selected_all, nodes_blob, commit, tree_stamp, selection_id
            )

        if not selected_all:
            self._emit_unit_only_increment_valid(cmd, selection_id, nodes_blob)
            return

        self._execute_red_check(
            cmd, state, sections, selected_by_layer, selection_id, nodes_blob
        )

    def _emit_empty_r2_failure(self, cmd, state) -> None:
        """Feature M-TEST empty R2 -> fail closed (check=empty_r2): a vacuous
        pass is refused; hotfix must declare its unit-only increment.

        Before blaming the author, screen for collect-pipeline blindness
        (operator finding 2026-08-24, run 01M0S0FQ): committed test artifacts
        that the collector never saw indicate a runtime/contract defect
        (check=collect_defect) -- no attempt charge, operator escalation,
        preserved checkpointed work."""
        defect = self._collect_defect_evidence()
        if defect is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "collect_defect",
                    "reason": defect,
                    "evidence": [],
                    "target_stage": "M-TEST",
                },
                command_id=cmd.command_id,
            )
            return
        self._emit(
            "verdict.failed",
            {
                "check": "empty_r2",
                "reason": (
                    "feature M-TEST produced an empty R2 selection against "
                    "the pre-WRITE snapshot: a vacuous pass is refused "
                    "(hotfix must declare its unit-only increment explicitly)"
                ),
                "evidence": [],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _collect_defect_evidence(self) -> str | None:
        """Infra-side collect blindness signatures for an empty-R2 gate hit.

        Signature A (total blindness): the latest persisted baseline AND the
        latest full collect both saw ZERO nodes while the contract's declared
        layer paths contain python test modules -- the collector parsed
        nothing (incident: pytest 9.1 quiet collect emits per-file counts
        without ``::`` node ids; the runtime parser keyed on ``::`` lines).

        Signature B (new-file blindness): test modules added/changed by this
        run's checkpointed Shield commit (test.written -> result.checkpointed
        base..commit diff) have NO collected node carrying that file prefix
        -- the collector never saw the new file at all.

        Returns a human-readable reason when either signature holds (the
        gate is a collect/contract defect, not the author's), else None.
        """
        layer_paths = self._declared_layer_paths()
        collected = self._latest_event("test.collected")
        baseline = self._latest_event("test.baseline_captured", status="passed")
        reason = self._total_blindness_reason(collected, baseline, layer_paths)
        if reason is not None:
            return reason
        return self._new_file_blindness_reason(collected, layer_paths)

    def _declared_layer_paths(self) -> list[str]:
        """Repo-relative layer path prefixes declared by the host contract."""
        try:
            sections = _contract_sections(load_contract(self.repo))
        except ContractError:
            sections = []
        layer_paths: list[str] = []
        for _name, sec in sections:
            if sec is None:
                continue
            declared = getattr(sec, "paths", None)
            if declared is None and hasattr(sec, "get"):
                declared = sec.get("paths")
            layer_paths.extend(
                rel for rel in declared or [] if isinstance(rel, str) and rel
            )
        return layer_paths

    def _total_blindness_reason(self, collected, baseline, layer_paths) -> str | None:
        """Signature A: zero-node baseline + zero-node collect while the
        declared layer dirs demonstrably contain test modules."""
        if collected is None or baseline is None:
            return None
        if collected.payload.get("collected_count") != 0:
            return None
        if baseline.payload.get("nodes_count") != 0:
            return None
        has_modules = any(
            (Path(self.repo) / rel).is_dir()
            and any((Path(self.repo) / rel).rglob("test_*.py"))
            for rel in layer_paths
        )
        if not has_modules:
            return None
        return (
            "collect pipeline blindness: baseline and full collect both "
            "parsed 0 nodes while declared layer paths contain test "
            "modules (suspect collector output format / parser mismatch, "
            "e.g. pytest 9.1 quiet collect per-file counts without "
            "node ids); Shield's committed artifacts were never seen"
        )

    def _new_file_blindness_reason(self, collected, layer_paths) -> str | None:
        """Signature B: checkpointed new/changed test modules absent from the
        collected node inventory entirely (file prefix never collected)."""
        written = self._latest_event("test.written")
        if collected is None or written is None:
            return None
        result_id = written.payload.get("result_id")
        commit = written.payload.get("commit_sha")
        base = self._checkpoint_base_sha(result_id)
        if not (base and commit and base != commit):
            return None
        test_modules = self._changed_test_modules(base, commit, layer_paths)
        if not test_modules:
            return None
        prefixes = self._collected_prefixes(collected)
        if prefixes is None:
            return None
        missing = [m for m in test_modules if m not in prefixes]
        if not missing:
            return None
        return (
            "collect pipeline blindness: checkpointed test modules have "
            f"zero collected nodes: {', '.join(missing[:5])} "
            "(collector never saw files the author committed)"
        )

    def _checkpoint_base_sha(self, result_id) -> str | None:
        """base_sha of the result.checkpointed event for ``result_id``."""
        for ev in self.store.events(self.run_id):
            if (
                ev.type == "result.checkpointed"
                and ev.payload.get("result_id") == result_id
                and ev.payload.get("base_sha")
            ):
                return ev.payload.get("base_sha")
        return None

    def _changed_test_modules(self, base, commit, layer_paths) -> list[str]:
        """Test modules changed in base..commit under declared layer paths."""
        try:
            proc = git(
                self.repo, "diff", "--name-only", f"{base}..{commit}", check=False
            )
            changed = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception:
            return []
        return sorted(
            ln
            for ln in changed
            if ln.endswith(".py")
            and any(
                ln == rel or ln.startswith(rel.rstrip("/") + "/")
                for rel in layer_paths
            )
        )

    def _collected_prefixes(self, collected) -> set[str] | None:
        """File-prefix set of the latest collect's per-node blob (None when
        the blob is unreadable -- the caller treats that as no signal)."""
        try:
            per_node = self._read_runtime_blob(collected.payload.get("per_node_blob"))
        except TestSelectError:
            return None
        return {(entry.get("node") or "").partition("::")[0] for entry in per_node}

    @staticmethod
    def _stale_selection_fields(
        persisted_selection,
        selection_id: str,
        tree_stamp: str,
        baseline_id,
        selected_all,
    ) -> list[str]:
        """FA-5: the persisted test.selected identity fields that diverge from
        the CURRENT recomputed identity (empty when no record or exact match)."""
        if persisted_selection is None:
            return []
        stale_keys = [
            key
            for key in ("selection_id", "tree_stamp")
            if persisted_selection.get(key) != {
                "selection_id": selection_id,
                "tree_stamp": tree_stamp,
            }[key]
        ]
        if persisted_selection.get("baseline") != str(baseline_id or ""):
            stale_keys.append("baseline")
        if list(persisted_selection.get("nodes") or []) != list(selected_all):
            stale_keys.append("nodes")
        return stale_keys

    def _emit_stale_selection_failure(
        self,
        cmd,
        state,
        persisted_selection,
        stale_keys: list[str],
        selection_id: str,
        tree_stamp: str,
        baseline_id,
        selected_all,
    ) -> None:
        """Refuse to execute against a moved tree under a stale selection
        identity (FA-5 evidence-integrity pin)."""
        log_ref = self._red_log_blob_ref({"stale_fields": stale_keys})
        self._emit(
            "verdict.failed",
            {
                "check": "stale",
                "target_stage": "M-TEST",
                "artifact_disposition": "rollback",
                "reason": (
                    "replayed command's persisted test.selected identity "
                    f"diverged on {', '.join(stale_keys)}: refusing to "
                    "execute against a moved tree under the stale "
                    "selection identity"
                ),
                "evidence": {
                    "stale_fields": stale_keys,
                    "persisted": {
                        key: persisted_selection.get(key)
                        for key in (
                            "selection_id",
                            "tree_stamp",
                            "baseline",
                            "nodes",
                        )
                    },
                    "recomputed": {
                        "selection_id": selection_id,
                        "tree_stamp": tree_stamp,
                        "baseline": str(baseline_id or ""),
                        "nodes": list(selected_all),
                    },
                },
                "log_ref": log_ref,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _persist_selection_nodes(
        self, cmd, state, selected_by_layer, selected_all
    ) -> str | None:
        """Write the per-node layer map blob for this selection; None (after
        emitting contract_error) when the blob write fails (fail closed)."""
        sel_blob = self.store.write_audit_blob(
            [
                {
                    "node": node,
                    "layer": next(
                        (lyr for lyr, ns in selected_by_layer.items() if node in ns), ""
                    ),
                }
                for node in selected_all
            ]
        )
        if sel_blob is None:
            self._emit_contract_error_red(
                cmd, state, "RED_CHECK selection nodes blob write failed"
            )
            return None
        return f".tracks/runtime/blobs/{sel_blob}"

    def _emit_test_selected(
        self, cmd, baseline_id, selected_all, nodes_blob, commit, tree_stamp, selection_id
    ) -> None:
        self._emit(
            "test.selected",
            {
                "scope": _R2_SCOPE,
                "basis": _R2_BASIS,
                "nodes_count": len(selected_all),
                "nodes": list(selected_all),
                "nodes_blob": nodes_blob,
                "baseline": str(baseline_id or ""),
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": None,
                "task_ifs": None,
            },
            command_id=cmd.command_id,
        )

    def _emit_unit_only_increment_valid(self, cmd, selection_id, nodes_blob) -> None:
        """Hotfix explicit unit-only increment bypass: empty selection with
        a declared increment releases without any execution (FR-0244)."""
        self._emit(
            "red.validated",
            {
                "status": "valid",
                "findings": [],
                "basis": "unit-only hotfix increment",
                "selection_id": selection_id,
                "nodes_blob": nodes_blob,
            },
            command_id=cmd.command_id,
        )

    def _execute_red_check(
        self, cmd, state, sections, selected_by_layer, selection_id, nodes_blob
    ) -> None:
        """Execute the selection, then classify legal Red per node: all-legit
        -> red.validated(valid) -> PRISM_REVIEW; illegit/unexpected pass ->
        red.validated(invalid) -> DIAGNOSE."""
        logs: dict[str, dict] = {}
        outcomes, findings, all_legit, error = self._execute_selected_layers(
            cmd, sections, selected_by_layer, logs
        )
        if error is not None:
            self._emit_contract_error_red(cmd, state, error)
            return
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            # Review-8: the normalized per-node outcomes table is the evidence
            # red.validated binds to -- never emit a valid verdict carrying a
            # null outcomes reference (fail closed via contract_error).
            self._emit_contract_error_red(
                cmd, state, "RED_CHECK outcomes evidence blob write failed"
            )
            return
        outcomes_ref_path = f".tracks/runtime/blobs/{outcomes_ref}"
        if all_legit:
            self._emit(
                "red.validated",
                {
                    "status": "valid",
                    "findings": findings,
                    "selection_id": selection_id,
                    "nodes_blob": nodes_blob,
                    "outcomes_ref": outcomes_ref_path,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit_red_check_invalid(
            cmd, state, findings, logs, outcomes, selection_id, nodes_blob, outcomes_ref_path
        )

    def _execute_selected_layers(
        self, cmd, sections, selected_by_layer, logs: dict[str, dict]
    ) -> tuple[list[dict], list[dict], bool, str | None]:
        """Run ONLY the selected nodes through each layer's contract
        ``run_selected`` template ({nodes}/{result} substituted; concurrency/
        junit flags are contract-owned, Runtime injects nothing).

        Returns ``(outcomes, findings, all_legit, error_reason)``; on a
        JUnit/contract/OS failure ``error_reason`` is set (FRB-G) while the
        staged per-command result files are still cleaned up (FA-4)."""
        outcomes: list[dict] = []
        findings: list[dict] = []
        all_legit = True
        staged_results: list[Path] = []
        try:
            for name, section in sections:
                nodes = selected_by_layer.get(name) or []
                if not nodes:
                    # A declared layer without R2 delta never executes here:
                    # M-TEST runs neither the full run command nor R1 history.
                    continue
                cwd = self.repo / section.cwd if section.cwd != "." else self.repo
                result_path = self._result_staging_path(cmd.command_id, name)
                staged_results.append(result_path)
                # Review-4: pre-existing XML at this command/layer path is a
                # previous attempt's record -- poison, not evidence. Unlink it
                # so a command writing no fresh result fails closed on the
                # missing file instead of reusing stale records.
                result_path.unlink(missing_ok=True)
                argv = resolve_selected_command(
                    section.run_selected, nodes, str(result_path), cwd
                )
                if not audit_selection_argv(
                    section.run_selected, nodes, str(result_path), list(argv), cwd
                ):
                    raise TestSelectError(
                        f"[{name}] executed argv diverges from the contract's "
                        "run_selected expansion (injected argument)"
                    )
                proc = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True)
                logs[name] = {
                    "returncode": proc.returncode,
                    "command_echo": list(argv),
                    "stdout": proc.stdout[-8000:],
                    "stderr": proc.stderr[-8000:],
                }
                cases = parse_junit_result(result_path)
                mapping = require_exact_node_coverage(cases, nodes)
                if not self._record_layer_outcomes(nodes, mapping, outcomes, findings):
                    all_legit = False
        except (JUnitResultError, TestSelectError, OSError, UnicodeError) as exc:
            # FRB-G: a missing run_selected executable (OSError) or an
            # undecodable output stream routes the contract_error channel
            # (red.validated invalid + verdict.failed), never a raw crash.
            return [], [], False, f"{type(exc).__name__}: {exc}"
        finally:
            # FA-4 (final review pin): the per-command staging XML is consumed
            # evidence, not residue -- unlink it after a normal parse success
            # AND after every handled failure so one attempt's record never
            # leaks into the next; then remove the per-run staging directory
            # when it is empty ("when possible": a non-empty dir keeps other
            # commands' live results and stays).
            for staged in staged_results:
                staged.unlink(missing_ok=True)
            if staged_results:
                with contextlib.suppress(OSError):
                    staged_results[0].parent.rmdir()
        return outcomes, findings, all_legit, None

    @staticmethod
    def _record_layer_outcomes(nodes, mapping, outcomes: list[dict], findings: list[dict]) -> bool:
        """Append one normalized outcome + finding per selected node (the
        {result} JUnit record is the sole per-node authority; stdout/stderr
        are logs only). Returns True when every node classified as legal Red;
        an unexpected pass/skip classifies unexpected_pass."""
        layer_legit = True
        for node in nodes:
            case = mapping[node]
            klass = (
                "unexpected_pass"
                if case.status in ("passed", "skipped")
                else classify_red_detail(case.detail or "", case.status)
            )
            if klass not in _LEGIT_RED:
                layer_legit = False
            outcomes.append(
                {
                    "node": node,
                    "status": case.status,
                    "classification": klass,
                    "detail": case.detail or "",
                }
            )
            findings.append(
                {
                    "test_id": node,
                    "classification": klass,
                    "detail": _short_detail(case.detail or ""),
                }
            )
        return layer_legit

    def _emit_red_check_invalid(
        self,
        cmd,
        state,
        findings: list[dict],
        logs: dict[str, dict],
        outcomes: list[dict],
        selection_id: str,
        nodes_blob: str,
        outcomes_ref_path: str,
    ) -> None:
        """Invalid Red -> DIAGNOSE: classify the gap and emit verdict.failed."""
        log_ref = self._red_log_blob_ref({"sections": logs, "outcomes": outcomes})
        self._emit(
            "red.validated",
            {
                "status": "invalid",
                "findings": findings,
                "log_ref": log_ref,
                "selection_id": selection_id,
                "nodes_blob": nodes_blob,
                "outcomes_ref": outcomes_ref_path,
            },
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
            if report.status == "pass":
                self._emit(
                    "increment.declared",
                    {"shield": "empty", "unit_rows": unit_rows, "trace_status": "pass"},
                    command_id=cmd.command_id,
                )
        else:
            # IF-HOTFIX-007: a hotfix run WITH Shield integration tests still
            # needs the plan-level hotfix trace closure (cross-version AC
            # markers resolve via the target version dir, not the delta dir).
            # Without hotfix_ctx the delta-dir check sees anchored ACs as
            # unbound (PRISM-V06-R16-01, T-004). No increment.declared (that
            # is empty-shield only; this path has Shield tests committed).
            if getattr(state, "hotfix_anchor_acs", None):
                increment = next(
                    (
                        ev.payload
                        for ev in reversed(list(self.store.events(self.run_id)))
                        if ev.type == "increment.declared"
                    ),
                    None,
                )
                unit_rows = increment.get("unit_rows", []) if increment else []
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
        default = "test_defect"
        backend = getattr(self, "backend", None)
        token_fn = getattr(backend, "token", None)
        token = token_fn("diagnose", "classification", default) if token_fn else default
        return (
            token
            if token in ("test_defect", "stub_gap", "ac_gap", "spec_gap", "impl_defect")
            else default
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
