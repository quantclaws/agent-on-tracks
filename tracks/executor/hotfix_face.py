"""v0.6 hotfix entry handlers plus PRECHECK/anchor helpers extracted from the
Executor (mixin ``ExecHotfixMixin``)."""

from __future__ import annotations

import sys
from pathlib import Path

from tracks import paths
from tracks.effects.github import GithubIssuesError, select_issue_backend
from tracks.executor.helpers import git
from tracks.executor.hotfix import (
    complete_hotfix_entry,
    parse_anchor_refs,
    precheck_hotfix,
    validate_anchor_refs,
)


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

    The kernel projects ``State.version`` from ``story.requested``
    (unconditional) and, while still unset, from any ``stage.entered``
    envelope (T-015: every envelope carries the run identity, so seeded
    partial streams surface it too; executor-emitted hotfix envelopes carry
    the already-derived hotfix identity, making the fallback a no-op there).
    A hotfix run without either projection gets its identity
    ``{target_version}-hotfix-{issue}`` re-derived from the projected hotfix
    fields. Pre-PRECHECK (target unknown yet) falls back to the stable
    ``hotfix-{issue}`` identity; a non-hotfix run keeps its projected version.
    """
    if state.version:
        return state.version
    if state.hotfix_issue is not None:
        if state.hotfix_target_version:
            return f"{state.hotfix_target_version}-hotfix-{state.hotfix_issue}"
        return f"hotfix-{state.hotfix_issue}"
    return ""


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


class ExecHotfixMixin:
    """Hotfix command handlers; module-level helpers below are shared by the CLI
hotfix entry points that remain in executor.py."""

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
