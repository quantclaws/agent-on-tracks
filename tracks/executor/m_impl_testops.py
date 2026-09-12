"""M-IMPL test operations (mixin ``MImplTestOpsMixin``): manifests, gate
worktrees, lint scope, anchor inventories and selected-layer execution.

Extracted from :mod:`tracks.executor.m_impl_runtime` for module-size
compliance (C0302); code moved verbatim.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from tracks.executor.helpers import git, parse_collected_nodes
from tracks.executor.host_contract import declared_install_interpreter
from tracks.executor.m_impl_anchor import _is_evidence_shape_error
from tracks.executor.quality_gate import (
    execute_gate_command,
    failed_summary_lines,
    observation_evidence,
)
from tracks.executor.rgr import red_base_sha
from tracks.executor.taskgraph import TaskNode, plan_row_targets
from tracks.executor.test_select import (
    TestSelectError,
    parse_test_result,
    require_exact_node_coverage,
    resolve_selected_command,
    select_task,
)
from tracks.executor.test_select import audit as audit_selection_argv
from tracks.executor.test_tasks import _coverage_rows_with_test
from tracks.executor.worktree import WorktreeHandle, create_gate_worktree, ensure_runtime_assets
from tracks.kernel.machine import State
from tracks.project import ContractError, layout_paths, load_contract


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


class MImplTestOpsMixin:
    """M-IMPL test operations mixin."""

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
            # pre_dirty_snapshot is deliberately NOT carried here (single
            # carriage: it rides assignment top-level; see the strip at the
            # copied-manifest refresh in _add_assignment_runtime_fields).
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
        existing = MImplTestOpsMixin._existing_gate_handle(self, task_id)
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
        touched = self._devon_unit_touched()
        acceptance_nodes = self._gate_acceptance_nodes(task, inventories)
        nodes = select_task(red_nodes, touched, inventories["unit"], acceptance_nodes)
        if not nodes:
            raise TestSelectError(f"empty SELECT_TASK for task {task.task_id}")
        return contract, nodes

    def _devon_unit_touched(self) -> list:
        """Changed unit-test paths from the last Devon outcome."""
        return [
            path
            for path in (self._last_devon_outcome() or {}).get("changed_paths", [])
            if str(path).startswith("tests/unit/")
        ]

    def _gate_acceptance_nodes(self, task: TaskNode, inventories: dict) -> list:
        """Declared acceptance + e2e anchors resolved against inventories."""
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
        return acceptance_nodes

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

    _LINT_TIMEOUT_SECONDS = 120
