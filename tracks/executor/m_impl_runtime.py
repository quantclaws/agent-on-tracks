"""M-IMPL Runtime handlers extracted from :mod:`executor`.

The mixin keeps the public ``Executor`` method surface while isolating the
task-graph, manifest, and RGR implementation from the other stage handlers.
All filesystem and event-store access still goes through the host executor.
"""

# pylint: disable=too-many-lines
# The M-IMPL Runtime handlers all live in a single mixin on the public Executor
# (matching machine.py): splitting would spread the RGR gate/task-graph logic
# across modules without reducing cognitive load.
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from tracks import paths
from tracks.baseline import (
    m_impl_baseline_digest,
    m_impl_baseline_missing,
    m_impl_baseline_summary,
)
from tracks.effects.backend import valid_test_tasks
from tracks.executor.anchor_probe import ProbeReport, probe_summary, probe_task_anchors
from tracks.executor.helpers import git, parse_collected_nodes
from tracks.executor.host_contract import declared_install_interpreter
from tracks.executor.oscillation import (
    OSCILLATION_CHECK as _OSCILLATION_CHECK,
)
from tracks.executor.oscillation import (
    detect_oscillation as _detect_oscillation,
)
from tracks.executor.oscillation import (
    parse_failed_nodes as _parse_failed_nodes,
)
from tracks.executor.quality_gate import (
    execute_gate_command,
    failed_summary_lines,
    observation_evidence,
)
from tracks.executor.rgr import (
    adopt_red_ref,
    create_green_commit,
    create_red_ref,
    red_base_sha,
    verify_lineage,
)
from tracks.executor.taskgraph import (
    TaskNode,
    parse_tasks_json,
    plan_row_targets,
    validate_ac_coverage,
    validate_acceptance_coverage,
    validate_dag,
    validate_debt_references,
    validate_island_closure,
    validate_issue_numbers,
    validate_scope,
    validate_scope_existence,
    validate_task_structure,
)
from tracks.executor.test_select import (
    EvidenceIdentity,
    LedgerCorruptionError,
    TestResultError,
    TestSelectError,
    emit_stale_propagation,
    evidence_identity,
    ledger_is_clean,
    make_selection_id,
    parse_test_result,
    rebuild_ledger,
    require_exact_node_coverage,
    resolve_selected_command,
    reuse_allowed,
    select_diff,
    select_task,
    settle_stale_identities,
)
from tracks.executor.test_select import (
    audit as audit_selection_argv,
)
from tracks.executor.test_tasks import (
    _coverage_rows_with_test,
    _extract_if_registry,
    _known_ac_ids,
    resolve_inherited_baseline_docs,
)
from tracks.executor.validate import parse_test_tasks
from tracks.executor.worktree import (
    WorktreeHandle,
    cleanup_worktree,
    create_gate_worktree,
    ensure_runtime_assets,
)
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK, State
from tracks.project import ContractError, layout_paths, load_contract

_SECRET_PATTERN = re.compile(r"(sk-|ghp_|gho_|AKIA)[A-Za-z0-9]{16,}")


class TaskSelectionFailure(Exception):
    """Selected task tests executed correctly but did not all pass."""

    def __init__(self, message: str, evidence: str):
        super().__init__(message)
        self.evidence = evidence


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
    if sum(ev["type"] == "verdict.failed" and ev["seq"] > cutoff for ev in events) > task.budget:
        return {
            "check": "budget",
            "reason": f"verdict.failed count exceeds task budget {task.budget}",
        }
    return None


def _batch_key(batch: str) -> tuple[int, str]:
    try:
        return (0, f"{int(batch):020d}")
    except (TypeError, ValueError):
        return (1, str(batch))


def _manifest_path_matches(path: str, rule: str) -> bool:
    if rule.endswith("/**"):
        root = rule[:-3].rstrip("/")
        return path == root or path.startswith(root + "/")
    return path == rule or path.startswith(rule.rstrip("/") + "/")


def _contract_unit_run(repo) -> str:
    """``[unit].run`` template from the host project contract (T-015).

    The engine never names a runner module: the only framework literals live
    in the host project.toml (interfaces §1h language-neutral construction).
    Empty string when the host carries no loadable contract (fail-closed:
    no phantom fallback command)."""
    try:
        return load_contract(Path(repo)).unit.run
    except ContractError:
        return ""


def _devon_path_scope_error(
    changed_paths: list[str],
    allowed: set[str],
    forbidden: set[str],
    devon_test_dirs: list[str],
) -> str | None:
    """Validate one changed path against the manifest scope rules.

    Devon test-dir paths (RED failing tests) are exempt from both
    allowed_paths and forbidden. Run 01M0S0FQ T-017 (2026-08-29): fixing
    [layout.shield] to include the tests/ root for Shield correctness also
    excluded tests/unit/ via forbidden `tests/**`, while
    manifest.red_test_paths still declared `tests/unit` -- a required test
    path was simultaneously required and forbidden.
    """
    for path in changed_paths:
        if path.startswith("/") or ".." in path.split("/"):
            return f"Devon evidence path is not repo-relative: {path}"
        is_red_dir = bool(
            devon_test_dirs and any(path == d or path.startswith(d + "/") for d in devon_test_dirs)
        )
        if is_red_dir:
            continue
        if any(_manifest_path_matches(path, item) for item in forbidden):
            return f"Devon evidence path is forbidden: {path}"
        if not any(_manifest_path_matches(path, item) for item in allowed):
            return f"Devon evidence path is outside manifest: {path}"
    return None


_M_IMPL_RED_CLASSIFICATIONS = frozenset({"assertion_failure", "symbol_missing"})

# b92 R3 role grading: Devon RGR substates and Shield write substates that
# receive the trimmed per-task test_tasks slice (anchor_surface stays None).
_DEVON_RGR_SUBSTATES = frozenset({"RED", "GREEN", "REFACTOR"})
_SHIELD_WRITE_SUBSTATES = frozenset({"WRITE", "SHIELD_FIX"})


def _red_classification_label(value: object) -> str:
    if value is None:
        return "missing"
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    return value or "missing"


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


def _combined_provenance(task) -> list[str]:
    """Combine current task ac_refs + fr_refs into the single Tracks-AC
    provenance list: de-duplicated with stable deterministic ordering."""
    return list(dict.fromkeys((*task.ac_refs, *task.fr_refs)))


# ARCH-006 §3.2 P-3: dev-scenario hotfix runs reconcile against the active
# release branch (executor/hotfix.py resolves entry bases from the same
# prefix; the BASELINE digest input reuses it, interfaces §1g).
_RELEASE_BRANCH_PREFIX = "releases/"


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


def _resolve_island_gate_2(version):
    """Resolve the run version's ``island_gate_2`` capability.

    ISLAND_GATE_2 dispatch seam (IF-FAILCLOSED-001, architecture 1.0.9):
    mirrors ``machine._resolve_before_mtest`` -- the lazy import keeps this
    module free of an import-time cycle with the executor composition root.
    Early versions select no callback (None); a registered extension that
    lacks the callback propagates ``CapabilityBlockedError`` fail-closed
    (interfaces 1h: no silent classic fallback).
    """
    from tracks.executor.version_extensions import resolve_capability

    return resolve_capability(version or "", "island_gate_2")


def _canonical_demo_hosts() -> tuple[str, ...]:
    """The canonical demonstration host sequence.

    The tracks host plus the demo host id declared in the packaged demo
    registry (``[quality_registry].host`` of the demo architecture asset,
    data source -- executor source stays language neutral).  Falls back to a
    tracks-only demonstration when the packaged registry is unavailable.
    """
    demo_host = _demo_registry_host()
    return ("tracks",) if not demo_host else ("tracks", demo_host)


def _demo_registry_host() -> str | None:
    """Read the demo host id from the packaged demo registry (data)."""
    asset = Path(__file__).resolve().parent.parent / "assets" / "demo_host" / "architecture.md"
    try:
        text = asset.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        table = text.index("[quality_registry]")
        table_end = text.index("[[quality_guard]", table)
    except ValueError:
        return None
    for line in text[table:table_end].splitlines():
        stripped = line.strip()
        if stripped.startswith("host"):
            value = stripped.split("=", 1)[1].strip().strip('"')
            return value or None
    return None


_EVIDENCE_SHAPE_PREFIXES = (
    "Devon evidence missing: ",
    "Devon evidence phase mismatch",
    "Devon evidence changed_paths must be a list",
    "Devon evidence commands must be a list",
    "Devon evidence is missing pre/post identity",
    "Devon evidence implemented_if_ids must be a list",
    "Devon evidence missing r_identity",
)


def _is_evidence_shape_error(reason: str | None) -> bool:
    if not isinstance(reason, str) or not reason:
        return False
    if "contains an invalid path" in reason:
        return True
    return reason.startswith(_EVIDENCE_SHAPE_PREFIXES)


class MImplRuntimeMixin:
    """M-IMPL assignment, graph, island, and RGR command handlers."""

    def _rebuild_task_log_projection(self) -> None:
        """Compatibility seam; tasks.md is generated with tasks.json."""

    def _materialize_m_impl_assignment(
        self,
        state: State,
        params: dict,
        command_id: str,
    ) -> dict:
        self._rebuild_task_log_projection()
        assignment = deepcopy(params.get("assignment") or {})
        if state.hotfix_issue is not None:
            assignment["hotfix_anchor_acs"] = list(state.hotfix_anchor_acs or [])
            assignment["hotfix_issue"] = state.hotfix_issue
            assignment["issue_number"] = state.hotfix_issue
        task = state.current_task_metadata
        if task is None:
            task = self._materialized_task(state)
        pre_dirty = self._dirty_snapshot()
        self._add_assignment_runtime_fields(
            assignment,
            state,
            params,
            task,
            pre_dirty,
        )
        vdir = self._vdir()
        acc_path, _ = resolve_inherited_baseline_docs(vdir / "test-plan.md")
        self._add_assignment_role_fields(assignment, state, params)
        if state.hotfix_issue is not None:
            assignment["issue_number"] = state.hotfix_issue
        self._apply_assignment_context_grading(assignment, params, task, vdir, acc_path)
        return assignment

    def _materialized_task(self, state: State) -> dict | None:
        if not state.current_task_id:
            return None
        task_node = next(
            (
                self._task_node(raw)
                for raw in state.task_refs
                if raw.get("task_id") == state.current_task_id
            ),
            None,
        )
        return self._task_payload(task_node) if task_node is not None else None

    def _anchor_surface_for_assignment(self) -> dict:  # noqa: CCR001
        """OOB b92: aggregated, AST-only, advisory-externalized anchor surface.

        Returns
        ``{violations: {task_id: {missing_edges: [{owner_task, module,
        anchors}]}}, advisories_ref, advisories_count, advisories_digest,
        sidecar_digest, recorded_tree, stats}`` or ``{"unavailable":
        "<reason>"}`` on sidecar miss / infra / schema-1.

        ``missing_edges`` is grouped per ``(owner_task, module)`` -- ``module``
        is a single string and ``anchors`` the deduped sorted set of anchors
        that actually exercise it (no ``{modules[], anchors[]}`` double array,
        no cartesian pairing). ``expand(missing_edges)`` equals
        ``validate_anchor_satisfiability``'s AST violations set; dynamic-only
        modules never enter ``violations``. Advisory detail stays out of the
        payload (ref/count/digest only).
        """
        vdir = self._vdir()
        tasks_json_path = vdir / "tasks.json"
        if not tasks_json_path.exists():
            return {"unavailable": "tasks.json missing"}
        try:
            raw = tasks_json_path.read_text(encoding="utf-8")
            tasks, err = parse_tasks_json(raw)
            if err is not None:
                return {"unavailable": f"tasks.json parse error: {err}"}
        except Exception as exc:
            return {"unavailable": f"tasks.json unreadable: {exc}"}
        if not tasks:
            return {"unavailable": "no tasks"}
        if any(getattr(t, "schema", 1) == 1 for t in tasks):
            return {"unavailable": "schema-1"}
        sidecar_path = vdir / "anchor-surface.json"
        try:
            from tracks.executor.anchor_surface import load_anchor_surface

            surface = load_anchor_surface(sidecar_path)
        except Exception as exc:
            return {"unavailable": f"sidecar unavailable: {exc}"}
        if isinstance(surface, dict) and surface.get("skipped"):
            return {"unavailable": str(surface.get("skipped_reason") or "skipped")}
        # Build the aggregated summary from the same logic as
        # validate_anchor_satisfiability (AST口径 hard / dynamic advisory).
        try:
            from tracks.executor.taskgraph import _module_to_path, _scope_paths

            # all scopes → owner
            all_scopes: set[str] = set()
            owner_map: dict[str, str] = {}
            task_scopes: dict[str, set[str]] = {}
            for t in tasks:
                paths, _ = _scope_paths(t.scope_boundary, t.task_id)
                normed = set(paths)
                task_scopes[t.task_id] = normed
                for p in normed:
                    all_scopes.add(p)
                    owner_map[p] = t.task_id
            task_by_id = {t.task_id: t for t in tasks}

            def _closure_scopes(tid: str) -> set[str]:
                stack = list(task_by_id[tid].depends_on) if tid in task_by_id else []
                visited: set[str] = set()
                scopes: set[str] = set()
                while stack:
                    cur = stack.pop()
                    if cur == "-" or cur in visited:
                        continue
                    visited.add(cur)
                    if cur in task_scopes:
                        scopes.update(task_scopes[cur])
                    if cur in task_by_id:
                        stack.extend(task_by_id[cur].depends_on)
                return scopes

            anchors_map = surface.get("anchors", {}) if isinstance(surface, dict) else {}
            if not isinstance(anchors_map, dict):
                anchors_map = {}

            # Per-entry (ast_modules, dynamic_modules); a legacy entry without
            # the split fields falls back to ast=modules / dynamic=[] (same
            # hard-gate semantics as the gate), but still aggregated here.
            ast_total = 0
            dyn_total = 0
            entry_ast: dict[str, list[str]] = {}
            for a, entry in anchors_map.items():
                if not isinstance(entry, dict):
                    continue
                if "ast_modules" in entry and "dynamic_modules" in entry:
                    ast = [m for m in entry.get("ast_modules", []) if isinstance(m, str)]
                    dyn = [m for m in entry.get("dynamic_modules", []) if isinstance(m, str)]
                else:
                    ast = [m for m in entry.get("modules", []) if isinstance(m, str)]
                    dyn = []
                entry_ast[a] = ast
                ast_total += len(ast)
                dyn_total += len(dyn)

            edges: dict[tuple[str, str, str], set[str]] = {}

            def _map_paths(modnames: list[str]) -> set[str]:
                out: set[str] = set()
                for m in modnames:
                    try:
                        out.add(_module_to_path(m))
                    except Exception:
                        out.add(m.replace(".", "/") + ".py")
                return out

            for t in tasks:
                refs = getattr(t, "acceptance_refs", None)
                if refs is None:
                    refs = getattr(t, "test_refs", ()) or ()
                own = task_scopes.get(t.task_id, set())
                dep_scopes = _closure_scopes(t.task_id)
                for a in refs:
                    if not isinstance(a, str) or not a:
                        continue
                    if a not in anchors_map:
                        # fail-closed: mirror the gate's missing-anchor violation
                        edges.setdefault((t.task_id, "", ""), set()).add(a)
                        continue
                    ast_mapped = _map_paths(entry_ast.get(a, []))
                    needs = ast_mapped & all_scopes
                    missing = needs - own - dep_scopes
                    for path in sorted(missing):
                        owner = owner_map.get(path, "unknown")
                        edges.setdefault((t.task_id, path, owner), set()).add(a)

            by_task: dict[str, dict[tuple[str, str], set[str]]] = {}
            for (task_id, module, owner), anchors in edges.items():
                by_task.setdefault(task_id, {}).setdefault((owner, module), set()).update(anchors)

            violations: dict[str, dict] = {}
            for t in tasks:
                tid = t.task_id
                bucket = by_task.get(tid, {})
                violations[tid] = {
                    "missing_edges": [
                        {
                            "owner_task": owner,
                            "module": module,
                            "anchors": sorted(anchor_set),
                        }
                        for (owner, module), anchor_set in sorted(bucket.items())
                    ]
                }

            # Advisory count via the authoritative gate (A3: count parity);
            # the detail itself stays externalized, never in the payload.
            advisories_count = 0
            try:
                from tracks.executor.taskgraph import validate_anchor_satisfiability

                _ok, _vs, advisories = validate_anchor_satisfiability(tasks, surface)
                advisories_count = len(advisories)
            except Exception:
                advisories_count = 0

            sidecar_digest = ""
            try:
                canonical = json.dumps(surface, sort_keys=True).encode("utf-8")
                sidecar_digest = hashlib.sha256(canonical).hexdigest()
            except Exception:
                sidecar_digest = ""
            blob = Path(self.repo) / ".tracks" / "runtime" / "blobs" / sidecar_digest
            if blob.exists():
                advisories_ref = f".tracks/runtime/blobs/{sidecar_digest}"
            else:
                try:
                    advisories_ref = sidecar_path.relative_to(Path(self.repo)).as_posix()
                except ValueError:
                    advisories_ref = str(sidecar_path)

            return {
                "violations": violations,
                "advisories_ref": advisories_ref,
                "advisories_count": advisories_count,
                "advisories_digest": f"sha256:{sidecar_digest}" if sidecar_digest else "",
                "sidecar_digest": sidecar_digest,
                "recorded_tree": str((surface or {}).get("recorded_tree") or ""),
                "stats": {
                    "ast_modules_total": ast_total,
                    "dynamic_modules_total": dyn_total,
                    "anchors_total": len(anchors_map),
                },
            }
        except Exception as exc:
            return {"unavailable": f"infra: {exc}"}

    def _apply_assignment_context_grading(
        self,
        assignment: dict,
        params: dict,
        task: dict | None,
        vdir,
        acc_path,
    ) -> None:
        """b92 R3: inject only machine-actionable context per role/substate.

        Archer PLANNING (and Prism PRISM_PLAN) receive the aggregated
        ``anchor_surface`` (violations + advisory refs/count/stats) and never
        the full test_tasks payload; Devon gets an anchors-form test_tasks
        slice for the current task only; Shield keeps the layers-form slice
        (valid_test_tasks shape) trimmed to the current task; unknown roles
        get ``anchor_surface=None / test_tasks=None``. Devon and Shield slice
        with separate functions per F-02.
        """
        role = params.get("role")
        substate = params.get("substate")
        plan_path = vdir / "test-plan.md"
        self._set_test_tasks_ref(assignment, vdir)
        if (role == "archer" and substate == "PLANNING") or (
            role == "prism" and substate == "PRISM_PLAN"
        ):
            assignment["test_tasks"] = None
            assignment["test_tasks_count"] = len(parse_test_tasks(acc_path, plan_path))
            try:
                assignment["anchor_surface"] = self._anchor_surface_for_assignment()
            except Exception as exc:  # fail-closed: unavailable marker, never crash
                assignment["anchor_surface"] = {"unavailable": f"infra: {exc}"}
            return
        if role == "devon" and substate in _DEVON_RGR_SUBSTATES:
            assignment["anchor_surface"] = None
            assignment["test_tasks"] = self._devon_test_tasks_slice(task)
            return
        if role == "shield" and substate in _SHIELD_WRITE_SUBSTATES:
            assignment["anchor_surface"] = None
            assignment["test_tasks"] = self._shield_test_tasks_slice(
                task, acc_path, plan_path, assignment.get("test_tasks")
            )
            return
        assignment["anchor_surface"] = None
        assignment["test_tasks"] = None

    def _set_test_tasks_ref(self, assignment: dict, vdir) -> None:
        """``test_tasks_ref`` pointing at the canonical test-plan §8 table."""
        try:
            rel = str((vdir / "test-plan.md").relative_to(Path(self.repo)))
        except ValueError:
            rel = str(vdir / "test-plan.md")
        assignment["test_tasks_ref"] = f"{rel}#8"

    @staticmethod
    def _devon_test_tasks_slice(task: dict | None) -> list[dict]:
        """Devon-only slice (b92 F-02): ``{ac_id, anchors, if_ids}`` per AC of
        the current task. Separate from the Shield trimmer -- Devon anchors
        are the task's acceptance anchors, not the layers-form contract."""
        if not task:
            return []
        anchors = [a for a in (task.get("acceptance_refs") or ()) if isinstance(a, str)]
        if not anchors:
            anchors = [a for a in (task.get("test_refs") or ()) if isinstance(a, str)]
        if_ids = [i for i in (task.get("if_ids") or ()) if isinstance(i, str)]
        return [
            {"ac_id": ac, "anchors": list(anchors), "if_ids": list(if_ids)}
            for ac in (task.get("ac_refs") or ())
            if isinstance(ac, str) and ac
        ]

    def _shield_test_tasks_slice(
        self,
        task: dict | None,
        acc_path,
        plan_path,
        incoming,
    ) -> list[dict]:
        """Shield-only slice (b92 F-02): the ``parse_test_tasks`` layers-form
        entries filtered to the current task's ACs, preserving the
        ``valid_test_tasks`` shape (``{ac_id, layers, if_ids}``). Keeps an
        already-valid incoming list (kernel-provided) only when the task's ACs
        carry no integration/e2e coverage rows -- never rewrites the shape to
        the Devon anchors form."""
        full = parse_test_tasks(acc_path, plan_path)
        ac_refs = {str(a) for a in ((task or {}).get("ac_refs") or ()) if a}
        sliced = [t for t in full if isinstance(t, dict) and t.get("ac_id") in ac_refs]
        if sliced:
            return sliced
        if valid_test_tasks(incoming):
            return list(incoming)
        return []

    def _add_assignment_runtime_fields(
        self,
        assignment: dict,
        state: State,
        params: dict,
        task: dict | None,
        pre_dirty: dict[str, str],
    ) -> None:
        role = params.get("role")
        substate = params.get("substate")
        task_id = task.get("task_id") if isinstance(task, dict) else "none"
        result_identity = self._result_identity(
            task_id,
            str(substate or "dispatch").lower(),
            state.current_attempt,
            pre_dirty,
            state.taskgraph_digest or "",
        )
        if role in ("archer", "prism"):
            # M5 card diet: non-writer authority cards are lean by
            # construction (see the helper docstring).
            self._add_assignment_runtime_fields_authority(
                assignment, role, substate, task, pre_dirty, result_identity
            )
            return
        manifest = dict(state.current_manifest) if state.current_manifest is not None else None
        if manifest is not None:
            manifest.update({"pre_dirty_snapshot": pre_dirty, "result_identity": result_identity})
            if substate in ("GREEN", "REFACTOR", "PRISM_RED", "PRISM_FINAL", "DIAGNOSE"):
                manifest["r_tree_identity"] = state.r_tree_identity
        assignment.update(
            {
                "role": role,
                "substate": substate,
                "task_id": task.get("task_id") if task else None,
                "task": dict(task) if task else None,
                "manifest": manifest,
                "pre_dirty_snapshot": pre_dirty,
                "result_identity": result_identity,
                "commands": {
                    "unit": self._unit_commands(),
                    "test": self._test_commands(),
                    "guard": self._guard_commands(),
                },
            }
        )
        if task:
            self._copy_task_ref_keys(assignment, task)

    def _copy_task_ref_keys(self, assignment: dict, task: dict) -> None:
        """Writer-only per-task ref keys, copied from the task payload."""
        for key in (
            "issue_number",
            "ac_refs",
            "fr_refs",
            "if_ids",
            "test_refs",
            # B50 (#65): the split contract travels with the assignment
            # so Devon/Shield see the layer-resolved anchors explicitly.
            "unit_refs",
            "acceptance_refs",
            # B94 deferred anchors and integration marker
            "deferred_refs",
            "integration",
            # #129 debt ledger (pure annotation, carried to assignment)
            "debt",
        ):
            value = task.get(key)
            assignment[key] = list(value) if isinstance(value, tuple) else value

    def _add_assignment_runtime_fields_authority(
        self,
        assignment: dict,
        role: str,
        substate,
        task: dict | None,
        pre_dirty: dict[str, str],
        result_identity: str,
    ) -> None:
        """M5 card diet (convergence plan 2026-09-05): non-writer authority
        roles (archer/prism) get a lean dispatch card. They read tasks.json,
        design docs, and the event log from disk -- the writer execution
        contract (manifest) and the full task payload are dead weight in
        their card (run 01M19FJVES7G113RD8QXXY3PQZ: a 22534-byte Archer
        RULING card -- 12KB manifest + 6.5KB task -- blew the M5 dispatch
        budget). Their failure context rides the evidence channel
        (merged in _assignment_with_evidence, deliberately unbudgeted).
        ``task`` shrinks to a slim identity; the per-task ref keys stay at
        their kernel base values (None). Writer roles (devon/shield) keep
        the full contract unchanged."""
        assignment.update(
            {
                "role": role,
                "substate": substate,
                "task_id": task.get("task_id") if task else None,
                "task": (
                    {
                        "task_id": task.get("task_id"),
                        "issue_number": task.get("issue_number"),
                        "ac_refs": list(task.get("ac_refs") or ()),
                        "if_ids": list(task.get("if_ids") or ()),
                    }
                    if task
                    else None
                ),
                "manifest": None,
                "pre_dirty_snapshot": pre_dirty,
                "result_identity": result_identity,
                "commands": {
                    "unit": self._unit_commands(),
                    "test": self._test_commands(),
                    "guard": self._guard_commands(),
                },
            }
        )

    def _add_assignment_role_fields(
        self,
        assignment: dict,
        state: State,
        params: dict,
    ) -> None:
        role = params.get("role")
        substate = params.get("substate")
        if role == "devon":
            phase = assignment.get("phase")
            assignment["phase"] = phase if phase in ("red", "green", "refactor") else None
            if assignment["phase"] in ("green", "refactor"):
                assignment["r_tree_identity"] = state.r_tree_identity
            if assignment["phase"] == "red":
                assignment["red_test_paths"] = self._devon_red_test_dirs()
                # 同时刷新 manifest 里的 phase_rules.red 文本，让存量任务 retry 也看到新合同
                if isinstance(assignment.get("manifest"), dict):
                    manifest = assignment["manifest"]
                    manifest["red_test_paths"] = self._devon_red_test_dirs()
                    pr = manifest.get("phase_rules")
                    if isinstance(pr, dict):
                        pr["red"] = (
                            "write failing unit tests only under red_test_paths; "
                            "allowed_paths lists the green-phase impl scope "
                            "and is not writable in RED"
                        )
        elif role == "prism":
            assignment["criteria_pack"] = dict(_M_IMPL_CRITERIA_PACK)
            if substate in ("PRISM_RED", "PRISM_FINAL", "DIAGNOSE"):
                assignment["r_tree_identity"] = state.r_tree_identity
        elif role == "shield" and substate == "WRITE":
            assignment["phase"] = "shield_fix"
            self._strip_test_forbidden_paths(assignment)
            self._grant_diagnosed_test_paths(assignment, state)
            self._align_shield_allowed_to_layout(assignment)

    def _align_shield_allowed_to_layout(self, assignment: dict) -> None:
        """SHIELD_FIX manifest must carry the Shield layout domain, not the
        product task's allowed_paths. Run 01M0S0FQ v0.7 boundary
        (2026-08-29): the dispatch inherited T-017's product manifest
        (authenticity_existing.py only) while the diagnosed defects lived in
        frozen test files; the over-reach auditor (manifest.allowed_paths)
        rolled back every legal Shield test write. Align the manifest with
        [layout.shield] (the same whitelist result_checkpoint attributes
        Shield artifacts by) plus the diagnosed-path grants, keeping the
        product entries as a harmless union."""
        manifest = assignment.get("manifest")
        if not isinstance(manifest, dict):
            return
        from tracks.project import layout_paths

        allowed = list(manifest.get("allowed_paths") or [])
        manifest["allowed_paths"] = list(
            dict.fromkeys([*layout_paths(Path(self.repo), "shield"), *allowed])
        )

    @staticmethod
    def _strip_test_forbidden_paths(assignment: dict) -> None:
        """Shield needs to write to test paths (tests/integration/, etc.) to
        fix diagnosed test defects. The manifest from the task graph carries
        Devon's RGR forbidden_paths which include these test dirs. Strip
        test-path entries so Shield agent doesn't self-restrict (the audit
        already uses layout_paths("shield") as the whitelist)."""
        manifest = assignment.get("manifest")
        if isinstance(manifest, dict):
            forbidden = manifest.get("forbidden_paths")
            if isinstance(forbidden, list):
                manifest["forbidden_paths"] = [p for p in forbidden if not p.startswith("tests/")]

    _DIAGNOSED_TEST_PATH_RE = re.compile(r"tests/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+")
    _BARE_TEST_FILENAME_RE = re.compile(r"(?<![A-Za-z0-9_./])[A-Za-z0-9_./-]+\.(?:py|json|toml|md)")

    def _grant_diagnosed_test_paths(self, assignment: dict, state) -> None:
        """SHIELD_FIX audit unlock (run 01KZTHE7 T-017): the over-reach audit
        whitelists Shield by [layout] dirs (tests/integration|e2e|...), but a
        diagnosed defect may live anywhere under tests/ (e.g. a unit-level
        RED contract test). Grant exactly the test files the Prism DIAGNOSE
        verdict names in its evidence by adding them to
        manifest.allowed_paths.

        Bare filenames (``hotfix_support.py`` without the tests/ prefix) are
        resolved against the repository tree — run 01M0S0FQ v0.7 boundary
        (2026-08-29): the diagnosis narrated the file by bare name, the
        literal-path regex matched nothing, and Shield's legal fix was rolled
        back as over-reach. Fail-closed: a bare name with zero or ambiguous
        repo matches grants nothing."""
        report = getattr(state, "diagnose_report", None) or {}
        evidence = report.get("evidence") or ""
        if not isinstance(evidence, str):
            evidence = str(evidence)
        named = set(self._DIAGNOSED_TEST_PATH_RE.findall(evidence))
        for bare in set(self._BARE_TEST_FILENAME_RE.findall(evidence)):
            named.update(self._resolve_bare_test_path(bare))
        if not named:
            return
        manifest = assignment.get("manifest")
        if not isinstance(manifest, dict):
            return
        allowed = list(manifest.get("allowed_paths") or [])
        manifest["allowed_paths"] = allowed + sorted(p for p in named if p not in allowed)

    def _resolve_bare_test_path(self, filename: str) -> list[str]:
        """Repo-relative paths under tests/ whose basename equals filename.

        Fail-closed on ambiguity: a name that matches zero files, or more
        than one file (e.g. helpers.py in both integration/ and e2e/),
        grants nothing — those live under layout dirs Shield already has."""
        root = Path(self.repo) / "tests"
        if not root.is_dir():
            return []
        # OOB 2026-09-03 (run 01M19FJVES7G113RD8QXXY3PQZ): DIAGNOSE evidence
        # may embed absolute paths (e.g. PosixPath('/.../demo_host.py')).
        # Path.rglob rejects non-relative patterns with NotImplementedError,
        # which killed the whole run. Only the basename is meaningful here.
        name = filename.rsplit("/", 1)[-1].strip()
        if not name:
            return []
        try:
            hits = [p for p in root.rglob(name) if "__pycache__" not in p.parts and p.is_file()]
        except (NotImplementedError, ValueError):
            return []
        if len(hits) != 1:
            return []
        return [hits[0].relative_to(self.repo).as_posix()]

    def _invalid_m_impl_assignment(
        self,
        role: str,
        substate: str,
        assignment: dict | None,
    ) -> str | None:
        if role == "devon":
            error = self._invalid_devon_assignment(assignment)
            if error is not None:
                return error
        if (
            role == "shield"
            and substate == "WRITE"
            and not valid_test_tasks((assignment or {}).get("test_tasks"))
        ):
            return (
                "Shield WRITE assignment.test_tasks is invalid: each entry needs "
                "non-empty ac_id, if_ids, and test_paths"
            )
        return None

    @staticmethod
    def _invalid_devon_assignment(assignment: dict | None) -> str | None:
        required = (
            "task_id",
            "if_ids",
            "ac_refs",
            "test_refs",
            "commands",
            "manifest",
            "phase",
            "pre_dirty_snapshot",
            "result_identity",
        )
        data = assignment or {}
        missing = [key for key in required if MImplRuntimeMixin._assignment_key_missing(data, key)]
        phase = data.get("phase")
        if phase in ("green", "refactor") and not data.get("r_tree_identity"):
            missing.append("r_tree_identity")
        if missing:
            return f"Devon assignment missing: {', '.join(missing)}"
        return None

    @staticmethod
    def _assignment_key_missing(assignment: dict, key: str) -> bool:
        value = assignment.get(key)
        required_list = key in ("if_ids", "ac_refs", "test_refs", "commands", "manifest")
        return value is None or (required_list and not value)

    def _lookup_task(self, task_id: str):
        state = self.store.state(self.run_id)
        for raw in state.task_refs:
            if raw.get("task_id") == task_id:
                return self._task_node(raw)
        if state.current_task_metadata and state.current_task_metadata.get("task_id") == task_id:
            return self._task_node(state.current_task_metadata)
        tasks_path = self._vdir() / "tasks.json"
        if not tasks_path.exists():
            return None
        tasks, err = parse_tasks_json(tasks_path.read_text(encoding="utf-8"))
        if err is not None:
            return None
        return next((t for t in tasks if t.task_id == task_id), None)

    @staticmethod
    def _task_node(raw: dict) -> TaskNode:
        # B50 (#65): raw dicts arrive from two eras -- schema-2 payloads carry
        # the explicit unit_refs/acceptance_refs split, legacy event payloads
        # only the monolithic test_refs (layer-routed by path prefix, the
        # verified 196cbc9 convention: tests/unit/ -> RED obligation,
        # integration -> acceptance anchor). A raw with both split fields
        # empty but a non-empty test_refs is a legacy direct construction.
        test_refs = tuple(str(ref) for ref in (raw.get("test_refs") or ()))
        unit_refs = tuple(raw.get("unit_refs") or ())
        acceptance_refs = tuple(raw.get("acceptance_refs") or ())
        if not unit_refs and not acceptance_refs and test_refs:
            unit_refs = tuple(r for r in test_refs if r.startswith("tests/unit/"))
            acceptance_refs = tuple(r for r in test_refs if not r.startswith("tests/unit/"))
        # B94 deferred anchors (optional, default empty; integration flag)
        deferred_raw = raw.get("deferred_refs") or ()
        deferred_refs = tuple(
            str(r) for r in deferred_raw if isinstance(r, str)
        )
        integration = bool(raw.get("integration", False))
        # #129 debt ledger (optional, default empty; pure annotation)
        debt_raw = raw.get("debt") or ()
        debt = tuple(dict(item) for item in debt_raw if isinstance(item, dict))
        return TaskNode(
            task_id=raw["task_id"],
            issue_number=raw["issue_number"],
            description=raw["description"],
            ac_refs=tuple(raw["ac_refs"]),
            fr_refs=tuple(raw["fr_refs"]),
            if_ids=tuple(raw["if_ids"]),
            test_refs=tuple(unit_refs) + tuple(acceptance_refs) or test_refs,
            scope_boundary=raw["scope_boundary"],
            depends_on=tuple(raw["depends_on"]),
            batch=raw["batch"],
            parallel=raw["parallel"],
            budget=raw["budget"],
            unit_refs=unit_refs,
            acceptance_refs=acceptance_refs,
            schema=int(raw.get("schema") or 1),
            deferred_refs=deferred_refs,
            integration=integration,
            debt=debt,
        )

    def _current_manifest(self) -> dict | None:
        state = self.store.state(self.run_id)
        if state.current_manifest is not None:
            return dict(state.current_manifest)
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "task.started":
                manifest = ev.payload.get("manifest")
                return dict(manifest) if isinstance(manifest, dict) else None
        return None

    def _last_devon_verdict(self) -> str | None:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "outcome.received" and ev.payload.get("role") == "devon":
                return ev.payload.get("verdict")
        return None

    def _last_devon_outcome(self) -> dict | None:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "outcome.received" and ev.payload.get("role") == "devon":
                return ev.payload
        return None

    def _devon_evidence_error(self, phase: str, state: State) -> str | None:
        outcome = self._last_devon_outcome()
        if not outcome or outcome.get("status") != "done":
            return "Devon did not provide a completed outcome"
        error = self._devon_evidence_fields_error(phase, outcome)
        if error is not None:
            return error
        error = self._devon_evidence_change_error(phase, outcome)
        if error is not None:
            return error
        return self._devon_evidence_scope_error(phase, state, outcome)

    @staticmethod
    def _devon_evidence_fields_error(phase: str, outcome: dict) -> str | None:
        required = (
            "phase",
            "changed_paths",
            "commands",
            "manifest_compliance",
            "pre_identity",
            "post_identity",
            "implemented_if_ids",
        )
        missing = [key for key in required if key not in outcome]
        if missing:
            return "Devon evidence missing: " + ", ".join(missing)
        if outcome.get("phase") != phase:
            return f"Devon evidence phase mismatch: expected {phase}"
        if not isinstance(outcome.get("changed_paths"), list):
            return "Devon evidence changed_paths must be a list"
        if any(not isinstance(path, str) or not path.strip() for path in outcome["changed_paths"]):
            return "Devon evidence changed_paths contains an invalid path"
        if not isinstance(outcome.get("commands"), list):
            return "Devon evidence commands must be a list"
        if outcome.get("manifest_compliance") is not True:
            return "Devon manifest_compliance is not true"
        if outcome.get("pre_identity") in (None, "") or outcome.get("post_identity") in (None, ""):
            return "Devon evidence is missing pre/post identity"
        if not isinstance(outcome.get("implemented_if_ids"), list):
            return "Devon evidence implemented_if_ids must be a list"
        if phase in ("green", "refactor") and not outcome.get("r_identity"):
            return "Devon evidence missing r_identity"
        return None

    @staticmethod
    def _devon_evidence_change_error(phase: str, outcome: dict) -> str | None:
        if phase == "red" and not outcome.get("changed_paths"):
            return "Devon RED evidence has no changed paths"
        if (
            phase == "green"
            and not outcome.get("changed_paths")
            and not outcome.get("no_change_reason")
        ):
            # Symmetric with refactor: a GREEN resubmit may legitimately carry
            # no new paths when the implementation is already on disk from a
            # prior attempt that passed GREEN_GATE but was killed by a later
            # guard (run 01KZTHE7 T-013 attempt 2, 2026-08-15). Require an
            # explicit no_change_reason so the gate does not short-circuit a
            # genuinely missing implementation; the unit commands still run.
            return "Devon GREEN evidence needs changed paths or no_change_reason"
        if (
            phase == "refactor"
            and not outcome.get("changed_paths")
            and not outcome.get("no_change_reason")
        ):
            return "Devon REFACTOR evidence needs changed paths or no_change_reason"
        return None

    def _devon_evidence_scope_error(
        self,
        phase: str,
        state: State,
        outcome: dict,
    ) -> str | None:
        task = state.current_task_metadata or {}
        expected = set(task.get("if_ids", []))
        claimed = set(outcome.get("implemented_if_ids", []))
        if not claimed <= expected:
            return "Devon evidence claims an IF id outside the current task"
        manifest = state.current_manifest or {}
        allowed = set(manifest.get("allowed_paths", []))
        forbidden = set(manifest.get("forbidden_paths", []))
        # RED evidence is a failing test that must live in a Devon test dir
        # (tests/unit/). Such paths are exempt from the allowed_paths gate
        # because impl-only manifests (no unit test_refs) grant no test path;
        # frozen Shield suites remain blocked by forbidden_paths, and the
        # post-loop RED check below still requires a test-dir location.
        devon_test_dirs = self._devon_red_test_dirs() if phase == "red" else []
        path_error = _devon_path_scope_error(
            outcome["changed_paths"], allowed, forbidden, devon_test_dirs
        )
        if path_error is not None:
            return path_error
        if (
            phase == "red"
            and devon_test_dirs
            and any(
                not any(path.startswith(d + "/") or path == d for d in devon_test_dirs)
                for path in outcome["changed_paths"]
            )
        ):
            return "Devon RED evidence includes a non-test path"
        return None

    def _frozen_test_paths(self) -> list[str]:
        try:
            contract = load_contract(self.repo)
        except ContractError:
            return []
        paths: list[str] = []
        for section in (contract.integration, contract.e2e):
            if section is None:
                continue
            paths.extend(section.paths)
        return sorted(set(paths))

    def _approval_digest(self) -> str:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "approval.recorded":
                return ev.payload.get("digest", "")
            if ev.type == "baseline.inherited":
                # Hotfixes consume the approved target-version baseline rather
                # than recreating an approval in their delta directory.
                return str(ev.payload.get("baseline_digest") or "")
        return ""

    def _issue_evidence(self) -> str:
        for ev in reversed(list(self.store.events(self.run_id))):
            if ev.type == "issues.created":
                mapping = ev.payload.get("mapping", {})
                return json.dumps(
                    mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            if ev.type == "hotfix.requested":
                issue = ev.payload.get("issue")
                if issue is not None:
                    return json.dumps({"hotfix_issue": issue}, separators=(",", ":"))
        return ""

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
        ac_ids, if_registry, doc_errors = MImplRuntimeMixin._taskgraph_docs(
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

    def _do_check_island_1(self, cmd, state, task_id, reconcile):
        vdir = self._vdir()
        errors = self._island_taskgraph_errors(
            vdir,
            vdir / "tasks.json",
            state,
            self._requirements_baseline_dir(state, vdir),
        )
        if errors:
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "; ".join(errors),
                    "evidence": "\n".join(errors),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        self._emit("verdict.passed", {"check": "island_1"}, command_id=cmd.command_id)

    def _island_taskgraph_errors(
        self,
        vdir: Path,
        tasks_path: Path,
        state: State,
        requirements_dir: Path | None = None,
    ) -> list[str]:
        raw, error = self._read_taskgraph(tasks_path)
        if error is not None:
            return [error]  # keep the full reason including remediation hint
        tasks, error = parse_tasks_json(raw)
        if error is not None:
            return [error]
        if (
            state.taskgraph_digest
            and hashlib.sha256(raw.encode("utf-8")).hexdigest() != state.taskgraph_digest
        ):
            return ["tasks.json changed after taskgraph.committed"]
        errors = self._taskgraph_errors(
            vdir,
            tasks,
            requirements_dir,
            state.hotfix_anchor_acs if state.hotfix_issue is not None else None,
        )
        arch_path = vdir / "architecture.md"
        if not arch_path.is_file():
            errors.append("architecture.md missing")
        else:
            ok, closure = validate_island_closure(
                tasks,
                arch_path.read_text(encoding="utf-8"),
            )
            if not ok:
                errors.extend(closure)
        return errors

    def _pass_island_2(self, cmd, state, replay: bool = False) -> None:
        """Emit the island_2 verdict and dispatch the fail-closed acceptance.

        Kept as one chokepoint so every ISLAND_GATE_2 success path (clean
        FULL round, ledger-FIXED proofs, fallback proofs) demonstrates the
        same acceptance contract.
        """
        self._demonstrate_island_gate_2(state, replay)
        self._emit("verdict.passed", {"check": "island_2"}, command_id=cmd.command_id)

    def _demonstrate_island_gate_2(self, state, replay: bool = False):
        """IF-FAILCLOSED-001: run the fail-closed demonstration at the gate.

        Resolves the run version's ``island_gate_2`` capability callback
        (T-013 v0.7 extension) and invokes it lazily, dispatching
        ``demonstrate_failclosed`` (T-016) over the canonical host sequence.
        Early versions resolve no callback and skip (classic behaviour,
        FR-0264-02); a registered extension that lacks the callback
        propagates ``CapabilityBlockedError`` fail-closed (no pseudo-success).

        SHIELD_FIX (issue 100 / T-016): ``demonstrate_failclosed`` only writes
        ``failclosed.demonstrated``/``failclosed.summary`` through its injected
        ``flush`` callable -- without one it falls back to printing and the
        append-only store (observed by the acceptance anchors via
        ``event_log``) never receives the events.  Inject a store-emit flush
        so the demonstration lands on the public event outlet.
        """
        callback = _resolve_island_gate_2(state.version or "")
        if callback is None:
            return None

        def _flush(host: str, event_type: str, payload) -> None:
            payload = dict(payload)
            payload["host"] = host
            self._emit(event_type, payload)

        return callback(_canonical_demo_hosts(), replay=replay, flush=_flush)

    def _do_check_island_2(self, cmd, state, task_id, reconcile):
        if reconcile and state.island_2_passed:
            return
        from tracks.checks.reach import check_reach_file

        reach = check_reach_file(self.repo)
        if reach.status != "pass":
            self._emit(
                "verdict.failed",
                {
                    "check": "island",
                    "reason": "reach check failed: " + "; ".join((*reach.errors, *reach.islands)),
                    "evidence": str(reach),
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        try:
            ledger = rebuild_ledger(
                [
                    {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
                    for ev in self.store.events(self.run_id)
                ]
            )
            full_rows = [ev for ev in self.store.events(self.run_id) if ev.type == "full.executed"]
            if full_rows and not full_rows[-1].payload.get("passed"):
                self._reconcile_full_failure_wal(cmd, state, full_rows[-1], ledger)
                ledger = rebuild_ledger(self.store.events(self.run_id))
            if not full_rows:
                round_name = "FULL_1"
            elif ledger_is_clean(ledger):
                round_name = "FULL_F"
            elif any(value == "FIXED" for value in ledger.values()):
                self._prove_fixed_ledger_entries(cmd, state, ledger)
                return
            elif any(value == "OPEN" for value in ledger.values()):
                self._settle_stale_island_2_open(cmd, state, ledger)
                return
            else:
                raise LedgerCorruptionError(
                    "FULL chain cannot execute while ledger is awaiting an external repair"
                )
            full = self._execute_full_round(cmd, state, round_name, ledger)
            self._record_full_failures(cmd, state, full, ledger)
            if full["failed_nodes"]:
                detail = "; ".join(full["failed_nodes"])
                self._emit(
                    "verdict.failed",
                    {
                        "check": "full_suite",
                        "reason": "FULL chain failures: " + detail,
                        "evidence": full["outcomes_ref"],
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                )
                return
            self._pass_island_2(cmd, state, reconcile)
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"FULL chain failed closed: {type(exc).__name__}: {exc}",
                evidence="FULL selection/result/ledger",
                task_id=None,
                attempt=state.current_attempt + 1,
            )

    def _settle_stale_island_2_open(self, cmd, state, ledger) -> None:
        """IF-FULLCHAIN-001 stale-identity settlement closure (island_2 OPEN).

        The OPEN branch runs one real FULL round first; non-PROVEN identities
        re-verified green — ``(node, signature)`` absent from this round's
        failure set and node in this round's executed set — are settled by
        the IF-LEDGER-001 emitter (OPEN→CLASSIFIED→FIXED with the explicit
        ``stale_identity_settlement`` program reason, never STALE), then the
        existing fallback proof settles every FIXED entry PROVEN in one full
        pass.  Identities still failing this round keep the per-item
        diagnosis loop and are never swallowed; WAL inconsistency fails
        closed through ``LedgerCorruptionError``.
        """
        full = self._execute_full_round(cmd, state, "full_settlement", ledger)
        self._record_full_failures(cmd, state, full, ledger)
        ledger = rebuild_ledger(self.store.events(self.run_id))
        round_failures = [
            (failure["node"], failure["failure_signature"]) for failure in full["failures"]
        ]
        for event in settle_stale_identities(ledger, full["evidence_by_node"], round_failures):
            self._emit(
                "ledger.transitioned",
                {
                    **event["payload"],
                    "attempt": state.current_attempt + 1,
                    "actor": "runtime",
                },
                command_id=cmd.command_id,
            )
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if full["failed_nodes"]:
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": "FULL settlement round failures: " + "; ".join(full["failed_nodes"]),
                    "evidence": full["outcomes_ref"],
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        if any(value == "FIXED" for value in rebuilt.values()):
            self._prove_fixed_via_fallback(cmd, state, rebuilt)
            return
        if ledger_is_clean(rebuilt):
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "stale settlement left the FULL ledger unclean without FIXED entries",
                "evidence": full["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _current_baseline_digest(self) -> str:
        return next(
            (
                str(ev.payload.get("digest") or "")
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "baseline.frozen" and ev.payload.get("status") == "current"
            ),
            "",
        )

    def _run_full_layers(self, cmd, round_name: str, sections, inventory):  # pylint: disable=too-many-locals
        """Run every declared FULL layer, staging one test result per layer."""
        outcomes: list[dict] = []
        command_echo: dict[str, list[str]] = {}
        staged: list[Path] = []
        try:
            for layer, section in sections.items():
                assert section is not None
                selected = sorted(node for node, owner in inventory.items() if owner == layer)
                section_cwd = self.repo / section.cwd if section.cwd != "." else self.repo
                result_path = self._result_staging_path(
                    cmd.command_id, f"full-{round_name.lower()}-{layer}"
                )
                staged.append(result_path)
                result_path.unlink(missing_ok=True)
                argv = resolve_selected_command(
                    section.run,
                    [],
                    str(result_path),
                    Path(section_cwd),
                )
                if not audit_selection_argv(
                    section.run,
                    [],
                    str(result_path),
                    list(argv),
                    Path(section_cwd),
                ):
                    raise TestSelectError(f"[{layer}] FULL argv diverges from contract")
                obs = execute_gate_command(shlex.join(argv), str(section_cwd), f"full-{layer}")
                command_echo[layer] = list(obs.argv)
                cases = parse_test_result(result_path)
                mapping = require_exact_node_coverage(cases, selected)
                outcomes.extend(
                    {
                        "node": node,
                        "status": mapping[node].status,
                        "detail": mapping[node].detail or "",
                    }
                    for node in selected
                )
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
            if staged:
                with contextlib.suppress(OSError):
                    staged[0].parent.rmdir()
        return outcomes, command_echo

    def _execute_full_round(self, cmd, state, round_name: str, ledger) -> dict:  # pylint: disable=too-many-locals
        contract = load_contract(self.repo)
        sections = {
            "unit": contract.unit,
            "integration": contract.integration,
            "e2e": contract.e2e,
        }
        if any(section is None for section in sections.values()):
            raise TestSelectError("FULL requires unit, integration, and e2e sections")
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "FULL collect failed")
        nodes = sorted(inventory)
        baseline = self._current_baseline_digest()
        if not baseline:
            raise TestSelectError("FULL lacks a frozen M-IMPL baseline identity")
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=nodes,
            scope="full",
            basis=f"full:{round_name}",
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        node_ref = self.store.write_audit_blob(
            [{"node": node, "layer": inventory[node]} for node in nodes]
        )
        if node_ref is None:
            raise TestSelectError("FULL nodes blob write failed")
        self._emit(
            "test.selected",
            {
                "scope": "full",
                "basis": f"full:{round_name}",
                "nodes_count": len(nodes),
                "nodes": nodes,
                "nodes_blob": f".tracks/runtime/blobs/{node_ref}",
                "baseline": baseline,
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": None,
                "task_ifs": None,
            },
            command_id=cmd.command_id,
        )
        outcomes, command_echo = self._run_full_layers(cmd, round_name, sections, inventory)
        command_identity = self._selection_command_identity(command_echo)
        identity = EvidenceIdentity(
            tree=tree_stamp,
            command=command_identity,
            env=self._gate_environment_identity(),
            selection_id=selection_id,
        )
        evidence_by_node = {
            outcome["node"]: evidence_identity(
                identity,
                outcome["node"],
                outcome["status"],
                state.current_attempt + 1,
                "runtime",
            )
            for outcome in outcomes
        }
        persisted_outcomes = [
            {**outcome, "evidence_id": evidence_by_node[outcome["node"]]} for outcome in outcomes
        ]
        outcomes_ref = self.store.write_audit_blob(persisted_outcomes)
        if outcomes_ref is None:
            raise TestSelectError("FULL outcomes blob write failed")
        failed = self._signed_failures(outcomes)
        serves_as_full_f = not failed and (
            (round_name == "FULL_1" and not ledger) or round_name == "fallback_full"
        )
        payload = {
            "round": round_name,
            "suite": ["unit", "integration", "e2e"],
            "passed": not failed,
            "failed_nodes": [outcome["node"] for outcome in failed],
            "command_echo": command_echo,
            "evidence_ids": sorted(evidence_by_node.values()),
            "serves_as_full_f": serves_as_full_f,
            "outcomes_ref": f".tracks/runtime/blobs/{outcomes_ref}",
            "gate": "ISLAND_GATE_2",
            "selection_id": selection_id,
            "failures": failed,
            "evidence_by_node": evidence_by_node,
        }
        event_payload = {
            key: value
            for key, value in payload.items()
            if key not in ("selection_id", "failures", "evidence_by_node")
        }
        self._emit("full.executed", event_payload, command_id=cmd.command_id)
        return payload

    def _reconcile_full_failure_wal(self, cmd, state, full_event, ledger) -> None:
        outcomes = self._read_runtime_blob(full_event.payload.get("outcomes_ref"))
        if not isinstance(outcomes, list):
            raise LedgerCorruptionError("failed full.executed lacks replayable outcomes WAL")
        failures = []
        evidence_by_node = {}
        for outcome in outcomes:
            if not isinstance(outcome, dict) or not outcome.get("node"):
                raise LedgerCorruptionError("FULL outcomes WAL is malformed")
            if outcome.get("status") not in ("failed", "error"):
                continue
            if not outcome.get("evidence_id"):
                raise LedgerCorruptionError("FULL failure WAL lacks evidence identity")
            failures.append(
                {
                    **outcome,
                    "failure_signature": self._full_failure_signature(outcome),
                }
            )
            evidence_by_node[str(outcome["node"])] = str(outcome["evidence_id"])
        if sorted(item["node"] for item in failures) != sorted(
            full_event.payload.get("failed_nodes") or []
        ):
            raise LedgerCorruptionError("FULL outcomes WAL disagrees with failed_nodes")
        selection = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "test.selected"
                and ev.seq < full_event.seq
                and ev.payload.get("scope") == "full"
            ),
            None,
        )
        if selection is None:
            raise LedgerCorruptionError("failed FULL lacks its preceding selection WAL")
        self._record_full_failures(
            cmd,
            state,
            {
                "selection_id": selection.payload.get("selection_id"),
                "failures": failures,
                "evidence_by_node": evidence_by_node,
            },
            ledger,
        )

    @staticmethod
    def _full_failure_signature(failure: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "node": failure["node"],
                    "status": failure["status"],
                    "detail": failure["detail"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _record_full_failures(self, cmd, state, full: dict, ledger) -> None:
        identities = {tuple(json.loads(key)): value for key, value in ledger.items()}
        for failure in full["failures"]:
            signature = failure.get("failure_signature") or self._full_failure_signature(failure)
            identity = (failure["node"], signature)
            prior = identities.get(identity)
            if prior == "PROVEN":
                self._emit(
                    "ledger.transitioned",
                    {
                        "node": failure["node"],
                        "failure_signature": signature,
                        "from": "PROVEN",
                        "to": "OPEN",
                        "attempt": state.current_attempt + 1,
                        "actor": "runtime",
                        "reason": "FULL reobserved a proven failure signature",
                    },
                    command_id=cmd.command_id,
                )
            elif prior is None:
                owner = self._full_failure_owner(failure["node"], state)
                self._emit(
                    "ledger.opened",
                    {
                        "node": failure["node"],
                        "failure_signature": signature,
                        "state": "OPEN",
                        "selection_id": full["selection_id"],
                        "evidence_id": full["evidence_by_node"][failure["node"]],
                        "reason": failure["detail"] or failure["status"],
                        **owner,
                    },
                    command_id=cmd.command_id,
                )

    def _full_failure_owner(self, node: str, state) -> dict:
        path = node.partition("::")[0]
        owners = [
            task
            for task in state.task_refs
            # B50 (#65): anchor ownership follows the declared acceptance
            # refs (legacy payloads fall back to their test_refs mapping).
            if any(
                str(ref).partition("::")[0] == path
                for ref in (task.get("acceptance_refs") or task.get("test_refs") or [])
            )
        ]
        if not owners and len(state.task_refs) == 1:
            owners = [state.task_refs[0]]
        if len(owners) != 1:
            return {}
        task = owners[0]
        task_id = str(task.get("task_id") or "")
        manifest = next(
            (
                dict(ev.payload.get("manifest") or {})
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "task.started" and ev.payload.get("task_id") == task_id
            ),
            {},
        )
        r_sha = next(
            (
                str(ev.payload.get("r_sha") or "")
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "red.checkpointed" and ev.payload.get("task_id") == task_id
            ),
            "",
        )
        return {
            "task_id": task_id,
            "task": dict(task),
            "manifest": manifest,
            "r_sha": r_sha,
        }

    def _transition_full_ledger(
        self,
        cmd,
        before: str,
        after: str,
        reason: str,
        actor: str,
    ) -> bool:
        ledger = rebuild_ledger(self.store.events(self.run_id))
        candidates = [key for key, value in ledger.items() if value == before]
        if not candidates:
            return False
        key = sorted(candidates)[0]
        node, signature = json.loads(key)
        owner = next(
            (
                {
                    field: ev.payload.get(field)
                    for field in ("task_id", "task", "manifest", "r_sha")
                    if ev.payload.get(field)
                }
                for ev in self.store.events(self.run_id)
                if ev.type == "ledger.opened"
                and ev.payload.get("node") == node
                and ev.payload.get("failure_signature") == signature
            ),
            {},
        )
        self._emit(
            "ledger.transitioned",
            {
                "node": node,
                "failure_signature": signature,
                "from": before,
                "to": after,
                "attempt": self.store.state(self.run_id).current_attempt + 1,
                "actor": actor,
                "reason": reason,
                **owner,
            },
            command_id=cmd.command_id,
        )
        return True

    @staticmethod
    def _fixed_ledger_key(ledger) -> str:
        fixed = next((key for key, value in sorted(ledger.items()) if value == "FIXED"), None)
        if fixed is None:
            raise LedgerCorruptionError("ledger proof requested without a FIXED entry")
        return fixed

    def _last_done_outcome_paths(self) -> list:
        return next(
            (
                list(ev.payload.get("changed_paths") or [])
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "outcome.received"
                and ev.payload.get("status") == "done"
                and ev.payload.get("role") in ("devon", "shield")
            ),
            [],
        )

    @staticmethod
    def _diff_test_reach(inventory, path) -> set:
        if not str(path).startswith("tests/"):
            return set()
        return {candidate for candidate in inventory if candidate.partition("::")[0] == str(path)}

    def _prove_fixed_ledger_entries(self, cmd, state, ledger) -> None:
        node, signature = json.loads(self._fixed_ledger_key(ledger))
        touched = self._last_done_outcome_paths()
        inventory, collect_error = self._collect_all_declared_layers()
        if inventory is None:
            raise TestSelectError(collect_error or "SELECT_DIFF collect failed")
        selection = select_diff(
            {"node": node, "failure_signature": signature, "state": "FIXED"},
            touched,
            lambda path: self._diff_test_reach(inventory, path),
        )
        if selection.reliable:
            self._prove_fixed_via_diff(cmd, state, node, signature, selection, inventory, ledger)
            return
        self._prove_fixed_via_fallback(cmd, state, ledger)

    def _prove_fixed_via_diff(
        self, cmd, state, node, signature, selection, inventory, ledger
    ) -> None:
        proof = self._execute_diff_selection(
            cmd,
            state,
            node,
            signature,
            selection,
            inventory,
        )
        target_failed = any(
            failure["node"] == node and failure["failure_signature"] == signature
            for failure in proof["failures"]
        )
        self._emit(
            "ledger.transitioned",
            {
                "node": node,
                "failure_signature": signature,
                "from": "FIXED",
                "to": "OPEN" if target_failed else "PROVEN",
                "attempt": state.current_attempt + 1,
                "actor": "runtime",
                "reason": "SELECT_DIFF proof result",
            },
            command_id=cmd.command_id,
        )
        self._record_full_failures(cmd, state, proof, ledger)
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if target_failed or not ledger_is_clean(rebuilt):
            self._emit(
                "verdict.failed",
                {
                    "check": "full_suite",
                    "reason": "SELECT_DIFF did not prove every ledger entry",
                    "evidence": proof["outcomes_ref"],
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
            )
            return
        final = self._execute_full_round(cmd, state, "FULL_F", rebuilt)
        self._record_full_failures(cmd, state, final, rebuilt)
        if not final["failed_nodes"]:
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "FULL_F revealed failures after SELECT_DIFF proof",
                "evidence": final["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _prove_fixed_via_fallback(self, cmd, state, ledger) -> None:
        full = self._execute_full_round(cmd, state, "fallback_full", ledger)
        failed_identities = {
            (failure["node"], failure["failure_signature"]) for failure in full["failures"]
        }
        for key, value in sorted(ledger.items()):
            if value != "FIXED":
                continue
            node, signature = json.loads(key)
            self._emit(
                "ledger.transitioned",
                {
                    "node": node,
                    "failure_signature": signature,
                    "from": "FIXED",
                    "to": "OPEN" if (node, signature) in failed_identities else "PROVEN",
                    "attempt": state.current_attempt + 1,
                    "actor": "runtime",
                    "reason": "fallback FULL proof result",
                },
                command_id=cmd.command_id,
            )
        self._record_full_failures(cmd, state, full, ledger)
        rebuilt = rebuild_ledger(self.store.events(self.run_id))
        if full["passed"] and ledger_is_clean(rebuilt):
            self._pass_island_2(cmd, state)
            return
        self._emit(
            "verdict.failed",
            {
                "check": "full_suite",
                "reason": "fallback FULL did not prove every ledger entry",
                "evidence": full["outcomes_ref"],
                "attempt": state.current_attempt + 1,
            },
            command_id=cmd.command_id,
        )

    def _run_diff_layers(self, cmd, nodes, sections, inventory):  # pylint: disable=too-many-locals
        """Run the SELECT_DIFF layers that own selected nodes."""
        outcomes: list[dict] = []
        commands: dict[str, list[str]] = {}
        staged: list[Path] = []
        try:
            for layer, section in sections.items():
                selected = [node for node in nodes if inventory[node] == layer]
                if not selected:
                    continue
                if section is None:
                    raise TestSelectError(f"SELECT_DIFF lacks [{layer}] contract")
                section_cwd = self.repo / section.cwd if section.cwd != "." else self.repo
                result_path = self._result_staging_path(cmd.command_id, f"select-diff-{layer}")
                staged.append(result_path)
                result_path.unlink(missing_ok=True)
                argv = resolve_selected_command(
                    section.run_selected,
                    selected,
                    str(result_path),
                    Path(section_cwd),
                )
                if not audit_selection_argv(
                    section.run_selected,
                    selected,
                    str(result_path),
                    list(argv),
                    Path(section_cwd),
                ):
                    raise TestSelectError(f"[{layer}] SELECT_DIFF argv diverges from contract")
                obs = execute_gate_command(
                    shlex.join(argv), str(section_cwd), f"select-diff-{layer}"
                )
                commands[layer] = list(obs.argv)
                mapping = require_exact_node_coverage(parse_test_result(result_path), selected)
                outcomes.extend(
                    {
                        "node": node,
                        "status": mapping[node].status,
                        "detail": mapping[node].detail or "",
                    }
                    for node in selected
                )
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
        return outcomes, commands

    def _signed_failures(self, outcomes) -> list[dict]:
        return [
            {**outcome, "failure_signature": self._full_failure_signature(outcome)}
            for outcome in outcomes
            if outcome["status"] in ("failed", "error")
        ]

    def _execute_diff_selection(  # pylint: disable=too-many-locals
        self,
        cmd,
        state,
        ledger_node: str,
        failure_signature: str,
        selection,
        inventory,
    ) -> dict:
        contract = load_contract(self.repo)
        sections = {
            "unit": contract.unit,
            "integration": contract.integration,
            "e2e": contract.e2e,
        }
        baseline = self._current_baseline_digest()
        if not baseline:
            raise TestSelectError("SELECT_DIFF lacks frozen baseline identity")
        nodes = sorted(selection.nodes)
        if any(node not in inventory for node in nodes):
            raise TestSelectError("SELECT_DIFF contains a node absent from full collect")
        commit = git(self.repo, "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp()
        selection_id = make_selection_id(
            nodes=nodes,
            scope="select_diff",
            basis=selection.basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        node_ref = self.store.write_audit_blob(
            [{"node": node, "layer": inventory[node]} for node in nodes]
        )
        if node_ref is None:
            raise TestSelectError("SELECT_DIFF nodes blob write failed")
        self._emit(
            "test.selected",
            {
                "scope": "select_diff",
                "basis": selection.basis,
                "nodes_count": len(nodes),
                "nodes": nodes,
                "nodes_blob": f".tracks/runtime/blobs/{node_ref}",
                "baseline": baseline,
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": state.current_task_id,
                "task_ifs": list((state.current_task_metadata or {}).get("if_ids") or []),
                "ledger_node": ledger_node,
                "failure_signature": failure_signature,
            },
            command_id=cmd.command_id,
            task_id=state.current_task_id,
        )
        outcomes, commands = self._run_diff_layers(cmd, nodes, sections, inventory)
        failures = self._signed_failures(outcomes)
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            raise TestSelectError("SELECT_DIFF outcomes blob write failed")
        identity = EvidenceIdentity(
            tree=tree_stamp,
            command=self._selection_command_identity(commands),
            env=self._gate_environment_identity(),
            selection_id=selection_id,
        )
        evidence_by_node = {
            outcome["node"]: evidence_identity(
                identity,
                outcome["node"],
                outcome["status"],
                state.current_attempt + 1,
                "runtime",
            )
            for outcome in outcomes
        }
        return {
            "selection_id": selection_id,
            "failed_nodes": [failure["node"] for failure in failures],
            "failures": failures,
            "evidence_by_node": evidence_by_node,
            "outcomes_ref": f".tracks/runtime/blobs/{outcomes_ref}",
        }

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
        task.completed events after the latest taskgraph.committed."""
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
        return set(state.retained_completed_task_ids or []) | current_gen

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

    @staticmethod
    def _parse_allowed_paths(scope_boundary: str) -> list[str]:
        parts = (p.strip().replace("\\", "/") for p in scope_boundary.replace("\n", ",").split(","))
        return sorted(
            {
                p.rstrip("/")
                for p in parts
                if p and not p.startswith("/") and ".." not in p.split("/")
            }
        )

    def _forbidden_paths(self) -> list[str]:
        forbidden = {".tracks/projects/**"}
        # Shield's writable dirs are forbidden for Devon (from project.toml).
        for path in layout_paths(self.repo, "shield"):
            clean = path.rstrip("/")
            forbidden.update({path, f"{clean}/**"})
        for path in self._frozen_test_paths():
            clean = path.rstrip("/")
            forbidden.update({path, f"{clean}/**"})
        return sorted(forbidden)

    def _test_commands(self) -> dict:
        try:
            contract = load_contract(self.repo)
        except ContractError:
            return {}
        return {
            name: section.run
            for name, section in (("integration", contract.integration), ("e2e", contract.e2e))
            if section is not None
        }

    def _guard_commands(self) -> list[str]:
        """(attempt-4 language neutrality) Guard command construction
        resolves the interpreter prefix through the host contract's
        declared install interpreter (IF-HOSTCONTRACT-001) — never a
        hardcoded env spelling (NFR-0147)."""
        interp = declared_install_interpreter(Path(self.repo))
        return [
            f"{interp} -m ruff check",
            f"{interp} -m flake8 --select=CCR001",
            f"{interp} -m pylint --disable=all --enable=R0801,C0302,R0915,R0914",
        ]

    def _unit_commands(self) -> list[str]:
        test_dirs = self._devon_red_test_dirs()
        if not test_dirs:
            return []
        unit_run = _contract_unit_run(self.repo)
        return [unit_run] if unit_run else []

    def _devon_red_test_dirs(self) -> list[str]:
        """RED-phase test write grant dirs: devon layout dirs containing 'test'.

        与 _devon_evidence_error 和 _unit_commands 同源派生，避免漂移。
        RED phase 允许 Devon 在这些目录写 failing unit tests，即使
        manifest.allowed_paths (impl scope) 不含测试路径。
        """
        return [d.rstrip("/") for d in layout_paths(self.repo, "devon") if "test" in d]

    def _task_manifest(self, task: TaskNode, state: State) -> dict:
        pre_dirty = self._dirty_snapshot()
        # The task's baseline identity is the committed taskgraph digest; a
        # graph-less replay falls back to the last frozen BASELINE digest
        # (single resolution site, shared with the scenario B reconcile).
        baseline_digest = state.taskgraph_digest or self._last_baseline_digest()
        allowed = self._task_allowed_paths(task)
        # B94 integration exemption: allowed = union of all task scopes
        if getattr(task, "integration", False):
            try:
                from tracks.executor.deferred_gate import integration_allowed_paths

                all_nodes = [self._task_node(r) for r in (state.task_refs or [])]
                if all_nodes:
                    allowed = integration_allowed_paths(all_nodes)
            except Exception:
                pass
        manifest = {
            "task_id": task.task_id,
            # M5 card diet: the full task payload already rides
            # assignment["task"] for writers; task_ref only pins the identity
            # (a duplicated 6.5KB payload cost the deadlocked RULING card).
            "task_ref": {"task_id": task.task_id},
            # OOB 2026-09-05 (writer-card dedup, second pass): drop the
            # manifest's copies of the task's ref lists too -- they ride
            # assignment.task exactly once. Zero readers of the manifest
            # copies (verified by grep); the triple carriage pushed T-042's
            # card over the M5 budget on contract data alone
            # (16489 > 16384, run 01M19FJVES7G113RD8QXXY3PQZ seq 3084).
            "issue_number": task.issue_number,
            "scope_boundary": task.scope_boundary,
            "allowed_paths": allowed,
            "forbidden_paths": self._forbidden_paths(),
            "red_test_paths": self._devon_red_test_dirs(),
            "frozen_test_paths": self._frozen_test_paths(),
            "phase_rules": {
                "red": "write failing unit tests only under red_test_paths; "
                "allowed_paths lists the green-phase impl scope and is not writable in RED",
                "green": "write implementation only; keep R tests immutable",
                "refactor": "quality-only changes; preserve green behavior",
            },
            "budget": task.budget,
            "baseline_identity": baseline_digest,
            "unit_commands": self._unit_commands(),
            "test_commands": self._test_commands(),
            "guard_commands": self._guard_commands(),
            "pre_dirty_snapshot": pre_dirty,
            "r_tree_identity": None,
            "result_identity": None,
        }
        manifest["result_identity"] = self._result_identity(
            task.task_id,
            "manifest",
            state.current_attempt,
            pre_dirty,
            baseline_digest,
        )
        return manifest

    def _task_allowed_paths(self, task: TaskNode) -> list[str]:
        # B94: integration tasks are exempt from scope isolation (union)
        if getattr(task, "integration", False):
            try:
                from tracks.executor.deferred_gate import integration_allowed_paths

                state = self.store.state(self.run_id)
                all_nodes = [self._task_node(r) for r in (state.task_refs or [])]
                # include current task if not yet in refs (e.g. during commit)
                if not any(n.task_id == task.task_id for n in all_nodes):
                    all_nodes.append(task)
                if all_nodes:
                    return integration_allowed_paths(all_nodes)
            except Exception:
                pass
        allowed = set(self._parse_allowed_paths(task.scope_boundary))
        # B50 (#65): only unit_refs are Devon-writable RED artifacts; legacy
        # graphs declare none (their test_refs are integration acceptance
        # anchors, Shield-owned and frozen).
        for ref in task.unit_refs:
            path = ref.split("::", 1)[0].strip()
            if path.startswith("tests/unit/"):
                allowed.add(path)
        return sorted(allowed)

    def _result_identity(
        self,
        task_id: str,
        phase: str,
        attempt: int,
        pre_dirty: dict[str, str],
        baseline_digest: str,
    ) -> str:
        material = json.dumps(
            {
                "run_id": self.run_id,
                "task_id": task_id,
                "phase": phase,
                "attempt": attempt,
                "pre_dirty": pre_dirty,
                "baseline": baseline_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _do_run_task_gates(self, cmd, state, task_id, reconcile):
        gate = cmd.params.get("gate")
        if gate == "RED_GATE":
            self._emit_task_gate(cmd, state, "red", "red_invalid", "red_valid")
        elif gate == "GREEN_GATE":
            self._run_green_gate(cmd, state)
        elif gate == "TASK_REVIEW":
            self._run_task_review_gate(cmd, state)
        if gate in ("RED_GATE", "GREEN_GATE", "TASK_REVIEW"):
            self._rebuild_task_log_projection()

    def _unit_commands_from_manifest(self) -> list[str]:
        commands = (self._current_manifest() or {}).get("unit_commands")
        if (
            isinstance(commands, list)
            and commands
            and all(isinstance(item, str) and item.strip() for item in commands)
        ):
            return list(commands)
        unit_run = _contract_unit_run(self.repo)
        return [unit_run] if unit_run else []

    def _existing_gate_handle(self, task_id: str) -> WorktreeHandle | None:
        """Reuse a pre-existing gate worktree for this task, if one exists."""
        gate_path = os.path.join(
            str(self.repo), ".tracks", "worktrees", self.run_id, task_id, "gate"
        )
        if task_id and os.path.isdir(gate_path):
            # Pre-existing worktree (e.g., test-created, or a leftover from a
            # cleanup failure): re-link runtime assets (.opencode) so gate unit
            # commands see the deployment meta-tests compare against (T-013).
            ensure_runtime_assets(str(self.repo), gate_path)
            # B2 (run 01KZTHE7, user ruling: no cross-gate sharing): return a
            # real handle so the caller's finally-cleanup removes the worktree.
            # The old ``return gate_path, None`` made every pre-existing gate
            # worktree a permanent leak (five accumulated across T-013..T-018;
            # ISLAND_GATE_2 reach then reported 1208 phantom islands).
            return WorktreeHandle(path=gate_path, base_sha="", kind="gate")
        return None

    @staticmethod
    def _usable_tree_identity(r_sha) -> bool:
        return bool(r_sha and r_sha.strip() and r_sha != "0" * 40)

    def _refactor_gate_base(self, task_id: str):
        green = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "green.committed" and ev.payload.get("task_id") == task_id
            ),
            None,
        )
        if green is None or not green.payload.get("g_sha"):
            return None
        return green.payload["g_sha"]

    def _latest_phase_diff_ref(self, phase: str):
        return next(
            (
                ev.payload.get("diff_ref")
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "outcome.received" and ev.payload.get("phase") == phase
            ),
            None,
        )

    def _gate_candidate_inputs(self, state: State, task_id: str, phase: str):
        """Resolve ``(r_sha, base, diff)`` for creating a gate worktree.

        Returns None when the gate must fall back to the repo cwd instead.
        """
        # Try to create a gate worktree from base B + Green impl + R tests
        r_sha = state.r_tree_identity
        if not self._usable_tree_identity(r_sha):
            return None  # Bogus R, can't create worktree

        base_sha = red_base_sha(str(self.repo), r_sha)
        if base_sha is None:
            return None  # Can't derive base B from R

        # When the immutable R tests are already present in the observed
        # working tree, run the gate against that state so a hidden R test
        # mutation is surfaced as `regression`. Only when they are missing
        # from the tree (they live solely inside the R commit) is a gate
        # worktree needed to restore them next to the Green impl.
        if self._r_tests_in_working_tree(r_sha):
            return None

        candidate_base = base_sha
        target_phase = "refactor" if phase == "refactor" else "green"
        if target_phase == "refactor":
            candidate_base = self._refactor_gate_base(task_id)
            if candidate_base is None:
                return None
        candidate_diff = self._latest_phase_diff_ref(target_phase)
        if not candidate_diff or not candidate_diff.strip():
            if target_phase == "green":
                return None
            candidate_diff = ""
        return r_sha, candidate_base, candidate_diff

    def _ensure_gate_worktree(
        self,
        state: State,
        phase: str = "green",
    ) -> tuple[str, WorktreeHandle | None]:
        """Find or create a gate worktree with R tests + Green impl applied.

        Returns (cwd, handle). If handle is not None, the caller must clean it up
        via cleanup_worktree(handle). If handle is None, the cwd is the repo
        itself (no cleanup needed).
        """
        task_id = state.current_task_id or ""
        # Keep the method callable against the lightweight Runtime-shaped test
        # doubles used by the worktree contract tests.
        existing = MImplRuntimeMixin._existing_gate_handle(self, task_id)
        if existing is not None:
            return existing.path, existing

        candidate = self._gate_candidate_inputs(state, task_id, phase)
        if candidate is None:
            return str(self.repo), None
        r_sha, candidate_base, candidate_diff = candidate

        try:
            handle = create_gate_worktree(
                str(self.repo),
                candidate_base,
                r_sha,
                candidate_diff,
                self.run_id,
                task_id,
            )
            return handle.path, handle
        except Exception:
            return str(self.repo), None  # Creation failed, fall back to repo cwd

    def _r_tests_in_working_tree(self, r_sha: str) -> bool:
        """Whether every tests/ file in the immutable R commit is present as a
        file in the main working tree (the observed candidate state)."""
        proc = git(
            self.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", r_sha, check=False
        )
        if proc.returncode != 0:
            return False
        rels = [
            line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("tests/")
        ]
        return bool(rels) and all(os.path.isfile(os.path.join(str(self.repo), rel)) for rel in rels)

    def _path_in_tree(self, sha: str, path: str) -> bool:
        """Whether ``path`` exists in the ``sha`` commit tree. Fail-open on git
        errors (missing/bogus sha) so a resolution failure skips the path
        instead of false-killing the GREEN gate."""
        proc = git(self.repo, "cat-file", "-e", f"{sha}:{path}", check=False)
        return proc.returncode == 0

    @staticmethod
    def _parse_diff_paths(diff_text: str) -> set[str]:
        """Extract the changed paths from a captured git diff (``diff --git
        a/<path> b/<path>`` headers). Parsing is deliberately naive and
        fail-open: an unparseable path is skipped rather than false-killing
        the gate (quoted/space-y paths are outside the repo's plain style)."""
        paths = set()
        for line in diff_text.splitlines():
            if not line.startswith("diff --git a/"):
                continue
            rest = line[len("diff --git a/") :]
            sep = rest.rfind(" b/")
            if sep != -1:
                paths.add(rest[:sep])
        return paths

    def _green_regression_tests(self, r_sha: str, state: State) -> list[str]:
        """R-frozen tests/ files this task's GREEN actually touched: the union
        of the Runtime's authoritative captured diff paths (parsed from the
        outcome diff_ref) and Devon's claimed changed_paths. Empty list means
        no regression — no R-frozen test was modified (B39, see seq 728)."""
        outcome = self._last_devon_outcome() or {}
        claimed = set(outcome.get("changed_paths") or [])
        captured_diff = self._validated_diff("green", state)[1]
        captured = self._parse_diff_paths(captured_diff) if captured_diff else set()
        candidates = claimed | captured
        return sorted(
            p for p in candidates if p.startswith("tests/") and self._path_in_tree(r_sha, p)
        )

    def _gate_evidence(self, obs) -> str:
        """Failed-gate evidence for the fixer relay: FAILED summary lines
        plus the blob path of the full captured output. Fixers are forbidden
        from re-running integration/e2e suites, so the evidence itself must
        carry everything they cannot obtain (user directive 2026-08-15)."""
        ref = self.store.write_audit_blob(
            {
                "argv": list(obs.argv),
                "cwd": obs.cwd,
                "exit_code": obs.exit_code,
                "stdout": obs.stdout,
                "stderr": obs.stderr,
            }
        )
        extra = {"failed": failed_summary_lines(obs.stdout)}
        if ref:
            extra["log_ref"] = f".tracks/runtime/blobs/{ref}"
        return observation_evidence(obs, extra)

    def _emit_gate_failure(
        self, cmd, *, check, reason, task_id, attempt, evidence="", failure_class=None
    ):
        if failure_class is None and _is_evidence_shape_error(reason):
            failure_class = "evidence_malformed"
            check = "evidence_malformed"
        payload: dict = {
            "check": check,
            "reason": reason,
            "evidence": evidence,
            "task_id": task_id,
            "attempt": attempt,
        }
        if failure_class is not None:
            payload["failure_class"] = failure_class
        self._emit(
            "verdict.failed",
            payload,
            command_id=cmd.command_id,
            task_id=task_id,
        )

    _LINT_TIMEOUT_SECONDS = 120

    def _lint_targets(self, phase: str) -> list[str]:
        """B4 (issue #5): Devon's delivered files this phase, lint-scoped.

        RED lints delivered test files (under tests/); GREEN lints
        delivered non-test sources. Only .py files existing on disk are
        handed over — the runtime stays language-neutral.
        """
        outcome = self._last_devon_outcome() or {}
        changed = outcome.get("changed_paths") or []

        def _under_tests(path: str) -> bool:
            return path.startswith("tests/")

        want_tests = phase == "red"
        return [
            path
            for path in changed
            if path.endswith(".py")
            and _under_tests(path) == want_tests
            and (self.repo / path).is_file()
        ]

    def _run_declared_lint(self, paths: list[str]) -> tuple[str | None, str]:
        """Run the Archer-declared lint command ([lint].check) over paths.

        Returns (findings, note): findings is None when clean or skipped,
        otherwise the linter output that fails the gate as check=lint.
        Fail-open on tool-environment problems (missing binary, timeout):
        hygiene tooling must not block the pipeline; actual findings
        fail closed (B4, issue #5).
        """
        from tracks.project import lint_check_command

        command = lint_check_command(self.repo)
        if command is None or not paths:
            return None, ""
        argv = shlex.split(command) + list(paths)
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                timeout=self._LINT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, f"lint skipped: {exc}"
        if proc.returncode != 0:
            return (proc.stdout + "\n" + proc.stderr).strip() or "lint failed", ""
        return None, ""

    @staticmethod
    def _expand_unit_refs(refs, unit_inventory: list[str]) -> list[str]:
        """B50 (#65): expand schema-2 ``unit_refs`` against the unit collect
        inventory, fail-closed on any absent ref.

        Unit refs are Devon's RED obligations -- the immutable R commit
        carries them (``_require_r_artifacts``) and only unit-layer nodes
        enter the RED node set. A FILE ref (no ``::``) expands to every
        collected node in that file (explicit whole-file declaration, not
        the retired path-prefix inference)."""
        inventory = list(unit_inventory or [])
        by_path: dict[str, list[str]] = {}
        for node in inventory:
            by_path.setdefault(node.partition("::")[0], []).append(node)
        selected: set[str] = set()
        for raw in refs or []:
            ref = str(raw).strip()
            path = ref.partition("::")[0]
            nodes = by_path.get(path)
            if nodes is None or ("::" in ref and ref not in inventory):
                raise TestSelectError(f"task unit ref is absent from unit collect: {ref}")
            if "::" in ref:
                selected.add(ref)
            else:
                selected.update(nodes)
        return sorted(selected)

    def _acceptance_anchors(
        self,
        refs,
        integration_inventory: list[str],
        plan_text: str,
        schema: int,
    ) -> list[str]:
        """B50 (#65): resolve the task's DECLARED acceptance anchors against
        the integration collect inventory.

        Binding is declared, not inferred: every ``acceptance_refs`` entry
        must resolve at gate time (NODE item exact, FILE item expands to the
        whole file) -- absent anchors fail closed. Schema-2 graphs add the
        §8 cross-check: a declared anchor must be a planned acceptance row
        target (the §8 IF index is retired as a binding source and kept only
        as this planning-contract cross-validation). Legacy (schema-1)
        graphs map their historical test_refs here and skip the cross-check."""
        by_path: dict[str, list[str]] = {}
        for node in integration_inventory or []:
            by_path.setdefault(node.partition("::")[0], []).append(node)
        resolved: set[str] = set()
        for raw in refs or []:
            self._resolve_one_acceptance_ref(raw, by_path, resolved)
        if schema == 2:
            self._require_plan_anchor_rows(resolved, plan_text)
        return sorted(resolved)

    @staticmethod
    def _resolve_one_acceptance_ref(raw, by_path: dict[str, list[str]], resolved: set[str]) -> None:
        """Resolve one acceptance_ref entry into ``resolved`` (fail-closed).

        A NODE entry must be an exact inventory node; a FILE entry expands to
        every inventory node in that file."""
        ref = str(raw).strip()
        path = ref.partition("::")[0]
        nodes = by_path.get(path)
        if nodes is None or ("::" in ref and ref not in nodes):
            raise TestSelectError(f"task acceptance ref is absent from integration collect: {ref}")
        if "::" in ref:
            resolved.add(ref)
        else:
            resolved.update(nodes)

    def _require_plan_anchor_rows(self, anchors: set[str], plan_text: str) -> None:
        """B50 (#65): every declared acceptance anchor must appear in §8."""
        if not plan_text or not anchors:
            return
        planned: set[str] = set()
        for _ac_id, layer_cell, test_cell, _if_cell in _coverage_rows_with_test(plan_text):
            if not self._row_has_integration_layer(layer_cell, test_cell):
                continue
            for path, node in self._integration_row_targets(test_cell):
                planned.add(node if node is not None else path)
        for anchor in sorted(anchors):
            if not self._anchor_is_planned(anchor, planned):
                raise TestSelectError(
                    f"task acceptance ref is not a test-plan §8 integration row target: {anchor}"
                )

    @staticmethod
    def _anchor_is_planned(anchor: str, planned: set[str]) -> bool:
        """PRISM-B49B50-R1-01: symmetric FILE/NODE coverage -- a NODE anchor
        is planned when §8 names it or its whole file; a FILE anchor is
        planned when §8 names the file or any node in it."""
        if anchor in planned or anchor.partition("::")[0] in planned:
            return True
        if "::" not in anchor:
            return any(p.startswith(anchor + "::") for p in planned)
        return False

    @staticmethod
    def _row_has_integration_layer(layer_cell: str | None, test_cell: str | None) -> bool:
        """Whether a §8 row names the integration layer and carries tests."""
        if not test_cell:
            return False
        layers = {
            part.strip().lower()
            for part in re.split(r"[,/+;\s]+", layer_cell or "")
            if part.strip()
        }
        return "integration" in layers

    @staticmethod
    def _integration_row_targets(test_cell: str) -> list[tuple[str, str | None]]:
        """(file, node|None) pairs a §8 test_cell names, item by item.

        Operator finding (2026-08-24, run 01M0S0FQ T-001): the old
        file-level expansion took only the FIRST item of a multi-test row
        (``A + B + C``) and then matched the WHOLE file -- dragging sibling
        tests of other tasks/IFs into this task's green requirement. Parse
        every ``+``-separated item: a NODE item (``file::test``) binds that
        node exactly; a FILE item binds the whole file.

        PRISM-B49B50-R1-01 (#65): delegates to the shared taskgraph parser
        so the gate cross-check and the commit-time coverage closure anchor
        identical (path, node) pairs."""
        return plan_row_targets(test_cell)

    def _collect_layer_inventories(self, contract, cwd: str) -> dict[str, list[str]]:
        """Run unit/integration (and e2e for B94 deferred) collects."""
        inventories: dict[str, list[str]] = {}
        layers = [("unit", contract.unit), ("integration", contract.integration)]
        # B94: deferred may be e2e, so collect e2e when present
        if getattr(contract, "e2e", None) is not None:
            layers.append(("e2e", contract.e2e))
        for layer, section in layers:
            if section is None:
                inventories[layer] = []
                continue
            section_cwd = str(Path(cwd) / section.cwd) if section.cwd != "." else cwd
            obs = execute_gate_command(section.collect, section_cwd, f"{layer}-collect")
            if obs.exit_code == 5:
                inventories[layer] = []
                continue
            if obs.exit_code != 0:
                # B94: e2e is optional for deferred; missing dir is not a hard
                # failure (unit/integration missing is). Treat as empty.
                if layer == "e2e":
                    inventories[layer] = []
                    continue
                raise TestSelectError(
                    f"[{layer}] collect failed (rc={obs.exit_code}): "
                    f"{(obs.stderr or obs.stdout).strip()[:400]}"
                )
            inventories[layer] = sorted(parse_collected_nodes(obs.stdout))
        return inventories

    def _task_r_family_shas(self, task_id: str) -> list[str]:
        """B91 family semantics: every ``red.checkpointed`` slot of this task
        (RED re-pins and sanctioned Shield rebaselines alike) is part of the
        task's immutable RED lineage. A multi-round task accumulates slots;
        each slot's content was red_valid-gated when pinned."""
        return list(
            dict.fromkeys(
                ev.payload["r_sha"]
                for ev in self.store.events(self.run_id)
                if ev.type == "red.checkpointed"
                and ev.payload.get("task_id") == task_id
                and ev.payload.get("r_sha")
            )
        )

    def _require_r_artifacts(self, red_nodes: list[str], r_sha: str, task_id: str) -> None:
        """A declared RED unit artifact must exist in the task's immutable R
        lineage. Run 01M0S0FQ T-016 (v0.7 boundary, 2026-08-28): the re-scoped
        task's unit_refs still declared the reach test pinned in earlier
        family slots while the latest RED re-pin carried only the new
        obligation's test -- a current-slot-only check fail-closed a legal
        multi-generation task, so presence is evaluated across the whole R
        family (current r_tree_identity included)."""
        shas = [s for s in dict.fromkeys([r_sha, *self._task_r_family_shas(task_id)]) if s]
        for node in red_nodes:
            path = node.partition("::")[0]
            if not shas or not any(self._path_in_tree(s, path) for s in shas):
                raise TestSelectError(
                    f"task RED unit artifact is absent from immutable R commit: {node}"
                )

    def _effective_refs_for_gate(self, task: TaskNode) -> list[str]:
        if getattr(task, "integration", False):
            try:
                from tracks.executor.deferred_gate import integration_hard_refs

                all_nodes = [
                    self._task_node(r) for r in (self.store.state(self.run_id).task_refs or [])
                ]
                return integration_hard_refs(task, all_nodes)
            except Exception:
                return list(task.acceptance_refs)
        return list(task.acceptance_refs) + list(getattr(task, "deferred_refs", ()) or [])

    def _resolve_e2e_nodes(self, e2e_refs: list[str], inventories: dict) -> set[str]:
        if not e2e_refs:
            return set()
        by_path: dict[str, list[str]] = {}
        for node in inventories.get("e2e", []) or []:
            by_path.setdefault(node.partition("::")[0], []).append(node)
        out: set[str] = set()
        for raw in e2e_refs:
            self._resolve_one_acceptance_ref(raw, by_path, out)
        return out

    def _collect_task_gate_nodes(self, cwd: str, task: TaskNode):
        """Collect unit/integration inventories and compute SELECT_TASK.

        B50 (#65) declared-binding contract: ``unit_refs`` expand strictly
        against the unit inventory (RED nodes, R-artifact gated); declared
        ``acceptance_refs`` resolve against the integration inventory and
        join the green requirement directly -- the §8 IF-index inference is
        retired (§8 remains as the schema-2 cross-validation and the
        planning-time coverage closure).
        B94: selection set is acceptance ∪ deferred; integration hard gate
        merges all deferred."""
        contract = load_contract(self.repo)
        inventories = self._collect_layer_inventories(contract, cwd)
        red_nodes = self._expand_unit_refs(task.unit_refs, inventories["unit"])
        r_sha = self.store.state(self.run_id).r_tree_identity or ""
        self._require_r_artifacts(red_nodes, r_sha, task.task_id)
        touched = [
            path
            for path in (self._last_devon_outcome() or {}).get("changed_paths", [])
            if str(path).startswith("tests/unit/")
        ]
        plan_path = self._vdir() / "test-plan.md"
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        effective = self._effective_refs_for_gate(task)
        int_refs = [r for r in effective if str(r).startswith("tests/integration/")]
        e2e_refs = [r for r in effective if str(r).startswith("tests/e2e/")]
        acceptance_nodes = self._acceptance_anchors(
            int_refs, inventories.get("integration", []), plan_text, task.schema
        )
        if e2e_refs:
            e2e_nodes = self._resolve_e2e_nodes(e2e_refs, inventories)
            acceptance_nodes = sorted(set(acceptance_nodes) | e2e_nodes)
        nodes = select_task(red_nodes, touched, inventories["unit"], acceptance_nodes)
        if not nodes:
            raise TestSelectError(f"empty SELECT_TASK for task {task.task_id}")
        return contract, nodes

    def _all_task_nodes(self):
        state = self.store.state(self.run_id)
        return [self._task_node(r) for r in (state.task_refs or [])]

    def _completed_ids(self) -> set[str]:
        completed: set[str] = set()
        for ev in self.store.events(self.run_id):
            if ev.type == "task.completed" and ev.payload.get("task_id"):
                completed.add(str(ev.payload.get("task_id")))
        state = self.store.state(self.run_id)
        completed.update(set(getattr(state, "retained_completed_task_ids", []) or []))
        return completed

    def _failed_ids(self, failed_nodes) -> list[str]:
        ids = [str(f.get("node") or "") for f in (failed_nodes or []) if isinstance(f, dict)]
        if not ids and failed_nodes:
            ids = [str(x) for x in failed_nodes if isinstance(x, str)]
        return ids

    def _check_drift_breaker(self, cmd, task, failed_nodes) -> None:
        """B94 drift breaker: failed - legal != empty -> drift_breaker."""
        if not failed_nodes:
            return
        try:
            from tracks.executor.deferred_gate import legal_red_refs, unexpected_reds

            legal = legal_red_refs(self._all_task_nodes(), self._completed_ids())
            failed_ids = self._failed_ids(failed_nodes)
            unexpected = unexpected_reds(failed_ids, legal)
            if unexpected:
                self._emit(
                    "drift_breaker",
                    {
                        "unexpected": unexpected,
                        "failed": sorted(failed_ids),
                        "legal": sorted(legal),
                    },
                    command_id=cmd.command_id,
                    task_id=task.task_id,
                )
        except Exception:
            return

    @staticmethod
    def _gate_environment_identity() -> str:
        trac_env = {key: value for key, value in os.environ.items() if key.startswith("TRAC_")}
        material = json.dumps(
            {
                "python": sys.version,
                "runner": "1.0.0",
                "trac_env": trac_env,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _task_content_snapshot(self, root: Path, rules) -> dict[str, str]:
        """Content identities for every file covered by task scope rules."""
        entries: dict[str, str] = {}
        for raw in sorted({str(rule) for rule in rules if str(rule).strip()}):
            rule = raw[:-3].rstrip("/") if raw.endswith("/**") else raw.rstrip("/")
            path = root / rule
            if path.is_dir():
                files = sorted(
                    item for item in path.rglob("*") if item.is_file() or item.is_symlink()
                )
                if not files:
                    entries[rule] = "empty-dir"
                for item in files:
                    entries[str(item.relative_to(root))] = self._path_identity(item)
            else:
                entries[rule] = self._path_identity(path)
        return entries

    @staticmethod
    def _snapshot_digest(entries: dict[str, str]) -> str:
        canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _selection_command_identity(commands) -> tuple[str, ...]:
        """Encode each selected layer's actual argv without losing boundaries."""
        return tuple(
            json.dumps(
                {"layer": layer, "argv": list(commands[layer])},
                separators=(",", ":"),
            )
            for layer in sorted(commands)
        )

    def _run_task_selected_layers(self, cmd, contract, cwd: str, by_layer):  # pylint: disable=too-many-locals
        """Execute the SELECT_TASK layers owning selected nodes.

        Returns (outcomes, failed_nodes, commands, templates,
        forensics_ref); staging paths are always cleaned up, even when a
        layer raises.
        B94: also handles e2e deferred anchors when present.
        M2 (convergence plan 2026-09-05): every layer subprocess runs with
        TRAC_FORENSICS_DIR set under .tracks/runtime/forensics/<command_id>/
        -- failing integration nodes persist their git-state forensic
        package there (conftest hook); when failures occur the package is
        written to an audit blob and referenced from the failure evidence
        so DIAGNOSE rules on live evidence, never post-teardown inference
        (T-042 :78 "wrong tree" misdiagnosis class).
        """
        outcomes: list[dict] = []
        failed_nodes: list[dict] = []
        staged: list[Path] = []
        commands: dict[str, tuple[str, ...]] = {}
        templates: dict[str, dict[str, str]] = {}
        forensics_root = self._forensics_root(cmd.command_id)
        try:
            layers = [
                ("unit", contract.unit),
                ("integration", contract.integration),
                ("e2e", getattr(contract, "e2e", None)),
            ]
            for layer, section in layers:
                if section is None:
                    continue
                selected = by_layer.get(layer) or []
                if not selected:
                    continue
                section_cwd = str(Path(cwd) / section.cwd) if section.cwd != "." else cwd
                result_path = self._result_staging_path(cmd.command_id, f"green-{layer}")
                staged.append(result_path)
                result_path.unlink(missing_ok=True)
                argv = resolve_selected_command(
                    section.run_selected,
                    selected,
                    str(result_path),
                    Path(section_cwd),
                )
                if not audit_selection_argv(
                    section.run_selected,
                    selected,
                    str(result_path),
                    list(argv),
                    Path(section_cwd),
                ):
                    raise TestSelectError(f"[{layer}] selected argv diverges from contract")
                obs = execute_gate_command(
                    shlex.join(argv),
                    section_cwd,
                    f"{layer}-selected",
                    env_extra={"TRAC_FORENSICS_DIR": str(forensics_root / layer)},
                )
                commands[layer] = tuple(obs.argv)
                templates[layer] = {
                    "run_selected": section.run_selected,
                    "cwd": section.cwd,
                }
                cases = parse_test_result(result_path)
                mapping = require_exact_node_coverage(cases, selected)
                self._record_task_node_outcomes(outcomes, failed_nodes, selected, mapping)
        finally:
            for path in staged:
                path.unlink(missing_ok=True)
            if staged:
                with contextlib.suppress(OSError):
                    staged[0].parent.rmdir()
        forensics_ref = self._collect_forensics_ref(forensics_root, failed_nodes)
        return outcomes, failed_nodes, commands, templates, forensics_ref

    def _forensics_root(self, command_id: str) -> Path:
        """M2: per-selection forensic capture root under runtime state.

        ``.tracks/runtime/forensics/<command_id>/<layer>/`` is untracked
        runtime state (never dirties the frozen tree); sibling dirs older
        than 24h are pruned so the diagnostic window stays bounded."""
        import time as _time

        root = paths.runtime_dir(paths.tracks_home(Path(self.repo))) / "forensics"
        root.mkdir(parents=True, exist_ok=True)
        cutoff = _time.time() - 24 * 3600
        for stale in root.iterdir():
            try:
                if stale.is_dir() and stale.stat().st_mtime < cutoff:
                    shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                continue
        return root / command_id

    def _collect_forensics_ref(self, forensics_root: Path, failed_nodes) -> str | None:
        """M2: persist the captured forensic package to an audit blob.

        Returns the blob path (referenced from the failure evidence) when
        failures occurred and the conftest hook captured forensics for
        them; None otherwise (nothing failed / nothing captured -- the
        evidence chain stays exactly as before)."""
        if not failed_nodes:
            return None
        records: list[dict] = []
        for path in sorted(forensics_root.glob("*/failures/*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        if not records:
            return None
        ref = self.store.write_audit_blob(
            {
                "forensics_dir": str(forensics_root),
                "records": records,
            }
        )
        if ref is None:
            return None
        return f".tracks/runtime/blobs/{ref}"

    @staticmethod
    def _record_task_node_outcomes(outcomes, failed_nodes, selected, mapping) -> None:
        for node in selected:
            case = mapping[node]
            detail = case.detail or ""
            outcomes.append({"node": node, "status": case.status, "detail": detail})
            if case.status != "passed":
                failed_nodes.append({"node": node, "status": case.status, "detail": detail})

    def _by_layer(self, nodes: list[str]) -> dict[str, list[str]]:
        return {
            "unit": [n for n in nodes if n.startswith("tests/unit/")],
            "integration": [n for n in nodes if n.startswith("tests/integration/")],
            "e2e": [n for n in nodes if n.startswith("tests/e2e/")],
        }

    def _is_deferred_only(self, failed_nodes, hard, deferred) -> bool:
        return bool(failed_nodes and not hard and deferred)

    def _snapshot_ref_or_raise(self, scope_rules, snapshot, commands, templates) -> str:
        ref = self.store.write_audit_blob(
            {
                "rules": scope_rules,
                "entries": snapshot,
                "commands": {layer: list(argv) for layer, argv in commands.items()},
                "templates": templates,
            }
        )
        if ref is None:
            raise TestSelectError("GREEN content identity blob write failed")
        return f".tracks/runtime/blobs/{ref}"

    def _raise_hard_failure(
        self, selection_id: str, outcomes_ref_path: str, hard, deferred,
        forensics_ref: str | None = None,
    ) -> None:
        evidence = {
            "selection_id": selection_id,
            "outcomes_ref": outcomes_ref_path,
            "failed_nodes": hard,
            "deferred_failures": (
                [str(x.get("node") or "") for x in deferred]
                if deferred
                else []
            ),
        }
        if forensics_ref:
            # M2: live forensic package (git state at failure + preserved
            # repos) -- DIAGNOSE must rule on this, never on static
            # post-teardown inference.
            evidence["forensics_ref"] = forensics_ref
        raise TaskSelectionFailure(
            "selected task tests did not all pass",
            json.dumps(evidence, sort_keys=True),
        )

    def _success_payload(
        self,
        selection_id,
        nodes_blob,
        outcomes_ref_path,
        evidence_ids,
        content_digest,
        command_identity,
        env_identity,
        scope_rules,
        snapshot,
        commands,
        templates,
    ) -> dict:
        snapshot_ref = self._snapshot_ref_or_raise(
            scope_rules, snapshot, commands, templates
        )
        return {
            "selection_id": selection_id,
            "nodes_blob": nodes_blob,
            "outcomes_ref": outcomes_ref_path,
            "evidence_ids": sorted(evidence_ids),
            "snapshot_ref": snapshot_ref,
            "identity_basis": {
                "tree": content_digest,
                "command": list(command_identity),
                "env": env_identity,
                "selection_id": selection_id,
            },
        }

    def _deferred_success_payload(
        self,
        selection_id,
        nodes_blob,
        outcomes_ref_path,
        evidence_ids,
        content_digest,
        command_identity,
        env_identity,
        scope_rules,
        snapshot,
        commands,
        templates,
        deferred,
    ) -> dict:
        payload = self._success_payload(
            selection_id,
            nodes_blob,
            outcomes_ref_path,
            evidence_ids,
            content_digest,
            command_identity,
            env_identity,
            scope_rules,
            snapshot,
            commands,
            templates,
        )
        payload["deferred_failures"] = sorted(
            str(x.get("node") or "") for x in deferred
        )
        return payload

    def _execute_task_selection(self, cmd, state: State, cwd: str, task: TaskNode) -> dict:
        """Emit and execute one task_if selection through contract run_selected.
        B94: selection includes deferred refs (and integration hard gate)."""
        contract, nodes = self._collect_task_gate_nodes(cwd, task)
        by_layer = self._by_layer(nodes)
        baseline = str(state.r_tree_identity or "")
        if not baseline:
            raise TestSelectError("SELECT_TASK lacks immutable R baseline identity")
        commit = git(Path(cwd), "rev-parse", "HEAD", check=False).stdout.strip()
        tree_stamp = self._dirty_tree_stamp(Path(cwd))
        basis = f"task:{task.task_id}:ifs:{','.join(sorted(task.if_ids))}"
        selection_id = make_selection_id(
            nodes=nodes,
            scope="task_if",
            basis=basis,
            baseline=baseline,
            commit=commit,
            tree_stamp=tree_stamp,
        )
        self._stale_prior_task_selection(cmd, task, nodes, baseline, selection_id)
        node_ref = self.store.write_audit_blob(
            [
                {
                    "node": node,
                    "layer": "unit" if node.startswith("tests/unit/") else "integration",
                }
                for node in nodes
            ]
        )
        if node_ref is None:
            raise TestSelectError("SELECT_TASK nodes blob write failed")
        nodes_blob = f".tracks/runtime/blobs/{node_ref}"
        self._emit(
            "test.selected",
            {
                "scope": "task_if",
                "basis": basis,
                "nodes_count": len(nodes),
                "nodes": nodes,
                "nodes_blob": nodes_blob,
                "baseline": baseline,
                "commit": commit,
                "tree_stamp": tree_stamp,
                "selection_id": selection_id,
                "task_id": task.task_id,
                "task_ifs": list(task.if_ids),
            },
            command_id=cmd.command_id,
            task_id=task.task_id,
        )
        scope_rules = list((state.current_manifest or {}).get("allowed_paths", []))
        scope_rules.extend(node.partition("::")[0] for node in nodes)
        scope_rules = sorted(set(scope_rules))
        snapshot = self._task_content_snapshot(Path(cwd), scope_rules)
        content_digest = self._snapshot_digest(snapshot)
        env_identity = self._gate_environment_identity()
        outcomes, failed_nodes, commands, templates, forensics_ref = (
            self._run_task_selected_layers(cmd, contract, cwd, by_layer)
        )
        command_identity = self._selection_command_identity(commands)
        identity = EvidenceIdentity(
            tree=content_digest,
            command=command_identity,
            env=env_identity,
            selection_id=selection_id,
        )
        evidence_ids = [
            evidence_identity(
                identity,
                outcome["node"],
                outcome["status"],
                state.current_attempt + 1,
                "runtime",
            )
            for outcome in outcomes
        ]
        outcomes_ref = self.store.write_audit_blob(outcomes)
        if outcomes_ref is None:
            raise TestSelectError("GREEN outcomes blob write failed")
        outcomes_ref_path = f".tracks/runtime/blobs/{outcomes_ref}"
        self._check_drift_breaker(cmd, task, failed_nodes)
        from tracks.executor.deferred_gate import partition_failures

        hard, deferred = partition_failures(
            failed_nodes, getattr(task, "deferred_refs", ()) or ()
        )
        if self._is_deferred_only(failed_nodes, hard, deferred):
            return self._deferred_success_payload(
                selection_id,
                nodes_blob,
                outcomes_ref_path,
                evidence_ids,
                content_digest,
                command_identity,
                env_identity,
                scope_rules,
                snapshot,
                commands,
                templates,
                deferred,
            )
        if hard:
            self._raise_hard_failure(
                selection_id, outcomes_ref_path, hard, deferred,
                forensics_ref=forensics_ref,
            )
        return self._success_payload(
            selection_id,
            nodes_blob,
            outcomes_ref_path,
            evidence_ids,
            content_digest,
            command_identity,
            env_identity,
            scope_rules,
            snapshot,
            commands,
            templates,
        )

    def _emit_stale_refs(
        self,
        cmd,
        task_id: str,
        selection_refs,
        evidence_refs,
        reason: str,
    ) -> None:
        targets = emit_stale_propagation(
            {
                "selection_refs": selection_refs,
                "evidence_refs": evidence_refs,
            }
        )
        already_stale = {
            (str(target.get("kind")), str(target.get("ref")))
            for ev in self.store.events(self.run_id)
            if ev.type == "evidence.staled"
            for target in (ev.payload.get("targets") or [])
            if isinstance(target, dict)
        }
        fresh_targets = [
            target for target in targets if (target["kind"], target["ref"]) not in already_stale
        ]
        if fresh_targets:
            self._emit(
                "evidence.staled",
                {"targets": fresh_targets, "reason": reason},
                command_id=cmd.command_id,
                task_id=task_id,
            )

    def _stale_prior_task_selection(
        self,
        cmd,
        task: TaskNode,
        nodes: list[str],
        baseline: str,
        selection_id: str,
    ) -> None:
        previous = next(
            (
                ev
                for ev in reversed(list(self.store.events(self.run_id)))
                if ev.type == "test.selected"
                and ev.payload.get("scope") == "task_if"
                and ev.payload.get("task_id") == task.task_id
            ),
            None,
        )
        if previous is None or previous.payload.get("selection_id") == selection_id:
            return
        changes = []
        if sorted(previous.payload.get("task_ifs") or []) != sorted(task.if_ids):
            changes.append("task_if_changed")
        if previous.payload.get("baseline") != baseline:
            changes.append("baseline_changed")
        if sorted(previous.payload.get("nodes") or []) != sorted(nodes):
            changes.append("selected_nodes_changed")
        if not changes:
            changes.append("selection_identity_changed")
        previous_id = str(previous.payload.get("selection_id") or "")
        evidence_refs = {
            str(ref)
            for ev in self.store.events(self.run_id)
            if ev.payload.get("selection_id") == previous_id
            or (ev.payload.get("identity_basis") or {}).get("selection_id") == previous_id
            for ref in (ev.payload.get("evidence_ids") or [])
            if ref
        }
        self._emit_stale_refs(
            cmd,
            task.task_id,
            [previous_id],
            evidence_refs,
            ",".join(changes),
        )

    def _run_green_gate(self, cmd, state: State) -> None:  # pylint: disable=too-many-locals
        task_id = state.current_task_id
        attempt = state.current_attempt + 1
        reason = self._devon_evidence_error("green", state)
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
            return
        cwd, gate_handle = self._ensure_gate_worktree(state)
        try:
            # B39 (run 01M0AMKV T-006 seq 728): regression is judged ONLY on
            # this task's Runtime-captured diff plus its claimed changed_paths
            # touching an R-frozen tests/ file — not on the whole worktree-vs-R
            # tests/ diff. The old full diff misjudged unrelated baseline test
            # additions (ops fixes, earlier batch siblings) as task cheating,
            # false-failing every later task in a batch that lands tests after
            # R. The captured diff still surfaces hidden R-test mutations that
            # Devon omits from changed_paths; the union closes the self-report.
            r_sha = state.r_tree_identity
            if r_sha and r_sha.strip() and r_sha != "0" * 40:
                bad = self._green_regression_tests(r_sha, state)
                if bad:
                    self._emit_gate_failure(
                        cmd,
                        check="regression",
                        reason="Runtime observed R unit tests changed",
                        evidence=json.dumps(
                            {"r_sha": r_sha, "changed_tests": bad},
                            sort_keys=True,
                        ),
                        task_id=task_id,
                        attempt=attempt,
                    )
                    return
            task = self._lookup_task(task_id or "")
            if task is None:
                raise TestSelectError(f"GREEN_GATE task not found: {task_id}")
            selection_evidence = self._execute_task_selection(cmd, state, cwd, task)
            lint_findings, lint_note = self._run_declared_lint(self._lint_targets("green"))
            if lint_findings is not None:
                self._emit_gate_failure(
                    cmd,
                    check="lint",
                    reason="lint findings in GREEN deliverables ([lint].check)",
                    evidence=lint_findings,
                    task_id=task_id,
                    attempt=attempt,
                )
                return
            payload = {"check": "green", "task_id": task_id, **selection_evidence}
            if lint_note:
                payload["lint"] = lint_note
            self._emit("verdict.passed", payload, command_id=cmd.command_id)
        except TaskSelectionFailure as exc:
            # M1-S1 (convergence plan 2026-09-05): mechanical oscillation
            # signature -- consecutive GREEN_GATE failures of this task
            # swapped anchor outcomes. Mutually exclusive anchors admit no
            # writer retry; route the contract authority instead of
            # re-burning the writer budget. The CURRENT failure's anchors
            # (exc.evidence, not yet stored) are compared against the last
            # recorded failure for this task so the signature trips at the
            # SECOND failure, not one attempt later.
            events = [
                {"type": ev.type, "payload": dict(ev.payload or {})}
                for ev in self.store.events(self.run_id)
            ]
            osc = _detect_oscillation(events, task_id, exc.evidence)
            if osc is not None:
                self._emit(
                    "oscillation.detected",
                    {
                        "task_id": task_id,
                        "signature": "S1",
                        **osc,
                        "task_selection": exc.evidence,
                    },
                    command_id=cmd.command_id,
                    task_id=task_id,
                )
                self._emit_gate_failure(
                    cmd,
                    check=_OSCILLATION_CHECK,
                    reason=(
                        "anchor oscillation (S1): healed∧newly_red∧common "
                        "across consecutive attempts — mutually exclusive "
                        "anchors, no writer retry can satisfy both; Archer "
                        "RULING carries the paired-delta decision"
                    ),
                    evidence=json.dumps(
                        {
                            "oscillation": osc,
                            "task_selection": exc.evidence,
                            # OOB 2026-09-05 (baseline-advance fix): carry the
                            # CURRENT failing set at the evidence root so the
                            # next detection compares against THIS round, not
                            # the pre-oscillation baseline. Without it the
                            # contract_conflict verdict is skipped by
                            # last_failed_nodes and the replan->Devon->gate
                            # loop re-fires the identical S1 forever (run
                            # 01M19FJVES7G113RD8QXXY3PQZ seq 3016==3054).
                            "failed_nodes": _parse_failed_nodes(exc.evidence),
                        },
                        sort_keys=True,
                    ),
                    task_id=task_id,
                    attempt=attempt,
                    failure_class="contract_conflict",
                )
                return
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=str(exc),
                evidence=exc.evidence,
                task_id=task_id,
                attempt=attempt,
            )
        except (ContractError, TestSelectError, TestResultError, OSError, UnicodeError) as exc:
            self._emit_gate_failure(
                cmd,
                check="contract_error",
                reason=f"SELECT_TASK failed closed: {type(exc).__name__}: {exc}",
                evidence="task_if selection/contract result",
                task_id=task_id,
                attempt=attempt,
            )
        finally:
            if gate_handle is not None:
                cleanup_worktree(gate_handle)

    def _run_task_review_gate(self, cmd, state: State) -> None:
        task_id = state.current_task_id
        attempt = state.current_attempt + 1
        events = [
            {"seq": ev.seq, "type": ev.type, "payload": dict(ev.payload)}
            for ev in self.store.events(self.run_id)
        ]
        # B84 (#84): scan reversed events for the most recent green event
        # (green.committed or green.no_change) whose task_id matches the
        # current task — never use a stale green.committed from another task.
        green_for_task = next(
            (
                ev
                for ev in reversed(events)
                if ev["type"] in ("green.committed", "green.no_change")
                and ev["payload"].get("task_id") == task_id
            ),
            None,
        )
        if green_for_task is not None and green_for_task["type"] == "green.no_change":
            self._run_task_review_no_change(cmd, task_id, attempt, events)
            return
        if green_for_task is not None:
            self._run_task_review_committed(cmd, task_id, attempt, events, green_for_task)
            return
        any_green_committed = any(ev["type"] == "green.committed" for ev in events)
        if any_green_committed:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="green.committed lacks task identity",
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit_gate_failure(
            cmd,
            check="lineage",
            reason="no green.committed event found",
            task_id=task_id,
            attempt=attempt,
        )

    def _run_task_review_no_change(self, cmd, task_id, attempt, events):
        """No-change review: no new G exists, scope/lineage/secret/provenance
        checks are vacuous — GREEN_GATE already programmatically verified
        the behavior. Budget check still runs. Source: #84 event 9074-9078."""
        task = self._lookup_task(task_id)
        if task is None:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="task lookup failed for no-change review",
                task_id=task_id,
                attempt=attempt,
            )
            return
        manifest = self._current_manifest() or {}
        failure = _task_review_failure(
            str(self.repo),
            self.run_id,
            events,
            task,
            None,
            None,
            {},
            manifest.get("allowed_paths") or [],
            task_id,
            attempt,
        )
        if failure is not None:
            self._emit_gate_failure(
                cmd,
                check=failure["check"],
                reason=failure["reason"],
                evidence=failure.get("evidence", ""),
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit("verdict.passed", {"check": "task_review"}, command_id=cmd.command_id)

    def _run_task_review_committed(self, cmd, task_id, attempt, events, green_ev):
        """Normal green.committed review: verify scope, lineage, secrets,
        provenance, and budget. Source: #84 event 9074-9078."""
        payload = green_ev["payload"]
        task = self._lookup_task(task_id)
        g_sha = payload.get("g_sha")
        base_sha = payload.get("base_sha")
        if task is None or not g_sha or not base_sha:
            self._emit_gate_failure(
                cmd,
                check="lineage",
                reason="green.committed lacks task or base identity",
                task_id=task_id,
                attempt=attempt,
            )
            return
        manifest = self._current_manifest() or {}
        failure = _task_review_failure(
            str(self.repo),
            self.run_id,
            events,
            task,
            g_sha,
            base_sha,
            payload.get("trailers") or {},
            manifest.get("allowed_paths") or [],
            payload.get("task_id") or task_id,
            payload.get("attempt") or attempt,
        )
        if failure is not None:
            self._emit_gate_failure(
                cmd,
                check=failure["check"],
                reason=failure["reason"],
                evidence=failure.get("evidence", ""),
                task_id=task_id,
                attempt=attempt,
            )
            return
        self._emit("verdict.passed", {"check": "task_review"}, command_id=cmd.command_id)

    def _emit_task_gate(self, cmd, state, phase, failure_check, pass_check):
        reason = self._devon_evidence_error(phase, state)
        if reason is None and phase == "red" and state.stage == "M-IMPL":
            outcome = self._last_devon_outcome() or {}
            classification_error = _m_impl_red_classification_error(outcome)
            if classification_error is not None:
                reason, evidence = classification_error
                self._emit(
                    "verdict.failed",
                    {
                        "check": failure_check,
                        "reason": reason,
                        "evidence": evidence,
                        "task_id": state.current_task_id,
                        "attempt": state.current_attempt + 1,
                    },
                    command_id=cmd.command_id,
                    task_id=state.current_task_id,
                )
                return
        if reason is not None:
            self._emit_gate_failure(
                cmd,
                check=failure_check,
                reason=reason,
                evidence="backend Devon outcome",
                task_id=state.current_task_id,
                attempt=state.current_attempt + 1,
            )
            return
        lint_findings, lint_note = None, ""
        if phase == "red":
            # B4 (issue #5): post-agent validation runs before the verdict —
            # Archer-declared lint over the delivered RED test files
            # (mechanical errors surface in the phase that introduced
            # them, not at commit time; T-018 burned 3 attempts on this).
            lint_findings, lint_note = self._run_declared_lint(self._lint_targets("red"))
        if lint_findings is not None:
            self._emit(
                "verdict.failed",
                {
                    "check": "lint",
                    "reason": "lint findings in RED deliverables ([lint].check)",
                    "evidence": lint_findings,
                    "task_id": state.current_task_id,
                    "attempt": state.current_attempt + 1,
                },
                command_id=cmd.command_id,
                task_id=state.current_task_id,
            )
            return
        payload = {"check": pass_check}
        if lint_note:
            payload["lint"] = lint_note
        self._emit("verdict.passed", payload, command_id=cmd.command_id)

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
            self._emit_gate_failure(
                cmd,
                check="impl_defect",
                reason=reason,
                evidence="backend Devon outcome",
                task_id=task_id,
                attempt=attempt,
            )
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
