"""M-IMPL assignment materialization and evidence surface (mixin
``MImplAnchorMixin``).

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path

from tracks.effects.backend import valid_test_tasks
from tracks.executor.taskgraph import parse_tasks_json
from tracks.executor.test_tasks import resolve_inherited_baseline_docs
from tracks.executor.validate import parse_test_tasks
from tracks.kernel.machine import _M_IMPL_CRITERIA_PACK, State


def _manifest_path_matches(path: str, rule: str) -> bool:
    if rule.endswith("/**"):
        root = rule[:-3].rstrip("/")
        return path == root or path.startswith(root + "/")
    return path == rule or path.startswith(rule.rstrip("/") + "/")


def _combined_provenance(task) -> list[str]:
    """Combine current task ac_refs + fr_refs into the single Tracks-AC
    provenance list: de-duplicated with stable deterministic ordering."""
    return list(dict.fromkeys((*task.ac_refs, *task.fr_refs)))


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


def _is_evidence_shape_error(reason: str | None) -> bool:
    if not isinstance(reason, str) or not reason:
        return False
    if "contains an invalid path" in reason:
        return True
    return reason.startswith(_EVIDENCE_SHAPE_PREFIXES)


_EVIDENCE_SHAPE_PREFIXES = (
    "Devon evidence missing: ",
    "Devon evidence phase mismatch",
    "Devon evidence changed_paths must be a list",
    "Devon evidence commands must be a list",
    "Devon evidence is missing pre/post identity",
    "Devon evidence implemented_if_ids must be a list",
    "Devon evidence missing r_identity",
)


# b92 R3 role grading: Devon RGR substates and Shield write substates that
# receive the trimmed per-task test_tasks slice (anchor_surface stays None).
_DEVON_RGR_SUBSTATES = frozenset({"RED", "GREEN", "REFACTOR"})


_SHIELD_WRITE_SUBSTATES = frozenset({"WRITE", "SHIELD_FIX"})


class MImplAnchorMixin:
    """M-IMPL assignment/evidence surface mixin."""

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
            # Single carriage (operator OOB 2026-09-06, user-authorized):
            # pre_dirty_snapshot rides assignment top-level only. Strip the
            # manifest copy a carried-over previous manifest may still hold
            # (state.current_manifest is the previous card's manifest), and
            # _task_manifest no longer writes it either — the dual carriage
            # was the pathology class M5's audit-first rule targets.
            manifest.pop("pre_dirty_snapshot", None)
            manifest.update({"result_identity": result_identity})
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
        missing = [key for key in required if MImplAnchorMixin._assignment_key_missing(data, key)]
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

    _DIAGNOSED_TEST_PATH_RE = re.compile(r"tests/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+")

    _BARE_TEST_FILENAME_RE = re.compile(r"(?<![A-Za-z0-9_./])[A-Za-z0-9_./-]+\.(?:py|json|toml|md)")
