"""M-VERIFY closure evidence producer (interfaces §1g/§1i, IF-CLOSURE-001).

Drives the REAL per-AC candidate-bound evidence chain the trace gate joins
over: for every approved AC without same-candidate evidence, resolve the
version's counterexample binding (kill-manifest primary, closure-bindings
supplement), build the mutation manifest (``build_manifest``), execute the
genuine isolated experiment (``run_mutation_experiment`` over real git
worktree / git apply / adapter-selected node runs) and emit the contracted events:

- ``phase0.baseline_repaired`` (bound node + candidate digest, §1a row 1)
- ``mutation.manifest`` (§1g field set, status declared, §1a row 8)
- ``mutation.experiment`` (§1a row 9, only on a genuinely run experiment)

Fail-closed discipline: a blocked experiment (target survived, control hit,
stale patch, dirty rollback) emits its blocked events and STOPS the chain --
no ``run_local_gates`` follows, no evidence is invented. Idempotent per
(candidate, AC): re-drives skip ACs whose manifest already binds the frozen
candidate (append-only history, no duplicate rounds).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from tracks.adapters.base import TestRunResult
from tracks.executor import mutation as _mutation_facade  # facade-first (cycle-safe)
from tracks.executor.helpers import git
from tracks.kernel.events import Command

# Per-node execution budget: single nodes run in seconds; the bound keeps a
# pathological node from hanging the chain (fail-closed on timeout).
_NODE_TIMEOUT_SECONDS = 900


class ClosureBindingsError(RuntimeError):
    """Malformed/unresolvable closure binding table (fail closed)."""


def _load_binding_tables(repo: Path, version: str) -> dict[str, dict]:
    """Merge kill-manifest (§12.2 primary) with the version's closure-bindings
    supplement into one ``{ac: binding}`` map (supplement wins on conflict).

    Both files are Shield/operator-owned assets under the version's tests/
    counterexamples directory and project dir respectively; neither is a
    product document, so the runtime reads them as data (test-plan §12.2:
    ``由 Runtime 在隔离 worktree 真跑对应真实 gate 验证 kill``).
    """
    merged: dict[str, dict] = {}
    primary = repo / "tests" / "counterexamples" / version / "kill-manifest.json"
    if primary.is_file():
        merged.update(_parse_bindings(primary, label=str(primary)))
    supplement = (
        repo / ".tracks" / "projects" / version / "closure-bindings.json"
    )
    if supplement.is_file():
        merged.update(
            _parse_bindings(supplement, label=str(supplement), supplement=True)
        )
    return merged


def _parse_bindings(
    path: Path, *, label: str, supplement: bool = False
) -> dict[str, dict]:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClosureBindingsError(f"{label}: unreadable ({exc})") from exc
    rows = blob.get("bindings") if isinstance(blob, dict) else None
    if not isinstance(rows, list):
        raise ClosureBindingsError(f"{label}: no bindings array")
    table: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ClosureBindingsError(f"{label}: non-object binding row")
        ac = row.get("ac")
        patch = row.get("patch")
        test = row.get("test")
        if_ref = row.get("if_ref")
        if not (
            isinstance(ac, str)
            and isinstance(patch, str)
            and isinstance(test, str)
            and isinstance(if_ref, str)
            and ac
            and patch
            and test
            and if_ref
        ):
            raise ClosureBindingsError(f"{label}: binding missing ac/patch/test/if_ref")
        table[ac] = {
            "ac": ac,
            "patch": patch,
            "test": test,
            "control": row.get("control") or "",
            "if_ref": if_ref,
        }
        if supplement and not table[ac]["control"]:
            raise ClosureBindingsError(
                f"{label}: supplement binding {ac} must declare an explicit control node"
            )
    return table


def _patch_paths(repo: Path, version: str, patch: str) -> Path:
    for base in (
        repo / "tests" / "counterexamples" / version / patch,
        repo / "tests" / "counterexamples" / patch,
    ):
        if base.is_file():
            return base
    raise ClosureBindingsError(f"patch {patch} not found for {version}")


def _allowed_change_scope(patch_file: Path) -> list[str]:
    """Product paths touched by the patch (diff --git headers, sorted)."""
    text = patch_file.read_text(encoding="utf-8", errors="replace")
    paths = {
        line[11:].split(" b/", 1)[0]
        for line in text.splitlines()
        if line.startswith("diff --git a/")
    }
    return sorted(p for p in paths if p)


def _open_worktree_factory(repo: Path, candidate_sha: str):
    from tracks.executor.worktree import ensure_runtime_assets

    def open_worktree(kind: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix=f"closure-{kind}-"))
        target = root / "wt"
        git(repo, "worktree", "add", "--detach", str(target), candidate_sha)
        # Link the host-declared gitignored environment into the worktree
        # (B59 #75 semantics): the host's run_selected resolves its relative
        # interpreter inside the candidate tree, imports stay worktree-first.
        ensure_runtime_assets(str(repo), str(target))
        return target

    return open_worktree


def _apply_patch_factory(patch_file: Path, patch_digest: str):
    def apply_patch(worktree: Path, expected_digest: str) -> str:
        if expected_digest != patch_digest:
            return "stale_patch:digest_mismatch"
        check_proc = subprocess.run(
            ["git", "apply", "--check", str(patch_file.resolve())],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )
        if check_proc.returncode != 0:
            # v0.7 §1g vocabulary: apply failures report a mismatch marker so
            # the experiment classifies them as stale_patch (blocked), never
            # as a silently unmutated target-survived run.
            return f"stale_patch_mismatch:{check_proc.stderr.strip()[:180]}"
        apply_proc = subprocess.run(
            ["git", "apply", str(patch_file.resolve())],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
        )
        if apply_proc.returncode != 0:
            return f"stale_patch_mismatch:{apply_proc.stderr.strip()[:180]}"
        return "identity_match"

    return apply_patch


def _resolve_closure_adapter(repo: Path):
    """Resolve the host-declared adapter + run section (fail closed).

    Language neutrality (NFR-0147): the runtime never guesses a host
    toolchain -- the contract's ``[adapter]`` declaration is the only
    channel; an undeclared/unknown host blocks the evidence step.
    """
    from tracks.adapters.base import UnknownAdapterError, resolve_adapter
    from tracks.project import load_contract

    contract = load_contract(repo)
    declared = getattr(contract, "adapter", None)
    section = getattr(contract, "integration", None) or contract.unit
    if declared is None or section is None:
        raise ClosureBindingsError("host contract declares no adapter")
    try:
        adapter = resolve_adapter(declared.id, declared.protocol, declared.version)
    except UnknownAdapterError as exc:
        raise ClosureBindingsError(str(exc)) from exc
    return adapter, section


def _execute_selected_nodes(adapter, section, cwd: Path, result_path: Path, nodes):
    """Resolve + execute the host run_selected for *nodes*; returns the
    subprocess CompletedProcess (never raises on command failure)."""
    argv = adapter.run_selected(section.run_selected, list(nodes), result_path, cwd)
    return subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_NODE_TIMEOUT_SECONDS,
        check=False,
    )


def _error_all(nodes, detail: str) -> dict[str, TestRunResult]:
    """Fail-closed per-node error results (shared by the guard paths)."""
    return {
        node: TestRunResult(node_id=node, status="error", detail=detail[:300])
        for node in nodes
    }


def _run_nodes_factory(repo: Path):
    """Run nodes through the HOST-DECLARED adapter (language-neutral, NFR-0147).

    The selected nodes execute via the contract's ``run_selected`` template
    (adapter protocol: resolve -> runtime executes -> parse the strict
    tracks-test-result file with EXACT node coverage, RED_CHECK parity).
    Fail-closed guards: a missing result file on failure, or a node absent
    from the parsed result, never credits a kill.
    """
    from tracks.executor.test_select import (
        parse_test_result,
        require_exact_node_coverage,
    )

    adapter, section = _resolve_closure_adapter(repo)

    def run_nodes(worktree: Path, nodes) -> dict[str, TestRunResult]:
        cwd = worktree if section.cwd == "." else worktree / section.cwd
        results: dict[str, TestRunResult] = {}
        import tempfile

        with tempfile.TemporaryDirectory(prefix="closure-node-") as tmp:
            result_path = Path(tmp) / "result.xml"
            try:
                proc = _execute_selected_nodes(
                    adapter, section, cwd, result_path, nodes
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _error_all(nodes, str(exc))
            if not result_path.exists():
                # The result file IS the evidence: without it an exit code
                # proves nothing about WHICH nodes failed (never a vacuous
                # kill) -- fail closed per node instead.
                detail = (proc.stderr or proc.stdout) or f"no result file (exit={proc.returncode})"
                return _error_all(nodes, detail)
            mapping = require_exact_node_coverage(
                parse_test_result(result_path), list(nodes)
            )
            for node in nodes:
                case = mapping[node]
                results[node] = TestRunResult(
                    node_id=node, status=case.status, detail=case.detail
                )
        return results

    return run_nodes


def _close_worktree_factory(repo: Path):
    def close_worktree(worktree: Path) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        parent = worktree.parent
        if parent.name.startswith("closure-"):
            import shutil

            shutil.rmtree(parent, ignore_errors=True)

    return close_worktree


def _manifest_digest(manifest) -> str:
    canonical = json.dumps(
        {
            "protocol_version": manifest.protocol_version,
            "ac": manifest.ac,
            "if_ref": manifest.if_ref,
            "candidate_digest": manifest.candidate_digest,
            "patch_digest": manifest.patch_digest,
            "target_nodes": list(manifest.target_nodes),
            "control_nodes": list(manifest.control_nodes),
            "runner_identity": manifest.runner_identity,
            "allowed_change_scope": list(manifest.allowed_change_scope),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def _manifest_blob(manifest) -> dict:
    """§1g field set as a JSON-ready blob for the event payload."""
    return {
        "protocol_version": manifest.protocol_version,
        "ac": manifest.ac,
        "if_ref": manifest.if_ref,
        "candidate_digest": manifest.candidate_digest,
        "patch_digest": manifest.patch_digest,
        "target_nodes": list(manifest.target_nodes),
        "control_nodes": list(manifest.control_nodes),
        "runner_identity": manifest.runner_identity,
        "allowed_change_scope": list(manifest.allowed_change_scope),
        "expected_result": dict(manifest.expected_result),
    }


class ExecClosureEvidenceMixin:
    """``produce_closure_evidence`` executor face (M-VERIFY chain)."""

    def _do_produce_closure_evidence(self, cmd, state, task_id, reconcile):
        """Produce the per-AC candidate-bound chain, then hand to the local
        gates. Fail-closed: any blocked/failed experiment stops the chain
        (the stop itself routes through the park disposition -- never a
        guessed pass)."""
        del reconcile, state, task_id
        params = dict(cmd.params or {})
        candidate_sha = params.get("candidate_sha", "")
        version = self.version
        if not candidate_sha or not version:
            raise ValueError("produce_closure_evidence requires candidate_sha+version")
        from tracks import paths
        from tracks.checks.trace import approved_acs

        bindings = _load_binding_tables(Path(self.repo), version)
        if not bindings:
            # Capability gating: a version that ships no counterexample
            # assets declares no closure duty -- the step is a pass-through
            # and the declared gates (if any) remain the only authority.
            self.issue(
                Command(
                    kind="run_local_gates", params={"candidate_sha": candidate_sha}
                )
            )
            return
        acs = approved_acs(paths.projects_dir(self.store.home), version)
        existing = self._closure_existing(candidate_sha)
        failures = self._produce_pending(
            acs, existing, bindings, candidate_sha, version, cmd
        )
        if failures:
            self._emit(
                "closure_evidence.failed",
                {
                    "candidate_sha": candidate_sha,
                    "failures": failures[:50],
                    "failed_count": len(failures),
                },
                command_id=cmd.command_id,
            )
            return
        self.issue(
            Command(kind="run_local_gates", params={"candidate_sha": candidate_sha})
        )

    def _produce_pending(
        self, acs, existing, bindings, candidate_sha, version, cmd
    ) -> list[str]:
        """Produce evidence for every approved AC not yet closed for this
        candidate; returns the fail-closed failure list (empty = all green)."""
        failures: list[str] = []
        for ac in acs:
            if ac in existing:
                continue
            binding = bindings.get(ac)
            if binding is None:
                failures.append(f"{ac}:no_binding")
                continue
            failures.extend(
                self._produce_one_binding(cmd, candidate_sha, version, binding)
            )
        return failures

    def _closure_existing(self, candidate_sha: str) -> set[str]:
        """ACs already carrying a same-candidate manifest (idempotent re-drive)."""
        done: set[str] = set()
        for event in self.store.events(self.run_id):
            if event.type != "mutation.manifest":
                continue
            payload = event.payload or {}
            if payload.get("candidate_digest") != candidate_sha:
                continue
            ac = str(payload.get("ac") or "").split("@", 1)[0]
            if ac:
                done.add(ac)
        return done

    def _produce_one_binding(
        self, cmd, candidate_sha: str, version: str, binding: Mapping
    ) -> list[str]:
        repo = Path(self.repo)
        patch_file = _patch_paths(repo, version, binding["patch"])
        patch_digest = "sha256:" + hashlib.sha256(patch_file.read_bytes()).hexdigest()
        manifest = _mutation_facade.build_manifest(
            ac=f"{binding['ac']}@{version}",
            if_ref=binding["if_ref"],
            candidate_digest=candidate_sha,
            patch_digest=patch_digest,
            target_nodes=[binding["test"]],
            control_nodes=[binding["control"]],
            runner_identity="runtime:mutation-v1",
            allowed_change_scope=_allowed_change_scope(patch_file),
        )
        manifest_digest = _manifest_digest(manifest)
        result = _mutation_facade.run_mutation_experiment(
            manifest,
            repo,
            _open_worktree_factory(repo, candidate_sha),
            _apply_patch_factory(patch_file, patch_digest),
            _run_nodes_factory(repo),
            _close_worktree_factory(repo),
        )
        experiment_payload = {
            "manifest_digest": manifest_digest,
            "status": result.status,
            "baseline": result.baseline,
            "apply": result.apply,
            "target_kill": result.target_kill,
            "controls": result.controls,
            "rollback": result.rollback,
            "command_echo": [["git", "worktree"], ["git", "apply"], ["adapter", "run_selected"]],
            "env_fingerprint": "runtime:closure-evidence-v1",
            "node_results_ref": result.node_results_ref,
            "failure_signatures": [result.blocked_reason] if result.blocked_reason else [],
            "digests": {"candidate": candidate_sha, "patch": patch_digest},
        }
        self._emit(
            "mutation.experiment",
            experiment_payload,
            command_id=cmd.command_id,
        )
        if result.status != "passed":
            self._emit(
                "mutation.manifest",
                {
                    "manifest_digest": manifest_digest,
                    "manifest_blob": _manifest_blob(manifest),
                    "status": "blocked",
                    "reason": result.blocked_reason,
                    "ac": manifest.ac,
                    "if_ref": manifest.if_ref,
                    "candidate_digest": candidate_sha,
                    "patch_digest": patch_digest,
                },
                command_id=cmd.command_id,
            )
            return [f"{binding['ac']}:{result.blocked_reason}"]
        self._emit(
            "mutation.manifest",
            {
                "manifest_digest": manifest_digest,
                "manifest_blob": _manifest_blob(manifest),
                "status": "declared",
                "ac": manifest.ac,
                "if_ref": manifest.if_ref,
                "candidate_digest": candidate_sha,
                "patch_digest": patch_digest,
            },
            command_id=cmd.command_id,
        )
        node_id = binding["test"]
        evidence_id = hashlib.sha256(
            f"{node_id}@{version}@{candidate_sha}".encode()
        ).hexdigest()
        self._emit(
            "phase0.baseline_repaired",
            {
                "ac": f"{binding['ac']}@{version}",
                "bound_node": {"node_id": node_id, "digest": candidate_sha},
                "selection_id": f"closure-{binding['ac'].lower()}-{candidate_sha[:12]}",
                "evidence_id": evidence_id,
                "command_echo": ["closure-evidence", binding["patch"], node_id],
                "status": "repaired",
            },
            command_id=cmd.command_id,
        )
        return []


