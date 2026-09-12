"""M-IMPL task graph, baseline freeze, planning gates and task-selection
ledger (mixin ``MImplLedgerMixin``).

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from tracks import paths
from tracks.baseline import m_impl_baseline_digest, m_impl_baseline_missing, m_impl_baseline_summary
from tracks.executor.anchor_probe import ProbeReport, probe_summary, probe_task_anchors
from tracks.executor.helpers import git
from tracks.executor.taskgraph import (
    TaskNode,
    parse_tasks_json,
    validate_ac_coverage,
    validate_acceptance_coverage,
    validate_dag,
    validate_debt_references,
    validate_issue_numbers,
    validate_scope,
    validate_scope_existence,
    validate_task_structure,
)
from tracks.executor.test_tasks import _extract_if_registry, _known_ac_ids
from tracks.kernel.machine import State
from tracks.project import ContractError, load_contract


def _batch_key(batch: str) -> tuple[int, str]:
    try:
        return (0, f"{int(batch):020d}")
    except (TypeError, ValueError):
        return (1, str(batch))


def _scenario_bound_digest(digest: str, scenario_branch_head: str | None) -> str:
    """Fold the scenario B active release branch HEAD into the M-IMPL baseline
    digest (interfaces §1g, IF-HOTFIX-008). Runs without a scenario input keep
    the canonical digest byte-identical: post-release hotfixes sit on base=main
    (independent of the active run) and canonical feature runs have no
    active-branch coupling, so pre-v0.6 replays never change identity."""
    if not scenario_branch_head:
        return digest
    joined = f"digest:{digest}\nscenario_branch_head:{scenario_branch_head}"
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _taskgraph_structure_errors(tasks: list[TaskNode]) -> list[str]:
    errors = validate_task_structure(tasks)
    valid, cycle = validate_dag(tasks)
    if not valid:
        errors.append(cycle or "task graph DAG is invalid (validate_dag returned no reason)")
    valid, overlap = validate_scope(tasks)
    if not valid:
        errors.extend(overlap)
    return errors


def _taskgraph_coverage_errors(
    tasks: list[TaskNode], ac_ids: list[str], if_registry: set[str]
) -> list[str]:
    errors: list[str] = []
    valid, coverage = validate_ac_coverage(tasks, ac_ids, if_registry)
    if not valid:
        errors.extend(coverage)
    valid, issue_errors = validate_issue_numbers(tasks)
    if not valid:
        errors.extend(issue_errors)
    return errors


# ARCH-006 §3.2 P-3: dev-scenario hotfix runs reconcile against the active
# release branch (executor/hotfix.py resolves entry bases from the same
# prefix; the BASELINE digest input reuses it, interfaces §1g).
_RELEASE_BRANCH_PREFIX = "releases/"


class MImplLedgerMixin:
    """M-IMPL taskgraph ledger mixin."""

    def _requirements_baseline_dir(self, state: State, vdir: Path) -> Path:
        """Return the read-only requirement baseline for a hotfix delta."""
        if state.hotfix_issue is None or not state.hotfix_target_version:
            return vdir
        return paths.projects_dir(self.store.home) / state.hotfix_target_version

    def _design_checkpoint_sha(self) -> str:
        """C_design: latest ``result.checkpointed.payload.commit_sha`` inside the
        real M-DESIGN stage window (stage.entered(M-DESIGN) .. the matching
        stage.exited). A checkpoint from any other stage, or one emitted outside
        a genuine M-DESIGN window, is never consumed."""
        checkpoint = ""
        in_design = False
        for ev in self.store.events(self.run_id):
            if ev.type == "stage.entered" and ev.payload.get("stage") == "M-DESIGN":
                in_design = True
                continue
            if ev.type == "stage.exited" and ev.payload.get("stage") == "M-DESIGN":
                in_design = False
                continue
            if in_design and ev.type == "result.checkpointed":
                sha = ev.payload.get("commit_sha")
                if isinstance(sha, str) and sha.strip():
                    checkpoint = sha.strip()
        return checkpoint

    def _freeze_scenario_head(self, state) -> str | None:
        """Scenario B audit input (interfaces §1a/§1g, AC-FR0245-02): the
        current HEAD of the active release branch a dev-scenario hotfix run
        must reconcile against before merge. The branch identity comes from
        the run's own entry ``branch.created`` base — Runtime is the only
        branch authority (IF-HOTFIX-005), so no naming is re-derived here.
        post-release hotfixes and canonical runs return None (no input)."""
        if state.hotfix_issue is None or state.hotfix_scenario != "dev":
            return None
        base = ""
        for ev in self.store.events(self.run_id):
            if ev.type == "branch.created":
                base = str(ev.payload.get("base") or "")
                break
        if not base.startswith(_RELEASE_BRANCH_PREFIX):
            return None
        head = git(self.repo, "rev-parse", base, check=False).stdout.strip()
        return head or None

    def _last_baseline_digest(self) -> str:
        """Digest carried by the latest CURRENT ``baseline.frozen`` event of
        this run within the CURRENT M-IMPL residency (scenario B reconcile
        reference; '' before the first freeze of this residency).

        Operator finding (2026-08-24, run 01M0S0FQ): freezes belonging to an
        abandoned M-IMPL cycle (rolled back through M-DESIGN re-approval) must
        not poison the reference -- the stale guard exists for a branch
        advancing WHILE the run is resident in M-IMPL, not across a
        framework-driven rollback + re-entry. A status=stale freeze is a
        conflict OBSERVATION (the parked evidence), never a baseline: after
        the operator reconciles (human.retry -> BASELINE) the re-freeze must
        anchor to reality, and reconcile itself legitimately moves the tree
        (operator fix commits). Scope the scan to events after the latest
        M-IMPL residency boundary (stage.entered or -- B51 (#67) -- the B32
        stage.recovered forward-recovery re-entry, which reuses the
        _on_stage_entered reducer under a different event type) and skip
        stale freezes."""
        digest = ""
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "baseline.frozen":
                if ev.payload.get("status") == "current":
                    digest = str(ev.payload.get("digest") or "")
                # stale freezes: conflict observations, not references
            elif (
                ev.type in ("stage.entered", "stage.recovered")
                and ev.payload.get("stage") == "M-IMPL"
            ):
                # Hit the residency boundary: the digest now held is the
                # latest current freeze of THIS residency ('' for fresh).
                return digest
        return digest

    def _do_freeze_baseline(self, cmd, state, task_id, reconcile):
        if reconcile and state.baseline_frozen:
            return
        self._emit(
            "baseline.frozen",
            self._baseline_frozen_payload(state),
            command_id=cmd.command_id,
        )

    def _baseline_frozen_payload(self, state) -> dict:  # pylint: disable=too-many-locals
        """Assemble the ``baseline.frozen`` payload (flow.md §10 BASELINE):
        the canonical identity inputs plus the scenario B reconcile inputs
        (interfaces §1a/§1g, AC-FR0245-02). Digest binding order is stable:
        canonical inputs first, then the active release branch head fold."""
        vdir = self._vdir()
        requirements_dir = self._requirements_baseline_dir(state, vdir)
        frozen = self._frozen_test_paths()
        approval_digest = self._approval_digest()
        issue_evidence = self._issue_evidence()
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        tip = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        design_checkpoint = self._design_checkpoint_sha()
        digest = m_impl_baseline_digest(
            vdir,
            self.repo,
            approval_digest=approval_digest,
            issue_evidence=issue_evidence,
            frozen_test_paths=frozen,
            branch=branch,
            tip=tip,
            design_checkpoint=design_checkpoint,
            requirements_dir=requirements_dir,
        )
        try:
            load_contract(self.repo)
            contract_valid = True
        except ContractError:
            contract_valid = False
        missing = list(
            m_impl_baseline_missing(
                vdir,
                self.repo,
                approval_digest=approval_digest,
                issue_evidence=issue_evidence,
                frozen_test_paths=frozen,
                contract_valid=contract_valid,
                branch=branch,
                tip=tip,
                design_checkpoint=design_checkpoint,
                requirements_dir=requirements_dir,
            )
        )
        # Scenario B (interfaces §1g): bind the dev-scenario active release
        # branch HEAD into the digest. An advanced active branch re-freezes to
        # a mismatched digest -> stale -> existing NEEDS_ATTENTION route
        # (flow.md §16.3.3 reconcile path; merge itself is FR-0246).
        scenario_head = self._freeze_scenario_head(state)
        if scenario_head is None and state.hotfix_scenario == "dev":
            # Declared dev scenario with an unresolvable active branch: fail
            # closed on the reconcile input rather than freeze unauditable.
            missing.append("scenario_branch_head")
        digest = _scenario_bound_digest(digest, scenario_head)
        prior_digest = self._last_baseline_digest()
        advanced = bool(prior_digest) and digest != prior_digest
        return {
            "status": "current" if not missing and not advanced else "stale",
            "digest": digest,
            "summary": m_impl_baseline_summary(vdir, requirements_dir),
            "frozen_test_paths": frozen,
            "missing": missing,
            "scenario_branch_head": scenario_head,
            "branch": branch,
            "tip": tip,
            "design_checkpoint": design_checkpoint,
        }

    def _do_commit_taskgraph(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        if reconcile and state.taskgraph_committed:
            self._rebuild_task_log_projection()
            return
        vdir = self._vdir()
        tasks_json_path = vdir / "tasks.json"
        raw, error = self._read_taskgraph(tasks_json_path)
        if error is not None:
            self._emit_taskgraph_failure(cmd, state, error, raw)
            return
        tasks, error = parse_tasks_json(raw)
        if error is not None:
            self._emit_taskgraph_failure(cmd, state, error, raw[:500])
            return
        errors = self._taskgraph_errors(
            vdir,
            tasks,
            self._requirements_baseline_dir(state, vdir),
            state.hotfix_anchor_acs if state.hotfix_issue is not None else None,
        )
        # B50 (#65): schema-2 acceptance coverage closure -- planning-time
        # fail-closed (§8 integration rows must be declared anchors), the
        # replacement for the retired GREEN_GATE IF-index inference.
        plan_path = vdir / "test-plan.md"
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        valid, coverage_errors = validate_acceptance_coverage(tasks, plan_text)
        if not valid:
            errors.extend(coverage_errors)
        # ------------------------------------------------------------------
        # OOB b89 OB-3 — scope existence + sidecar freshness + satisfiability
        # ------------------------------------------------------------------
        satisfiability, satisfiability_advisories_for_evidence = self._b89_planning_gate(
            tasks, vdir, plan_text, errors
        )

        if errors:
            # evidence for satisfiability hard gate includes violations +
            # advisories line-by-line (advisories not already in errors too)
            evidence_parts: list[str] = list(errors)
            for adv in satisfiability_advisories_for_evidence:
                if adv not in evidence_parts:
                    evidence_parts.append(adv)
            self._emit_taskgraph_failure(cmd, state, "; ".join(errors), "\n".join(evidence_parts))
            return
        # B33（#34）：规划期锚点实测——任务型裁定机械化（r1 回滚 #2 的
        # 拦截：锚已绿的任务不得排成标准 RGR/preset-anchor）。
        probe_report = self._probe_anchor_types(tasks)
        if probe_report.errors:
            self._emit_taskgraph_failure(
                cmd,
                state,
                "; ".join(probe_report.errors),
                "\n".join(probe_report.errors + probe_report.advisory()),
            )
            return
        for line in probe_report.advisory():
            print(f"  [taskgraph] {line}", file=sys.stderr, flush=True)
        # OB-4: use the single rendering implementation (delegated)
        (vdir / "tasks.md").write_text(self._tasks_md(tasks), encoding="utf-8")
        retained_ids = self._retained_completed_ids(tasks)
        # OB-3: enrich anchor_probe summary and committed payload with satisfiability
        probe_payload = probe_summary(probe_report)
        probe_payload["satisfiability"] = dict(satisfiability)
        # #133 advisory: scope files with no static anchor coverage (debt exempts)
        scope_advisory = self._scope_anchor_advisories(tasks, vdir)
        probe_payload["scope_anchor_advisory"] = list(scope_advisory)
        committed_payload: dict = {
            "task_count": len(tasks),
            "task_ids": [t.task_id for t in tasks],
            "tasks": [self._task_payload(t) for t in tasks],
            "path": "tasks.json",
            "validate_status": "pass",
            "digest": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "tasks_md": "tasks.md",
            "anchor_probe": probe_payload,
            "satisfiability": dict(probe_payload["satisfiability"]),
            "probe_summary": dict(probe_payload),
            "scope_anchor_advisory": list(scope_advisory),
            "retained_completed_task_ids": retained_ids,
        }
        self._emit(
            "taskgraph.committed",
            committed_payload,
            command_id=cmd.command_id,
        )
        self._rebuild_task_log_projection()

    def _retained_completed_ids(self, tasks: list[TaskNode]) -> list[str]:
        """B83: retained completions are derived from EVENT history, not live
        state -- the scope route (and any M-IMPL re-entry) clears
        taskgraph_committed/task_refs before this commit, so state-only
        guards would silently retain nothing. The previous committed graph
        (last taskgraph.committed event) is the sole old-payload source;
        completed tasks whose payload is fully equivalent in the new graph
        carry over, redefined/merged ones re-run. This also preserves the
        FR-0150 cross-cycle semantics (completions count across cycles)."""
        prev_graph_ev = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "taskgraph.committed"
            ),
            None,
        )
        if prev_graph_ev is None:
            return []
        events = list(self.store.events(self.run_id))
        completed_ids = {
            ev.payload.get("task_id")
            for ev in events
            if ev.type == "task.completed" and ev.payload.get("task_id")
        }
        old_payloads = [t for t in prev_graph_ev.payload.get("tasks") or [] if isinstance(t, dict)]
        return self._compute_retained_completions(
            old_payloads, [self._task_payload(t) for t in tasks], completed_ids
        )

    def _scope_anchor_advisories(self, tasks: list[TaskNode], vdir: Path) -> list[str]:
        try:
            from tracks.executor.anchor_surface import load_anchor_surface
            from tracks.executor.taskgraph import scope_anchor_advisories

            surface = load_anchor_surface(vdir / "anchor-surface.json")
            return scope_anchor_advisories(tasks, surface, repo=self.repo)
        except Exception:
            return []

    def _b89_planning_gate(
        self,
        tasks: list[TaskNode],
        vdir: Path,
        plan_text: str,
        errors: list[str],
    ) -> tuple[dict, list[str]]:
        """OOB b89 OB-3: scope existence + sidecar freshness + satisfiability.

        Schema-1 legacy graphs skip all three checks (same bound as
        ``validate_acceptance_coverage``). Scope-existence errors join the
        hard-gate ``errors`` channel; anchor-satisfiability infra failures
        degrade to a non-blocking ``skipped`` marker (stderr already printed
        inside the helper) so an otherwise-good graph still commits without
        a silent pass. Returns ``(satisfiability, advisories_for_evidence)``.
        """
        is_legacy = bool(tasks) and any(getattr(t, "schema", 1) == 1 for t in tasks)
        if is_legacy:
            return {"violations": [], "advisories": [], "skipped": "schema-1"}, []
        # 1) scope existence (hard gate, same errors channel)
        try:
            arch_path = vdir / "architecture.md"
            arch_text = arch_path.read_text(encoding="utf-8") if arch_path.exists() else ""
            design_text = arch_text + "\n" + plan_text
            ok_scope, scope_errs = validate_scope_existence(
                tasks, repo=Path(self.repo), design_docs_text=design_text
            )
            if not ok_scope:
                errors.extend(scope_errs)
        except Exception as exc:  # infra / unexpected
            print(f"scope existence check failed (infra): {exc}", file=sys.stderr, flush=True)
            errors.append(f"scope existence infra failure: {exc}")
        # 2+3) sidecar freshness + satisfiability (hard gate, infra → skipped)
        satisfiability: dict
        advisories_for_evidence: list[str] = []
        try:
            violations, advisories, skipped = self._evaluate_satisfiability(tasks, vdir)
            if skipped is not None:
                satisfiability = {
                    "violations": [],
                    "advisories": advisories,
                    "skipped": skipped,
                }
            else:
                satisfiability = {"violations": violations, "advisories": advisories}
                if violations:
                    errors.extend(violations)
            advisories_for_evidence = list(advisories)
        except Exception as exc:
            # infra failure already printed inside helper; keep skip marker
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            satisfiability = {"violations": [], "advisories": [], "skipped": f"infra: {exc}"}
        return satisfiability, advisories_for_evidence

    def _probe_anchor_types(self, tasks):
        """B33（#34）：load_contract 失败（宿主无合同）时跳过实测。"""
        try:
            contract = load_contract(self.repo)
        except ContractError:
            return ProbeReport(skipped_reason="anchor probe skipped: no project contract")
        return probe_task_anchors(Path(self.repo), tasks, contract)

    def _evaluate_satisfiability(  # noqa: CCR001
        self, tasks, vdir: Path
    ) -> tuple[list[str], list[str], str | None]:
        """OOB b89 OB-3: sidecar freshness + ``validate_anchor_satisfiability``.

        Returns ``(violations, advisories, skipped)``. ``skipped`` non-None
        indicates infra skip (stderr already printed) and violations/advisories
        must not be treated as hard failures.

        Freshness keys: ``schema==1``, ``anchor_set_digest`` vs current anchor
        set, ``recorded_tree`` vs current ``_dirty_tree_stamp``. Stale triggers
        a regen via ``collect_anchor_surface(jobs=4)`` and an atomic write to
        ``vdir/anchor-surface.json`` + ``.tracks/runtime/blobs/<sha>``.
        """
        import hashlib as _hashlib
        import json as _json
        from pathlib import Path as _Path

        from tracks.executor.anchor_surface import (
            AnchorSurfaceError,
            collect_anchor_surface,
            load_anchor_surface,
        )
        from tracks.executor.taskgraph import validate_anchor_satisfiability

        repo = _Path(self.repo)
        vdir = _Path(vdir)
        sidecar_path = vdir / "anchor-surface.json"

        # Collect anchor node-ids (mirrors anchor_surface._load_anchors_from_tasks)
        anchors: list[str] = []
        seen: set[str] = set()
        for t in tasks:
            refs = getattr(t, "acceptance_refs", None)
            if refs is None:
                refs = getattr(t, "test_refs", ()) or ()
            for r in refs or []:
                if isinstance(r, str) and r.startswith("tests/") and r not in seen:
                    seen.add(r)
                    anchors.append(r)
        anchors = sorted(seen)

        # Expected digest (mirrors anchor_surface._anchor_set_digest)
        expected_digest = _hashlib.sha256("\n".join(sorted(anchors)).encode("utf-8")).hexdigest()
        # Current dirty tree stamp (mirrors executor._dirty_tree_stamp)
        try:
            current_tree = self._dirty_tree_stamp()  # type: ignore[attr-defined]
        except Exception:
            # fallback to anchor_surface's stamp
            from tracks.executor.anchor_surface import _dirty_tree_stamp as _surface_stamp

            current_tree = _surface_stamp(repo)

        # Try to load existing sidecar and decide freshness
        need_regen = False
        surface: dict | None = None
        try:
            data = load_anchor_surface(sidecar_path)
            if (
                data.get("schema") != 1
                or data.get("anchor_set_digest") != expected_digest
                or data.get("recorded_tree") != current_tree
            ):
                need_regen = True
            else:
                surface = data
        except AnchorSurfaceError as exc:
            need_regen = True
            # keep exc for stale reason; regen will be attempted
            _regen_reason = str(exc)
        except Exception as exc:
            need_regen = True
            _regen_reason = str(exc)

        if surface is not None and not need_regen:
            ok, violations, advisories = validate_anchor_satisfiability(tasks, surface)
            return violations, advisories, None

        # Need regeneration (stale or missing)
        # Load contract as _probe_anchor_types does
        try:
            contract = load_contract(repo)
        except ContractError as exc:
            # no contract → satisfiability cannot be evaluated; treat as infra skip
            msg = f"infra: no contract: {exc}"
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            return [], [], msg
        except Exception as exc:
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            return [], [], f"infra: {exc}"

        # Collect (jobs=4)
        try:
            surface = collect_anchor_surface(repo, anchors, contract, jobs=4)
        except AnchorSurfaceError as exc:
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            return [], [], f"infra: {exc}"
        except Exception as exc:
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            return [], [], f"infra: {exc}"

        # Persist sidecar + blobs cache (best-effort)
        try:
            sidecar_path.parent.mkdir(parents=True, exist_ok=True)
            sidecar_path.write_text(
                _json.dumps(surface, indent=2, sort_keys=True), encoding="utf-8"
            )
            try:
                stamp = _json.dumps(surface, sort_keys=True).encode("utf-8")
                h = _hashlib.sha256(stamp).hexdigest()
                blobs_dir = repo / ".tracks" / "runtime" / "blobs"
                blobs_dir.mkdir(parents=True, exist_ok=True)
                (blobs_dir / h).write_text(_json.dumps(surface, sort_keys=True), encoding="utf-8")
            except Exception:
                pass
        except Exception as exc:
            # write failure → treat as infra skip (but still try to validate with in-memory surface)
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            # still validate with the in-memory surface if usable
            pass

        # Validate with the (fresh or best-effort) surface
        try:
            ok, violations, advisories = validate_anchor_satisfiability(tasks, surface)
            return violations, advisories, None
        except Exception as exc:
            print(f"anchor-surface regen failed (infra): {exc}", file=sys.stderr, flush=True)
            return [], [], f"infra: {exc}"

    @staticmethod
    def _read_taskgraph(path: Path) -> tuple[str, str | None]:
        if not path.exists():
            return "", "tasks.json not found; Archer must produce it"
        try:
            return path.read_text(encoding="utf-8"), None
        except (OSError, UnicodeDecodeError) as exc:
            return "", f"tasks.json unreadable: {exc}"

    def _emit_taskgraph_failure(
        self,
        cmd,
        state,
        reason: str,
        evidence: str = "",
    ) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "taskgraph",
                "reason": reason,
                "evidence": evidence,
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    @staticmethod
    def _taskgraph_errors(
        vdir: Path,
        tasks: list[TaskNode],
        requirements_dir: Path | None = None,
        anchor_acs: list[str] | None = None,
    ) -> list[str]:
        errors = _taskgraph_structure_errors(tasks)
        ac_ids, if_registry, doc_errors = MImplLedgerMixin._taskgraph_docs(
            (requirements_dir or vdir) / "acceptance.md",
            (requirements_dir or vdir) / "interfaces.md",
        )
        errors.extend(doc_errors)
        if anchor_acs:
            anchored_ids = {ac.split("@", 1)[0] for ac in anchor_acs}
            for task in tasks:
                for ac_id in task.ac_refs:
                    if ac_id not in anchored_ids:
                        errors.append(f"{task.task_id} declares unanchored AC {ac_id}")
            ac_ids = sorted(anchored_ids)
        errors.extend(_taskgraph_coverage_errors(tasks, ac_ids, if_registry))
        errors.extend(validate_debt_references(tasks))
        return errors

    @staticmethod
    def _taskgraph_docs(
        acc_path: Path,
        if_path: Path,
    ) -> tuple[list[str], set[str], list[str]]:
        errors: list[str] = []
        if not acc_path.is_file():
            errors.append("acceptance.md missing; cannot determine required ACs")
            ac_ids = []
        else:
            ac_ids = sorted(_known_ac_ids(acc_path.read_text(encoding="utf-8")))
        if not if_path.is_file():
            errors.append("interfaces.md missing; cannot validate IF registry")
            return ac_ids, set(), errors
        if_registry = _extract_if_registry(if_path.read_text(encoding="utf-8"))
        if if_registry is None:
            errors.append("interfaces.md missing IF Registry")
            if_registry = set()
        return ac_ids, if_registry, errors

    @staticmethod
    def _task_payload(task: TaskNode) -> dict:
        return {
            "task_id": task.task_id,
            "issue_number": task.issue_number,
            "description": task.description,
            "ac_refs": list(task.ac_refs),
            "fr_refs": list(task.fr_refs),
            "if_ids": list(task.if_ids),
            "test_refs": list(task.test_refs),
            # B50 (#65) schema v2 split (test_refs stays as the combined list
            # for legacy consumers; schema marks which contract the graph used).
            "unit_refs": list(task.unit_refs),
            "acceptance_refs": list(task.acceptance_refs),
            "schema": task.schema,
            "scope_boundary": task.scope_boundary,
            "depends_on": list(task.depends_on),
            "batch": task.batch,
            "parallel": task.parallel,
            "budget": task.budget,
            "deferred_refs": list(getattr(task, "deferred_refs", ())),
            "integration": bool(getattr(task, "integration", False)),
            "debt": [dict(item) for item in (getattr(task, "debt", ()) or ())],
        }

    @staticmethod
    def _task_payloads_equivalent(old: dict, new: dict) -> bool:
        # #129 debt is a pure annotation: excluded from identity on purpose.
        keys = (
            "task_id",
            "issue_number",
            "description",
            "ac_refs",
            "fr_refs",
            "if_ids",
            "test_refs",
            "unit_refs",
            "acceptance_refs",
            "schema",
            "scope_boundary",
            "depends_on",
            "batch",
            "parallel",
            "budget",
            "deferred_refs",
            "integration",
        )
        return all(old.get(key) == new.get(key) for key in keys)

    def _compute_retained_completions(
        self, old_tasks: list[dict], new_tasks: list[dict], completed_ids: set
    ) -> list[str]:
        old_by_id = {t.get("task_id"): t for t in old_tasks if t.get("task_id")}
        new_by_id = {t.get("task_id"): t for t in new_tasks if t.get("task_id")}
        retained = []
        for tid in sorted(completed_ids):
            if (
                tid in old_by_id
                and tid in new_by_id
                and self._task_payloads_equivalent(old_by_id[tid], new_by_id[tid])
            ):
                retained.append(tid)
        return retained

    @staticmethod
    def _tasks_md(tasks) -> str:
        """OOB b89 OB-4: delegate to the single ``taskgraph.render_tasks_md`` implementation."""
        from tracks.executor.taskgraph import render_tasks_md

        return render_tasks_md(tasks)

    def _do_select_task(self, cmd, state, task_id, reconcile):  # pylint: disable=too-many-locals
        if state.current_task_id:
            return
        if state.hotfix_issue is not None and state.hotfix_scenario == "dev":
            prior = next(
                (
                    ev.payload
                    for ev in reversed(list(self.store.events(self.run_id)))
                    if ev.type == "baseline.frozen"
                ),
                None,
            )
            current_head = self._freeze_scenario_head(state)
            if (
                prior
                and prior.get("scenario_branch_head")
                and current_head
                and prior.get("scenario_branch_head") != current_head
            ):
                payload = self._baseline_frozen_payload(state)
                self._emit("baseline.frozen", payload, command_id=cmd.command_id)
                return
        tasks = [self._task_node(raw) for raw in state.task_refs]
        if not tasks:
            self._emit_no_task_failure(cmd, state, "no tasks available")
            return
        events = list(self.store.events(self.run_id))
        completed = self._effective_completed_task_ids(events, state)
        # FR-0150 rollback re-entry: a task started in a PREVIOUS M-IMPL cycle
        # but never completed is stale work, not an in-flight lease -- the old
        # blanket guard deadlocked the fresh cycle on it (T-017 stranded,
        # run 01KZTHE7, 2026-08-17). Completions count across cycles (the work
        # is committed in git); only task.started events after the latest
        # M-IMPL stage.entered gate the single-in-flight rule, and stale
        # starts remain selectable so the task re-runs on a clean budget.
        impl_entry_seq = max(
            (
                e.seq
                for e in events
                if e.type in ("stage.entered", "stage.recovered")
                and e.payload.get("stage") == "M-IMPL"
            ),
            default=0,
        )
        # B83: also gate on the latest taskgraph commit -- a scope-failure
        # replan replaces the graph WITHIN one M-IMPL residency (no new
        # stage.entered), so old-generation starts of merged/redefined tasks
        # (e.g. T-006/T-007) would otherwise block selection forever.
        latest_taskgraph_seq = max(
            (e.seq for e in events if e.type == "taskgraph.committed"),
            default=0,
        )
        started_cutoff = max(impl_entry_seq, latest_taskgraph_seq)
        started_this_cycle = {
            e.payload.get("task_id")
            for e in events
            if e.type == "task.started" and e.seq > started_cutoff and e.payload.get("task_id")
        }
        if started_this_cycle - completed:
            return
        if state.writelock_held:
            self._recover_task_lease(cmd, events, completed)
            return
        ready = [
            task
            for task in tasks
            if task.task_id not in completed
            and all(dep == "-" or dep in completed for dep in task.depends_on)
        ]
        if not ready:
            self._emit_no_task_failure(cmd, state, "no ready tasks")
            return
        chosen = min(ready, key=lambda task: (_batch_key(task.batch), task.task_id))
        self._start_task(cmd, chosen, self._task_manifest(chosen, state))

    @staticmethod
    def _effective_completed_task_ids(events, state) -> set[str]:
        """B83: generation-aware completion projection.

        Returns the set of completed task IDs combining retained IDs from
        prior generations (state.retained_completed_task_ids) with
        task.completed events after the latest taskgraph.committed.

        FR-0286 §5: known_issue.registered tasks are terminal as ``waived``
        (an independent terminal state, never task.completed) -- selection
        must not keep their lease in flight after the machine projection
        closed them."""
        latest_taskgraph_seq = max(
            (e.seq for e in events if e.type == "taskgraph.committed"),
            default=0,
        )
        current_gen = {
            ev.payload.get("task_id")
            for ev in events
            if ev.type == "task.completed"
            and ev.payload.get("task_id")
            and ev.seq > latest_taskgraph_seq
        }
        waived = {
            ev.payload.get("task_id")
            for ev in events
            if ev.type == "known_issue.registered"
            and ev.seq > latest_taskgraph_seq
            and not (ev.payload or {}).get("fixed")
        }
        return (
            set(state.retained_completed_task_ids or [])
            | current_gen
            | {task for task in waived if task}
        )

    def _recover_task_lease(self, cmd, events, completed: set[str]) -> None:
        lease = next((ev for ev in reversed(events) if ev.type == "writelock.granted"), None)
        if lease is None:
            return
        payload = lease.payload
        lease_task = payload.get("task_id")
        # B83: a lease granted before the latest taskgraph.committed belongs
        # to a REPLACED graph (scope-failure replan / M-IMPL re-entry). Its
        # task may be merged away or redefined, so resurrecting it with the
        # old manifest would dispatch work that no longer exists. Release
        # the stale lease and let normal selection restart the task (if it
        # still exists) under the new graph's manifest.
        latest_taskgraph_seq = max(
            (ev.seq for ev in events if ev.type == "taskgraph.committed"),
            default=0,
        )
        if lease.seq < latest_taskgraph_seq:
            # B88 (#88): the idempotency check must be scoped to releases
            # AFTER this lease (ev.seq > lease.seq). An all-time scan finds
            # the PREVIOUS generation's release for the same task id and
            # skips emitting -> writelock_held stays True forever and
            # select_task livelocks (~280ms/cycle; run 01M0S0FQ T-010:
            # old-gen release at seq 35, new-gen lease at 11480 unreleased).
            released = any(
                ev.type == "writelock.released"
                and ev.payload.get("task_id") == lease_task
                and ev.seq > lease.seq
                for ev in events
            )
            if not released:
                self._emit(
                    "writelock.released",
                    {"task_id": lease_task, "reason": "taskgraph_replaced"},
                    command_id=cmd.command_id,
                )
                self._rebuild_task_log_projection()
            return
        if lease_task in completed:
            # B88: same seq-scoping for the same-generation branch -- a
            # release BEFORE the last lease (task re-selected in one
            # generation) must not suppress the release of the CURRENT lease.
            released = any(
                ev.type == "writelock.released"
                and ev.payload.get("task_id") == lease_task
                and ev.seq > lease.seq
                for ev in events
            )
            if not released:
                self._emit("writelock.released", {"task_id": lease_task}, command_id=cmd.command_id)
            self._rebuild_task_log_projection()
            return
        if lease_task and payload.get("manifest"):
            self._emit(
                "task.started",
                {
                    "task_id": lease_task,
                    "task": payload.get("task", {}),
                    "manifest": payload["manifest"],
                },
                command_id=cmd.command_id,
            )
            self._rebuild_task_log_projection()

    def _start_task(self, cmd, task: TaskNode, manifest: dict) -> None:
        payload = {"task_id": task.task_id, "task": self._task_payload(task), "manifest": manifest}
        self._emit(
            "writelock.granted",
            {
                **payload,
                "allowed_paths": self._task_allowed_paths(task),
                "forbidden_paths": manifest["forbidden_paths"],
            },
            command_id=cmd.command_id,
        )
        self._emit("task.started", payload, command_id=cmd.command_id)
        self._rebuild_task_log_projection()

    def _emit_no_task_failure(self, cmd, state, reason: str) -> None:
        self._emit(
            "verdict.failed",
            {
                "check": "island",
                "reason": reason,
                "evidence": "no tasks available for dispatch",
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _do_complete_task(self, cmd, state, task_id, reconcile):
        tid = cmd.params.get("task_id") or state.current_task_id
        if state.full_chain_round is not None and self._transition_full_ledger(
            cmd,
            "CLASSIFIED",
            "FIXED",
            "Devon repair task completed",
            "devon",
        ):
            if state.writelock_held:
                self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
            self._rebuild_task_log_projection()
            return
        events = list(self.store.events(self.run_id))
        # B83: generation-aware completion check. A task.completed event
        # from a prior generation (before the latest taskgraph.committed)
        # must not block re-completion of a redefined task.
        if tid not in (state.retained_completed_task_ids or []):
            latest_taskgraph_seq = max(
                (e.seq for e in events if e.type == "taskgraph.committed"),
                default=0,
            )
            already_completed = any(
                ev.type == "task.completed"
                and ev.payload.get("task_id") == tid
                and ev.seq > latest_taskgraph_seq
                for ev in events
            )
        else:
            already_completed = True
        if already_completed:
            released = any(
                ev.type == "writelock.released" and ev.payload.get("task_id") == tid
                for ev in events
            )
            if not released:
                self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
            self._rebuild_task_log_projection()
            return
        self._emit("task.completed", {"task_id": tid}, command_id=cmd.command_id)
        self._emit("writelock.released", {"task_id": tid}, command_id=cmd.command_id)
        self._rebuild_task_log_projection()
